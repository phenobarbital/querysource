"""FEAT-148 TASK-740 — GET /api/v1/queries/describe."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.auth.slug_visibility import Principal, PrincipalKind
from querysource.handlers.describe import QueryDescribe

# ``fake_qs_connection`` (a fresh FakeQSConnection per test) is provided by
# tests/handlers/conftest.py — reused here rather than re-imported, since
# ``tests/`` is not a package (no ``tests/handlers/__init__.py``).


@pytest_asyncio.fixture
async def test_client(fake_qs_connection):
    """aiohttp TestClient with only ``QueryDescribe`` registered."""
    app = web.Application()
    app["qs_connection"] = fake_qs_connection
    dh = QueryDescribe()
    app.router.add_get("/api/v1/queries/describe", dh.describe_list, allow_head=True)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _patch_principal(principal: Principal):
    """Bypass session/resolve_principal plumbing: pin ``QueryDescribe._principal``."""
    return patch.object(QueryDescribe, "_principal", new_callable=AsyncMock, return_value=principal)


async def test_list_401_without_principal(test_client, fake_qs_connection):
    """No session at all -> 401, no SQL executed (AC4)."""
    with patch.object(QueryDescribe, "_get_user_session", new_callable=AsyncMock, return_value=None):
        resp = await test_client.get("/api/v1/queries/describe")
        assert resp.status == 401
    assert fake_qs_connection.calls == []


async def test_list_204_no_programs(test_client, fake_qs_connection):
    """NO_PROGRAMS principal -> deny-all predicate -> 204 with zero headers."""
    principal = Principal(kind=PrincipalKind.NO_PROGRAMS)
    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/queries/describe")
    assert resp.status == 204
    assert resp.headers["X-Total-Count"] == "0"
    assert resp.headers["X-Total-Pages"] == "0"
    assert fake_qs_connection.calls == []


async def test_list_program_prefilter_bound_args(test_client, fake_qs_connection):
    """PROGRAMS principal -> program predicate uses bound args, including 'default'."""
    principal = Principal(kind=PrincipalKind.PROGRAMS, programs=("acme",))
    rows = [
        {"query_slug": "s1", "provider": "db", "description": "d1",
         "program_slug": "acme", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["s1"]):
        resp = await test_client.get("/api/v1/queries/describe")

    assert resp.status == 200
    assert fake_qs_connection.args_log == [(["acme", "default"],)]
    sql = fake_qs_connection.calls[0][1]
    assert 'lower("program_slug") = ANY($1::text[])' in sql


async def test_list_superuser_no_predicate(test_client, fake_qs_connection):
    """SUPERUSER principal -> no program predicate (no bound args, no ANY() fragment)."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    rows = [
        {"query_slug": "s1", "provider": "db", "description": "d1",
         "program_slug": "other", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["s1"]):
        resp = await test_client.get("/api/v1/queries/describe")

    assert resp.status == 200
    assert fake_qs_connection.args_log == [()]
    sql = fake_qs_connection.calls[0][1]
    assert "ANY(" not in sql


async def test_list_abac_union_and_exact_totals(test_client, fake_qs_connection):
    """total reflects only rows filter_visible allowed, not the raw fetched count."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    rows = [
        {"query_slug": "visible1", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"},
        {"query_slug": "hidden1", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"},
        {"query_slug": "visible2", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock,
                  return_value=["visible1", "visible2"]):
        resp = await test_client.get("/api/v1/queries/describe")

    assert resp.status == 200
    data = await resp.json()
    assert data["meta"]["total"] == 2
    assert [r["query_slug"] for r in data["data"]] == ["visible1", "visible2"]


async def test_list_truncation_header(test_client, fake_qs_connection):
    """More rows than QS_DESCRIBE_MAX_SCAN -> capped + X-Truncated header."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    # 4 rows fetched, cap = 3: describe_list must report truncated.
    rows = [
        {"query_slug": f"s{i}", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"}
        for i in range(4)
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.QS_DESCRIBE_MAX_SCAN", 3), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock,
                  return_value=[f"s{i}" for i in range(4)]):
        resp = await test_client.get("/api/v1/queries/describe")

    assert resp.status == 200
    assert resp.headers["X-Truncated"] == "true"
    data = await resp.json()
    assert data["meta"]["total"] == 3


async def test_list_invalid_sort_400(test_client, fake_qs_connection):
    """Unknown sort field -> 400."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    with _patch_principal(principal):
        resp = await test_client.get("/api/v1/queries/describe?sort=not_a_real_field")
    assert resp.status == 400
    assert fake_qs_connection.calls == []


async def test_list_head_headers_only(test_client, fake_qs_connection):
    """HEAD request: headers present, empty body."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    rows = [
        {"query_slug": "s1", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["s1"]):
        resp = await test_client.head("/api/v1/queries/describe")

    assert resp.status == 200
    assert "X-Total-Count" in resp.headers
    body = await resp.read()
    assert body == b""


async def test_list_q_alias_search(test_client, fake_qs_connection):
    """'q' query param is aliased to 'search' when 'search' is absent."""
    principal = Principal(kind=PrincipalKind.SUPERUSER)
    rows = [
        {"query_slug": "s1", "provider": "db", "description": "d",
         "program_slug": "p", "updated_at": "2026-01-01"},
    ]
    fake_qs_connection.fetch_handler = lambda sql, *args: rows

    with _patch_principal(principal), \
            patch("querysource.handlers.describe.filter_visible", new_callable=AsyncMock, return_value=["s1"]):
        resp = await test_client.get("/api/v1/queries/describe?q=foo")

    assert resp.status == 200
    sql = fake_qs_connection.calls[0][1]
    assert "'%foo%'" in sql
