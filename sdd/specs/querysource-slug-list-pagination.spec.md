# Feature Specification: QuerySource Slug List Pagination

**Feature ID**: FEAT-090
**Date**: 2026-04-22
**Author**: Jesus Lara / Claude
**Status**: draft
**Target version**: 5.x

---

## 1. Motivation & Business Requirements

### Problem Statement

The Query Slug management endpoint `GET /api/v1/management/queries` (served by
`QueryManager` at `querysource/handlers/manager.py`) has **no pagination**. It
unconditionally calls `QueryModel.all(...)` and serialises every row as JSON.

With the current production dataset (> 4,000 rows), a single request returns a
multi-megabyte payload that:

1. **Breaks the UI** — the management console freezes / times out when rendering
   the full list.
2. **Wastes bandwidth and DB time** — most UI use cases only need page-sized
   slices plus the total count.
3. **Has no server-side sort or search envelope** — clients must filter locally,
   reinforcing the "load everything" anti-pattern.

We must add first-class pagination (with total count and page metadata) to the
Query Slug CRUD `GET` path without breaking existing consumers of the other verbs
(`PUT`, `POST`, `PATCH`, `DELETE`) or the single-slug `GET /{slug}` path.

### Goals
- Add `page` + `page_size` query parameters to `GET /api/v1/management/queries`.
- Return a **paginated response envelope** with `data`, `total`, `page`,
  `page_size`, `total_pages`.
- Support **server-side sorting** via `sort` (e.g. `sort=updated_at:desc`) and
  **basic text search** via `search` against `query_slug`, `description`,
  `program_slug`.
- Default behaviour when no pagination params are supplied must protect the
  backend: cap page size at a sane ceiling (default **50**, max **200**).
- Preserve existing behaviour for:
  - `GET /api/v1/management/queries/{slug}` (single-row fetch).
  - `GET /api/v1/management/queries:meta` (schema introspection).
  - `GET /api/v1/management/queries/{slug}:insert` (INSERT-SQL generation).
  - `PUT`, `POST`, `PATCH`, `DELETE` verbs.
- Keep the response **backward-compatible where possible**: document the
  envelope change clearly and expose pagination metadata in both JSON body and
  response headers (`X-Total-Count`, `X-Page`, `X-Page-Size`).

### Non-Goals (explicitly out of scope)
- Pagination for the `MultiQuery`, `QueryExecutor`, or `QueryService` endpoints.
- Cursor/keyset pagination — offset pagination is sufficient at this scale.
- Reworking the `QueryModel` datamodel itself.
- Extending `asyncdb.Model.all()` / `Model.filter()` upstream to accept
  `_limit` / `_offset` (handler-level raw SQL is used instead).
- Rewriting authentication / authorization for the management endpoint.

---

## 2. Architectural Design

### Overview

Pagination will be implemented **inside `QueryManager.get()`** using raw SQL
issued through the existing `qs_connection` pool. `asyncdb.Model.all()` /
`Model.filter()` do not natively accept `LIMIT`/`OFFSET` (verified — see
Codebase Contract §6), so the handler will:

1. Parse query-string params (`page`, `page_size`, `sort`, `search`, plus
   existing `fields` and filter kwargs).
2. Build a `WHERE` clause from search + filter kwargs (re-using `QueryModel`'s
   column metadata for safe identifier resolution).
3. Run two queries in a single acquired connection:
   - `SELECT COUNT(*) FROM troc.queries WHERE <where>` → total.
   - `SELECT <fields> FROM troc.queries WHERE <where> ORDER BY <sort> LIMIT <n> OFFSET <m>` → page.
4. Emit a JSON envelope + pagination headers.

The single-slug `GET /{slug}`, `:meta`, and `:insert` branches are **untouched**.

### Component Diagram
```
GET /api/v1/management/queries?page=1&page_size=50&sort=updated_at:desc&search=sales
         │
         ▼
QueryManager.get()
         │
         ├── _is_list_request()  ──► True (no slug in URL, no :meta suffix)
         │
         ▼
QueryManager._paginate_list()      (NEW helper)
         ├── _parse_pagination_params(qp)    → PaginationParams (Pydantic)
         ├── _build_where_clause(params, qp) → (where_sql, search_sql)
         ├── _build_order_by(params)         → order_sql
         │
         ├── async with db.acquire() as conn:
         │       total = await conn.fetchval(count_sql)
         │       rows  = await conn.fetch(page_sql)
         │
         └── json_response(PaginatedResponse(...), headers={X-Total-Count, X-Page, X-Page-Size})
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QueryManager` (`querysource/handlers/manager.py:18`) | extends `.get()` method | Adds list-pagination branch; preserves slug / meta / insert branches |
| `QueryView` (`querysource/utils/handlers.py:51`) | uses | Re-uses `query_parameters()`, `json_response()`, `error()`, `no_content()` |
| `QueryModel` (`querysource/models.py:48`) | uses (metadata only) | Column introspection for allowlist (sort / search / filter fields) |
| `qs_connection` pool (`request.app['qs_connection']`) | uses | Acquires a pg connection and runs raw `SELECT`/`COUNT` |
| `Entity.toSQL` / `Entity.quoteString` (`querysource/types/validators.py`) | uses | Safe value coercion for WHERE clause (same helper used by `get_query_insert`) |

### Data Models

```python
# querysource/handlers/manager.py (or a new querysource/handlers/_pagination.py)

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


SortDirection = Literal["asc", "desc"]

DEFAULT_PAGE_SIZE: int = 50
MAX_PAGE_SIZE: int = 200
DEFAULT_SORT_FIELD: str = "updated_at"
DEFAULT_SORT_DIRECTION: SortDirection = "desc"


class PaginationParams(BaseModel):
    """Validated pagination / sort / search parameters for the slug list endpoint."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort_field: str = Field(default=DEFAULT_SORT_FIELD)
    sort_direction: SortDirection = Field(default=DEFAULT_SORT_DIRECTION)
    search: Optional[str] = Field(default=None, max_length=255)
    fields: Optional[list[str]] = Field(default=None)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel):
    data: list[dict]
    meta: PaginationMeta
```

### New Public Interfaces

The HTTP contract is the public interface. No new Python classes are exported
outside the handler module.

```
GET /api/v1/management/queries
    ?page=<int, default 1>
    &page_size=<int, default 50, max 200>
    &sort=<field>[:asc|desc]         # e.g. sort=query_slug:asc
    &search=<str>                    # matches query_slug, description, program_slug
    &fields=<csv of QueryModel columns>
    &<any QueryModel column>=<value> # existing filter behaviour preserved

Response 200 (application/json):
{
  "data": [
    { "query_slug": "...", "description": "...", ... },
    ...
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total": 4231,
    "total_pages": 85
  }
}

Response headers:
  X-Total-Count: 4231
  X-Page: 1
  X-Page-Size: 50
  X-Total-Pages: 85

Response 204 when total == 0 (preserving existing `self.no_content()` semantics).
Response 400 on invalid page / page_size / sort.
```

---

## 3. Module Breakdown

### Module 1: Pagination parameter & response models
- **Path**: `querysource/handlers/_pagination.py` (NEW)
- **Responsibility**: `PaginationParams`, `PaginationMeta`, `PaginatedResponse`
  Pydantic models + module-level constants (`DEFAULT_PAGE_SIZE`,
  `MAX_PAGE_SIZE`, allowlists of sortable / searchable columns derived from
  `QueryModel`).
- **Depends on**: `pydantic`, `querysource.models.QueryModel`

### Module 2: SQL builders
- **Path**: `querysource/handlers/_pagination.py` (same file as Module 1)
- **Responsibility**: Pure helpers:
  - `build_where_clause(params: PaginationParams, extra_filters: dict) -> tuple[str, list]`
  - `build_order_by(params: PaginationParams) -> str`
  - `build_count_sql(where: str) -> str`
  - `build_page_sql(fields, where, order_by, limit, offset) -> str`
  All builders MUST validate column identifiers against a fixed allowlist
  (keys of `QueryModel.columns(QueryModel)`) and route scalar values through
  `Entity.toSQL` / `Entity.quoteString` to prevent SQL injection.
- **Depends on**: Module 1, `querysource/types/validators.py` (`Entity`)

### Module 3: `QueryManager.get()` list branch
- **Path**: `querysource/handlers/manager.py` (MODIFIED)
- **Responsibility**: New private method `_paginate_list(qp, args, meta)` invoked
  from `get()` when the request is a "list" request (no slug in URL, no
  `:meta` suffix, not `:insert`). Existing single-slug / `:meta` / `:insert`
  branches remain untouched.
- **Depends on**: Module 1, Module 2.

### Module 4: Tests
- **Path**: `tests/handlers/test_querymanager_pagination.py` (NEW directory
  `tests/handlers/` — currently empty).
- **Responsibility**: Unit + integration coverage (see §4).
- **Depends on**: `pytest`, `pytest-asyncio`, `aiohttp.test_utils`, test fixture
  Postgres (same harness used by `tests/test_api.py` if present).

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_pagination_params_defaults` | 1 | `PaginationParams()` yields page=1, page_size=50, sort `updated_at:desc` |
| `test_pagination_params_clamps_page_size` | 1 | `page_size=9999` raises ValidationError (> MAX_PAGE_SIZE) |
| `test_pagination_params_rejects_negative_page` | 1 | `page=0` or `page<0` rejected |
| `test_pagination_params_parses_sort_string` | 1 | `sort=query_slug:asc` resolves to `sort_field='query_slug'`, direction `asc` |
| `test_pagination_params_rejects_unknown_sort_field` | 1 | `sort=evil_col:desc` raises (not in QueryModel columns allowlist) |
| `test_build_where_clause_search_only` | 2 | `search='sales'` → `ILIKE '%sales%'` applied to allowlisted columns |
| `test_build_where_clause_extra_filters` | 2 | `{"provider": "db"}` merges into WHERE, identifiers validated |
| `test_build_where_clause_rejects_injection` | 2 | extra filter with key `"query_slug; DROP TABLE"` is dropped (not in allowlist) |
| `test_build_order_by_allowlist` | 2 | `sort_field='updated_at'` OK; `sort_field='password'` rejected (not a column) |
| `test_build_page_sql_applies_limit_offset` | 2 | Generated SQL contains `LIMIT 50 OFFSET 100` for `page=3, page_size=50` |

### Integration Tests
| Test | Description |
|---|---|
| `test_get_queries_default_pagination` | `GET /api/v1/management/queries` returns at most 50 rows; envelope present; `X-Total-Count` header set |
| `test_get_queries_second_page` | `?page=2&page_size=10` returns rows 11-20 of the ordered set |
| `test_get_queries_search` | `?search=<slug-prefix>` only returns matching rows; total matches filtered count |
| `test_get_queries_sort_asc` | `?sort=query_slug:asc` returns rows ordered alphabetically |
| `test_get_queries_invalid_page_size` | `?page_size=9999` returns 400 with useful error body |
| `test_get_queries_empty_result` | `?search=__no_such_slug__` returns 204 (no_content) with `X-Total-Count: 0` |
| `test_get_single_slug_unchanged` | `GET /api/v1/management/queries/known_slug` behaviour identical to pre-change |
| `test_get_meta_unchanged` | `GET /api/v1/management/queries:meta` still returns schema JSON |
| `test_get_insert_meta_unchanged` | `GET /api/v1/management/queries/known_slug:insert` still returns INSERT SQL |
| `test_put_post_patch_delete_unchanged` | Smoke tests that the other verbs still work |

### Test Data / Fixtures

```python
# tests/handlers/conftest.py
import pytest

@pytest.fixture
async def seeded_query_slugs(qs_connection):
    """Insert ~120 synthetic slug rows into troc.queries so pagination is observable."""
    rows = [
        {"query_slug": f"fixture_slug_{i:03d}",
         "description": f"fixture {i}",
         "program_slug": "default",
         "provider": "db"}
        for i in range(120)
    ]
    async with await qs_connection.acquire() as conn:
        # bulk insert; tear down in fixture finalizer
        ...
    yield rows
    # cleanup
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `GET /api/v1/management/queries` returns at most `MAX_PAGE_SIZE` (200) rows
      per request, regardless of dataset size.
- [ ] Default response (no query-string params) returns page 1 with page size 50
      and a valid `meta` + pagination headers.
- [ ] Response includes `X-Total-Count`, `X-Page`, `X-Page-Size`, `X-Total-Pages`
      headers.
- [ ] `page`, `page_size`, `sort`, `search` work as documented.
- [ ] Unknown / unsafe sort / search / filter column names are rejected (400),
      not silently ignored, and never injected into SQL.
- [ ] All unit tests pass (`pytest tests/handlers/ -v`).
- [ ] All integration tests pass against a live Postgres fixture with ≥ 120
      seeded rows.
- [ ] Single-slug `GET`, `:meta`, `:insert`, and `PUT` / `POST` / `PATCH` /
      `DELETE` verbs have identical behaviour and wire format to pre-change.
- [ ] A request that previously returned 4,000+ rows (now paginated) responds in
      **< 300 ms** p95 on the staging DB (benchmark with `wrk` or `ab`).
- [ ] Documentation updated in `docs/` (or a short `CHANGES.md` note) describing
      the new envelope + query parameters.
- [ ] No new `print()` statements — all logging uses `self.logger`.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods not
> listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# verified: querysource/handlers/manager.py:13
from ..models import QueryModel

# verified: querysource/handlers/manager.py:15
from ..utils.handlers import QueryView

# verified: querysource/handlers/manager.py:16
from ..types.validators import Entity

# verified: querysource/handlers/manager.py:7
from aiohttp.web import View   # QueryView inherits from navigator.views.BaseView (see below)

# verified: querysource/handlers/manager.py:8
from navconfig.logging import logging

# verified: querysource/handlers/manager.py:10
from asyncdb.exceptions import NoDataFound

# verified: querysource/handlers/manager.py:9
from datamodel.exceptions import ValidationError

# verified: querysource/utils/handlers.py:12
from navigator.views import BaseHandler, BaseView
```

### Existing Class Signatures

```python
# querysource/handlers/manager.py:18
class QueryManager(QueryView):
    _model: QueryModel = None                                      # line 19

    def post_init(self, *args, **kwargs): ...                      # line 21
    def get_model(self, **kwargs) -> QueryModel: ...               # line 25
    def get_query_insert(self, query: QueryModel) -> str: ...      # line 33

    async def get(self): ...                                       # line 57   <-- WILL BE EXTENDED
    async def patch(self): ...                                     # line 137  (unchanged)
    async def delete(self): ...                                    # line 208  (unchanged)
    async def put(self): ...                                       # line 283  (unchanged)
    async def post(self): ...                                      # line 348  (unchanged)
```

```python
# querysource/utils/handlers.py:51
class QueryView(BaseView):
    cors_config: dict                                              # line 53
    def query_parameters(self, request: web.Request = None) -> dict: ...  # line 63
    def parse_qs(self, request: web.Request = None) -> Optional[dict]: ...# line 68

# Inherited from navigator.views.BaseView (used in QueryManager.get):
#   self.request               -> aiohttp.web.Request
#   self.get_arguments()       -> dict (URL match + query)
#   self.match_parameters(req) -> dict (URL match info)
#   self.json_data()           -> parsed JSON body
#   self.json_response(body, *, status=200, headers=None)
#   self.no_content(headers=None)
#   self.error(response=..., reason=..., exception=..., status=..., headers=...)
#   self.critical(response=..., exception=..., traceback=...)
```

```python
# querysource/models.py:48
class QueryModel(Model):
    # Primary key
    query_slug: str                 # line 49, required=True, primary_key=True
    # Commonly listed fields (see §1 default columns list)
    description: str                # line 50
    source: Optional[str]           # line 52
    params: Optional[dict]          # line 53  (jsonb)
    attributes: Optional[dict]      # line 54  (jsonb)
    conditions: Optional[dict]      # line 61  (jsonb)
    cond_definition: Optional[dict] # line 62  (jsonb)
    fields: List[str]               # line 64  (array)
    filtering: Optional[dict]       # line 65  (jsonb)
    ordering: List[str]             # line 66  (array)
    grouping: List[str]             # line 67  (array)
    qry_options: Optional[dict]     # line 68  (jsonb)
    h_filtering: bool               # line 69
    query_raw: str                  # line 71
    is_raw: bool                    # line 72
    is_cached: bool                 # line 73
    provider: str                   # line 74
    parser: str                     # line 75
    cache_timeout: int              # line 76
    cache_refresh: int              # line 77
    cache_options: Optional[dict]   # line 78  (jsonb)
    program_id: int                 # line 80
    program_slug: str               # line 81
    dwh: bool                       # line 83
    dwh_driver: str                 # line 84
    dwh_info: Optional[dict]        # line 85  (jsonb)
    dwh_scheduler: Optional[dict]   # line 86  (jsonb)
    created_at: datetime            # line 88
    created_by: int                 # line 93
    updated_at: datetime            # line 94
    updated_by: int                 # line 99

    class Meta:                     # line 101
        driver = "pg"
        name = QS_QUERIES_TABLE     # from querysource.conf
        schema = QS_QUERIES_SCHEMA  # from querysource.conf
        strict = True
        frozen = False
        remove_nulls = True

# Classmethods inherited from asyncdb.models.Model (verified in
# ~/.local/lib/python3.10/site-packages/asyncdb/models/model.py):
#   async def get(cls, **kwargs)        -> Model       # line 346
#   async def filter(cls, *args, **kw)  -> list[Model] # line 319
#   async def all(cls, **kwargs)        -> list[Model] # line 375
#   def columns(cls)                    -> dict[str, Field]
```

```python
# querysource/services.py  (route registration)
# line 128-135 — the routes QueryManager serves
r = self.app.router.add_view(
    r'/api/v1/management/queries/{slug}', QueryManager          # line 129
)
r = self.app.router.add_view(
    r'/api/v1/management/queries{meta:\:?.*}', QueryManager     # line 133
)
# NOTE: the LIST request matches the second route with meta == '' (empty).
#       That is the branch the new pagination logic must target.
```

```python
# Connection acquisition pattern (verified: manager.py:102-104)
db = self.request.app['qs_connection']
async with await db.acquire() as conn:
    # conn is an asyncdb pg connection — supports .fetch(sql), .fetchval(sql), .fetchrow(sql)
    ...
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `_pagination.PaginationParams` | `QueryManager.get()` | instantiated from `self.query_parameters(self.request)` | `querysource/handlers/manager.py:62` |
| `_pagination.build_*_sql()` | pg connection | `await conn.fetch(sql)` / `await conn.fetchval(sql)` | `querysource/handlers/manager.py:102-104` |
| column allowlist | `QueryModel.columns(QueryModel)` | dict keys | `querysource/models.py:48` + asyncdb `Model.columns` |
| safe value coercion | `Entity.toSQL(val, type)` / `Entity.quoteString(...)` | static calls | `querysource/handlers/manager.py:46-49` |

### Does NOT Exist (Anti-Hallucination)

- ~~`QueryModel.paginate()`~~ — NOT a method on `QueryModel` or on
  `asyncdb.models.Model`. Do not invoke.
- ~~`QueryModel.all(limit=..., offset=...)`~~ — `asyncdb.drivers.pg._all_` does
  NOT forward `limit` / `offset` kwargs (verified: `pg.py:1505-1519` builds
  `SELECT ... FROM table` with no LIMIT clause). Use raw SQL via the acquired
  connection instead.
- ~~`QueryModel.filter(_limit=..., _offset=...)`~~ — same reason
  (`pg.py:1405-1438`). The `_limit` / `_offset` fields exist only on the
  separate `QueryObject(ClassDict)` runtime options class (`models.py:41-43`)
  and are NOT query-builder hooks for `QueryModel`.
- ~~`QueryModel.count()`~~ — does not exist on `asyncdb.models.Model`.
  Use `await conn.fetchval("SELECT COUNT(*) FROM ...")`.
- ~~`self.paginate(...)`~~ / ~~`QueryView.paginate(...)`~~ — no such helper in
  `querysource/utils/handlers.py` or `navigator.views.BaseView`.
- ~~`QueryModel.Meta.columns`~~ — use `QueryModel.columns(QueryModel)` (the
  asyncdb model classmethod), not a Meta attribute.
- ~~`from querysource.handlers.pagination import ...`~~ — file does not exist
  yet; it is created by this feature as `querysource/handlers/_pagination.py`
  (underscore-prefixed, since it is an internal helper).

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Async-first throughout — all DB calls via `await conn.fetch / fetchval`.
- Pydantic for all structured input (`PaginationParams`) and output
  (`PaginatedResponse`).
- Replace `print(...)` debug statements in the handler with `self.logger.debug`
  / `self.logger.error` when touching surrounding lines.
- Follow the existing error-envelope pattern: `self.error(response={...},
  exception=..., status=...)` + `X-STATUS` / `X-MESSAGE` / `X-ERROR` headers.
- Column identifiers in generated SQL MUST be drawn from the
  `QueryModel.columns(QueryModel)` allowlist — never interpolate raw
  user-supplied strings.
- Literal values in generated SQL MUST go through `Entity.toSQL` +
  `Entity.quoteString` (same path used by `get_query_insert`) to prevent SQL
  injection.
- Keep the list-pagination branch in `get()` **purely additive**; do not alter
  the single-slug, `:meta`, or `:insert` branches.

### Known Risks / Gotchas
- **Backward compatibility of the response shape.** Today the list endpoint
  returns a bare JSON array. The new envelope (`{data, meta}`) is a breaking
  change for any consumer that iterates the top-level array. Mitigation: the
  only known consumer is the broken UI; document the change in `CHANGES.md`,
  and additionally expose `X-Total-*` headers so clients that only need the
  list can still be adapted with minimal churn.
- **Sort on jsonb / array columns.** Allow sorting only on scalar columns
  (`query_slug`, `description`, `program_slug`, `provider`, `is_cached`,
  `created_at`, `updated_at`). Reject array / jsonb columns at parse time.
- **`search` LIKE performance.** `ILIKE '%term%'` on a 4k-row table is fine,
  but if the table grows past ~50k rows we will need a pg `pg_trgm` index.
  Out of scope for v1 — noted here for future work.
- **Empty-result semantics.** Today, `NoDataFound` triggers `no_content()`
  (204). Preserve this: when `total == 0`, return `no_content()` with
  pagination headers set (`X-Total-Count: 0`).
- **Concurrent COUNT + page fetch** both run on the same connection inside one
  `async with db.acquire()` block — sequential, not pipelined. This keeps the
  pool happy and avoids dealing with asyncpg cursor semantics.
- **`kwargs` filter passthrough.** The current `get()` passes arbitrary query
  params straight into `QueryModel.filter(**qp)`. Under the new code path, any
  filter key NOT in the `QueryModel.columns` allowlist MUST be rejected (400)
  rather than silently forwarded — tightening this is deliberate.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pydantic` | `>=2.0` | `PaginationParams` / `PaginatedResponse` models (already a project dep) |

No new dependencies.

---

## 8. Open Questions

- [ ] Should the response envelope be `{data, meta}` (proposed) or the
      flatter `{items, total, page, page_size}`? — *Owner: Jesus Lara*
- [ ] Do any internal clients other than the UI call
      `GET /api/v1/management/queries` expecting a bare array? If yes, we may
      need a transitional `?envelope=false` escape hatch. — *Owner: Jesus Lara*
- [ ] Should `search` match `query_slug` + `description` + `program_slug`, or
      also `source`? — *Owner: Jesus Lara*
- [ ] Default sort: `updated_at:desc` (proposed) vs `query_slug:asc` — which
      matches the UI's current expectation? — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Default isolation unit**: `per-spec` (sequential tasks in one worktree).
- All modules (§3) are in the same file tree and share types — no
  parallelisable tasks.
- **Cross-feature dependencies**: none. FEAT-090 is self-contained in
  `querysource/handlers/` and `tests/handlers/`.
- Recommended branch / worktree:
  ```
  git worktree add -b feat-FEAT-090-querysource-slug-list-pagination \
      .worktrees/feat-FEAT-090-querysource-slug-list-pagination HEAD
  ```

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-22 | Jesus Lara / Claude | Initial draft |
