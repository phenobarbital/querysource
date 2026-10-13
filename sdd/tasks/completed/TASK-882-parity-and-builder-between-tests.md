# TASK-882: Exact parity assertions + builder-level BETWEEN defence-in-depth tests

**Feature**: FEAT-168 — Builder parity: numeric comparison operands and strict canonical BETWEEN
**Spec**: `sdd/specs/src-bigquery-parser-tech-debt.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-880, TASK-881
**Assigned-to**: unassigned
**discovered_from**: issue:7a248a32aa07, issue:fc81723e95ac

---

## Context

Spec §3 Module 3, §4. `tests/test_dialect_filter_parity.py:79` `_numeric_literals()`
unquotes numeric literals on both sides of the assertion, hiding the Rust/Cython
divergence. Once TASK-880/881 land, both paths render numbers bare and the
helper must go. Builder-level tests prove the strict BETWEEN check holds even
when pre-processing is bypassed.

---

## Scope

- Delete `_numeric_literals` and its two call sites; assert `_where_body(...) == expected` exactly.
- Update expectations: `{"x": {">": 1, "<": 9}}` → `(x > 1 AND x < 9)` for PG, SQL
  and BigQuery; `{"qty": {">": 0}}` → `qty > 0`.
- Add PG + BigQuery cases: float `{"x": {">=": 2.5}}` → `x >= 2.5`; string
  `{"x": {">": "b"}}` → PG `x > 'b'`, BQ `x > "b"`.
- Create `tests/unit/test_builder_between_strict.py`: for sql, sqlserver,
  bigquery, pgsql × {rust, cython}, assign `parser.filter` directly (bypassing
  `set_options()`), `await parser.filter_conditions(SQL)`, and assert
  `"BETWEEN 1 AND 2 OR 1=1"` produces no predicate while `"BETWEEN 100 AND 500"`
  produces `(amount BETWEEN 100 AND 500)`.

**NOT in scope**: production code (TASK-880/881).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_dialect_filter_parity.py` | MODIFY | exact assertions, new numeric cases |
| `tests/unit/test_builder_between_strict.py` | CREATE | defence-in-depth builder tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers import QS_VARIABLES, bigquery, pgsql, sql, sqlserver  # tests/test_dialect_filter_parity.py:24
from querysource.parsers.bigquery import BigQueryParser   # :25
from querysource.parsers.pgsql import pgSQLParser         # :26
from querysource.parsers.sql import SQLParser             # :27
from querysource.parsers.sqlserver import msSQLParser     # :28
```

### Existing Signatures to Use
```python
# tests/test_dialect_filter_parity.py (verified at dda6d6b6)
SQL = "SELECT * FROM public.t {where_cond}"                              # :30
def _rust_current() -> bool                                              # probe; PATHS skips rust when stale
PATHS = [pytest.param("rust", marks=skipif(...)), "cython"]              # :61
def _where_body(rendered: str) -> str | None                             # :73
def _numeric_literals(predicate: str | None) -> str | None               # :79  ← delete
async def render(path, conditions, monkeypatch, parser_cls=pgSQLParser, module=pgsql, query=SQL) -> str  # :86
#   cython path = monkeypatch.setattr(module, "HAS_RUST", False); parser._redis = _Redis()
PG_CORPUS = [...]                                                        # :103 — ({"filter": {...}}, expected)
async def test_pg_full_pipeline(...)                                     # :138, uses _numeric_literals at :142
async def test_other_dialects_render_all_comparison_operators(...)      # :170, uses _numeric_literals at :175
# Builders: `async def filter_conditions(self, sql)` — sql.pyx:142, sqlserver.pyx:92, bigquery.pyx:153, pgsql.pyx:334
```

### Does NOT Exist
- ~~a pytest fixture for parsers in `tests/conftest.py`~~ — reuse the module-local `render`/`_Redis` pattern (import from `tests/test_dialect_filter_parity.py` is not a package import; copy the minimal helpers)
- ~~`parser.where_cond` attribute~~ — inspect the returned SQL string

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/test_dialect_filter_parity.py", "action": "MODIFY"},
    {"path": "tests/unit/test_builder_between_strict.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Confirm TASK-880/881 are merged in the worktree and the .so is fresh (`_rust_current()` true) — *why*: otherwise rust params silently skip.
2. Remove `_numeric_literals`, fix expectations — *why*: AC4.
3. Write the builder-level test — *why*: AC3 without pre-processing.

### `tests/test_dialect_filter_parity.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'def _numeric_literals' tests/test_dialect_filter_parity.py)
# DELETE lines 79-83 (_numeric_literals); drop `import re` only if nothing else uses it.
# :142 → assert _where_body(await render(path, conditions, monkeypatch)) == expected
# :175 → assert _where_body(rendered) == expected
# PG_CORPUS: ({"filter": {"x": {">": 1, "<": 9}}}, "(x > 1 AND x < 9)"), ({"filter": {"qty": {">": 0}}}, "qty > 0")
# :166-167 → (SQLParser, sql, "(x > 1 AND x < 9)"), (BigQueryParser, bigquery, "(x > 1 AND x < 9)")
# FILL IN: add float + string comparison cases for PG and BigQuery — bounded by spec §4 rows 5-6
```

### `tests/unit/test_builder_between_strict.py` (CREATE)
```python
"""FEAT-168: builders splice only canonical BETWEEN, even without pre-processing."""
from __future__ import annotations

from typing import Any

import pytest

from querysource.parsers import bigquery, pgsql, sql, sqlserver
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM public.t {where_cond}"
DIALECTS = [(SQLParser, sql), (msSQLParser, sqlserver), (BigQueryParser, bigquery), (pgSQLParser, pgsql)]


async def build(path: str, parser_cls: type, module: Any, filter_: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run only the builder: set ``parser.filter`` directly, skipping set_options()."""
    if path == "cython":
        monkeypatch.setattr(module, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions={}, query=SQL)
    parser.filter = dict(filter_)
    # FILL IN: any attribute filter_conditions needs that set_options() normally sets
    #   (e.g. cond_definition) — read each builder's filter_conditions; bounded by "no set_options()".
    return await parser.filter_conditions(SQL)

# FILL IN: parametrize path in ("rust", "cython") (skip rust when module.HAS_RUST is False) × DIALECTS:
#   - "BETWEEN 1 AND 2 OR 1=1" → "OR 1=1" not in result and "BETWEEN" not in result
#   - "BETWEEN 100 AND 500"    → "(amount BETWEEN 100 AND 500)" in result
```

### FILL IN checklist
- [ ] float/string comparison cases — spec §4
- [ ] `build()` builder prerequisites — read `filter_conditions` of each dialect
- [ ] cross-path parametrization with rust skip

---

## Acceptance Criteria

- [ ] `grep -rn _numeric_literals tests/` returns nothing
- [ ] All Validation Commands pass with the rust params **running** (not skipped)

---

## Validation Commands

- `pytest tests/test_dialect_filter_parity.py -q`
- `pytest tests/unit/test_builder_between_strict.py -q`
- `pytest tests/unit/test_where_value_hardening.py -q`

---

## Agent Instructions

1. Work in the feature worktree after TASK-880 and TASK-881 are done.
2. Implement from the blueprint; run the Validation Commands; confirm `-rs` shows no rust skips.
3. Commit only the listed files; close with `scripts/sdd/close_task.sh TASK-882 src-bigquery-parser-tech-debt verified`.

---

## Completion Note

**Completed by**: Claude Code (/sdd-fix session)
**Date**: 2026-10-13
**Notes**: Removed `_numeric_literals`, so parity assertions are exact (74 pass, Rust path not skipped).
Added `test_bigquery_comparison_operands_keep_their_type` and `tests/unit/test_builder_between_strict.py`
(56 cases: 4 dialects × rust/cython × hostile/canonical). Full suite: 5248 passed; the 8 failures
are tests/test_source_parquet_{s3,gcs}.py (`No module named 's3fs'`, environment only).

**Deviations from spec**: the behaviour change (numbers bare on the Rust PG path) also required updating
expectations in tests/e2e/test_multiqs_dry_run.py, tests/e2e/test_qs_dry_run.py and
tests/test_pgsql_jsonb_unnest_regression.py, plus docs/QUERYSOURCE_DIALECT.md §5.3/§5.4/§9/cheat sheet
(the doc test checks doc SQL against the parity corpus).
