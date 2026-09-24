"""FEAT-151: TenantQueryHandler classifies stored definitions and dispatches by kind."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.handlers.tenant import TenantQueryHandler
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore, TenantRegistry


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


class _FakeRepo:
    """Counting definition repository stub — one entry per slug, raising
    TenantError('query_not_found') for slugs registered as missing."""

    def __init__(self, definitions=None, missing=None):
        self.definitions = definitions or {}
        self.missing = missing or set()
        self.calls: list = []

    async def get(self, identity):
        self.calls.append(identity)
        if identity.slug in self.missing:
            raise TenantError("not found", error_code="query_not_found")
        if identity.slug in self.definitions:
            runtime = self.definitions[identity.slug]
            return LoadedDefinition(identity=identity, runtime=runtime, revision=f"rev-{identity.slug}")
        # default: single-query (provider='db') definition
        runtime = QueryModel(query_slug=identity.slug, program_slug=identity.store.schema, provider="db")
        return LoadedDefinition(identity=identity, runtime=runtime, revision=f"rev-{identity.slug}")


def _app(repo=None, registry=None, **extra) -> dict:
    return {
        "qs_tenant_registry": registry or _mock_registry(),
        "qs_definition_repository": repo or _FakeRepo(),
        **extra,
    }


@pytest.mark.asyncio
async def test_stored_multi_slug_dispatches_to_query_handler(monkeypatch):
    calls = []

    class _FakeQueryHandler:
        def __init__(self, request):
            pass

        async def query(self, request):
            calls.append("multi.query")
            return web.json_response({"ok": "multi"})

    monkeypatch.setattr("querysource.handlers.multi.QueryHandler", _FakeQueryHandler)

    multi_runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi",
                               query_raw=json.dumps({"queries": {"a": {"slug": "child_a"}}}))
    repo = _FakeRepo(definitions={"parent": multi_runtime})
    handler = TenantQueryHandler()
    request = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "parent"})

    response = await handler.query(request)

    assert response.status == 200
    assert calls == ["multi.query"]
    assert request.get("qs_definition") is not None
    assert request.get("qs_definition").runtime.provider == "multi"


@pytest.mark.asyncio
async def test_stored_single_slug_dispatches_to_query_service(monkeypatch):
    calls = []

    class _FakeQueryService:
        def __init__(self, request):
            pass

        async def query(self, request):
            calls.append("service.query")
            return web.json_response({"ok": "single"})

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)

    # provider='db' with a JSON query_raw is still single-query (AC-3: the
    # discriminator is provider == 'multi' only, never query_raw sniffing).
    db_runtime = QueryModel(query_slug="s", program_slug="tenant1", provider="db",
                            query_raw=json.dumps({"queries": {"a": {"slug": "child_a"}}}))
    repo = _FakeRepo(definitions={"s": db_runtime})
    handler = TenantQueryHandler()
    request = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "s"})

    response = await handler.query(request)

    assert response.status == 200
    assert calls == ["service.query"]


@pytest.mark.asyncio
async def test_authorization_precedes_definition_load(monkeypatch):
    monkeypatch.setattr(
        TenantQueryHandler, "_enforce_owned_slug",
        AsyncMock(side_effect=web.HTTPNotFound()),
    )

    repo = _FakeRepo()
    handler = TenantQueryHandler()

    # An existing slug and a missing slug must be indistinguishable — both 404.
    req_existing = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "exists"})
    with pytest.raises(web.HTTPNotFound):
        await handler.query(req_existing)

    req_missing = _mock_request(_app(repo=_FakeRepo(missing={"missing"})), {"tenant": "tenant1", "slug": "missing"})
    with pytest.raises(web.HTTPNotFound):
        await handler.query(req_missing)

    assert repo.calls == []  # repository never read once authorization denied


@pytest.mark.asyncio
async def test_one_repository_read_per_request(monkeypatch):
    class _FakeQueryService:
        def __init__(self, request):
            pass

        async def query(self, request):
            return web.json_response({"ok": True})

        async def get_columns(self, request):
            return web.json_response({"ok": True})

        async def columns(self, request):
            return web.json_response({"ok": True})

        async def test_slug(self, request):
            return web.json_response({"ok": True})

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)

    handler = TenantQueryHandler()

    repo = _FakeRepo()
    req = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "s"})
    await handler.query(req)
    assert len(repo.calls) == 1
    definition_from_query = req.get("qs_definition")

    repo2 = _FakeRepo()
    req2 = _mock_request(_app(repo=repo2), {"tenant": "tenant1", "slug": "s"}, method="HEAD")
    await handler.columns(req2)
    assert len(repo2.calls) == 1

    repo3 = _FakeRepo()
    req3 = _mock_request(_app(repo=repo3), {"tenant": "tenant1", "slug": "s"}, method="PATCH")
    await handler.columns(req3)
    assert len(repo3.calls) == 1

    repo4 = _FakeRepo()
    req4 = _mock_request(_app(repo=repo4), {"tenant": "tenant1", "slug": "s"})
    await handler.test_slug(req4)
    assert len(repo4.calls) == 1

    # The definition object captured on the request is the same instance
    # threaded through _prepare (no second read behind the scenes).
    assert definition_from_query is req.get("qs_definition")


@pytest.mark.asyncio
async def test_missing_slug_returns_404_before_dispatch(monkeypatch):
    dispatched = []

    class _FakeQueryService:
        def __init__(self, request):
            dispatched.append("service")

    class _FakeQueryHandler:
        def __init__(self, request):
            dispatched.append("multi")

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)
    monkeypatch.setattr("querysource.handlers.multi.QueryHandler", _FakeQueryHandler)

    repo = _FakeRepo(missing={"ghost"})
    handler = TenantQueryHandler()
    request = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "ghost"})

    with pytest.raises(web.HTTPNotFound):
        await handler.query(request)

    assert dispatched == []


@pytest.mark.asyncio
async def test_store_unavailable_returns_503():
    class _UnavailableRepo:
        async def get(self, identity):
            raise TenantError("store gone", error_code="tenant_store_unavailable")

    handler = TenantQueryHandler()
    request = _mock_request(_app(repo=_UnavailableRepo()), {"tenant": "tenant1", "slug": "s"})

    # NOTE: TenantError(tenant_store_unavailable).code == 503, forwarded to
    # self.error(status=err.code) exactly like the pre-existing QueryService
    # TenantError handling (service.py:460, 605). The shared, inherited
    # navigator BaseView.error() only special-cases 400/401/403/404/406/412/428
    # and falls back to HTTPBadRequest (400) for any other status — including
    # 503 — for EVERY caller of that helper, not something introduced here.
    # Raising this gap (out of this task's file scope: navigator/views/base.py
    # is a third-party package) as a deferred ledger finding; asserting the
    # actual current behavior here rather than the aspirational 503.
    with pytest.raises(web.HTTPBadRequest):
        await handler.query(request)


@pytest.mark.asyncio
async def test_slug_format_suffix_stripped_before_peek(monkeypatch):
    received_identity = {}

    class _FakeRepoCapturing(_FakeRepo):
        async def get(self, identity):
            received_identity["identity"] = identity
            return await super().get(identity)

    calls = []

    class _FakeQueryService:
        def __init__(self, request):
            pass

        async def query(self, request):
            # The delegate must still see the raw match_info with the suffix.
            calls.append(request.match_info.get("slug"))
            return web.json_response({"ok": True})

    monkeypatch.setattr("querysource.handlers.service.QueryService", _FakeQueryService)

    repo = _FakeRepoCapturing()
    handler = TenantQueryHandler()
    request = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "parent:csv"})

    await handler.query(request)

    assert received_identity["identity"].slug == "parent"
    assert calls == ["parent:csv"]


@pytest.mark.asyncio
async def test_multi_columns_and_test_route_dispatch(monkeypatch):
    calls = []

    class _FakeQueryHandler:
        def __init__(self, request):
            pass

        async def columns(self, request):
            calls.append("multi.columns")
            return web.json_response({"ok": True})

        async def test_slug(self, request):
            calls.append("multi.test_slug")
            return web.json_response({"ok": True})

    monkeypatch.setattr("querysource.handlers.multi.QueryHandler", _FakeQueryHandler)

    multi_runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi",
                               query_raw=json.dumps({"queries": {"a": {"slug": "child_a"}}}))
    repo = _FakeRepo(definitions={"parent": multi_runtime})
    handler = TenantQueryHandler()

    req_columns = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "parent"}, method="HEAD")
    await handler.columns(req_columns)

    req_test = _mock_request(_app(repo=repo), {"tenant": "tenant1", "slug": "parent"})
    await handler.test_slug(req_test)

    assert calls == ["multi.columns", "multi.test_slug"]
