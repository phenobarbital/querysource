"""FEAT-182 TASK-945: driver:query on the direct driver route; raw_query:execute on /api/v2/test/queries.

Real ``setup_pbac`` + ``PolicyEvaluator`` loaded from YAML in ``tmp_path``;
``enforce_request_pbac`` is never mocked. Only the session source
(``querysource.handlers.abstract.get_session``) and ``get_source`` (the
provider resolution that needs a database) are patched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from navigator_auth.conf import AUTH_SESSION_OBJECT

from querysource.auth import setup_pbac
from querysource.handlers.service import QueryService

_PATCH_SESSION = "querysource.handlers.abstract.get_session"

_POLICIES = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: admin_all
    effect: allow
    resources: ["slug:*", "driver:*", "raw_query:*", "datasource:*"]
    actions: ["slug:execute", "driver:query", "driver:use", "raw_query:execute", "datasource:use"]
    subjects:
      groups: ["admin"]
    priority: 100
  - name: http_only
    effect: allow
    resources: ["driver:http"]
    actions: ["driver:query"]
    subjects:
      groups: ["widgets"]
    priority: 20
  - name: slug_runners
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:execute"]
    subjects:
      groups: ["runners"]
    priority: 20
  - name: sql_writers
    effect: allow
    resources: ["raw_query:raw_query"]
    actions: ["raw_query:execute"]
    subjects:
      groups: ["sqlers"]
    priority: 20
  - name: sql_pg_driver
    effect: allow
    resources: ["driver:pg"]
    actions: ["driver:use"]
    subjects:
      groups: ["sqlers", "driveronly"]
    priority: 20
"""

_USERS = {
    "admin": ["admin"],
    "widget": ["widgets"],
    "runner": ["runners"],
    "sqler": ["sqlers"],
    "driveronly": ["driveronly"],
    "nobody": ["others"],
}


async def _fake_get_session(request, *args, **kwargs):
    who = request.headers.get("X-Test-User")
    if not who:
        return None
    info = {"username": who, "user_id": who, "groups": _USERS[who], "roles": []}
    return {AUTH_SESSION_OBJECT: info}


def _h(who: str) -> dict:
    return {"X-Test-User": who}


def _routes(app: web.Application) -> None:
    qs = QueryService()
    app.router.add_get("/api/v2/queries/{driver}/{source}/{attribute}", qs.query)
    app.router.add_get("/api/v2/services/queries/{slug}", qs.query)
    app.router.add_post("/api/v2/test/queries", qs.run_query)


@pytest_asyncio.fixture
async def env(tmp_path):
    (tmp_path / "defaults.yaml").write_text(_POLICIES)
    app = web.Application()
    setup_pbac(app, policy_dir=str(tmp_path))
    assert app.get("security") is not None
    _routes(app)
    get_source = AsyncMock(return_value=None)  # gate passed → "no provider" 400
    with patch(_PATCH_SESSION, side_effect=_fake_get_session), \
            patch.object(QueryService, "get_source", get_source):
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            yield client, get_source
        finally:
            await client.close()


_DRIVER_HTTP = "/api/v2/queries/http/rssapp/get_bundle"
_DRIVER_PG = "/api/v2/queries/pg/some_source/some_attr"


@pytest.mark.asyncio
async def test_driver_route_evaluates_driver_query(env):
    """driver:query on driver:http allowed → reaches get_source; pg denied → 404."""
    client, get_source = env
    resp = await client.get(_DRIVER_HTTP, headers=_h("widget"))
    assert resp.status != 404
    get_source.assert_awaited_once()
    get_source.reset_mock()

    resp = await client.get(_DRIVER_PG, headers=_h("widget"))
    assert resp.status == 404
    get_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_driver_route_denies_without_grant_or_session(env):
    """No driver:query grant (even slug:execute) and no session → 404, get_source untouched."""
    client, get_source = env
    for who in ("nobody", "runner"):
        resp = await client.get(_DRIVER_HTTP, headers=_h(who))
        assert resp.status == 404, who
    assert (await client.get(_DRIVER_HTTP)).status == 404
    get_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_driver_route_admin_not_blocked(env):
    """Regression: an admin is no longer 404'd by the empty slug name."""
    client, get_source = env
    for path in (_DRIVER_HTTP, _DRIVER_PG):
        resp = await client.get(path, headers=_h("admin"))
        assert resp.status != 404, path
    assert get_source.await_count == 2


@pytest.mark.asyncio
async def test_slug_route_unchanged(env):
    """With a slug the check is still slug:execute (driver:query is irrelevant)."""
    client, get_source = env
    resp = await client.get("/api/v2/services/queries/some_slug", headers=_h("runner"))
    assert resp.status != 404
    get_source.assert_awaited_once()
    get_source.reset_mock()
    # a driver:query-only user cannot execute a slug
    resp = await client.get("/api/v2/services/queries/some_slug", headers=_h("widget"))
    assert resp.status == 404
    get_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_query_requires_raw_query(env):
    """POST /api/v2/test/queries without raw_query:execute → 404; get_source never awaited."""
    client, get_source = env
    body = {"query": "SELECT 1", "driver": "pg"}
    for who in ("nobody", "runner", "widget"):
        resp = await client.post("/api/v2/test/queries", json=body, headers=_h(who))
        assert resp.status == 404, who
    assert (await client.post("/api/v2/test/queries", json=body)).status == 404
    get_source.assert_not_awaited()

    # driver:use without raw_query:execute is not enough either
    resp = await client.post("/api/v2/test/queries", json=body, headers=_h("driveronly"))
    assert resp.status == 404
    get_source.assert_not_awaited()

    # raw_query:execute alone is not enough: driver:use on the requested driver too
    resp = await client.post(
        "/api/v2/test/queries", json={"query": "SELECT 1", "driver": "mysql"}, headers=_h("sqler")
    )
    assert resp.status == 404
    get_source.assert_not_awaited()

    resp = await client.post("/api/v2/test/queries", json=body, headers=_h("sqler"))
    assert resp.status != 404
    get_source.assert_awaited_once()
    get_source.reset_mock()

    resp = await client.post("/api/v2/test/queries", json=body, headers=_h("admin"))
    assert resp.status != 404
    get_source.assert_awaited_once()


@pytest.mark.asyncio
async def test_driver_and_test_routes_pbac_off_unchanged():
    """No app['security'] → both routes reach get_source without any session."""
    app = web.Application()
    _routes(app)
    get_source = AsyncMock(return_value=None)
    with patch.object(QueryService, "get_source", get_source):
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            assert (await client.get(_DRIVER_PG)).status != 404
            assert (await client.post(
                "/api/v2/test/queries", json={"query": "SELECT 1", "driver": "pg"}
            )).status != 404
            assert get_source.await_count == 2
        finally:
            await client.close()
