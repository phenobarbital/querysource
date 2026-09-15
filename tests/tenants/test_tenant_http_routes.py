"""Expose unified tenant execution and inspection routes regression contracts."""
import importlib

import pytest
from aiohttp import web

from querysource.handlers.tenant import TenantQueryHandler
from querysource.tenants import QueryStore, TenantRegistry


def _mock_registry() -> TenantRegistry:
    registry = TenantRegistry()
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


def _mock_request(app: dict, match_info: dict, method: str = "GET", query: dict | None = None) -> web.Request:
    from unittest.mock import MagicMock

    request = MagicMock(spec=web.Request)
    request.app = app
    request.match_info = match_info
    request.method = method
    request.query = query or {}
    storage = {}
    request.get = lambda key, default=None: storage.get(key, default)

    def _setitem(key, value):
        storage[key] = value

    request.__setitem__ = MagicMock(side_effect=_setitem)
    return request


@pytest.mark.asyncio
async def test_route_method_matrix_and_slash_aliases() -> None:
    """route method matrix and slash aliases."""
    from querysource import services

    importlib.reload(services)
    qs = services.QuerySource(lazy=True)
    app = web.Application()
    qs.setup(app)

    route_specs = set()
    for resource in app.router.resources():
        info = resource.get_info()
        path = info.get("path") or info.get("formatter")
        if not path or "{tenant}" not in path:
            continue
        for route in resource:
            route_specs.add((route.method, path))

    # Collection: GET/POST, with and without a trailing slash (AC-2
    # "register collection slash aliases directly without POST redirects").
    assert ("GET", "/api/v1/{tenant}/queries/") in route_specs
    assert ("POST", "/api/v1/{tenant}/queries/") in route_specs
    assert ("GET", "/api/v1/{tenant}/queries") in route_specs
    assert ("POST", "/api/v1/{tenant}/queries") in route_specs
    # Stored slug: GET/POST, HEAD/PATCH for columns.
    assert ("GET", "/api/v1/{tenant}/queries/{slug}") in route_specs
    assert ("POST", "/api/v1/{tenant}/queries/{slug}") in route_specs
    assert ("HEAD", "/api/v1/{tenant}/queries/{slug}") in route_specs
    assert ("PATCH", "/api/v1/{tenant}/queries/{slug}") in route_specs
    # test_slug: GET/POST.
    assert ("GET", "/api/v1/{tenant}/queries/{slug}/test") in route_specs
    assert ("POST", "/api/v1/{tenant}/queries/{slug}/test") in route_specs


@pytest.mark.asyncio
async def test_single_multi_inline_dispatch(monkeypatch) -> None:
    """single multi inline dispatch."""
    registry = _mock_registry()
    app = {"qs_tenant_registry": registry, "qs_definition_repository": object()}

    dispatched = {}

    class _FakeQueryService:
        def __init__(self, request):
            pass

        async def query(self, request):
            dispatched["target"] = "single"
            dispatched["tenant"] = request.get("qs_tenant")
            return web.json_response({"ok": "single"})

    class _FakeQueryHandler:
        def __init__(self, request):
            pass

        async def query(self, request):
            dispatched["target"] = "multi"
            dispatched["tenant"] = request.get("qs_tenant")
            return web.json_response({"ok": "multi"})

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)
    monkeypatch.setattr("querysource.handlers.multi.QueryHandler", _FakeQueryHandler)

    handler = TenantQueryHandler()

    # Stored slug present -> single-query dispatch (QueryService).
    request = _mock_request(app, {"tenant": "tenant1", "slug": "my_slug"})
    response = await handler.query(request)
    assert response.status == 200
    assert dispatched["target"] == "single"
    assert dispatched["tenant"] == "tenant1"
    # AC-4: tenant selector never reaches query conditions — it only ever
    # flows through request['qs_tenant'], never merged into a slug/body.
    assert request.match_info.get("slug") == "my_slug"

    # No slug (collection POST) -> inline/multi dispatch (QueryHandler).
    dispatched.clear()
    request2 = _mock_request(app, {"tenant": "tenant1"})
    response2 = await handler.query(request2)
    assert response2.status == 200
    assert dispatched["target"] == "multi"
    assert dispatched["tenant"] == "tenant1"


@pytest.mark.asyncio
async def test_legacy_and_management_precedence() -> None:
    """legacy and management precedence."""
    from querysource import services

    importlib.reload(services)
    qs = services.QuerySource(lazy=True)
    app = web.Application()
    qs.setup(app)

    # A literal "management"/"qs"/"datasources" first path segment must
    # resolve against the pre-existing, more specific route, not get
    # captured as {tenant} — verified by asserting those literal routes
    # were registered as their own resources (not swallowed), and that
    # they were registered before the tenant routes (AC-4 "legacy routes
    # take precedence" — aiohttp's UrlDispatcher resolves in registration
    # order).
    paths = []
    for resource in app.router.resources():
        info = resource.get_info()
        paths.append(info.get("path") or info.get("formatter"))

    management_idx = paths.index("/api/v1/management/queries/{slug}")
    tenant_idx = next(i for i, p in enumerate(paths) if p == "/api/v1/{tenant}/queries")
    assert management_idx < tenant_idx

    qs_idx = paths.index("/api/v1/qs/audit_log")
    assert qs_idx < tenant_idx

    datasources_idx = paths.index("/api/v1/datasources")
    assert datasources_idx < tenant_idx


@pytest.mark.asyncio
async def test_columns_test_and_output_suffixes(monkeypatch) -> None:
    """columns test and output suffixes."""
    registry = _mock_registry()
    app = {"qs_tenant_registry": registry, "qs_definition_repository": object()}

    calls = []

    class _FakeQueryService:
        def __init__(self, request):
            pass

        async def get_columns(self, request):
            calls.append("get_columns")
            return web.json_response({"ok": "head"})

        async def columns(self, request):
            calls.append("columns")
            return web.json_response({"ok": "patch"})

        async def test_slug(self, request):
            calls.append("test_slug")
            return web.json_response({"ok": "test"})

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)

    handler = TenantQueryHandler()

    # HEAD -> get_columns (existing single-query inspection semantics).
    request = _mock_request(app, {"tenant": "tenant1", "slug": "s"}, method="HEAD")
    response = await handler.columns(request)
    assert response.status == 200
    assert calls == ["get_columns"]

    # PATCH -> columns.
    calls.clear()
    request2 = _mock_request(app, {"tenant": "tenant1", "slug": "s"}, method="PATCH")
    response2 = await handler.columns(request2)
    assert response2.status == 200
    assert calls == ["columns"]

    # GET .../test -> test_slug (dry run, no data query execution).
    calls.clear()
    request3 = _mock_request(app, {"tenant": "tenant1", "slug": "s"})
    response3 = await handler.test_slug(request3)
    assert response3.status == 200
    assert calls == ["test_slug"]


@pytest.mark.asyncio
async def test_unknown_tenant_selector_rejected() -> None:
    """An unregistered tenant selector is rejected before any delegation."""
    registry = _mock_registry()
    app = {"qs_tenant_registry": registry, "qs_definition_repository": object()}
    handler = TenantQueryHandler()

    # TenantError(error_code="tenant_not_available").code == 404 (TASK-716
    # OWNERSHIP_STATUS table) -> _resolve_or_raise maps it to HTTPNotFound.
    request = _mock_request(app, {"tenant": "no_such_tenant", "slug": "s"})
    with pytest.raises(web.HTTPNotFound):
        await handler.query(request)


@pytest.mark.asyncio
async def test_tenant_feature_not_configured_returns_404() -> None:
    """A deployment without qs_tenant_registry/qs_definition_repository
    published (tenant feature not wired up) returns 404 for tenant
    routes rather than crashing with a KeyError.
    """
    handler = TenantQueryHandler()
    request = _mock_request(app={}, match_info={"tenant": "tenant1", "slug": "s"})
    with pytest.raises(web.HTTPNotFound):
        await handler.query(request)
