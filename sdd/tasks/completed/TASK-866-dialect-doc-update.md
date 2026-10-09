# TASK-866: Update `QUERYSOURCE_DIALECT.md` — replace known-issue notes

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-865
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6 / §5. `docs/QUERYSOURCE_DIALECT.md` currently documents the
three bugs as known issues. Once TASK-865 proves the fixed output, the doc must
describe the fixed behaviour with verified SQL.

---

## Scope

Update these sections (use the exact SQL asserted in `tests/test_dialect_filter_parity.py`):
- §5.3: replace the "one comparison per column / last one kept" note with the
  AND-ed multi-operator behaviour (`{">": 1, "<": 9}` → `(x > '1' AND x < '9')`).
- §5.4: retitle to ``### 5.4 `BETWEEN` ``; document `BETWEEN` / `NOT BETWEEN`,
  the `!` suffix, the bound grammar (numbers, quoted, bare tokens, date
  keywords) and the `ParserError` for malformed clauses.
- §5.5: restore the `!` → `NOT BETWEEN` and `|` overlap rows.
- §7.1: document implicit containment as supported (all keys, correct JSON);
  keep `@>` as the recommended explicit form.
- §8: replace the "Not reachable" note: typed columns work when filtered via
  `filter`/`where_cond` and the key is not a template placeholder.
- §13.3: unknown `@name` → `ParserError` (HTTP 400).
- §15 matrix row "Typed arrays / ranges" → "yes" for PostgreSQL; §16 errors table;
  §18 quick reference: re-add `BETWEEN` and implicit containment lines.

- Add `tests/test_dialect_doc_examples.py`: every SQL example inside the
  §5.3/§5.4/§7.1/§8 tables must appear in `CORPUS` of
  `tests/test_dialect_filter_parity.py` (TASK-865), so doc drift fails CI.

**NOT in scope**: any implementation code change.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/QUERYSOURCE_DIALECT.md` | MODIFY | replace known-issue notes |
| `tests/test_dialect_doc_examples.py` | CREATE | doc examples ⊆ parity corpus |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# none — documentation only
```

### Existing Signatures to Use
```text
docs/QUERYSOURCE_DIALECT.md
### 5.4 `BETWEEN` (known issue)          (occurrences: 1)
### 7.1 Containment: always use explicit `@>`   (occurrences: 1)
## 8. Typed columns: arrays and ranges    (occurrences: 1)
### 13.3 `@variables`                     (occurrences: 1)
```

### Does NOT Exist
- ~~SQL Server comparison-dict support~~ — do not document it (spec Non-Goals).
- ~~typed filters for flat (non-`filter`) keys~~ — later feature (spec §8 Q2).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/QUERYSOURCE_DIALECT.md", "action": "MODIFY"},
    {"path": "tests/test_dialect_doc_examples.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Run `pytest tests/test_dialect_filter_parity.py -v` and copy rendered SQL from its corpus — *why*: the doc must only show verified output.
2. Edit each section listed in Scope — *why*: AC "doc updated accordingly".
3. Grep the doc for `known issue`, `Currently broken`, `Not reachable`, `unreliable` — none may remain.

### `docs/QUERYSOURCE_DIALECT.md` (MODIFY)
```markdown
# occurrences: 1 (verified: grep -c '### 5.4 `BETWEEN` (known issue)' docs/QUERYSOURCE_DIALECT.md)
# REPLACE the whole §5.4 section with:
### 5.4 `BETWEEN`

| Request | SQL |
|---|---|
| `{"amount": "BETWEEN 100 AND 500"}` | `(amount BETWEEN 100 AND 500)` |
| `{"amount!": "BETWEEN 1 AND 5"}` | `(amount NOT BETWEEN 1 AND 5)` |
<!-- FILL IN: date bounds (quoted + bare), date keywords, NOT BETWEEN prefix,
     the bound grammar and the ParserError message — bounded by tests/test_dialect_filter_parity.py -->
```
**Why**: the table format matches §5.1/§5.2; every row is backed by a parity test.

### `tests/test_dialect_doc_examples.py` (CREATE)
```python
"""FEAT-165: SQL shown in the dialect doc is backed by the parity corpus."""
import re
from pathlib import Path

from tests.test_dialect_filter_parity import CORPUS  # created by TASK-865

DOC = Path(__file__).resolve().parent.parent / "docs" / "QUERYSOURCE_DIALECT.md"


def _section(text: str, heading: str) -> str:
    """Return the markdown between ``heading`` and the next heading of the same or higher level."""
    # FILL IN: slice the section — bounded by the headings listed in Scope
    raise NotImplementedError


def test_doc_examples_are_verified():
    expected = {sql for _, sql in CORPUS}
    # FILL IN: collect `...` SQL cells from the 'SQL' column of the §5.3/§5.4/§7.1/§8 tables
    #          and assert each is in `expected` (or a substring of one) — bounded by AC
```
*(If `tests` is not importable as a package, load `CORPUS` with `importlib.util.spec_from_file_location` instead.)*

### FILL IN checklist
- [ ] `test_dialect_doc_examples.py` section slicing and table parsing
- [ ] §5.3, §5.4, §5.5, §7.1, §8, §13.3, §15, §16, §18 — bounded by the parity corpus

---

## Acceptance Criteria

- [ ] No "known issue" / "Currently broken" / "Not reachable" / "unreliable" wording remains for the fixed behaviours
- [ ] Every new SQL example appears in `tests/test_dialect_filter_parity.py`

## Validation Commands

- `pytest tests/test_dialect_doc_examples.py -q`

## Completion Note

Attempts 1–2 failed (gpt-5.6-terra: empty_delivery; codex-spark: model unsupported on ChatGPT account — infrastructure, no feedback filed). Attempt 3 implemented by the orchestrator (docs-only, standard). Doc sections 5.3/5.4/5.5/7.1/8/13.3/15/16/18 updated; `tests/test_dialect_doc_examples.py` imports `PG_CORPUS` + the typed-filter parametrisation (the parity module has no `CORPUS`) and checks every SQL cell of 5.3/5.4/7.1/8. Test passes, ruff clean. No review recorded (no coder attempt to attribute).
