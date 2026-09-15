"""Tenant selector parsing and store resolution for management reads.

One selector parser prevents GET and write handlers from disagreeing about ownership.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiohttp import web

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

    # Query string parameter (e.g., ?tenant=client_a)
    if "tenant" in request.query:
        query_tenant = request.query["tenant"]

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
