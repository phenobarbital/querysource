# TASK-784: Wire the JSONB-unnest planner into `pgSQLParser.build_query`

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-779, TASK-781, TASK-783
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (second half), §2 Overview steps 1–5, AC1/AC3/AC4/AC7/AC8/AC9. When the
planner is active, `build_query` renders the inner query (row filters only), blanks the
structural placeholders, wraps it, then runs the existing `group_by` → `HAVING` →
`order_by` → `limiting`. When inactive, the method must behave exactly as before
(TASK-779's frozen regression suite proves it).

---

## Scope

- `pgsql.pyx`: import the planner, add `STRUCTURAL_PLACEHOLDERS`, `_unnest_plan`,
  `_unnest_wrap`, and the three hook points in `build_query`.
- Dispatch: Rust `_rs.pgsql_unnest_plan` / `_rs.pgsql_unnest_wrap` when `HAS_RUST` and the
  function exists (TASK-785/786 add them); Rust `ValueError` → `ParserError` **without**
  fallback; any other Rust exception → Cython fallback; Cython `ValueError` → `ParserError`.
- Reject `add_fields=True` in plan mode.
- Tests `tests/test_pgsql_jsonb_unnest.py` (Cython path; Rust parity is TASK-787).

**NOT in scope**: Rust code; docs; live DB test.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/pgsql.pyx` | MODIFY | Planner dispatch + build_query hooks |
| `tests/test_pgsql_jsonb_unnest.py` | CREATE | End-to-end parser rendering tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# inside querysource/parsers/pgsql.pyx (relative imports, same package)
from ..exceptions import EmptySentence                       # verified: pgsql.pyx:14 (extend with ParserError)
from ..exceptions import ParserError                         # verified: querysource/exceptions.py:86 (default_code = 400)
from .jsonb_unnest import is_plan_candidate, unnest_plan, unnest_wrap   # created by TASK-780/782/783
# tests
from querysource.exceptions import ParserError               # verified: querysource/handlers/service.py:18 imports it
from querysource.models import QueryObject                   # verified: querysource/models.py:24
from querysource.parsers import pgsql                        # verified: tests/test_pgsql_jsonb_filters.py:20
from querysource.parsers.pgsql import pgSQLParser            # verified: querysource/providers/pg.py:12
```

### Existing Signatures to Use
```python
# querysource/parsers/pgsql.pyx
try:
    from querysource.qs_parsers import _qs_parsers as _rs    # line 20
    HAS_RUST = True                                          # line 21
cdef class pgSQLParser(SQLParser):                           # line 199
    async def build_query(self, querylimit: int = None, offset: int = None):  # line 458
        # line 476: `        sql = await self.process_fields(sql)`
        # lines 478-494: QS function filters (may extend self.filter and self.ordering)
        # line 499: `        sql = await self.filter_conditions(sql)`
        # line 501: `        sql = await self.group_by(sql)`
        # line 502: `        if self.ordering:`
# querysource/parsers/abstract.pxd: fields (list, :19), ordering (list, :20), grouping (list, :21),
#   filter (dict, :17), attributes (dict, :35), _add_fields (bint), logger (object)
#   having (object) — added by TASK-781
# querysource/exceptions.py:14 — QueryException.__init__(self, message: str, code: int | None = None, **kwargs)
# jsonb_unnest (TASK-780/782/783): is_plan_candidate(...)->bool; unnest_plan(...)->dict|None; unnest_wrap(inner, plan)->str
```

### Does NOT Exist
- ~~`_rs.pgsql_unnest_plan` / `_rs.pgsql_unnest_wrap`~~ until TASK-786/785 — guard with `hasattr`.
- ~~`SQLParser.having`~~ / ~~HAVING support in `group_by()`~~ — HAVING is appended here.
- ~~A depth-aware `order_by`~~ — it only appends; call it on the OUTER SQL only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "tests/test_pgsql_jsonb_unnest.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.build_query",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.filter_conditions",
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_plan",
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_wrap",
    "sym:querysource/parsers/jsonb_unnest.pyx#is_plan_candidate",
    "sym:querysource/exceptions.py#ParserError"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Non-plan path must stay byte-identical: the ONLY new work on it is one
  `_unnest_plan()` call that returns `None` after `is_plan_candidate` (a string scan).
- `STRUCTURAL_PLACEHOLDERS = ('{grouping}', '{group_by}', '{order_by}', '{ordering}', '{offset}', '{limit}')`
  are blanked (`str.replace(p, '')`) in the INNER SQL before wrapping — otherwise
  `limiting()` fills `{limit}` inside the subquery (AC9).
- QS function filters (lines 478-494) may append to `self.ordering` while it is cleared in plan
  mode; those entries reference inner row columns the outer query cannot see — discard them
  with `self.logger.warning(...)` (record in Completion Note).
- `HAVING` is appended right after `group_by()`: `f"{sql} HAVING {' AND '.join(plan['having'])}"`.
- `ParserError(message)` keeps the planner's message verbatim (AC7 compares messages across paths).

---

## Implementation Blueprint

### Steps (in order)
1. Imports + constant — *why*: needed by the new methods.
2. Add `_unnest_plan` / `_unnest_wrap` methods — *why*: single dispatch point for Rust/Cython and error mapping.
3. Hook 3 points in `build_query` — *why*: spec §2 Overview steps 2–4.
4. Tests; `make build-inplace`; run validation incl. TASK-779's regression suite.

### `querysource/parsers/pgsql.pyx` (MODIFY) — imports
```python
# occurrences: 1 (verified: grep -c '^from ..exceptions import EmptySentence$' querysource/parsers/pgsql.pyx)
# REPLACE `from ..exceptions import EmptySentence` (verified: pgsql.pyx:14) with:
from ..exceptions import EmptySentence, ParserError
from .jsonb_unnest import is_plan_candidate, unnest_plan, unnest_wrap
```

### `querysource/parsers/pgsql.pyx` (MODIFY) — constant
```python
# occurrences: 1 (verified: grep -c "^PG_TEXT_OPERATORS = " querysource/parsers/pgsql.pyx)
# AFTER — insert below `PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)` (verified: pgsql.pyx:34)
# Structural placeholders blanked in the inner query of a JSONB-unnest plan (FEAT-153).
STRUCTURAL_PLACEHOLDERS = ('{grouping}', '{group_by}', '{order_by}', '{ordering}', '{offset}', '{limit}')
```

### `querysource/parsers/pgsql.pyx` (MODIFY) — methods
```python
# occurrences: 1 (verified: grep -c '    async def build_query(self, querylimit: int = None, offset: int = None):' querysource/parsers/pgsql.pyx)
# BEFORE — insert above `    async def build_query(self, querylimit: int = None, offset: int = None):` (verified: pgsql.pyx:458)
    def _unnest_plan(self):
        """Return the JSONB-unnest plan (FEAT-153) or None when the query does not use it.

        Rust fast path when available; a Rust ``ValueError`` is a validation verdict and is
        re-raised as ``ParserError`` without fallback. Any other Rust error falls back to Cython.

        Raises:
            ParserError: invalid path/alias/config/having (HTTP 400).
        """
        having = self.having if self.having is not None else {}
        config = (self.attributes or {}).get('jsonb_unnest') or {}
        fields = list(self.fields or [])
        grouping = list(self.grouping or [])
        ordering = list(self.ordering or [])
        _filter = self.filter or {}
        if not is_plan_candidate(fields, grouping, ordering, _filter, having, config):
            return None
        if HAS_RUST and hasattr(_rs, 'pgsql_unnest_plan'):
            try:
                return _rs.pgsql_unnest_plan(fields, grouping, ordering, _filter, having, config)
            except ValueError as exc:
                raise ParserError(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 — any non-validation Rust failure falls back
                self.logger.warning(f"jsonb_unnest: Rust planner failed, using Cython: {exc}")
        try:
            return unnest_plan(fields, grouping, ordering, _filter, having, config)
        except ValueError as exc:
            raise ParserError(str(exc)) from exc

    def _unnest_wrap(self, str inner_sql, dict plan) -> str:
        """Blank the structural placeholders of ``inner_sql`` and wrap it per ``plan``."""
        for placeholder in STRUCTURAL_PLACEHOLDERS:
            inner_sql = inner_sql.replace(placeholder, '')
        if HAS_RUST and hasattr(_rs, 'pgsql_unnest_wrap'):
            try:
                return _rs.pgsql_unnest_wrap(inner_sql, plan)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"jsonb_unnest: Rust wrap failed, using Cython: {exc}")
        return unnest_wrap(inner_sql, plan)
```
**Why**: `ValueError` is caught before the generic handler so a Rust validation verdict can
never be "rescued" by a Cython render that might differ (AC7).

### `querysource/parsers/pgsql.pyx` (MODIFY) — build_query hook A (before process_fields)
```python
# occurrences: 1 (verified: grep -c '        sql = await self.process_fields(sql)' querysource/parsers/pgsql.pyx)
# BEFORE — insert above `        sql = await self.process_fields(sql)` (verified: pgsql.pyx:476)
        plan = self._unnest_plan()
        if plan is not None:
            if self._add_fields:
                raise ParserError("jsonb_unnest: add_fields is not supported with aggregation")
            self.fields = []
            self.grouping = []
            self.ordering = []
            self.filter = plan['row_filter']
```

### build_query hook B (after filter_conditions)
```python
# occurrences: 1 (verified: grep -c '        sql = await self.filter_conditions(sql)' querysource/parsers/pgsql.pyx)
# AFTER — insert below `        sql = await self.filter_conditions(sql)` (verified: pgsql.pyx:499)
        if plan is not None:
            if self.ordering:
                self.logger.warning(
                    f"jsonb_unnest: discarding ordering added by query filters: {self.ordering}"
                )
            sql = self._unnest_wrap(sql, plan)
            self.grouping = list(plan['group_by'])
            self.ordering = list(plan['order_by'])
```

### build_query hook C (after group_by)
```python
# occurrences: 1 (verified: grep -c '        sql = await self.group_by(sql)' querysource/parsers/pgsql.pyx)
# AFTER — insert below `        sql = await self.group_by(sql)` (verified: pgsql.pyx:501)
        if plan is not None and plan['having']:
            sql = f"{sql} HAVING {' AND '.join(plan['having'])}"
```
**Why**: `group_by()` finds no depth-0 `GROUP BY` in the wrapped SQL (the inner one is inside
parentheses), so it appends the outer clause; HAVING must follow it and precede ORDER BY.
`cdef`-typed locals: `plan` is a Python object — do NOT declare it `cdef dict` (it may be None).

### `tests/test_pgsql_jsonb_unnest.py` (CREATE)
```python
"""FEAT-153: pgSQLParser.build_query in JSONB-unnest plan mode (Cython path)."""
from __future__ import annotations

import pytest
import sqlglot

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

WHERE_SQL = "SELECT * FROM students {where_cond}"
TABLE_SQL = "SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}"


def _make(query: str = WHERE_SQL, **attrs) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser


@pytest.fixture(autouse=True)
def _cython_only(monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", False)


async def test_spec_example_renders_and_parses():
    parser = _make(
        fields=["graduation_details[].course", "graduation_details[].category",
                "count(distinct student_uid) as graduates"],
        grouping=["graduation_details[].course", "graduation_details[].category"],
        ordering=["graduates DESC"],
        filter={"licensee": "'Asia'", "graduation_details[].course_date::date": {">=": "'2025-01-01'"}},
        having={"graduates": {">": 5}},
    )
    sql = await parser.build_query(querylimit=10)
    assert sql.index("GROUP BY") < sql.index("HAVING") < sql.index("ORDER BY") < sql.index("LIMIT 10")
    inner = sql.split(") AS _qs_src", 1)[0]
    assert "LIMIT" not in inner and "GROUP BY" not in inner and "licensee='Asia'" in inner
    sqlglot.parse_one(sql, read="postgres")


async def test_table_template_placeholders_stay_outside():
    parser = _make(TABLE_SQL, schema="public", tablename="students",
                   fields=["graduation_details[].course"], grouping=["graduation_details[].course"])
    sql = await parser.build_query(querylimit=5, offset=10)
    inner = sql.split(") AS _qs_src", 1)[0]
    assert "LIMIT" not in inner and "OFFSET" not in inner and "{" not in sql
    assert sql.endswith("LIMIT 5 OFFSET 10")

# FILL IN: invalid token -> ParserError with .code == 400 and the planner message; add_fields=True
#   in plan mode -> ParserError; two array columns -> ParserError; having-only (no array) renders
#   HAVING and no LATERAL; `having` present but not a mapping -> ParserError; config from
#   parser.attributes['jsonb_unnest'] aliases (group_by=['course']); non-plan query unchanged
#   (spot-check one TASK-779 case) — bounded by AC3/AC7/AC8/AC9.
```

### FILL IN checklist
- [ ] tests — listed cases

---

## Acceptance Criteria

- [ ] `pytest tests/test_pgsql_jsonb_unnest_regression.py -q` passes UNCHANGED (AC1) — do not edit its `EXPECTED`.
- [ ] `pytest tests/test_pgsql_jsonb_filters.py tests/test_rust_parsers.py tests/test_grouping_sync.py -q` passes (AC2).
- [ ] Plan-mode SQL parses with `sqlglot` and never has LIMIT/OFFSET/GROUP BY/ORDER BY inside `_qs_src` (AC9).
- [ ] Invalid input raises `ParserError` code 400 (AC7); `add_fields` + plan raises (AC8).

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_unnest.py -q`
- `pytest tests/test_pgsql_jsonb_unnest_regression.py -q`
- `pytest tests/test_pgsql_jsonb_filters.py -q`
- `pytest tests/test_rust_parsers.py -q`

---

## Test Specification

See the test block above.

---

## Agent Instructions

1. Read spec §2 Overview and §7; confirm TASK-779, TASK-781, TASK-783 are completed.
2. Implement; `make build-inplace` (exclusive resource).
3. If the regression suite fails, fix the integration — NEVER edit `EXPECTED`.
4. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:36:12+00:00
**Notes**: Hooks A/B/C + _unnest_plan/_unnest_wrap; 15 e2e tests; regression, jsonb_filters, rust_parsers, grouping_sync all pass (378 total). Ordering added by QS filters is discarded with a warning in plan mode. Verified spec example also under Rust group_by/limiting (HAS_RUST=True, planner falls to Cython until Rust fns exist).

**Deviations from spec**: QS-filter-added ordering is discarded (with a warning) in plan mode.
