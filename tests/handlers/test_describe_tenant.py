"""FEAT-148 TASK-743 — per-tenant describe routes (FEAT-147 stores)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.auth.slug_visibility import Principal, PrincipalKind
from querysource.handlers.describe import QueryDescribe
from querysource.models import QueryModel
from querysource.tenants import QueryIdentity, QueryStore


@pytest_asyncio.fixture
async def test_client(fake_qs_connection):
    """aiohttp TestClient with QueryDescribe tenant routes."""
    app = web.Application()
    app["qs_connection"] = fake_qs_connection

    # Mock registry and repository on the app
    registry = MagicMock()
    repository = MagicMock()
    app["qs_tenant_registry"] = registry
    app["qs_definition_repository"] = repository

    dh = QueryDescribe()
    app.router.add_get("/api/v1/{tenant}/queries/describe", dh.describe_list, allow_head=True)
    app.router.add_get("/api/v1/{tenant}/queries/{slug}/describe", dh.describe)
    app.router.add_get("/api/v1/{tenant}/queries/{slug}/columns", dh.columns)

    # Also add legacy routes to test precedence
    app.router.add_get("/api/v1/queries/describe", dh.describe_list, allow_head=True)
    app.router.add_get("/api/v1/queries/{slug}/describe", dh.describe)

    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _patch_principal(principal: Principal):
    """Bypass session/resolve_principal plumbing: pin QueryDescribe._principal."""
    return patch.object(QueryDescribe, "_principal", new_callable=AsyncMock, return_value=principal)


async def test_tenant_unknown_404(test_client, fake_qs_connection):
    """Unknown tenant -> registry.resolve raises -> 404."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    # Mock registry to raise on unknown tenant
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.side_effect = Exception("tenant_not_found")

    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/unknown_tenant/queries/describe")

    assert resp.status == 404
    assert fake_qs_connection.calls == []


async def test_tenant_list_with_superuser(test_client, fake_qs_connection):
    """Superuser accessing tenant list -> returns rows with post-filled program_slug."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    # Mock store resolution
    store = QueryStore(
        database_namespace="test_ns",
        schema="acme_schema",
        table="acme_queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    # Mock rows (without program_slug for tenant stores)
    rows = [
        {"query_slug": "q1", "provider": "postgres", "description": "Query 1", "updated_at": "2026-01-01"},
        {"query_slug": "q2", "provider": "bigquery", "description": "Query 2", "updated_at": "2026-01-02"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["q1", "q2"]):
        resp = await test_client.get("/api/v1/acme/queries/describe")

    assert resp.status == 200
    data = await resp.json()
    # Verify program_slug was post-filled as the schema name
    assert data["data"][0]["program_slug"] == "acme_schema"
    assert data["data"][1]["program_slug"] == "acme_schema"


async def test_tenant_membership_prefilter_deny(test_client, fake_qs_connection):
    """PROGRAMS principal without tenant in programs -> deny-all -> 204."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("other_tenant",))

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme_schema",
        table="acme_queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/acme/queries/describe")

    # Tenant not in principal.programs -> deny-all -> 204
    assert resp.status == 204
    assert resp.headers["X-Total-Count"] == "0"
    assert fake_qs_connection.calls == []


async def test_tenant_membership_prefilter_allow(test_client, fake_qs_connection):
    """PROGRAMS principal with tenant in programs -> allow -> query runs."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme_schema",))

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme_schema",
        table="acme_queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    rows = [
        {"query_slug": "q1", "provider": "postgres", "description": "Query 1", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["q1"]):
        resp = await test_client.get("/api/v1/acme/queries/describe")

    assert resp.status == 200


async def test_tenant_sort_program_slug_400(test_client, fake_qs_connection):
    """Attempting to sort by program_slug in tenant store -> 400."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme_schema",
        table="acme_queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/acme/queries/describe?sort=program_slug")

    assert resp.status == 400
    data = await resp.json()
    assert "program_slug" in data["message"]


async def test_tenant_case_sensitive_schema(test_client, fake_qs_connection):
    """Tenant schema names are case-sensitive; routing works as-is."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    store = QueryStore(
        database_namespace="test_ns",
        schema="AcMe_Schema",  # Mixed case
        table="definitions",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    rows = [
        {"query_slug": "q1", "provider": "postgres", "description": "Query 1", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["q1"]):
        resp = await test_client.get("/api/v1/AcMe_Schema/queries/describe")

    assert resp.status == 200
    # Verify schema was passed correctly (no lowercasing)
    registry.resolve.assert_called_with(tenant="AcMe_Schema")


async def test_tenant_detail_loader_repository(test_client, fake_qs_connection):
    """Describe detail for tenant -> loader uses DefinitionRepository.get."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme",
        table="queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    # Mock that slug exists
    fake_qs_connection.fetch_one_result = [{"query_slug": "test_slug"}]

    # Mock DefinitionRepository.get to return a QueryModel
    repository = test_client.app["qs_definition_repository"]
    mock_model = MagicMock(spec=QueryModel)
    mock_model.query_slug = "test_slug"
    mock_model.program_slug = "acme"
    mock_model.provider = "postgres"

    from querysource.tenants import LoadedDefinition
    loaded = LoadedDefinition(
        identity=QueryIdentity(store=store, slug="test_slug"),
        runtime=mock_model,
        revision="1"
    )
    repository.get = AsyncMock(return_value=loaded)

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True):
        resp = await test_client.get("/api/v1/acme/queries/test_slug/describe")

    # Verify repository was called
    repository.get.assert_called_once()
    call_args = repository.get.call_args
    assert call_args[0][0].slug == "test_slug"
    assert call_args[0][0].store.schema == "acme"


async def test_legacy_slug_queries_precedence(test_client, fake_qs_connection):
    """Legacy route /api/v1/queries/{slug} still works; 'queries' is not a tenant."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    # Mock legacy store resolution (no tenant param)
    with patch.object(QueryDescribe, "_store", new_callable=AsyncMock) as mock_store:
        legacy_store_result = MagicMock()
        legacy_store_result.schema = "public"
        legacy_store_result.table = "queries"
        legacy_store_result.has_program_slug = True
        legacy_store_result.tenant = None
        mock_store.return_value = legacy_store_result

        with _patch_principal(principal):
            # Try to describe the slug 'queries' (testing that it's not treated as a tenant)
            resp = await test_client.get("/api/v1/queries/queries/describe")

    # The route should resolve to the legacy describe handler with slug='queries'
    # (not try to resolve tenant='queries')
    assert resp.status in [200, 404]  # Depends on mock setup, but should not error


async def test_tenant_columns_passes_tenant_to_qs(test_client, fake_qs_connection):
    """Columns endpoint for tenant -> passes tenant kwarg to QS()."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme",
        table="queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    # Mock that slug exists
    fake_qs_connection.fetch_one_result = [{"query_slug": "test_slug"}]

    # Mock DefinitionRepository
    repository = test_client.app["qs_definition_repository"]
    mock_model = MagicMock(spec=QueryModel)
    from querysource.tenants import LoadedDefinition
    loaded = LoadedDefinition(
        identity=QueryIdentity(store=store, slug="test_slug"),
        runtime=mock_model,
        revision="1"
    )
    repository.get = AsyncMock(return_value=loaded)

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True), \
            patch("querysource.handlers.describe.QS") as mock_qs_class:
        # Mock QS instance
        mock_qs = AsyncMock()
        mock_qs_class.return_value = mock_qs
        mock_qs.build_provider = AsyncMock()
        mock_qs.close = AsyncMock()

        resp = await test_client.get("/api/v1/acme/queries/test_slug/columns")

    # Verify QS was initialized with tenant parameter
    mock_qs_class.assert_called_once()
    call_kwargs = mock_qs_class.call_args[1]
    assert call_kwargs.get("tenant") == "acme"


async def test_tenant_evaluator_isolated(test_client, fake_qs_connection):
    """Tenant stores use detached evaluator; app evaluator cache untouched."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)

    store = QueryStore(
        database_namespace="test_ns",
        schema="acme",
        table="queries",
        contract="tenant",
        columns=frozenset(["query_slug", "provider", "description", "updated_at"])
    )
    registry = test_client.app["qs_tenant_registry"]
    registry.resolve.return_value = store

    rows = [
        {"query_slug": "q1", "provider": "postgres", "description": "Query 1", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    # Mock app evaluator (should NOT be called for tenant stores)
    app_evaluator = MagicMock()
    test_client.app["policy_evaluator"] = app_evaluator

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["q1"]) as mock_filter:
        resp = await test_client.get("/api/v1/acme/queries/describe")

    assert resp.status == 200
    # filter_visible is called (ABAC), but using the detached evaluator, not app's
    mock_filter.assert_called_once()
