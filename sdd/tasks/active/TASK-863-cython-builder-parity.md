# TASK-863: Cython builders — base-key type lookup, anchored BETWEEN, AND-ed comparison dicts

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-859
**Assigned-to**: unassigned

---

## Context

Spec §2 D / §3 Module 4. Once pre-processing (TASK-861/862) delivers intact
values, the Cython builders must (a) look the column type up by its base key
(`tags|` → `tags`), (b) pick the string `BETWEEN` branch only for the canonical
`BETWEEN …` / `NOT BETWEEN …` form, and (c) render every operator of a
comparison dict, AND-ed. The Rust fast path gets the same changes in TASK-864.

---

## Scope

- `pgsql.pyx` (`_filter_conditions_cy`), `sql.pyx` (`filter_conditions`
  Cython fallback): (a), (b), (c).
- `sqlserver.pyx`: (a), (b) only — it has no comparison-dict branch (spec Non-Goals).
- `bigquery.pyx`: (b), (c) only — it has no typed-format lookup.
- Builder-level tests in `tests/test_cython_builder_filter_fixes.py` (set
  `parser.filter` directly, like `tests/test_pgsql_partial_matching.py`).

**NOT in scope**: Rust (TASK-864); pre-processing (TASK-861/862); CQL/SOQL builders.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/pgsql.pyx` | MODIFY | (a)(b)(c) |
| `querysource/parsers/sql.pyx` | MODIFY | (a)(b)(c) |
| `querysource/parsers/sqlserver.pyx` | MODIFY | (a)(b) |
| `querysource/parsers/bigquery.pyx` | MODIFY | (b)(c) |
| `tests/test_cython_builder_filter_fixes.py` | CREATE | builder tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .filter_values import base_key, is_comparison_dict   # created by TASK-859; add to each of the 4 builders
from ..types.validators import Entity                       # already imported: pgsql.pyx:16, sql.pyx:14, sqlserver.pyx:11, bigquery.pyx:12
# tests
from querysource.models import QueryObject                  # verified: tests/test_pgsql_partial_matching.py:9
from querysource.parsers.pgsql import pgSQLParser           # verified: tests/test_pgsql_partial_matching.py:11
```

### Existing Signatures to Use
```python
# querysource/parsers/pgsql.pyx
async def _filter_conditions_cy(self, sql)        # line 345
    _format = self.cond_definition[key]           # line 375 (inside try/except KeyError)
    op, v = next(reversed(value.items()))  # never popitem(): ...   # line 405, then `if op in COMPARISON_TOKENS:` (406-409)
    elif "BETWEEN" in str_value:                  # line 490
# querysource/parsers/sql.pyx
async def filter_conditions(self, sql)            # line 141 (Rust fast path, then Cython fallback)
    _format = self.cond_definition[key]           # line 177
    op, v = next(reversed(value.items()))  # ...  # line 197, then COMPARISON_TOKENS branch (199-202)
    if "BETWEEN" in str_value:                    # line 227
# querysource/parsers/sqlserver.pyx
    if key in self.cond_definition:               # line 135
        _format = self.cond_definition[key]       # line 136
    if "BETWEEN" in str_value:                    # line 170
# querysource/parsers/bigquery.pyx
    op, v = next(reversed(value.items()))  # ...  # line 245; comparison renders f"{field_expr} {op} {bq_quote_string(str(v))}"
    if "BETWEEN" in str_value:                    # line 285
# test helper pattern: tests/test_pgsql_partial_matching.py:60-73 (_make_parser / _render cython)
```

### Does NOT Exist
- ~~comparison-dict rendering in `sqlserver.pyx`~~ — dicts that are not partial matches fall through; leave them.
- ~~a typed-format lookup in `bigquery.pyx`'s builder~~ — only `cond_definition.get(key) == "json"` checks exist (lines 112, 140, 205); do not add one.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/sql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/sqlserver.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/bigquery.pyx", "action": "MODIFY"},
    {"path": "tests/test_cython_builder_filter_fixes.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser._filter_conditions_cy",
    "sym:querysource/parsers/sql.pyx#SQLParser.filter_conditions"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- A single comparison operator must render byte-identically to today (AC).
- Multi-operator output: `(x > '1' AND x < '9')` — parentheses only when > 1 part.
- Keep the BETWEEN injection-marker check (defence in depth).
- Rebuild: `python setup.py build_ext --inplace --build-temp /tmp/qs-build`; exclusive task.

---

## Implementation Blueprint

### Steps (in order)
1. Add the `filter_values` import to the four builders.
2. Replace the type lookups (pgsql/sql/sqlserver) with `base_key(key)` — *why*: `tags|` must find the `array` type.
3. Anchor the string BETWEEN branch in all four — *why*: a quoted value containing "BETWEEN" is plain equality.
4. Insert the comparison-dict branch in pgsql/sql/bigquery before the existing single-op reduction — *why*: keep every operator (bug 2).
5. Rebuild and test.

### `querysource/parsers/pgsql.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '                    _format = self.cond_definition\[key\]' pgsql.pyx)
# REPLACE line 375
                    _format = self.cond_definition[base_key(key)]
```
```python
# occurrences: 1 (verified: grep -c '                    op, v = next(reversed(value.items()))  # never popitem()' pgsql.pyx)
# BEFORE line 405 insert:
                    if is_comparison_dict(value):
                        parts = [
                            f"{key} {op} {Entity.quoteString(v) if isinstance(v, str) else str(v)}"
                            for op, v in value.items()
                        ]
                        where_cond.append(parts[0] if len(parts) == 1 else '(' + ' AND '.join(parts) + ')')
                        continue
```
```python
# occurrences: 1 (verified: grep -c '                    elif "BETWEEN" in str_value:' pgsql.pyx)
# REPLACE line 490
                    elif str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):
```

### `querysource/parsers/sql.pyx` (MODIFY)
```python
# line 177 (occurrences: 1): _format = self.cond_definition[base_key(key)]
# BEFORE line 197 (occurrences: 1): the same comparison-dict block as pgsql
# line 227 (occurrences: 1): if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):
```

### `querysource/parsers/sqlserver.pyx` (MODIFY)
```python
# occurrences: 1 each (verified: grep -c)
# REPLACE lines 135-136
                if base_key(key) in self.cond_definition:
                    _format = self.cond_definition[base_key(key)]
# REPLACE line 170
                    if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):
```

### `querysource/parsers/bigquery.pyx` (MODIFY)
```python
# BEFORE line 245 (occurrences: 1) insert:
                    if is_comparison_dict(value):
                        parts = [f"{field_expr} {op} {bq_quote_string(str(v))}" for op, v in value.items()]
                        where_cond.append(parts[0] if len(parts) == 1 else '(' + ' AND '.join(parts) + ')')
                        continue
# REPLACE line 285 (occurrences: 1)
                    if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):
```
**Why**: the insert sits after the partial-match/JSONB handling and before the
legacy single-operator reduction, so only all-comparison dicts change; every
other dict keeps today's path.

### `tests/test_cython_builder_filter_fixes.py` (CREATE)
```python
"""FEAT-165: Cython builders — type lookup, anchored BETWEEN, comparison dicts."""
import pytest

from querysource.models import QueryObject
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"


def _pg(filter_: dict, cond_definition: dict | None = None) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = cond_definition or {}
    parser.filter = filter_
    return parser


async def test_pg_multi_operator():
    sql = await _pg({"x": {">": "'1'", "<": "'9'"}})._filter_conditions_cy(SQL)
    assert "(x > '1' AND x < '9')" in sql


# FILL IN: single op unchanged; {"note": "'IN BETWEEN'"} → equality; canonical
#          "NOT BETWEEN 1 AND 5" on key "amount" → "(amount NOT BETWEEN 1 AND 5)";
#          {"tags|": ["a", "b"]} with cond_definition {"tags": "array"} → "&&";
#          generic SQL (querysource.parsers.sql.SQLParser), SQL Server and BigQuery parsers
#          for the cases they support (patch each module's HAS_RUST to False)
#          — bounded by spec §2 D
```

### FILL IN checklist
- [ ] remaining tests per dialect — bounded by spec §2 D

---

## Acceptance Criteria

- [ ] `pytest tests/test_cython_builder_filter_fixes.py -v` passes after an in-place rebuild
- [ ] Existing suites pass: `tests/test_pgsql_partial_matching.py`, `tests/test_sql_partial_matching.py`, `tests/test_mssql_partial_matching.py`, `tests/test_bigquery_partial_matching.py`, `tests/test_sql_parser_combinations.py`
- [ ] Single-operator comparison output unchanged

## Validation Commands

- `pytest tests/test_cython_builder_filter_fixes.py -q`
- `pytest tests/test_pgsql_partial_matching.py -q`
- `pytest tests/test_sql_partial_matching.py -q`
- `pytest tests/test_mssql_partial_matching.py -q`
- `pytest tests/test_bigquery_partial_matching.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`
