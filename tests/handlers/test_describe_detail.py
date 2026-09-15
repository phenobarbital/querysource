"""FEAT-148 TASK-740 — GET /api/v1/queries/{slug}/describe."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.auth.slug_visibility import Principal, PrincipalKind
from querysource.handlers.describe import QueryDescribe
from querysource.models import QueryModel

# ``fake_qs_connection`` is provided by tests/handlers/conftest.py.


@pytest_asyncio.fixture
async def test_client(fake_qs_connection):
    """aiohttp TestClient with only ``QueryDescribe`` registered."""
    app = web.Application()
    app["qs_connection"] = fake_qs_connection
    dh = QueryDescribe()
    app.router.add_get("/api/v1/queries/{slug}/describe", dh.describe)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _patch_principal(principal: Principal):
    return patch.object(QueryDescribe, "_principal", new_callable=AsyncMock, return_value=principal)


def _model(**overrides) -> QueryModel:
    data = {
        "query_slug": "test_slug",
        "provider": "db",
        "description": "Test query",
        "program_slug": "acme",
    }
    data.update(overrides)
    return QueryModel(**data)


async def test_detail_401_without_principal(test_client, fake_qs_connection):
    """No session at all -> 401, no SQL executed."""
    with patch.object(QueryDescribe, "_get_user_session", new_callable=AsyncMock, return_value=None):
        resp = await test_client.get("/api/v1/queries/test_slug/describe")
    assert resp.status == 401
    assert fake_qs_connection.calls == []


async def test_detail_404_indistinguishable(test_client, fake_qs_connection):
    """Missing slug and ABAC-denied slug return byte-identical 404s (AC6)."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))

    # Case 1: slug does not exist (fetch_one -> None).
    fake_qs_connection.fetch_one_handler = lambda sql, *args: None
    with _patch_principal(principal):
        resp1 = await test_client.get("/api/v1/queries/missing_slug/describe")
    body1 = await resp1.read()

    # Case 2: slug exists, model loads, but ABAC denies both actions.
    fake_qs_connection.fetch_one_handler = lambda sql, *args: {"exists": 1}
    with _patch_principal(principal), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=_model()), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=False):
        resp2 = await test_client.get("/api/v1/queries/denied_slug/describe")
    body2 = await resp2.read()

    assert resp1.status == 404
    assert resp2.status == 404
    # Byte-identical: a caller cannot distinguish "does not exist" from
    # "exists but ABAC denies" (aiohttp's default HTTPNotFound body, the
    # same generic text either way — never a cause-specific payload).
    assert body1 == body2


async def test_detail_execute_implies_describe(test_client, fake_qs_connection):
    """_load_visible wires slug:execute as the fallback for the slug:describe check.

    can_access()'s own primary/fallback semantics are exhaustively unit-tested by
    TASK-737 (tests/auth/test_slug_visibility.py); this integration test verifies
    the handler actually passes ``fallback_action="slug:execute"`` and that a
    granted access (as a real caller with only ``slug:execute`` would receive)
    reaches a 200 response.
    """
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    fake_qs_connection.fetch_one_handler = lambda sql, *args: {"exists": 1}

    with _patch_principal(principal), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=_model()), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True) as mock_access, \
            patch("querysource.handlers.describe.describe_grants", new_callable=AsyncMock) as mock_grants:
        from querysource.queries.describe import DescribeGrants
        mock_grants.return_value = DescribeGrants(raw=False, admin=False)
        resp = await test_client.get("/api/v1/queries/test_slug/describe")

    assert resp.status == 200
    assert mock_access.call_args.args[3] == "slug:describe"
    assert mock_access.call_args.args[4] == "slug:execute"


async def test_detail_redaction_by_grants(test_client, fake_qs_connection):
    """Without the raw grant, query_raw is omitted and listed in 'redacted'."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    fake_qs_connection.fetch_one_handler = lambda sql, *args: {"exists": 1}
    model = _model(query_raw="SELECT * FROM t")

    with _patch_principal(principal), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=model), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True), \
            patch("querysource.handlers.describe.describe_grants", new_callable=AsyncMock) as mock_grants:
        from querysource.queries.describe import DescribeGrants
        mock_grants.return_value = DescribeGrants(raw=False, admin=False)
        resp = await test_client.get("/api/v1/queries/test_slug/describe")

    assert resp.status == 200
    data = await resp.json()
    assert "query_raw" not in data
    assert "query_raw" in data["redacted"]


async def test_detail_pbac_disabled_shows_raw(test_client, fake_qs_connection):
    """When the raw grant is True, query_raw is present and NOT in 'redacted'."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    fake_qs_connection.fetch_one_handler = lambda sql, *args: {"exists": 1}
    model = _model(query_raw="SELECT * FROM t")

    with _patch_principal(principal), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=model), \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True), \
            patch("querysource.handlers.describe.describe_grants", new_callable=AsyncMock) as mock_grants:
        from querysource.queries.describe import DescribeGrants
        mock_grants.return_value = DescribeGrants(raw=True, admin=False)
        resp = await test_client.get("/api/v1/queries/test_slug/describe")

    assert resp.status == 200
    data = await resp.json()
    assert data["query_raw"] == "SELECT * FROM t"
    assert "query_raw" not in data["redacted"]


async def test_detail_invalid_slug_404(test_client, fake_qs_connection):
    """A slug with characters outside SLUG_PATTERN never reaches SQL -> 404."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/queries/invalid@slug/describe")
    assert resp.status == 404
    assert fake_qs_connection.calls == []


async def test_detail_never_mutates_meta(test_client, fake_qs_connection):
    """legacy_store()/_load_visible never assign QueryModel.Meta; the loader is exercised."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    fake_qs_connection.fetch_one_handler = lambda sql, *args: {"exists": 1}
    original_schema = QueryModel.Meta.schema
    original_name = QueryModel.Meta.name

    with _patch_principal(principal), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=_model()) as mock_get, \
            patch("querysource.handlers.describe.can_access", new_callable=AsyncMock, return_value=True), \
            patch("querysource.handlers.describe.describe_grants", new_callable=AsyncMock) as mock_grants:
        from querysource.queries.describe import DescribeGrants
        mock_grants.return_value = DescribeGrants(raw=False, admin=False)
        resp = await test_client.get("/api/v1/queries/test_slug/describe")

    assert resp.status == 200
    assert mock_get.await_count == 1
    assert QueryModel.Meta.schema == original_schema
    assert QueryModel.Meta.name == original_name
