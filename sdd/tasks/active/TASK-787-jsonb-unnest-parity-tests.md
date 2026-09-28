# TASK-787: Rust ↔ Cython parity tests for the JSONB-unnest planner

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-784, TASK-786
**Assigned-to**: unassigned

---

## Context

Spec AC7/AC10, §4 `test_rust_cython_parity` and `test_invalid_raises_parser_error`. Proves
that `_qs_parsers.pgsql_unnest_plan` / `pgsql_unnest_wrap` produce the same plan dicts, SQL
and error messages as the Cython reference, and that `pgSQLParser.build_query` yields
identical SQL on both paths, with a Rust `ValueError` surfacing as `ParserError` without
fallback. (The spec listed parity inside `tests/test_pgsql_jsonb_unnest.py`; it gets its own
file so it can run after both implementations exist.)

---

## Scope

- Create `tests/test_pgsql_jsonb_unnest_parity.py`.
- A shared `CASES` list (valid inputs) and `ERROR_CASES` list (invalid inputs) covering every
  feature area; compare Rust vs Cython at planner level AND at `build_query` level.
- Skip the whole module when `_qs_parsers` lacks `pgsql_unnest_plan`.

**NOT in scope**: any production code change. A parity failure is a bug to report against
TASK-785/786 (fix it there, in Rust, to match Cython).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_pgsql_jsonb_unnest_parity.py` | CREATE | Parity tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import ParserError                   # querysource/exceptions.py:86
from querysource.models import QueryObject                        # querysource/models.py:24
from querysource.parsers import jsonb_unnest as ju                # TASK-780/782/783
from querysource.parsers import pgsql                             # tests/test_pgsql_jsonb_filters.py:20
from querysource.parsers.pgsql import pgSQLParser                 # querysource/providers/pg.py:12
```

### Existing Signatures to Use
```python
pgsql.HAS_RUST: bool; pgsql._rs  # module attribute when the extension imports (pgsql.pyx:20)
_rs.pgsql_unnest_plan(fields, grouping, ordering, filter_dict, having, config) -> dict | None   # TASK-786
_rs.pgsql_unnest_wrap(inner_sql, plan) -> str                                                  # TASK-785
ju.unnest_plan(fields, grouping, ordering, filter, having, config) -> dict | None
ju.unnest_wrap(inner_sql, plan) -> str
pgSQLParser._unnest_plan(self) / build_query(self, querylimit=None, offset=None)               # TASK-784
# tests/test_pgsql_jsonb_unnest.py (TASK-784) — `_make(query, **attrs)` helper pattern
```

### Does NOT Exist
- ~~A shared golden-case module~~ — cases live in this file.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/test_pgsql_jsonb_unnest_parity.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_plan",
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_wrap",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.build_query"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write the module below — *why*: one case table drives three comparisons.
2. Fill the case tables — *why*: coverage of every feature area (AC10).
3. Run; any diff is a Rust bug → report / fix in the Rust task scope, never by editing Cython.

### `tests/test_pgsql_jsonb_unnest_parity.py` (CREATE)
```python
"""FEAT-153 AC7/AC10: the Rust planner matches the Cython reference exactly."""
from __future__ import annotations

from typing import Any

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import jsonb_unnest as ju
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

pytestmark = pytest.mark.skipif(
    not (pgsql.HAS_RUST and hasattr(pgsql._rs, "pgsql_unnest_plan")),
    reason="qs_parsers without pgsql_unnest_plan",
)

WHERE_SQL = "SELECT * FROM students {where_cond}"

# (fields, grouping, ordering, filter, having, config)
CASES: list[tuple[list, list, list, dict, Any, Any]] = [
    (["graduation_details[].course", "count(distinct student_uid) as graduates"],
     ["graduation_details[].course"], ["graduates DESC"],
     {"licensee": "'Asia'", "graduation_details[].course_date::date": {">=": "'2025-01-01'"}},
     {"graduates": {">": 5}}, {}),
    # FILL IN: >= 15 more: nested keys, every bucket, sum/avg implicit numeric, safe_cast
    #   (column + global), empty include, aliases, strict via alias, prefilter = / IN, '|'
    #   suffix collision, negation, IN, NULL, numbers, bool, having multi-op, having-only,
    #   empty fields, NULLS LAST, row filters with @> / @>| passed through — bounded by AC10.
]

ERROR_CASES: list[tuple[list, list, list, dict, Any, Any]] = [
    (["a[].k::regclass"], [], [], {}, {}, {}),
    # FILL IN: one case per distinct message in TASK-780/782/783 message tables
    #   (config errors, strict, two arrays, undeclared column, duplicate alias, having errors,
    #   filter value/operator errors) — bounded by AC7.
]


@pytest.mark.parametrize("case", CASES)
def test_plan_parity(case):
    assert pgsql._rs.pgsql_unnest_plan(*case) == ju.unnest_plan(*case)


@pytest.mark.parametrize("case", CASES)
def test_wrap_parity(case):
    plan = ju.unnest_plan(*case)
    inner = "SELECT * FROM students WHERE licensee='Asia'"
    assert pgsql._rs.pgsql_unnest_wrap(inner, plan) == ju.unnest_wrap(inner, plan)


@pytest.mark.parametrize("case", ERROR_CASES)
def test_error_message_parity(case):
    with pytest.raises(ValueError) as rust_err:
        pgsql._rs.pgsql_unnest_plan(*case)
    with pytest.raises(ValueError) as cy_err:
        ju.unnest_plan(*case)
    assert str(rust_err.value) == str(cy_err.value)


def _parser(case) -> pgSQLParser:
    fields, grouping, ordering, filter_, having, config = case
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=WHERE_SQL), query=WHERE_SQL)
    parser.cond_definition = {}
    parser.fields, parser.grouping, parser.ordering = list(fields), list(grouping), list(ordering)
    parser.filter = dict(filter_)
    parser.having = having
    parser.attributes = {"jsonb_unnest": config} if config else {}
    return parser


@pytest.mark.parametrize("case", CASES)
async def test_build_query_parity(case, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    rust_sql = await _parser(case).build_query(querylimit=10)
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    cython_sql = await _parser(case).build_query(querylimit=10)
    assert rust_sql == cython_sql


@pytest.mark.parametrize("case", ERROR_CASES)
async def test_rust_validation_error_is_parser_error_without_fallback(case, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    calls = []
    monkeypatch.setattr(pgsql, "unnest_plan", lambda *a: calls.append(a))
    with pytest.raises(ParserError) as err:
        await _parser(case).build_query()
    assert err.value.code == 400
    assert calls == []  # the Cython planner was never consulted
```
**Why**: `monkeypatch.setattr(pgsql, "unnest_plan", ...)` replaces the name `pgsql.pyx` imported
(module global), proving no fallback happens after a Rust `ValueError` (AC7).
FILL IN: if Cython module globals of `pgsql` are not patchable in your build, assert instead
via `caplog` that no "Rust planner failed" warning was logged — bounded by AC7.

### FILL IN checklist
- [ ] `CASES` — ≥ 16 cases covering the listed areas
- [ ] `ERROR_CASES` — one per distinct message
- [ ] fallback assertion works on the compiled module

---

## Acceptance Criteria

- [ ] All parity tests pass with the staged Rust extension (AC10).
- [ ] `ruff check tests/test_pgsql_jsonb_unnest_parity.py` is clean.

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_unnest_parity.py -q`

---

## Test Specification

The module above.

---

## Agent Instructions

1. Confirm TASK-784 and TASK-786 are completed and the Rust extension is staged (`make stage-rust`).
2. Implement; run the validation command.
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: parity tests in their own file (spec §3 M4 listed them in `test_pgsql_jsonb_unnest.py`).
