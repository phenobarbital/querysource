# TASK-788: Live PostgreSQL integration test for JSONB-array aggregation

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-784
**Assigned-to**: unassigned

---

## Context

Spec §4 Integration Tests (`test_live_graduates_per_course`), AC12. Golden strings prove the
SQL shape; this test proves the SQL **means** the right thing on Postgres 12+: counts per
course, element vs row filter difference, `having`, buckets, `empty: include`, `safe_cast`.
It is opt-in: skipped unless `QS_TEST_POSTGRES_DSN` points at an isolated test database (the
same env var the tenants integration suite uses).

---

## Scope

- Create `tests/integration/test_pgsql_jsonb_unnest_live.py`: create a `TEMP` table in one
  connection, insert fixture rows, render queries with `pgSQLParser.build_query`, execute them
  on the same connection, assert results, close the connection.
- Run on the Cython path and, when available, the Rust path.

**NOT in scope**: production code; slug storage; HTTP handlers.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_pgsql_jsonb_unnest_live.py` | CREATE | Opt-in live DB test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from asyncdb import AsyncDB                          # verified: tests/tenants/conftest.py:84
from querysource.models import QueryObject           # querysource/models.py:24
from querysource.parsers import pgsql                # tests/test_pgsql_jsonb_filters.py:20
from querysource.parsers.pgsql import pgSQLParser    # querysource/providers/pg.py:12
```

### Existing Signatures to Use
```python
# tests/tenants/conftest.py:79-82 — opt-in pattern
postgres_dsn = os.environ.get("QS_TEST_POSTGRES_DSN")
if not postgres_dsn: pytest.skip(...)
# tests/tenants/conftest.py:92-95 — connection pattern
db = AsyncDB("pg", dsn=postgres_dsn); conn = await db.connection()
_, error = await conn.execute("<ddl>")                # returns (result, error)
# querysource/providers/sql.py:235 — `result, error = await conn.query(sql)`
# tests/tenants/conftest.py:127 — `await conn.close()` in finally
# tests/test_pgsql_jsonb_unnest.py (TASK-784) — `_make(query, **attrs)` parser helper
```

### Does NOT Exist
- ~~A shared pg fixture in `tests/conftest.py` for this feature~~ — create the connection in the test module.
- ~~Permanent tables~~ — use `CREATE TEMP TABLE` only (dropped with the session).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/integration/test_pgsql_jsonb_unnest_live.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.build_query"
  ]
}
```

---

## Implementation Notes

- **Fixture rows**, table `jsonb_unnest_students(student_uid text, licensee text, graduation_details jsonb)`:
  1. `s1`, Asia, `[{"course":"Pilates Studio","category":"Comprehensive","course_date":"2025-09-19"}]`
  2. `s2`, Asia, `[{"course":"Pilates Studio",...,"course_date":"2024-03-01"},{"course":"Pilates Mat","category":"Mat","course_date":"2025-01-10"}]`
  3. `s3`, Europe, `[{"course":"Pilates Mat",...,"course_date":"2025-05-05"}]`
  4. `s4`, Asia, `[]`
  5. `s5`, Asia, `NULL`
  6. `s6`, Asia, `{"not":"array"}` (non-array guard)
  7. `s7`, Asia, `[{"course":"Pilates Studio","course_date":"not-a-date","level":5}]` (safe_cast + numeric key)
- Insert with the DDL/INSERT text built in the test (static fixture data, no user input).
- Rows come back as asyncpg records; compare via `{r["course"]: r["graduates"] for r in result}`.

### Assertions (minimum)
| Query | Expected |
|---|---|
| per course, `count(distinct student_uid)`, Asia, `safe_cast` on | Studio: 3 (s1,s2,s7), Mat: 1 (s2) |
| element filter `course = 'Pilates Studio'` grouped by course | only Studio row |
| row filter `graduation_details @> [{"course":"Pilates Studio"}]` grouped by course (Asia) | Studio AND Mat rows (s2's other diploma) — documents the difference |
| `having {"graduates": {">": 1}}` | only Studio |
| `year(course_date::date)` bucket, safe_cast on | 2025-01-01 / 2024-01-01 buckets; s7 in NULL bucket |
| `empty: include` with `count(graduation_details[].course)` per course | extra NULL-course group (s4, s5, s6) with count 0 |
| `safe_cast` OFF with s7 present and a `::date` path | DB error (documented default) |
| prefilter on, element filter `{"graduation_details[].level": "5"}` (a str value, as `set_where` leaves `"5"`) on the numeric JSON key | documents why prefilter is opt-in: with prefilter the row is dropped (0 rows), without it 1 row |

---

## Implementation Blueprint

### Steps (in order)
1. Module-level skip + connection fixture — *why*: never touch a DB unless explicitly configured.
2. Seed the TEMP table on the same connection — *why*: TEMP tables are session-scoped.
3. One test per assertion row; parametrize `use_rust` like TASK-784's tests.

### `tests/integration/test_pgsql_jsonb_unnest_live.py` (CREATE)
```python
"""FEAT-153 AC12: JSONB-array aggregation executed against a real PostgreSQL (opt-in).

Runs only when ``QS_TEST_POSTGRES_DSN`` points at an isolated test database.
Uses a TEMP table, so nothing persists after the connection closes.
"""
from __future__ import annotations

import os

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

POSTGRES_DSN = os.environ.get("QS_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not POSTGRES_DSN, reason="QS_TEST_POSTGRES_DSN not set")

QUERY = "SELECT * FROM jsonb_unnest_students {where_cond}"
DDL = (
    "CREATE TEMP TABLE jsonb_unnest_students "
    "(student_uid text, licensee text, graduation_details jsonb)"
)
ROWS = [
    # FILL IN: the 7 fixture rows from Implementation Notes as (uid, licensee, json_text|None)
]


@pytest.fixture
async def conn():
    from asyncdb import AsyncDB

    db = AsyncDB("pg", dsn=POSTGRES_DSN)
    connection = await db.connection()
    try:
        _, error = await connection.execute(DDL)
        assert not error, error
        # FILL IN: INSERT the ROWS (static data; json via '...'::jsonb literals) — bounded by
        #   "Insert with the DDL/INSERT text built in the test".
        yield connection
    finally:
        await connection.close()


def _make(**attrs) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=QUERY), query=QUERY)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser


@pytest.mark.parametrize("use_rust", [
    pytest.param(True, marks=pytest.mark.skipif(
        not (pgsql.HAS_RUST and hasattr(pgsql._rs, "pgsql_unnest_plan")), reason="no Rust planner")),
    False,
])
async def test_graduates_per_course(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=["graduation_details[].course", "count(distinct student_uid) as graduates"],
        grouping=["graduation_details[].course"],
        filter={"licensee": "'Asia'"},
        attributes={"jsonb_unnest": {"safe_cast": True}},
    )
    result, error = await conn.query(await parser.build_query())
    assert not error, error
    assert {r["course"]: r["graduates"] for r in result} == {"Pilates Studio": 3, "Pilates Mat": 1}

# FILL IN: one test per remaining row of the Assertions table — bounded by AC12.
```

### FILL IN checklist
- [ ] `ROWS` + INSERT
- [ ] remaining assertion tests

---

## Acceptance Criteria

- [ ] With `QS_TEST_POSTGRES_DSN` set to a PG 12+ test DB, all tests pass (AC12).
- [ ] Without it, the module is skipped (no connection attempted).
- [ ] `ruff check tests/integration/test_pgsql_jsonb_unnest_live.py` is clean.

---

## Validation Commands

- `pytest tests/integration/test_pgsql_jsonb_unnest_live.py -q`

---

## Test Specification

The module above.

---

## Agent Instructions

1. Confirm TASK-784 is completed. Ask the operator for an isolated `QS_TEST_POSTGRES_DSN`; never point it at a shared/production DB.
2. Implement; run with and without the env var.
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note (state whether the DB run happened).

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
