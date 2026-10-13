# TASK-885: Python literal helpers protect braces; build_query restores them

**Feature**: FEAT-172 — Protect braces in partial-match LIKE literals from later format_map passes
**Spec**: `sdd/specs/parsers-deltatbl-tech-debt.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-884
**Assigned-to**: unassigned
**discovered_from**: issue:64c82d35d831

---

## Context

Spec §3 Module 1. `sql_like_literal`/`mssql_like_literal`/`bq_like_literal` keep raw `{ }`,
and `SQLParser.build_query`'s later `format_map` passes (the final one is `NullDefault`) strip
them: `contains '{limit}'` renders `LIKE '%%'`. This task adds the sentinels on the
Python side and restores them in every `build_query` that carries SQLParser filter output.

## Scope

- Add `BRACE_OPEN_SENTINEL`, `BRACE_CLOSE_SENTINEL`, `protect_braces` and `restore_braces` to `partial_matching.py`.
- Apply `protect_braces` in the three literal helpers.
- Call `restore_braces(sql)` immediately before `self.query_parsed = sql` in `sql.pyx`, `deltatbl.pyx` and `iceberg.pyx`.
- Add unit and end-to-end `build_query` tests (sql/mssql/bq × rust/cython).

**NOT in scope**: the Rust helpers (TASK-884); PostgreSQL; plain string filters.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/partial_matching.py` | MODIFY | sentinels, protect/restore, apply in helpers |
| `querysource/parsers/sql.pyx` | MODIFY | restore in build_query |
| `querysource/parsers/deltatbl.pyx` | MODIFY | restore in build_query |
| `querysource/parsers/iceberg.pyx` | MODIFY | restore in build_query |
| `tests/test_partial_matching_operators.py` | MODIFY | helper unit tests |
| `tests/test_partial_match_braces.py` | CREATE | build_query end-to-end tests |

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .partial_matching import (  # verified: sql.pyx:16 (extend the existing import)
from .sql cimport SQLParser      # verified: deltatbl.pyx:13, iceberg.pyx:13
from querysource.parsers.partial_matching import bq_like_literal, mssql_like_literal, sql_like_literal  # tests/test_partial_matching_operators.py:7-12
```

### Existing Signatures to Use
```python
# querysource/parsers/partial_matching.py
def sql_like_literal(pattern: str) -> str:    # line 87
def mssql_like_literal(pattern: str) -> str:  # line 92
def bq_like_literal(pattern: str) -> str:     # line 97
# querysource/parsers/sql.pyx
async def build_query(self, querylimit: int = None, offset: int = None):  # line 433
        self.query_parsed = sql   # line 477, occurrences: 1
# deltatbl.pyx:114 / iceberg.pyx:109: `self.query_parsed = sql`, occurrences: 1 each
```

### Does NOT Exist
- ~~`protect_braces` / `restore_braces` in Python~~: created here.

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/partial_matching.py", "action": "MODIFY"},
    {"path": "querysource/parsers/sql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/deltatbl.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/iceberg.pyx", "action": "MODIFY"},
    {"path": "tests/test_partial_matching_operators.py", "action": "MODIFY"},
    {"path": "tests/test_partial_match_braces.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/partial_matching.py#sql_like_literal",
    "sym:querysource/parsers/partial_matching.py#mssql_like_literal",
    "sym:querysource/parsers/partial_matching.py#bq_like_literal",
    "sym:querysource/parsers/sql.pyx#SQLParser.build_query"
  ]
}
```

## Implementation Blueprint

### Steps (in order)
1. Add the sentinels and helpers to `partial_matching.py`, because one shared definition keeps the Python side, the restore step and the Rust twin in agreement.
2. Apply `protect_braces` as the last step of each literal helper.
3. Restore in each `build_query` immediately before `self.query_parsed = sql`, because that comes after every `format_map` pass.
4. Rebuild Cython and stage the TASK-884 Rust wheel, then run the validation commands.

### `querysource/parsers/partial_matching.py` (MODIFY)
```python
BRACE_OPEN_SENTINEL: str = ""
BRACE_CLOSE_SENTINEL: str = ""

def protect_braces(text: str) -> str:
    """Replace ``{``/``}`` with private-use sentinels that ``str.format_map`` ignores."""
    return text.replace("{", BRACE_OPEN_SENTINEL).replace("}", BRACE_CLOSE_SENTINEL)

def restore_braces(sql: str) -> str:
    """Undo :func:`protect_braces` once no ``format_map`` pass remains."""
    return sql.replace(BRACE_OPEN_SENTINEL, "{").replace(BRACE_CLOSE_SENTINEL, "}")
```

### FILL IN checklist
- [ ] apply in helpers; three restore call sites
- [ ] end-to-end tests (AC1-AC3)

## Acceptance Criteria

- [ ] AC1: `build_query` keeps `a{b}c` / `{limit}` operands intact for sql, mssql and bq (rust + cython).
- [ ] AC2: the literal helpers never emit raw braces.
- [ ] AC3: DeltaTable/Iceberg `build_query` restore the sentinels.
- [ ] AC4: the existing partial-matching suites pass.

## Validation Commands
- `pytest tests/test_partial_match_braces.py -q`
- `pytest tests/test_partial_matching_operators.py -q`
- `pytest tests/test_sql_partial_matching.py tests/test_mssql_partial_matching.py tests/test_bigquery_partial_matching.py tests/test_partial_matching_conformance.py -q`

## Completion Note

**Completed**: 2026-10-13 by agent:sdd-fix (commit 9c20938a)

- Added `BRACE_OPEN_SENTINEL`, `BRACE_CLOSE_SENTINEL`, `protect_braces` and `restore_braces` to `partial_matching.py`. The three literal helpers now protect braces.
- `restore_braces(sql)` runs before `self.query_parsed = sql` in `sql.pyx` (inherited by `msSQLParser` and `BigQueryParser`), `deltatbl.pyx` and `iceberg.pyx`.
- New `tests/test_partial_match_braces.py` covers 3 dialects × 2 paths × 3 operands, plus DeltaTable and Iceberg. There are also 2 new helper tests in `test_partial_matching_operators.py`.
- Validation, after `build_ext --inplace` and staging the TASK-884 wheel: 1752 passed with no skips. Wider parser suites: 243 + 110 passed.
- Repro: `contains '{limit}'` now renders `LIKE '%{limit}%'`; before the fix it rendered `'%%'`.
- Ledger: resolves `issue:64c82d35d831`.
