"""FEAT-182 TASK-944: move + store-catalogue PBAC gates.

Real ``setup_pbac`` + ``PolicyEvaluator`` loaded from YAML in ``tmp_path``;
``enforce_request_pbac`` is never mocked. A middleware installs the session
the way navigator-session does (``request['user_session']``).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from navigator_auth.conf import AUTH_SESSION_OBJECT

from querysource.auth import setup_pbac
from querysource.handlers.query_moves import QueryMoveView
from querysource.handlers.query_stores import QueryStoreCatalog

_POLICIES = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: editors_manage
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:view", "slug:manage"]
    subjects:
      groups: ["editors"]
    priority: 20
  - name: viewers_view
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:view"]
    subjects:
      groups: ["viewers"]
    priority: 20
"""

_SESSIONS = {
    "editor": {"username": "eve", "user_id": "eve", "groups": ["editors"], "roles": []},
    "viewer": {"username": "vic", "user_id": "vic", "groups": ["viewers"], "roles": []},
    "nobody": {"username": "bob", "user_id": "bob", "groups": ["others"], "roles": []},
}


@web.middleware
async def _session_mw(request: web.Request, handler):
    who = request.headers.get("X-Test-User")
    if who:
        request["user_session"] = {AUTH_SESSION_OBJECT: _SESSIONS[who]}
    return await handler(request)


def _h(who: str) -> dict:
    return {"X-Test-User": who}


def _routes(app: web.Application) -> None:
    app.router.add_route("POST", r"/api/v1/management/queries/{slug}/move", QueryMoveView)
    app.router.add_get("/api/v1/management/query-stores", QueryStoreCatalog().list)


@pytest_asyncio.fixture
async def pbac_client(tmp_path):
    (tmp_path / "defaults.yaml").write_text(_POLICIES)
    app = web.Application(middlewares=[_session_mw])
    setup_pbac(app, policy_dir=str(tmp_path))
    assert app.get("security") is not None
    _routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_move_requires_manage(pbac_client):
    """viewer / nobody / sessionless -> 404 before body validation; editor passes the gate."""
    url = "/api/v1/management/queries/fixture_slug/move"
    for who in ("viewer", "nobody"):
        resp = await pbac_client.post(url, json={"tenant": None, "destination_tenant": "x"}, headers=_h(who))
        assert resp.status == 404, who
        # a denied caller learns nothing about body validation (even for a bad body)
        resp = await pbac_client.post(url, data="not json", headers=_h(who))
        assert resp.status == 404, who
    resp = await pbac_client.post(url, json={"tenant": None, "destination_tenant": "x"})
    assert resp.status == 404
    # editor passes the gate: the handler answers its own tenant/validation error
    resp = await pbac_client.post(url, data="not json", headers=_h("editor"))
    assert resp.status != 404
    assert "error_code" in await resp.json()


@pytest.mark.asyncio
async def test_store_catalog_requires_view(pbac_client):
    """No slug:view on '*' -> 404; with it the handler answers as before (503 w/o registry)."""
    url = "/api/v1/management/query-stores"
    assert (await pbac_client.get(url, headers=_h("nobody"))).status == 404
    assert (await pbac_client.get(url)).status == 404
    resp = await pbac_client.get(url, headers=_h("viewer"))
    assert resp.status == 503
    assert (await resp.json())["error_code"] == "tenant_store_unavailable"


@pytest.mark.asyncio
async def test_moves_stores_pbac_off_unchanged():
    """No app['security'] -> no gate, no session needed."""
    app = web.Application()
    _routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/api/v1/management/query-stores")
        assert resp.status == 503
        resp = await client.post("/api/v1/management/queries/s/move", data="not json")
        assert resp.status != 404
    finally:
        await client.close()
