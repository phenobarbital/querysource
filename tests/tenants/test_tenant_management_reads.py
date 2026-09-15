"""Add selector parsing and tenant-aware management reads regression contracts."""

import pytest

from aiohttp import web
from querysource.handlers.tenant import resolve_request_store
from querysource.tenants import TenantRegistry, QueryStore


def _mock_registry() -> TenantRegistry:
    """Create a mock registry with a tenant store."""
    registry = TenantRegistry()
    # Add a tenant store
    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


def _mock_request(
    method: str = "GET",
    path: str = "/api/v1/management/queries",
    query: dict | None = None,
    match_info: dict | None = None,
    json_data: dict | None = None,
) -> web.Request:
    """Create a mock aiohttp request for testing."""
    from unittest.mock import MagicMock

    request = MagicMock(spec=web.Request)
    request.method = method
    request.rel_url = MagicMock()
    request.rel_url.query = query or {}
    request.match_info = match_info or {}
    request.json = MagicMock(return_value=json_data) if json_data else MagicMock()
    return request


@pytest.mark.asyncio
async def test_selector_missing_null_duplicates_conflicts() -> None:
    """Test that missing, null, and duplicate selectors are handled correctly."""
    registry = _mock_registry()

    # Test 1: No selector → configured default store
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "tenant1"
    assert store.contract == "tenant"

    # Test 2: Explicit tenant selector → exact store
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "tenant1"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "tenant1"
    assert store.contract == "tenant"

    # Test 3: Literal URL 'null' → treated as schema name
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "null"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    # 'null' is treated as a schema name, not JSON null
    assert store.schema == "null"
    assert store.contract == "legacy"

    # Test 4: Empty string selector → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": ""},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid query selector" in str(exc_info.value.reason)

    # Test 5: Non-string selector → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": 123},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid query selector" in str(exc_info.value.reason)

    # Test 6: Duplicate selectors → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "tenant1", "other": "value"},
        match_info={"tenant": "tenant2"},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Multiple conflicting tenant selectors" in str(exc_info.value.reason)

    # Test 7: Unknown tenant → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "unknown"},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid tenant selector" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_metadata_export_tenant_shape() -> None:
    """Test that metadata export preserves tenant shape."""
    from querysource.handlers.manager import QueryManager

    registry = _mock_registry()
    app = MagicMock()
    app['tenant_registry'] = registry
    app['qs_connection'] = MagicMock()

    # Create a mock request for :meta endpoint
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries/:meta",
        query={},
        match_info={"meta": ":meta"},
    )
    request.app = app

    manager = QueryManager(request)
    # The :meta endpoint should return the full QueryModel schema
    # (not tenant-specific, as it's metadata about the model itself)
    response = await manager.get()
    assert response.status == 200
    data = await response.json()
    assert "query_slug" in data
    assert "description" in data
    assert "program_slug" in data


@pytest.mark.asyncio
async def test_tenant_pagination_headers_and_empty() -> None:
    """Test that tenant pagination headers and empty results are preserved."""
    from querysource.handlers.manager import QueryManager

    registry = _mock_registry()
    app = MagicMock()
    app['tenant_registry'] = registry
    app['qs_connection'] = MagicMock()

    # Create a mock request for paginated list
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"page": "1", "page_size": "10"},
        match_info={},
    )
    request.app = app

    manager = QueryManager(request)
    response = await manager.get()
    assert response.status == 200
    data = await response.json()
    assert "data" in data
    assert "meta" in data
    assert data["meta"]["page"] == 1
    assert data["meta"]["page_size"] == 10
    assert "X-Total-Count" in response.headers
    assert "X-Page" in response.headers
    assert "X-Page-Size" in response.headers
    assert "X-Total-Pages" in response.headers


@pytest.mark.asyncio
async def test_legacy_overrides_explicit_public() -> None:
    """Test that explicit public remains literal and allowlisted even with legacy overrides."""
    registry = _mock_registry()

    # Test 1: Explicit public selector → literal public.queries
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "public"
    assert store.contract == "legacy"

    # Test 2: Public in allowlist → resolved store
    registry_with_public = _mock_registry()
    public_store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="public",
        table="queries",
        contract="legacy",
        columns=frozenset({"query_slug", "description", "program_slug"}),
    )
    registry_with_public._stores = (public_store,)
    registry_with_public._default_store = public_store

    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    store = resolve_request_store(request, registry_with_public)
    assert store.schema == "public"
    assert store.contract == "legacy"

    # Test 3: Unknown public → 400 error
    registry_unknown = _mock_registry()
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry_unknown)
    assert "Invalid tenant selector" in str(exc_info.value.reason)
