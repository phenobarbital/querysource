# TASK-740: Describe handler — list and detail endpoints + routes

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-735, TASK-736, TASK-737
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 7**, first half. It creates `QueryDescribe(AbstractHandler)` with:
- `GET /api/v1/queries/describe` — list: program pre-filter SQL → ABAC batch → in-memory pagination;
- `GET /api/v1/queries/{slug}/describe` — detail: pre-filter + ABAC + describer + redaction.

It registers both routes. `/columns` and `/vocabulary` are added by TASK-741 on the same class.

It composes TASK-735 (SQL helpers, config), TASK-736 (describer) and TASK-737 (visibility).

---

## Scope

- Create `querysource/handlers/describe.py` with:
  - `QueryDescribe`: `LIST_FIELDS`, `_principal`, `_store`, `_load_visible`, `describe_list`, `describe`;
  - module constants `SLUG_PATTERN` and `VOCABULARY_LINK`.
- Export `QueryDescribe` from `querysource/handlers/__init__.py`.
- Register the 2 GET routes in `querysource/services.py`, right after the `QueryExecutor` block.
- Extend `tests/handlers/conftest.py`:
  - `FakeConn.fetch_all(sql, *args)` records args;
  - add `fetch_one(sql, *args)`.
  - Existing QueryManager tests must keep passing.
- Write `tests/handlers/test_describe_list.py` and `tests/handlers/test_describe_detail.py`, and extend `tests/test_route_registration.py`.

**NOT in scope**:
- `columns()` and `vocabulary()` endpoints (TASK-741).
- Tenant routes (TASK-743).
- Docs and changelog (TASK-742).
- Modifying `QueryManager` or `_enforce_pbac`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/describe.py` | CREATE | `QueryDescribe` (list + detail) |
| `querysource/handlers/__init__.py` | MODIFY | export |
| `querysource/services.py` | MODIFY | import + 2 routes |
| `tests/handlers/conftest.py` | MODIFY | `*args` support in fakes, `fetch_one` |
| `tests/handlers/test_describe_list.py` | CREATE | list integration tests |
| `tests/handlers/test_describe_detail.py` | CREATE | detail integration tests |
| `tests/test_route_registration.py` | MODIFY | describe routes present |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from math import ceil
from aiohttp import web
from pydantic import ValidationError as PydanticValidationError     # verified: handlers/manager.py:18
from datamodel.exceptions import ValidationError                    # verified: handlers/manager.py:14
from asyncdb.exceptions import NoDataFound                          # verified: interfaces/connections.py:13-17
from querysource.handlers.abstract import AbstractHandler           # verified: handlers/abstract.py:27
from querysource.handlers._pagination import (                      # verified: handlers/_pagination.py
    PaginationParams, PaginatedResponse, build_where_clause, build_order_by,
    compose_where, build_scan_sql,                                  # compose_where/build_scan_sql added by TASK-735
)
from querysource.exceptions import SlugNotFound                     # verified: querysource/exceptions.py:34
from querysource.conf import QS_DESCRIBE_MAX_SCAN                   # added by TASK-735
from querysource.auth.slug_visibility import (                      # added by TASK-737
    Principal, PrincipalKind, DescribeStore, resolve_principal, legacy_store,
    build_program_predicate, filter_visible, can_access, describe_grants,
)
from querysource.queries.describe import describe_slug              # added by TASK-736
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):                                  # :27  (instantiated with no args, e.g. QueryExecutor())
    def post_init(self, *args, **kwargs): self.logger = logging.getLogger('QS.Handler')  # :33-34
    async def _get_user_session(self, request: web.Request) -> Optional[SessionData]:   # :289
# navigator BaseHandler (.venv/.../navigator/views/base.py)
    def no_content(self, headers=None, content_type=...)            # :111
    def json_response(self, response=None, reason=None, headers=None, status=200, state=None, cls=None)  # :144
    def error(self, response=None, exception=None, status=400, ...) # :201
    def query_parameters(self, request) -> dict  # {k: v for k, v in request.query.items()}  :309-310

# querysource/handlers/_pagination.py
PaginationParams.from_query_string(qs: dict)   # :121-186 — reads page, page_size, sort ("f" | "f:dir"), search, fields; NOT "q"
    # raises ValueError / pydantic ValidationError
build_where_clause(params, extra_filters: dict) -> str   # :251 — ValueError on unknown filter keys
PaginatedResponse(data=list[dict], meta={page, page_size, total, total_pages}).model_dump()  # :197
# QueryManager 400 style (handlers/manager.py:170-175): self.error(response={"message": f"Invalid ...: {err}"}, status=400)
# QueryManager headers (manager.py:214-219): X-Total-Count, X-Page, X-Page-Size, X-Total-Pages; 204 via self.no_content(headers=)

# asyncdb pg connection
async def fetch_all(self, sentence: str, *args, **kwargs)   # asyncdb/drivers/pg.py:1013
async def fetch_one(self, sentence: str, *args, **kwargs)   # asyncdb/drivers/pg.py:1036
# pool: async with await request.app['qs_connection'].acquire() as conn   # handlers/manager.py:211-212, connections.py:123

# querysource/services.py
from .handlers import (                                     # :21-28 (import block; LoggingService is last, no trailing comma)
    QueryService, QueryHandler, QueryExecutor, QueryManager, VariablesService, LoggingService
)
r = self.app.router.add_post('/api/v1/queries/schema', ds.schema)   # :166 (occurrences: 1)
routes.append(r)                                                     # :167

# querysource/handlers/__init__.py
from .scheduler import SchedulerJobsView     # :12 (occurrences: 1)
    'SchedulerJobsView',                     # :22 (occurrences: 1)

# tests/handlers/conftest.py — FakeConn.fetch_all(self, sql) at :59-64; fetchrow(self, sql) at :66-68;
#   FakeQSConnection.calls: list[tuple[str, str]] at :100
```

### Does NOT Exist
- ~~`QueryDescribe`~~: created here.
- ~~GET routes under `/api/v1/queries`~~: none exist yet (only POST test/run/schema, `services.py:161-166`).
- ~~`q` support in `PaginationParams.from_query_string`~~: the handler maps `q` → `search` when `search` is absent.
- ~~`FakeConn.fetch_one` / `*args` in the fakes~~: added here.
- ~~`QS.get_definition()`~~: not used in this task.
- ~~`querysource.tenants.QueryStore`~~: FEAT-147. Use `DescribeStore` from `slug_visibility`.

---

## Implementation Notes

### Key Constraints (spec §2/§3 Module 7 — binding)
- **`_principal(request)`:** `session = await self._get_user_session(request)`, then `principal = await resolve_principal(request, session)`. `PrincipalKind.NONE` → `raise web.HTTPUnauthorized()` **before any SQL** (AC4).
- **`_store(request)`:** returns `legacy_store()` in this task. TASK-743 adds the tenant branch keyed on `request.match_info.get("tenant")`.
- **`describe_list` flow:**
  1. Principal.
  2. `qp = self.query_parameters(request)`; if `"q" in qp and not qp.get("search")`, set `qp["search"] = qp.pop("q")`.
  3. `PaginationParams.from_query_string(qp)`; `ValueError` or pydantic error → `self.error(response={"message": f"Invalid pagination params: {err}"}, status=400)`.
  4. `predicate = build_program_predicate(principal, store)`; `deny_all` → `self.no_content(headers=<zero headers>)`.
  5. `extra = {k: v for k, v in qp.items() if k not in {"page","page_size","sort","search","q","fields"}}`.
  6. Build `where`, `order_by(nulls_last=True)` and `build_scan_sql(store.schema, store.table, fields, where, order_by, QS_DESCRIBE_MAX_SCAN + 1)`. `ValueError` → 400 `"Invalid filter/sort: ..."`.
  7. `fields = list(LIST_FIELDS)` unless `params.fields`; then `["query_slug", *[f for f in params.fields if f != "query_slug"]]`.
  8. `rows = await conn.fetch_all(sql, *predicate.args)`, then `[dict(r) for r in rows or []]`.
  9. `truncated = len(rows) > QS_DESCRIBE_MAX_SCAN`; if so, keep the first N and `self.logger.warning(...)`.
  10. `allowed = await filter_visible(request, principal, [r["query_slug"] for r in rows], "slug:list", "slug:execute")`; keep the rows whose slug is in `set(allowed)`, in order.
  11. `total = len(visible)`; `total_pages = ceil(total / page_size) if total else 0`; `page_rows = visible[params.offset: params.offset + params.page_size]`.
  12. Headers as in QueryManager, plus `X-Truncated: "true"` when truncated.
  13. `total == 0` → `self.no_content(headers=headers)`; else `self.json_response(PaginatedResponse(...).model_dump(), headers=headers)`.
  - `HEAD` is served automatically by `add_get(..., allow_head=True)`.
- **`_load_visible(request, principal, store, slug)` → `QueryModel`:** raise `web.HTTPNotFound()` for every failure, with **no body**, so the three 404 causes are identical (AC6).
  1. `re.fullmatch(SLUG_PATTERN, slug or "")` must match, where `SLUG_PATTERN = r"[A-Za-z0-9_.\-:]{1,255}"`.
  2. `predicate = build_program_predicate(principal, store, param_index=2)`; `deny_all` → 404.
  3. `exists_sql = f'SELECT 1 FROM "{store.schema}"."{store.table}" WHERE "query_slug" = $1'`, plus `f" AND ({predicate.sql})"` when `predicate.sql`. Validate identifiers first with `_pagination._validate_bare_identifier` (legacy store only).
  4. `row = await conn.fetch_one(exists_sql, slug, *predicate.args)`; `None` → 404.
  5. `model = await store.loader(conn, slug)`; `NoDataFound`, `ValidationError` or `SlugNotFound` → `logger.warning` + 404.
  6. `await can_access(request, principal, slug, "slug:describe", "slug:execute")`; False → 404.
- **`describe`:** principal → store → `_load_visible` → `grants = await describe_grants(request, principal, slug)` → `describe_slug(model, grants, columns_link=f"/api/v1/queries/{slug}/columns", vocabulary_link=VOCABULARY_LINK)` → `self.json_response(payload)`.
  - The handler's datamodel JSON encoder serialises datetimes.
  - For tenant routes (TASK-743), `columns_link` gets the tenant prefix. Use `request.path.rsplit("/", 1)[0] + "/columns"`, because that works for both legacy and tenant paths.
- **Never** include `query_raw` in logs.

---

## Implementation Blueprint

### Steps (in order)
1. Extend `tests/handlers/conftest.py` fakes — *why*: the new handler passes bound args, and the fakes must accept them without breaking QueryManager tests.
2. Create `handlers/describe.py`, part 1 (class, helpers, `_load_visible`) — *why*: shared by detail now and columns in TASK-741.
3. Add `describe_list` and `describe` (part 2) — *why*: the two endpoints of this task.
4. Export and register the routes — *why*: the endpoints are reachable only once registered.
5. Write the tests and run `pytest tests/handlers tests/test_route_registration.py -q` — *why*: AC4–AC12, AC19.

### `tests/handlers/conftest.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "    async def fetch_all(self, sql: str) -> list\[dict\]:" tests/handlers/conftest.py → use -F)
# REPLACE FakeConn.fetch_all (verified :59-64) and fetchrow (:66-68) with:
    async def fetch_all(self, sql: str, *args: Any) -> list[dict]:
        self._pool.calls.append(("fetch_all", sql))
        self._pool.args_log.append(args)
        handler = self._pool.fetch_handler
        if callable(handler):
            return handler(sql, *args) if args else handler(sql)
        return handler or []

    async def fetch_one(self, sql: str, *args: Any) -> Optional[dict]:
        self._pool.calls.append(("fetch_one", sql))
        self._pool.args_log.append(args)
        handler = self._pool.fetch_one_handler
        if callable(handler):
            return handler(sql, *args)
        return handler

    async def fetchrow(self, sql: str) -> Optional[dict]:
        self._pool.calls.append(("fetchrow", sql))
        return None
# AND in FakeQSConnection.__init__ AFTER `        self.calls: list[tuple[str, str]] = []` (verified :100, occurrences: 1):
        self.args_log: list[tuple] = []
        self.fetch_one_handler: Any = None
```
**Why**: `handler(sql)` stays the call shape when no args are passed, so existing QueryManager handlers (single-argument lambdas) keep working.

### `querysource/handlers/describe.py` (CREATE — part 1)
```python
"""Read-only describe endpoints for stored query slugs (FEAT-148)."""
from __future__ import annotations

import re
from math import ceil

from aiohttp import web
from asyncdb.exceptions import NoDataFound
from datamodel.exceptions import ValidationError
from pydantic import ValidationError as PydanticValidationError

from ..auth.slug_visibility import (
    DescribeStore, Principal, PrincipalKind, build_program_predicate, can_access,
    describe_grants, filter_visible, legacy_store, resolve_principal,
)
from ..conf import QS_DESCRIBE_MAX_SCAN
from ..exceptions import SlugNotFound
from ..queries.describe import describe_slug
from ._pagination import (
    PaginatedResponse, PaginationParams, _validate_bare_identifier, build_order_by,
    build_scan_sql, build_where_clause, compose_where,
)
from .abstract import AbstractHandler

SLUG_PATTERN = r"[A-Za-z0-9_.\-:]{1,255}"
VOCABULARY_LINK = "/api/v1/queries/vocabulary"
_PAGINATION_KEYS = frozenset({"page", "page_size", "sort", "search", "q", "fields"})


class QueryDescribe(AbstractHandler):
    """Read-only describe/columns/vocabulary endpoints for query slugs (FEAT-148)."""

    LIST_FIELDS: tuple[str, ...] = ("query_slug", "provider", "description", "program_slug", "updated_at")

    async def _principal(self, request: web.Request) -> Principal:
        """Resolve the caller; raise ``web.HTTPUnauthorized`` when there is none (AC4)."""
        session = await self._get_user_session(request)
        principal = await resolve_principal(request, session)
        if principal.kind is PrincipalKind.NONE:
            raise web.HTTPUnauthorized()
        return principal

    async def _store(self, request: web.Request) -> DescribeStore:
        """Legacy definitions store (tenant branch added by TASK-743)."""
        return legacy_store()

    async def _load_visible(
        self, request: web.Request, principal: Principal, store: DescribeStore, slug: str
    ):
        """Return the visible definition or raise a body-less ``web.HTTPNotFound`` (AC6/AC7)."""
        if not slug or not re.fullmatch(SLUG_PATTERN, slug):
            raise web.HTTPNotFound()
        predicate = build_program_predicate(principal, store, param_index=2)
        if predicate.deny_all:
            raise web.HTTPNotFound()
        # FILL IN: validate identifiers; build exists_sql (+ AND (predicate.sql)); acquire conn;
        #          fetch_one(exists_sql, slug, *predicate.args); loader; map NoDataFound/ValidationError/SlugNotFound → 404;
        #          can_access(slug:describe, slug:execute) → 404 — bounded by AC6/AC7/AC10
        raise web.HTTPNotFound()
```

### `querysource/handlers/describe.py` (CREATE — part 2, append inside `QueryDescribe`)
```python
    async def describe_list(self, request: web.Request) -> web.Response:
        """GET .../queries/describe — 200 | 204 | 400 | 401 (spec §2 List)."""
        principal = await self._principal(request)
        store = await self._store(request)
        qp = self.query_parameters(request)
        if "q" in qp and not qp.get("search"):
            qp["search"] = qp.pop("q")
        try:
            params = PaginationParams.from_query_string(qp)
        except (ValueError, PydanticValidationError) as err:
            return self.error(response={"message": f"Invalid pagination params: {err}"}, status=400)
        predicate = build_program_predicate(principal, store)
        # FILL IN: deny_all → 204 with zero headers; extra filters; fields; where/order_by/scan SQL (ValueError → 400);
        #          fetch_all(sql, *predicate.args); truncation + warning; filter_visible(slug:list, slug:execute);
        #          in-memory page; headers (+X-Truncated); 204 when empty — bounded by AC8/AC9/AC10
        raise NotImplementedError

    async def describe(self, request: web.Request) -> web.Response:
        """GET .../queries/{slug}/describe — 200 | 401 | 404 (spec §2 Detail)."""
        principal = await self._principal(request)
        store = await self._store(request)
        slug = request.match_info.get("slug", "")
        model = await self._load_visible(request, principal, store, slug)
        grants = await describe_grants(request, principal, slug)
        payload = describe_slug(
            model, grants,
            columns_link=request.path.rsplit("/", 1)[0] + "/columns",
            vocabulary_link=VOCABULARY_LINK,
        )
        return self.json_response(payload)
```
**Why this shape**: method names are fixed by spec §3 Module 7 and wired by name in `services.py` and TASK-743. Visibility is evaluated before any describer work, so a denied caller learns nothing, not even timing-heavy metadata.

### `querysource/handlers/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "from .scheduler import SchedulerJobsView" querysource/handlers/__init__.py)
# AFTER — insert below `from .scheduler import SchedulerJobsView` (verified :12)
from .describe import QueryDescribe
# occurrences: 1 (verified: grep -c "    'SchedulerJobsView'," querysource/handlers/__init__.py)
# AFTER — insert below `    'SchedulerJobsView',` (verified :22)
    'QueryDescribe',
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cxF "    LoggingService" querysource/services.py → 1, line :27 inside `from .handlers import (` :21-28)
# REPLACE `    LoggingService` (last item of the `from .handlers import (` block, :27) with:
    LoggingService,
    QueryDescribe,
# occurrences: 1 (verified: grep -c "        r = self.app.router.add_post('/api/v1/queries/schema', ds.schema)" querysource/services.py)
# AFTER — insert below that line's following `        routes.append(r)` (verified :166-167)

        ### Describe API (FEAT-148): read-only slug discovery.
        dh = QueryDescribe()
        r = self.app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)
        routes.append(r)
```
**Why**: these routes are registered next to the other `/api/v1/queries/*` routes, before any future FEAT-147 `/{tenant}/queries/...` routes.

### Tests (CREATE)
```python
# tests/handlers/test_describe_list.py
"""FEAT-148 TASK-740 — GET /api/v1/queries/describe."""
# FILL IN: app fixture: web.Application(); app['qs_connection']=FakeQSConnection(); optional app['security']/
#   app['policy_evaluator'] mocks; QueryDescribe() routes; patch QueryDescribe._get_user_session / resolve_principal inputs.
# Tests: test_list_401_without_principal, test_list_204_no_programs, test_list_program_prefilter_bound_args,
#   test_list_superuser_no_predicate, test_list_abac_union_and_exact_totals, test_list_truncation_header,
#   test_list_invalid_sort_400, test_list_head_headers_only, test_list_q_alias_search

# tests/handlers/test_describe_detail.py
"""FEAT-148 TASK-740 — GET /api/v1/queries/{slug}/describe."""
# Tests: test_detail_401_without_principal, test_detail_404_indistinguishable (missing / other program / ABAC deny → same
#   status and body), test_detail_execute_implies_describe, test_detail_redaction_by_grants,
#   test_detail_pbac_disabled_shows_raw, test_detail_invalid_slug_404, test_detail_never_mutates_meta
#   (store.loader patched to return a QueryModel built from a dict)

# tests/test_route_registration.py — APPEND class TestDescribeRoutes: build web.Application, register the 2 routes as
#   services.py does, assert ("GET", "/api/v1/queries/describe"), ("HEAD", "/api/v1/queries/describe"),
#   ("GET", "/api/v1/queries/{slug}/describe"); assert `from querysource.handlers import QueryDescribe` works.
```

### FILL IN checklist
- [ ] `_load_visible` SQL, loader and ABAC: bounded by AC6/AC7/AC10.
- [ ] `describe_list` body: bounded by AC8/AC9/AC10.
- [ ] All integration test bodies.

---

## Acceptance Criteria

- [ ] `pytest tests/handlers tests/test_route_registration.py -q` passes, including the unchanged QueryManager tests (AC19).
- [ ] AC4: 401 without a principal, with no SQL recorded in `FakeQSConnection.calls`.
- [ ] AC5/AC7: program predicate with bound args on list and detail; `NO_PROGRAMS` → 204 (list) / 404 (detail).
- [ ] AC6: missing slug, other program and ABAC deny give byte-identical 404s; execute implies describe.
- [ ] AC8/AC9: exact totals after ABAC; `NULLS LAST` default order; `X-Truncated` on cap.
- [ ] AC12: redaction by grants.
- [ ] AC14: no datasource connection in list or detail (no `QS`, no provider).
- [ ] `ruff check querysource/handlers/describe.py querysource/handlers/__init__.py querysource/services.py tests/handlers tests/test_route_registration.py` is clean.

---

## Test Specification

See the blueprint test outlines above. Use the aiohttp `TestServer`/`TestClient` pattern from `tests/handlers/conftest.py` (`test_client` fixture).

---

## Agent Instructions

1. **Read the spec** (§2 List/Detail, principal & ABAC rules; §3 Module 7; §5 AC4–AC14, AC19).
2. **Check dependencies**: TASK-735, TASK-736 and TASK-737 completed.
3. **Verify the Codebase Contract**: re-run anchor counts; confirm `_validate_bare_identifier` is still importable.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
