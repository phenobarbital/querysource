# TASK-707: Wire provider `raw_query()` to the validating substitution

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-706
**Assigned-to**: unassigned

---

## Context

Raw slugs (`is_raw=True`) call `raw_query()`/`get_raw_query()`, which currently do
`sql.format_map(defaultdict(str, SafeDict(**conditions)))` — the direct injection
sink. This task replaces those calls with the validating Rust substitution
(`safe_format_map_validated`) and maps a rejection to a clean HTTP 400. Implements
spec §3 Module 4.

---

## Scope

- In each provider's `raw_query()` / `get_raw_query()`, replace the
  `format_map(SafeDict(...))` call with `safe_format_map_validated(template,
  conditions, cond_definition)`, where `conditions = {**self.replacement,
  **(self._conditions or {})}` and `cond_definition` is the slug's definition
  metadata (pass `{}` when unavailable — see TASK-704 fallback).
- Catch the rejection exception and raise `ParserError` (already imported in these
  modules) so the handler returns HTTP 400 with a generic message — never echo the
  payload or DB error.
- Apply to: `sql.py`, `default.py`, `mysql.py`, `sqlserver.py`, `cassandra.py`,
  `documentdb.py`.
- Add unit tests per provider: benign render works; PoC payload → `ParserError`.

**NOT in scope**: `filter_conditions` (TASK-705/708), Rust impl (TASK-704), datasource
(TASK-709). Do not change the `is_raw` selection logic or provider constructors.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/sql.py` | MODIFY | `raw_query` (162), `get_raw_query` (171) → validating call |
| `querysource/providers/default.py` | MODIFY | `raw_query` (67) → validating call |
| `querysource/providers/mysql.py` | MODIFY | same pattern |
| `querysource/providers/sqlserver.py` | MODIFY | same pattern |
| `querysource/providers/cassandra.py` | MODIFY | same pattern |
| `querysource/providers/documentdb.py` | MODIFY | `get_raw_query` (uses `format_map`) |
| `tests/unit/test_provider_raw_query_validated.py` | CREATE | benign + injection tests |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Verified Imports
```python
from querysource.qs_parsers import _qs_parsers as _rs   # exposes safe_format_map_validated (after TASK-706)
from querysource.exceptions import ParserError          # already used in providers (e.g. default.py)
```

### Existing Signatures to Use
```python
# querysource/providers/sql.py
class sqlProvider(...):
    def raw_query(self, query: str):                       # line 162
        sql = query
        conditions = {**self.replacement}                  # line 164
        if self._conditions:                               # request-derived (BaseProvider._conditions)
            conditions = {**conditions, **self._conditions} # line 166
        return sql.format_map(defaultdict(str, SafeDict(**conditions)))  # line 167  ← REPLACE
    def get_raw_query(self, query: str): ...               # line 171 (format_map at 175/179) ← REPLACE

# querysource/providers/default.py
class defaultProvider(...):
    def raw_query(self, query: str): ...                   # line 67 (format_map at 72) ← REPLACE

# querysource/providers/abstract.py
class BaseProvider(...):
    replacement: dict = { ... }                            # line 28
    self._conditions = copy.deepcopy(conditions)           # line 82 (request-derived)
    self._definition  # the QueryModel (has query_raw/is_raw); source of cond_definition metadata
```

### Does NOT Exist
- ~~`pgProvider.raw_query`~~ — pg inherits `raw_query` from `sqlProvider`; editing
  `sql.py` covers PostgreSQL. Do NOT add a `raw_query` to `pg.py`/`db.py`.
- ~~`self.cond_definition` on the provider~~ — that attribute lives on the *parser*
  (`AbstractParser.cond_definition`). On the provider, derive cond metadata from
  `self._definition` (the QueryModel) or pass `{}` and rely on TASK-704's safe default.

---

## Implementation Notes

### Pattern to Follow
```python
# Conceptual (NOT final code): replace format_map with validated substitution
conditions = {**self.replacement, **(self._conditions or {})}
try:
    return _rs.safe_format_map_validated(query, conditions, cond_def or {})
except Exception as err:
    raise ParserError(f"Invalid query conditions") from err   # generic; no payload echo
```

### Key Constraints
- Keep `SafeDict`/`defaultdict` semantics for *unmatched* placeholders (left intact) —
  TASK-704 preserves this; do not reintroduce raw `format_map` as a fallback.
- Generic error text only; log details with `self._logger` server-side.

### References in Codebase
- `querysource/providers/sql.py:124-135` — where `raw_query` is invoked (`is_raw` path).

---

## Acceptance Criteria

- [ ] All six providers route raw substitution through `safe_format_map_validated`.
- [ ] PoC payloads raise `ParserError` → HTTP 400 with generic message (no payload/DB
      error in body).
- [ ] Benign raw slug renders correctly and returns rows (regression).
- [ ] `ruff check querysource/providers` clean; `pytest tests/unit/test_provider_raw_query_validated.py -v` passes.
- [ ] No `format_map(SafeDict(...))` remains in the touched providers
      (`grep -rn "format_map" querysource/providers` shows none in raw_query paths).

---

## Test Specification

```python
# tests/unit/test_provider_raw_query_validated.py
import pytest
from querysource.exceptions import ParserError
# construct a provider (or call raw_query directly) with conditions set

def test_benign_renders():
    # raw_query("WHERE client_slug = '{client_slug}'") with {"client_slug":"acme"}
    ...

def test_injection_raises_parsererror():
    with pytest.raises(ParserError):
        ...  # conditions = {"k": 'x" IS NULL UNION SELECT version()--'}
```

---

## Agent Instructions

1. Confirm TASK-706 in `completed/` (the symbol must be importable).
2. Update index → `in-progress`.
3. `source .venv/bin/activate` before any python/pytest.
4. Implement across the six providers; add tests.
5. Move to `completed/`, update index, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none

## Completion Notes
✅ Completed: 2026-06-19T15:38:15+00:00
✅ Status: verified
