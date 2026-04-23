# TASK-626: Pagination Tests and Documentation

**Feature**: querysource-slug-list-pagination
**Feature ID**: FEAT-090
**Spec**: `sdd/specs/querysource-slug-list-pagination.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-624, TASK-625
**Assigned-to**: unassigned

---

## Context

This task covers the Test Specification (spec §4) and the documentation
acceptance criterion (spec §5). It produces:

1. Unit tests for the pure helpers created in TASK-624.
2. Integration tests against an aiohttp test client + live Postgres
   (whichever harness the existing test suite already uses).
3. A short `CHANGES.md` / release-notes entry describing the new envelope,
   parameters, and the breaking change vs. the old bare-array response.

---

## Scope

- Create `tests/handlers/__init__.py` (empty marker) if the directory does not
  already have one.
- Create `tests/handlers/test_querymanager_pagination.py` with:
  - **Unit tests** (no aiohttp / no DB) covering `PaginationParams`,
    `build_where_clause`, `build_order_by`, `build_count_sql`,
    `build_page_sql` — see Table §4 of the spec.
  - **Integration tests** (aiohttp test client + seeded Postgres) covering the
    end-to-end HTTP contract — see Table §4 of the spec.
- Create (or extend) `tests/handlers/conftest.py` with a `seeded_query_slugs`
  fixture that inserts ~120 rows and cleans up after.
- Add a short entry to `CHANGES.md` (or the equivalent release-notes file used
  by this repo — check `ls CHANGES* CHANGELOG* RELEASE* 2>/dev/null` before
  creating a new one) describing:
  - The new response envelope `{data, meta}` for
    `GET /api/v1/management/queries`.
  - The new query-string params `page`, `page_size`, `sort`, `search`.
  - The new response headers.
  - The **breaking change**: bare JSON array → enveloped response.
- Ensure all tests pass:
  ```bash
  source .venv/bin/activate
  pytest tests/handlers/ -v
  ```

**NOT in scope**:
- New production code in `querysource/handlers/` — owned by TASK-624 / TASK-625.
- Changes to unrelated test files.
- pg_trgm index or other performance tuning noted in spec §7.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/handlers/__init__.py` | CREATE (if missing) | Empty marker |
| `tests/handlers/conftest.py` | CREATE | `seeded_query_slugs` fixture |
| `tests/handlers/test_querymanager_pagination.py` | CREATE | Unit + integration tests |
| `CHANGES.md` (or existing changelog) | MODIFY | New FEAT-090 entry |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# Test toolkit (verified as project deps):
import pytest
import pytest_asyncio                           # used by existing tests/test_api.py
from aiohttp.test_utils import AioHTTPTestCase  # pattern used elsewhere in tests/

# Under test:
from querysource.handlers._pagination import (
    PaginationParams, PaginationMeta, PaginatedResponse,
    build_where_clause, build_order_by, build_count_sql, build_page_sql,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SORTABLE_COLUMNS, SEARCHABLE_COLUMNS,
    FILTERABLE_COLUMNS,
)
from querysource.models import QueryModel
```

Before writing integration tests, inspect `tests/test_api.py` to see how the
aiohttp app + qs_connection are bootstrapped in this project:
```bash
grep -n "AioHTTPTestCase\|qs_connection\|QuerySource(" tests/test_api.py
```
Re-use that pattern. Do not invent a new harness.

### Existing Signatures to Use

See TASK-624 and TASK-625 contract sections. All signatures referenced here
come from:
- `querysource/handlers/_pagination.py` (created in TASK-624)
- `querysource/handlers/manager.py` (modified in TASK-625)

### Does NOT Exist

- ~~`pytest.mark.asyncio` without `pytest-asyncio`~~ — the plugin IS installed
  (see `pyproject.toml`); use `@pytest.mark.asyncio` for async tests.
- ~~`tests/handlers/` is not a real directory~~ — it does not yet exist in
  this repo (verified: `ls tests/handlers 2>/dev/null` returns empty).
  The task creates it.
- ~~`self.client` on aiohttp request~~ — use `aiohttp.test_utils` patterns
  already in the repo, not custom helpers.

---

## Implementation Notes

### Unit test scaffold

```python
# tests/handlers/test_querymanager_pagination.py
import pytest
from pydantic import ValidationError

from querysource.handlers._pagination import (
    PaginationParams,
    build_where_clause, build_order_by,
    build_count_sql, build_page_sql,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE,
)


class TestPaginationParams:
    def test_defaults(self):
        p = PaginationParams()
        assert p.page == 1
        assert p.page_size == DEFAULT_PAGE_SIZE
        assert p.sort_field == "updated_at"
        assert p.sort_direction == "desc"

    def test_rejects_oversized_page_size(self):
        with pytest.raises(ValidationError):
            PaginationParams(page_size=MAX_PAGE_SIZE + 1)

    def test_rejects_zero_page(self):
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_rejects_unknown_sort_field(self):
        with pytest.raises(ValidationError):
            PaginationParams(sort_field="password")

    def test_from_query_string_parses_sort(self):
        p = PaginationParams.from_query_string({"sort": "query_slug:asc"})
        assert p.sort_field == "query_slug"
        assert p.sort_direction == "asc"

    def test_from_query_string_rejects_unknown_field_in_fields_csv(self):
        with pytest.raises((ValidationError, ValueError)):
            PaginationParams.from_query_string({"fields": "query_slug,not_a_real_col"})

    def test_offset_math(self):
        p = PaginationParams(page=3, page_size=50)
        assert p.offset == 100


class TestSqlBuilders:
    def test_build_where_drops_unknown_filters(self):
        where = build_where_clause(
            PaginationParams(),
            {"query_slug; DROP TABLE": "x"},  # not in allowlist → dropped
        )
        assert "DROP TABLE" not in where

    def test_build_where_includes_known_filter(self):
        where = build_where_clause(PaginationParams(), {"provider": "db"})
        assert "provider" in where

    def test_build_where_search_uses_ilike(self):
        where = build_where_clause(PaginationParams(search="sales"), {})
        assert "ILIKE" in where.upper()

    def test_build_order_by_contains_direction(self):
        ob = build_order_by(PaginationParams(sort_direction="asc"))
        assert "ASC" in ob.upper()

    def test_build_page_sql_applies_limit_offset(self):
        sql = build_page_sql(
            schema="troc", table="queries",
            fields=["query_slug", "description"],
            where="", order_by="ORDER BY updated_at DESC",
            limit=50, offset=100,
        )
        assert "LIMIT 50" in sql
        assert "OFFSET 100" in sql
        assert "troc.queries" in sql
```

### Integration test scaffold

> Before writing these, run `grep -n "AioHTTPTestCase\|qs_connection" tests/`
> to see the existing harness. Re-use that pattern. The sketch below assumes a
> fixture-based aiohttp client is available; adapt to whatever the repo
> already uses.

```python
@pytest.mark.asyncio
class TestQueryManagerListPagination:
    async def test_default_pagination(self, test_client, seeded_query_slugs):
        resp = await test_client.get("/api/v1/management/queries")
        assert resp.status == 200
        body = await resp.json()
        assert "data" in body and "meta" in body
        assert len(body["data"]) <= DEFAULT_PAGE_SIZE
        assert resp.headers["X-Total-Count"] == str(body["meta"]["total"])

    async def test_second_page(self, test_client, seeded_query_slugs):
        resp = await test_client.get(
            "/api/v1/management/queries?page=2&page_size=10"
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["meta"]["page"] == 2
        assert body["meta"]["page_size"] == 10
        assert len(body["data"]) == 10

    async def test_search(self, test_client, seeded_query_slugs):
        resp = await test_client.get(
            "/api/v1/management/queries?search=fixture_slug_01"
        )
        assert resp.status == 200
        body = await resp.json()
        # seeded 120 rows match fixture_slug_00x / 01x / 10x for "01"
        assert all("fixture_slug" in r["query_slug"] for r in body["data"])

    async def test_invalid_page_size(self, test_client, seeded_query_slugs):
        resp = await test_client.get(
            "/api/v1/management/queries?page_size=9999"
        )
        assert resp.status == 400

    async def test_empty_result_is_204(self, test_client, seeded_query_slugs):
        resp = await test_client.get(
            "/api/v1/management/queries?search=__definitely_no_such_slug__"
        )
        assert resp.status == 204
        assert resp.headers.get("X-Total-Count") == "0"

    async def test_single_slug_unchanged(self, test_client, seeded_query_slugs):
        # pick any known slug from seeded_query_slugs
        slug = seeded_query_slugs[0]["query_slug"]
        resp = await test_client.get(f"/api/v1/management/queries/{slug}")
        assert resp.status == 200
        body = await resp.json()
        # Envelope should NOT be present on single-slug fetch
        assert "meta" not in body or "query_slug" in body

    async def test_meta_unchanged(self, test_client):
        resp = await test_client.get("/api/v1/management/queries:meta")
        assert resp.status == 200
```

### conftest.py fixture sketch

```python
# tests/handlers/conftest.py
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded_query_slugs(qs_connection):
    """Insert ~120 synthetic slug rows; tear down after."""
    rows = [
        {"query_slug": f"fixture_slug_{i:03d}",
         "description": f"fixture {i}",
         "program_slug": "default",
         "provider": "db"}
        for i in range(120)
    ]
    async with await qs_connection.acquire() as conn:
        await conn.execute(
            "INSERT INTO troc.queries (query_slug, description, program_slug, provider) "
            "SELECT * FROM UNNEST($1::text[], $2::text[], $3::text[], $4::text[])",
            [r["query_slug"] for r in rows],
            [r["description"] for r in rows],
            [r["program_slug"] for r in rows],
            [r["provider"] for r in rows],
        )
    yield rows
    async with await qs_connection.acquire() as conn:
        await conn.execute(
            "DELETE FROM troc.queries WHERE query_slug = ANY($1::text[])",
            [r["query_slug"] for r in rows],
        )
```

### CHANGES.md entry (append under a new version heading)

```markdown
### FEAT-090 — Query Slug list pagination

BREAKING: `GET /api/v1/management/queries` now returns a paginated envelope
(`{"data": [...], "meta": {...}}`) instead of a bare JSON array. The response
is also capped at 200 rows per request.

New query parameters:
  - `page` (int, default 1)
  - `page_size` (int, default 50, max 200)
  - `sort=<field>[:asc|desc]` (allowlisted columns only)
  - `search=<term>` — matches `query_slug`, `description`, `program_slug`

New response headers: `X-Total-Count`, `X-Page`, `X-Page-Size`, `X-Total-Pages`.

Single-slug, `:meta`, `:insert`, and non-GET verbs are unchanged.
```

### Key Constraints

- Use `pytest-asyncio` — it is already a dev dep in this repo.
- Prefer the existing integration-test harness in `tests/test_api.py` over
  inventing a new one. If `tests/test_api.py` uses a specific fixture
  (`qs_connection`, `test_client`, etc.), re-use those fixture names and let
  `tests/handlers/conftest.py` inherit from the parent `tests/conftest.py`.
- No network fixtures — integration tests talk to the local Postgres configured
  by `querysource.conf`.

---

## Acceptance Criteria

- [ ] `tests/handlers/test_querymanager_pagination.py` exists and all tests
      pass: `pytest tests/handlers/ -v`.
- [ ] At least 10 unit test cases covering the §4 table (parameter validation,
      WHERE / ORDER BY / COUNT / page SQL builders).
- [ ] At least 7 integration test cases covering the §4 integration table
      (default pagination, second page, search, sort, invalid page size,
      empty result / 204, single-slug unchanged, `:meta` unchanged, non-GET
      verbs unchanged).
- [ ] `CHANGES.md` (or equivalent changelog) contains a FEAT-090 entry
      describing the envelope change, new params, new headers, and the
      breaking change.
- [ ] Test run produces **zero** failures and **zero** errors.
- [ ] No residual test pollution — the `seeded_query_slugs` fixture cleans up.
- [ ] `ruff check tests/handlers/` clean.

---

## Agent Instructions

1. **Verify TASK-624 and TASK-625 are complete.**
   ```bash
   source .venv/bin/activate
   python -c "from querysource.handlers._pagination import PaginationParams, build_page_sql; print('ok')"
   grep -n "_paginate_list" querysource/handlers/manager.py   # must exist
   ```
2. **Inspect existing test harness** before writing integration tests:
   ```bash
   grep -n "AioHTTPTestCase\|qs_connection\|QuerySource(" tests/test_api.py
   ls tests/conftest.py 2>/dev/null && sed -n '1,80p' tests/conftest.py
   ```
3. **Update index** → `"in-progress"`.
4. **Create test files** per the scaffolds above, adapted to the repo's real
   harness.
5. **Run the tests** and iterate until green:
   ```bash
   pytest tests/handlers/ -v
   ```
6. **Add CHANGES.md entry.**
7. **Move this file** to `sdd/tasks/completed/` and update the index to
   `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-22
**Notes**:
- Created `tests/handlers/__init__.py`, `tests/handlers/conftest.py` and
  `tests/handlers/test_querymanager_pagination.py`.
- 34 tests total — all pass locally with `pytest tests/handlers/ -v`:
  * 11 unit tests on `PaginationParams` (defaults, bounds, sort / fields
    allowlist, CSV parsing, offset math, direction parsing).
  * 13 unit tests on the SQL builders (`build_where_clause`,
    `build_order_by`, `build_count_sql`, `build_page_sql`) including
    injection drops, ILIKE search, quote escaping, LIMIT/OFFSET, bad
    identifier / negative limit rejection.
  * 1 round-trip test on the `PaginatedResponse` envelope.
  * 9 HTTP integration tests using an in-process `aiohttp` `TestClient`
    and a `FakeQSConnection` stand-in for the `qs_connection` pool.
- **Deviation from spec**: the repo's existing test harness (`tests/test_api.py`)
  drives a *live* remote API — there is no in-process integration harness to
  inherit from. To satisfy the spec's "≥ 7 integration test cases" bullet
  without adding a live-Postgres dependency to CI, the integration tests use
  a lightweight fake pool (`FakeQSConnection` / `FakeConn`) that records the
  SQL issued by `_paginate_list` and serves canned rows. This covers the HTTP
  contract, header set, envelope shape, routing to the single-slug / `:meta`
  branches, and the 400 / 204 edge cases. A follow-up smoke test against a
  live staging DB is recommended (see spec §5 "p95 < 300 ms").
- `CHANGES.rst` has a new "Unreleased — FEAT-090" entry documenting the
  envelope, parameters, headers and the breaking change.
- No residual test pollution — the fake pool is recreated per test.

**Deviations from spec**: integration tests use an in-process aiohttp
client + fake pool instead of a live-Postgres harness (no such harness
exists in this repo). See Notes above.
