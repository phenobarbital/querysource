# TASK-762: Kind-aware dispatch in TenantQueryHandler

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-760
**Assigned-to**: unassigned

---

## Context

Spec §2 Overview, §3 Module 3, design-research S1/S2. This is the fix itself: the
tenant route must classify the stored definition and dispatch multi definitions to
`QueryHandler`. One shared `_prepare()` does resolve → **authorize** → load →
classify → stash, so `query`, `columns` and `test_slug` cannot drift. Authorization
comes before the repository read so an unauthorized caller cannot tell an existing
slug from a missing one (S1, AC-9).

---

## Scope

- Add `_prepare`, `_load_definition`, `_is_multi` to `TenantQueryHandler`.
- Rewrite the slug paths of `query`, `columns`, `test_slug` to use them; the no-slug inline path of `query` is unchanged.
- Update the existing route tests that now need a fake repository, and add dispatch tests.

**NOT in scope**: `QueryHandler` internals (TASK-760), `QueryService` forwarding (TASK-761), docs and integration test (TASK-763).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/tenant.py` | MODIFY | `_prepare`, `_load_definition`, `_is_multi`; kind-aware routes |
| `tests/tenants/test_tenant_http_routes.py` | MODIFY | supply a fake repository with `get()` to existing slug tests |
| `tests/tenants/test_tenant_multi_dispatch.py` | CREATE | dispatch, ordering, read-count, parity tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# already in querysource/handlers/tenant.py:13-16
from querysource.handlers.abstract import AbstractHandler
from querysource.repositories import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry
# add (verified: querysource/tenants.py:46,54):
from querysource.tenants import LoadedDefinition, QueryIdentity
```
Keep `from .service import QueryService` / `from .multi import QueryHandler` as lazy
imports inside the methods (`tenant.py:242, 246`): the existing tests monkeypatch
`querysource.handlers.service.QueryService` and `querysource.handlers.multi.QueryHandler`.

### Existing Signatures to Use
```python
# querysource/handlers/tenant.py
def _resolve_or_raise(registry: TenantRegistry, tenant: str | None) -> QueryStore:  # 127
class TenantQueryHandler(AbstractHandler):                                          # 142
    def _registry(self, request) -> TenantRegistry:                                 # 156
    def _repository(self, request) -> DefinitionRepository:                         # 162
    async def query(self, request) -> web.StreamResponse:                           # 228 (slug branch 240-244)
    async def columns(self, request) -> web.StreamResponse:                         # 251
    async def test_slug(self, request) -> web.StreamResponse:                       # 266

# querysource/handlers/abstract.py:463
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None  # no-op when app.get("security") is None; web.HTTPNotFound on deny

# querysource/repositories/definitions.py:161
async def get(self, identity: QueryIdentity) -> LoadedDefinition  # TenantError(error_code="query_not_found") when missing

# querysource/tenant_errors.py
class TenantError(QueryException): error_code: str; code: int  # OWNERSHIP_STATUS: invalid_tenant 400, tenant_not_available 404, query_not_found 404, tenant_store_unavailable 503, tenant_write_forbidden 403, tenant_worker_unsupported 502

# delegates (after TASK-760)
QueryHandler.query(request) ; QueryHandler.columns(request) ; QueryHandler.test_slug(request)
QueryService.query(request) ; QueryService.get_columns(request) ; QueryService.columns(request) ; QueryService.test_slug(request)
```

### Does NOT Exist
- ~~`TenantQueryHandler.get_columns`~~ — HEAD stays inside `columns()`.
- ~~`request['qs_definition']`~~ before this task — created here.
- ~~A `query_raw` sniffing classifier~~ — only `runtime.provider == 'multi'` (AC-3).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/tenant.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_tenant_http_routes.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_tenant_multi_dispatch.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/tenant.py#TenantQueryHandler",
    "sym:querysource/handlers/tenant.py#_resolve_or_raise",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_owned_slug",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.get",
    "sym:querysource/handlers/multi.py#QueryHandler.test_slug"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Order inside `_prepare` is fixed: resolve → authorize → load (S1). A PBAC deny and a missing slug are both plain 404 (AC-9, AC-11).
- Strip a `:format` suffix only for the identity; leave `match_info['slug']` untouched so delegates still parse the format.
- Exactly one `DefinitionRepository.get` per request (AC-4).
- Tenant selector and definition travel only on the request (AC-10).

---

## Implementation Blueprint

### Steps (in order)
1. Add the helpers after `_repository` — *why*: S2, one shared front half.
2. Rewrite the slug branch of `query` and the bodies of `columns` / `test_slug` — *why*: AC-1, AC-6, AC-8.
3. Update existing tests to provide a fake repository — *why*: `object()` has no `get()`.
4. Add the new tests.

### `querysource/handlers/tenant.py` (MODIFY) — helpers
```python
# AFTER the `_repository` method (verified: querysource/handlers/tenant.py:162-166)
    async def _load_definition(
        self, request: web.Request, store: QueryStore, slug: str
    ) -> LoadedDefinition:
        """Read the stored definition once; map owner errors to HTTP.

        Raises:
            web.HTTPNotFound: ``query_not_found`` / ``tenant_not_available``.
            web.HTTPBadRequest: ``invalid_tenant``.
            TenantError: any other code (e.g. 503); callers answer with ``self.error``.
        """
        repo = self._repository(request)
        try:
            return await repo.get(QueryIdentity(store=store, slug=slug))
        except TenantError as err:
            if err.code == 404:
                raise web.HTTPNotFound(reason=f"Query not found: {slug}") from err
            if err.code == 400:
                raise web.HTTPBadRequest(reason=str(err)) from err
            raise

    @staticmethod
    def _is_multi(definition: LoadedDefinition) -> bool:
        """True iff the stored definition is a multi-query (``provider == 'multi'``)."""
        return getattr(definition.runtime, "provider", None) == "multi"

    async def _prepare(self, request: web.Request) -> tuple[LoadedDefinition, bool]:
        """Resolve tenant, authorize, load and classify the stored slug.

        Stashes ``request['qs_tenant']`` and ``request['qs_definition']``.
        """
        tenant = request.match_info.get("tenant")
        store = _resolve_or_raise(self._registry(request), tenant)
        slug = str(request.match_info.get("slug") or "").split(":", 1)[0]
        request["qs_tenant"] = tenant
        await self._enforce_owned_slug(
            request, identity=QueryIdentity(store=store, slug=slug), action="slug:execute"
        )
        definition = await self._load_definition(request, store, slug)
        request["qs_definition"] = definition
        return definition, self._is_multi(definition)
```

### `querysource/handlers/tenant.py` (MODIFY) — routes
```python
# occurrences: 1 (verified: grep -c '        slug = request.match_info.get("slug")' querysource/handlers/tenant.py)
# In query(): REPLACE lines 240-244 (the `slug = ...` / `if slug:` QueryService branch) with:
        slug = request.match_info.get("slug")
        if slug:
            try:
                _definition, is_multi = await self._prepare(request)
            except TenantError as err:
                return self.error(response={"message": str(err)}, status=err.code)
            if is_multi:
                from .multi import QueryHandler

                return await QueryHandler(request).query(request)
            from .service import QueryService

            return await QueryService(request).query(request)
# The existing `request["qs_tenant"] = tenant` (line 238) and the inline branch
# (lines 246-249) stay as they are.
```
```python
# columns() — REPLACE the body after the docstring (lines 253-264) with:
        try:
            _definition, is_multi = await self._prepare(request)
        except TenantError as err:
            return self.error(response={"message": str(err)}, status=err.code)
        if is_multi:
            from .multi import QueryHandler

            return await QueryHandler(request).columns(request)
        from .service import QueryService

        handler = QueryService(request)
        if request.method == "HEAD":
            return await handler.get_columns(request)
        return await handler.columns(request)
```
```python
# test_slug() — REPLACE the body after the docstring (lines 268-277) with the same
# shape: _prepare(); multi -> QueryHandler(request).test_slug(request);
# else -> QueryService(request).test_slug(request).
```
Update the three method docstrings to state the kind-aware behavior.

### `tests/tenants/test_tenant_http_routes.py` (MODIFY)
```python
# FILL IN: add a module-level `_FakeRepo` whose `get(identity)` returns a LoadedDefinition
#   with provider="db" for any slug, and use it instead of `object()` as
#   app["qs_definition_repository"] in test_single_multi_inline_dispatch (line 82) and
#   test_columns_test_and_output_suffixes (line 165). Assertions stay the same: a
#   provider="db" slug must still reach QueryService. Bounded by AC-2 (no behavior change
#   for single slugs).
```

### `tests/tenants/test_tenant_multi_dispatch.py` (CREATE)
```python
"""FEAT-151: TenantQueryHandler classifies stored definitions and dispatches by kind."""
import pytest
from aiohttp import web

from querysource.handlers.tenant import TenantQueryHandler
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore, TenantRegistry

# FILL IN: _mock_registry/_mock_request copied from tests/tenants/test_tenant_http_routes.py:11-41;
#   a counting _FakeRepo; fakes for QueryService/QueryHandler recording which method ran.
# FILL IN tests (spec §4):
#   test_stored_multi_slug_dispatches_to_query_handler
#   test_stored_single_slug_dispatches_to_query_service   (incl. JSON query_raw under provider='db')
#   test_authorization_precedes_definition_load           (patch _enforce_owned_slug to raise
#       web.HTTPNotFound; repo.get never called; existing vs missing slug give identical 404)
#   test_one_repository_read_per_request                  (query, columns HEAD/PATCH, test_slug)
#   test_missing_slug_returns_404_before_dispatch
#   test_store_unavailable_returns_503                    (TenantError tenant_store_unavailable)
#   test_slug_format_suffix_stripped_before_peek          ('parent:csv' -> identity slug 'parent')
#   test_multi_columns_and_test_route_dispatch
```

### FILL IN checklist
- [ ] `test_slug()` body — same shape as `columns()`; bounded by AC-8.
- [ ] existing route tests' fake repository — bounded by AC-2.
- [ ] eight new tests — bounded by AC-1, AC-3, AC-4, AC-9, AC-11.

---

## Acceptance Criteria

- [ ] Stored multi slug → `QueryHandler`; other providers → `QueryService` (AC-1, AC-2, AC-3).
- [ ] Authorization happens before the definition read; deny is indistinguishable from missing (AC-9).
- [ ] One repository read per request (AC-4); errors map per AC-11.
- [ ] Existing tenant route tests pass.
- [ ] `ruff check querysource/handlers/tenant.py tests/tenants/test_tenant_http_routes.py tests/tenants/test_tenant_multi_dispatch.py`

## Validation Commands

- `pytest tests/tenants/test_tenant_multi_dispatch.py -q`
- `pytest tests/tenants/test_tenant_http_routes.py -q`
- `pytest tests/tenants/test_tenant_policy_preflight.py -q`

---

## Agent Instructions

1. Read the spec. 2. Confirm TASK-760 is done (`QueryHandler.test_slug` exists). 3. Verify the Codebase Contract. 4. Implement from the blueprint and complete every `FILL IN`. 5. Run the validation commands and ruff. 6. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
