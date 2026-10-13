# TASK-887: Python/Cython partial-match keys: base column, `!` flips operator, other suffixes rejected

**Feature**: FEAT-173 — Partial-match keys honour suffixes: base column, `!` negates, others rejected
**Spec**: `sdd/specs/parsers-bigquery-fixes.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-886
**Assigned-to**: unassigned
**discovered_from**: issue:36f51186eac1

---

## Context

Spec §2/§3 Module 2. This task is the Python twin of TASK-886: `validate_partial_match_dict` applies the suffix rule,
and the four Cython render sites use `base_key(key)`.

## Scope

- Add `resolve_key_suffix(key, op) -> PartialMatchOp` to `partial_matching.py`, and return its result from `validate_partial_match_dict`.
- `sql.pyx:195`, `sqlserver.pyx:145` and `pgsql.pyx:396`: pass `base_key(key)`. `bigquery.pyx`: build the partial-match `field_expr` from the base key.
- Add the end-to-end test module `tests/test_partial_match_key_suffix.py`.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/partial_matching.py` | MODIFY | resolve_key_suffix; validate_partial_match_dict returns resolved op |
| `querysource/parsers/sql.pyx` | MODIFY | base column |
| `querysource/parsers/sqlserver.pyx` | MODIFY | base column |
| `querysource/parsers/pgsql.pyx` | MODIFY | base column |
| `querysource/parsers/bigquery.pyx` | MODIFY | base column field_expr |
| `tests/test_partial_match_key_suffix.py` | CREATE | sql/mssql/bq/pg × rust/cython |

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .filter_values import base_key, is_comparison_dict  # sql.pyx:15, pgsql.pyx:17 (verify in sqlserver/bigquery)
from ..exceptions import ParserError                      # partial_matching.py:13
```

### Existing Signatures to Use
```python
def validate_partial_match_dict(key: str, value: dict, *, supports_regex: bool) -> PartialMatchOp | None:  # partial_matching.py:163
PARTIAL_MATCH_OPERATORS: dict[str, PartialMatchOp]  # keyed by name, each with a not_ counterpart
```

### Does NOT Exist
- ~~`partial_matching.resolve_key_suffix`~~: created here.

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/partial_matching.py", "action": "MODIFY"},
    {"path": "querysource/parsers/sql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/sqlserver.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/bigquery.pyx", "action": "MODIFY"},
    {"path": "tests/test_partial_match_key_suffix.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/parsers/partial_matching.py#validate_partial_match_dict"]
}
```

## Implementation Blueprint

### Steps (in order)
1. Add `resolve_key_suffix`, which must match TASK-886 exactly, including the error message, because the Cython fallback re-raises it as a `ParserError`.
2. `validate_partial_match_dict` returns the resolved op, so `abstract.pyx:604` rejects bad suffixes before any builder runs.
3. Change the four render sites to the base column.
4. Rebuild Cython, stage the TASK-886 wheel, and run the validation commands.

### FILL IN checklist
- [ ] bigquery.pyx JSON field handling with the base key
- [ ] e2e test bodies

## Acceptance Criteria
- [ ] AC1-AC3 of the spec on all 4 dialects × 2 paths.
- [ ] The existing partial-matching suites pass.

## Validation Commands
- `pytest tests/test_partial_match_key_suffix.py -q`
- `pytest tests/test_partial_matching_operators.py tests/test_sql_partial_matching.py tests/test_mssql_partial_matching.py tests/test_bigquery_partial_matching.py tests/test_pgsql_partial_matching.py tests/test_partial_matching_conformance.py -q`

## Completion Note

**Completed**: 2026-10-13 by agent:sdd-fix (commit 0f669b51)

- `resolve_key_suffix()` is in `partial_matching.py`, and `validate_partial_match_dict` returns the resolved op, so `abstract.pyx` pre-validation rejects bad suffixes early.
- `sql.pyx`, `sqlserver.pyx` and `pgsql.pyx` render `base_key(key)`. `bigquery.pyx` builds `field_expr` from the base column (JSON path and json-typed handling included).
- New `tests/test_partial_match_key_suffix.py`: 58 passed, covering 4 dialects × rust/cython with no skips.
- Regression suites (partial matching, dialect filters, Rust-backed tests): 2109 passed.
- Repro: `{"n!": {"contains": "abc"}}` now renders `n NOT LIKE '%abc%'` on every dialect and path. `n~` raises `ParserError`.
