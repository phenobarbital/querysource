"""Tenant selector parsing and store resolution for management reads.

One selector parser prevents GET and write handlers from disagreeing about ownership.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiohttp import web

from querysource.handlers.abstract import AbstractHandler
from querysource.repositories import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry


def resolve_request_store(
    request: web.Request,
    registry: TenantRegistry,
    payload: Mapping[str, Any] | None = None,
) -> QueryStore:
    """Validate all present routing selectors and reject disagreement with 400.

    Implements spec §2 owner and legacy-store resolution matrix:
    - No tenant / Python or JSON None → configured default store
    - Explicit nonempty tenant → exact registered schema
    - Explicit 'public' → literal public.queries, independently from configured default
    - Child definition without tenant → inherit parent's resolved store
    - Child definition with tenant: null → explicit configured legacy store
    - Unknown/disallowed owner or missing slug → fail with 400

    Rejects:
    - Empty string, non-string selectors
    - Duplicate keys in URL/query/body
    - Conflicting selectors between URL, query, and body
    - Literal URL string 'null' is a schema name, not JSON null

    Args:
        request: HTTP request with possible tenant selector in URL or query string
        registry: Immutable tenant registry from QuerySource initialization
        payload: Optional JSON body payload (for write handlers)

    Returns:
        The resolved QueryStore for this request.

    Raises:
        web.HTTPBadRequest: On invalid, conflicting, or unknown tenant selectors.
    """
    # Extract selectors from URL, query string, and body
    url_tenant = None
    query_tenant = None
    body_tenant = None

    # URL path parameter (e.g., /api/v1/management/queries?tenant=client_a)
    if "tenant" in request.match_info:
        url_tenant = request.match_info["tenant"]

    # Query string parameter (e.g., ?tenant=client_a). request.query is a
    # MultiDictProxy — request.query["tenant"] silently returns only the
    # FIRST value when the key repeats (?tenant=a&tenant=b), which would
    # let a conflicting duplicate slip through unnoticed. Spec: "Multiple
    # query-string values are accepted only when equal" — so every value
    # must be read via .getall() and compared, not just the first.
    if "tenant" in request.query:
        query_tenant_values = request.query.getall("tenant")
        if len(set(query_tenant_values)) > 1:
            raise web.HTTPBadRequest(
                reason=(
                    "Multiple conflicting tenant selectors in query string: "
                    f"{query_tenant_values}"
                )
            )
        query_tenant = query_tenant_values[0]

    # JSON body parameter (for write handlers)
    if payload is not None and isinstance(payload, Mapping) and "tenant" in payload:
        body_tenant = payload["tenant"]

    # Validate and resolve selectors
    # Count non-None selectors
    selectors = {
        "url": url_tenant,
        "query": query_tenant,
        "body": body_tenant,
    }
    present_selectors = {k: v for k, v in selectors.items() if v is not None}

    # Reject empty string selectors
    for key, value in present_selectors.items():
        if not isinstance(value, str) or value == "":
            raise web.HTTPBadRequest(
                reason=f"Invalid {key} selector: must be a non-empty string or omitted"
            )

    # Reject duplicate keys
    if len(present_selectors) > 1:
        raise web.HTTPBadRequest(
            reason=f"Multiple conflicting tenant selectors: {list(present_selectors.keys())}"
        )

    # Resolve the single selector
    if not present_selectors:
        # No selector → configured default store
        return registry.resolve(tenant=None)

    # Single selector present. Literal URL string 'null' is treated as an
    # ordinary schema name here, never converted to Python/JSON None — the
    # lookup below already does exactly that by never special-casing the
    # string "null"; it is rejected with the same 400 as any other
    # unregistered name if no store is actually named "null".
    selector = next(iter(present_selectors.values()))

    # Explicit nonempty tenant → exact registered schema
    try:
        return registry.resolve(tenant=selector)
    except Exception as err:
        # registry.resolve raises TenantError for unknown/disallowed owners.
        # web.HTTPBadRequest (an HTTPException) has no exception= kwarg;
        # use `raise ... from err` for proper exception chaining instead.
        raise web.HTTPBadRequest(
            reason=f"Invalid tenant selector: {selector}",
        ) from err


def _resolve_or_raise(registry: TenantRegistry, tenant: str | None) -> QueryStore:
    """Resolve a URL tenant selector to a QueryStore, or raise a clean 400/404.

    Shared by every TenantQueryHandler method so GET/POST/HEAD/PATCH all
    agree on ownership resolution (mirrors resolve_request_store's role
    for the management handler — AC-4).
    """
    try:
        return registry.resolve(tenant)
    except TenantError as err:
        if err.code == 404:
            raise web.HTTPNotFound(reason=f"Tenant not found: {tenant}") from err
        raise web.HTTPBadRequest(reason=f"Invalid tenant selector: {tenant}") from err


class TenantQueryHandler(AbstractHandler):
    """One tenant handler selects existing single/multi execution behavior.

    Reuses querysource.handlers.service.QueryService/querysource.handlers.
    multi.QueryHandler's own request-independent execution/PBAC/format
    logic for query/columns/test_slug (AC-1 "do not instantiate
    QueryManager just to call get" — QueryManager is never touched here;
    delegation targets are QueryService/QueryHandler, the single/multi
    execution handlers). list() goes directly through
    DefinitionRepository, the same repository-first pattern
    QueryManager._paginate_list uses (TASK-723), without needing
    QueryManager itself.
    """

    def _registry(self, request: web.Request) -> TenantRegistry:
        registry = request.app.get("qs_tenant_registry")
        if registry is None:
            raise web.HTTPNotFound(reason="Tenant feature is not configured")
        return registry

    def _repository(self, request: web.Request) -> DefinitionRepository:
        repo = request.app.get("qs_definition_repository")
        if repo is None:
            raise web.HTTPNotFound(reason="Tenant feature is not configured")
        return repo

    async def list(self, request: web.Request) -> web.StreamResponse:
        """List selected tenant definitions with management pagination conventions."""
        tenant = request.match_info.get("tenant")
        registry = self._registry(request)
        store = _resolve_or_raise(registry, tenant)
        repo = self._repository(request)

        qp = dict(request.query)
        try:
            page = max(int(qp.pop("page", 1)), 1)
            page_size = min(max(int(qp.pop("page_size", 50)), 1), 200)
        except (TypeError, ValueError) as err:
            return self.error(
                response={"message": f"Invalid pagination params: {err}"},
                status=400,
            )
        sort_field = qp.pop("sort", None) or "updated_at"
        sort_direction = qp.pop("sort_direction", "desc")
        fields = qp.pop("fields", None)
        if fields is not None:
            fields = [f.strip() for f in fields.split(",") if f.strip()]

        try:
            page_result = await repo.list(
                store,
                {
                    "page": page,
                    "page_size": page_size,
                    "sort_field": sort_field,
                    "sort_direction": sort_direction,
                    "fields": fields,
                    "filters": qp,
                },
            )
        except TenantError as err:
            return self.error(
                response={"message": str(err)},
                status=err.code,
            )

        total_pages = -(-page_result.total // page_size) if page_result.total else 0
        headers = {
            "X-Total-Count": str(page_result.total),
            "X-Page": str(page),
            "X-Page-Size": str(page_size),
            "X-Total-Pages": str(total_pages),
        }
        if page_result.total == 0:
            return self.no_content(headers=headers)
        response = {
            "data": [dict(row) for row in page_result.rows],
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": page_result.total,
                "total_pages": total_pages,
            },
        }
        return self.json_response(response, headers=headers)

    async def query(self, request: web.Request) -> web.StreamResponse:
        """Execute stored single/multi or inline multi under URL owner."""
        tenant = request.match_info.get("tenant")
        registry = self._registry(request)
        _resolve_or_raise(registry, tenant)  # validate before delegating

        # Tenant selector never reaches query conditions (AC-4) — it is
        # threaded to QS/MultiQS construction only via request['qs_tenant']
        # (read by QueryService.query/QueryHandler.query, both MODIFY
        # targets of this task), never merged into params/conditions.
        request["qs_tenant"] = tenant

        slug = request.match_info.get("slug")
        if slug:
            from .service import QueryService

            handler = QueryService(request)
            return await handler.query(request)
        from .multi import QueryHandler

        handler = QueryHandler(request)
        return await handler.query(request)

    async def columns(self, request: web.Request) -> web.StreamResponse:
        """Inspect selected definition using existing single/multi semantics."""
        tenant = request.match_info.get("tenant")
        registry = self._registry(request)
        _resolve_or_raise(registry, tenant)

        request["qs_tenant"] = tenant

        from .service import QueryService

        handler = QueryService(request)
        if request.method == "HEAD":
            return await handler.get_columns(request)
        return await handler.columns(request)

    async def test_slug(self, request: web.Request) -> web.StreamResponse:
        """Dry-run selected saved definition without executing its data query."""
        tenant = request.match_info.get("tenant")
        registry = self._registry(request)
        _resolve_or_raise(registry, tenant)

        request["qs_tenant"] = tenant

        from .service import QueryService

        handler = QueryService(request)
        return await handler.test_slug(request)
