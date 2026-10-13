# TASK-883: BETWEEN suffix negation and format-aware template placeholder detection

**Feature**: FEAT-170 — filter_values / abstract edge cases — BETWEEN key negation and template placeholder detection
**Spec**: `sdd/specs/parsers-abstract-tech-debt.spec.md`
**Status**: done
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:1149c6ed91ea

---

## Context

Ledger issue `issue:1149c6ed91ea` (spec §1). `parse_between` only negates when
`!` is the last suffix character, so `amount!~` silently loses its negation.
`set_conditions` detects template placeholders with a `'{base}' in template`
substring test, which misses `{base!s}` / `{base:fmt}` and matches an escaped
`{{base}}`. Implements spec §3 Module 1.

---

## Scope

- Fix `key_negated` in `parse_between`: negate when `"!"` occurs anywhere in the stripped suffix.
- Add `template_fields(template) -> frozenset[str]` to `filter_values.py` and export it.
- Use `template_fields` in `AbstractParser.set_conditions` (computed once per call).
- Add unit and parser-level tests.

**NOT in scope**: builder last-char suffix handling in `sql.pyx`/`pgsql.pyx`; any Rust change.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/filter_values.py` | MODIFY | Fix key_negated; add template_fields |
| `querysource/parsers/abstract.pyx` | MODIFY | Use template_fields in set_conditions |
| `tests/test_filter_values.py` | MODIFY | Unit tests |
| `tests/test_dialect_typed_filters.py` | MODIFY | Parser-level placeholder tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .filter_values import TYPED_FILTER_FORMATS, base_key, is_comparison_dict, parse_between  # verified: abstract.pyx:21
import string  # stdlib, for string.Formatter
```

### Existing Signatures to Use
```python
# querysource/parsers/filter_values.py
KEY_SUFFIX_CHARS: str = "|!~#@:"   # line 15
def base_key(key: str) -> str:      # line 44
def parse_between(key: str, value: str) -> BetweenClause | None:  # line 95
# querysource/parsers/abstract.pyx
async def set_conditions(self, conditions: dict, connection: object) -> dict:  # line 548
# tests/test_dialect_typed_filters.py
async def render(conditions: dict, monkeypatch, query: str = SQL) -> str:  # line 54
```

### Does NOT Exist
- ~~`filter_values.template_fields`~~ — created by this task.
- ~~Rust `parse_between`~~ — no mirror.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/filter_values.py", "action": "MODIFY"},
    {"path": "querysource/parsers/abstract.pyx", "action": "MODIFY"},
    {"path": "tests/test_filter_values.py", "action": "MODIFY"},
    {"path": "tests/test_dialect_typed_filters.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/filter_values.py#parse_between",
    "sym:querysource/parsers/filter_values.py#base_key",
    "sym:querysource/parsers/abstract.pyx#set_conditions"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. In `filter_values.py`, replace the `key_negated` line with a suffix check, because BETWEEN collapses the key to `base_key(key)`, so every suffix except `!` is meaningless here and must not hide the negation.
2. Add `template_fields()` after `base_key`, because placeholder detection has to follow `format_map` field semantics.
3. In `abstract.pyx`, compute `fields = template_fields(template)` once, then test `base not in fields`, because parsing the template once per call keeps it O(1) per element.
4. Rebuild Cython in place, then run the validation commands.

### `querysource/parsers/filter_values.py` (MODIFY)
```python
# REPLACE `key_negated = base_key(key) != key and key.rstrip().endswith("!")`
# occurrences: 1 (verified: grep -c line 109)
    key_negated = "!" in key[len(base_key(key)):]

# AFTER base_key() (verified: line 44)
def template_fields(template: str) -> frozenset[str]:
    """Return the base field names a ``str.format_map`` template substitutes.
    # FILL IN: Formatter().parse; strip conversion/spec; cut at '.'/'['; skip None/''.
    #   On ValueError fall back to brace-delimited names (AC2).
    """
```

### `querysource/parsers/abstract.pyx` (MODIFY)
```python
# anchor `template = self.query_raw or ''` occurrences: 1 (line 555)
        fields = template_fields(template)
# anchor `and '{' + base + '}' not in template` occurrences: 1 (line 561)
                and base not in fields
```

### FILL IN checklist
- [ ] `template_fields` body and fallback (AC2)
- [ ] test bodies (AC1, AC3)

---

## Acceptance Criteria

- [ ] AC1: `!` anywhere in the suffix negates BETWEEN; existing negation tests pass.
- [ ] AC2: `template_fields` returns base names and ignores escaped braces; malformed templates do not raise.
- [ ] AC3: `set_conditions` uses `template_fields`; the escaped-brace and conversion cases behave per spec §4.
- [ ] AC4: the validation commands pass after `python setup.py build_ext --inplace`.
- [ ] AC5: `ruff check` is clean on the touched Python files.

## Validation Commands
- `pytest tests/test_filter_values.py -q`
- `pytest tests/test_dialect_typed_filters.py -q`

---

## Completion Note

**Completed**: 2026-10-13 by agent:sdd-fix (commit 44053fee)

- `parse_between`: `key_negated = "!" in key[len(base_key(key)):]`.
- New `filter_values.template_fields()` uses `string.Formatter().parse`, with a regex fallback on `ValueError`. It is exported in `__all__`.
- `AbstractParser.set_conditions` computes the fields once per call and tests `base not in fields`.
- Tests: 5 unit tests in `tests/test_filter_values.py` and 2 parser-level tests in `tests/test_dialect_typed_filters.py`.
- Validation, after `build_ext --inplace`: the two validation modules give 43 passed. Related parser suites give 191 passed. `ruff` is clean.
- Ledger: resolves `issue:1149c6ed91ea`.
