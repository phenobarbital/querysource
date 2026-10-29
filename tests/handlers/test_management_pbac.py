"""FEAT-182 TASK-943: QueryManager PBAC gate (slug:view / slug:manage).

Requests go through a real aiohttp app. PBAC is bootstrapped with the real
``setup_pbac`` (a genuine navigator-auth ``PolicyEvaluator`` loaded from YAML in
``tmp_path``); ``enforce_request_pbac`` is never mocked. The only test seam is
a middleware that installs the user session the way navigator-session would
(``request['user_session']``, the slot ``get_request_session`` reads).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from navigator_auth.conf import AUTH_SESSION_OBJECT

from querysource.auth import setup_pbac
from querysource.handlers.manager import QueryManager

from .conftest import FakeQSConnection

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

_WRITE_BODY = {"query_slug": "fixture_slug", "description": "x", "provider": "db"}


@web.middleware
async def _session_mw(request: web.Request, handler):
    """Install the session the way navigator-session does, picked by header."""
    who = request.headers.get("X-Test-User")
    if who:
        request["user_session"] = {AUTH_SESSION_OBJECT: _SESSIONS[who]}
    return await handler(request)


def _routes(app: web.Application) -> None:
    app.router.add_view(r"/api/v1/management/queries/{slug}", QueryManager)
    app.router.add_view(r"/api/v1/management/queries{meta:\:?.*}", QueryManager)


async def _start(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest_asyncio.fixture
async def pbac_env(tmp_path):
    """PBAC-on app + fake pool + a repository double that must stay untouched."""
    (tmp_path / "defaults.yaml").write_text(_POLICIES)
    pool = FakeQSConnection()
    repo = MagicMock()
    for name in ("get", "upsert", "patch", "delete", "export_insert", "list"):
        setattr(repo, name, AsyncMock())
    app = web.Application(middlewares=[_session_mw])
    app["qs_connection"] = pool
    setup_pbac(app, policy_dir=str(tmp_path))
    assert app.get("security") is not None, "real PBAC bootstrap failed"
    _routes(app)
    client = await _start(app)
    try:
        yield client, pool, repo, app
    finally:
        await client.close()


def _h(who: str) -> dict:
    return {"X-Test-User": who}


def _untouched(pool: FakeQSConnection, repo: MagicMock) -> bool:
    return not pool.calls and not any(
        getattr(repo, n).called
        for n in ("get", "upsert", "patch", "delete", "export_insert", "list")
    )


@pytest.mark.asyncio
async def test_manager_get_single_denied_404(pbac_env):
    """No slug:view -> 404 and nothing is read."""
    client, pool, repo, app = pbac_env
    app["qs_definition_repository"] = repo
    resp = await client.get("/api/v1/management/queries/secret_slug", headers=_h("nobody"))
    assert resp.status == 404
    assert _untouched(pool, repo)
    # sessionless request is also denied (fail-closed)
    resp = await client.get("/api/v1/management/queries/secret_slug")
    assert resp.status == 404
    assert _untouched(pool, repo)


@pytest.mark.asyncio
async def test_manager_gate_not_swallowed(pbac_env):
    """Denied GET is a 404, never the 500 from get()'s catch-all."""
    client, pool, _repo, _app = pbac_env
    for path in (
        "/api/v1/management/queries/secret_slug",
        "/api/v1/management/queries/secret_slug:meta",
        "/api/v1/management/queries:meta",
        "/api/v1/management/queries",
    ):
        resp = await client.get(path, headers=_h("nobody"))
        assert resp.status == 404, (path, resp.status)
    assert not pool.calls


@pytest.mark.asyncio
async def test_manager_list_requires_view_star(pbac_env):
    """List and :meta need slug:view on '*'; viewers get the normal response."""
    client, pool, _repo, _app = pbac_env
    denied_list = await client.get("/api/v1/management/queries", headers=_h("nobody"))
    denied_meta = await client.get("/api/v1/management/queries:meta", headers=_h("nobody"))
    assert denied_list.status == 404
    assert denied_meta.status == 404
    assert not pool.calls

    pool.fetchval_handler = 0
    pool.fetch_handler = []
    ok_meta = await client.get("/api/v1/management/queries:meta", headers=_h("viewer"))
    assert ok_meta.status in (200, 204)
    ok_list = await client.get("/api/v1/management/queries", headers=_h("viewer"))
    assert ok_list.status in (200, 204)
    assert pool.calls, "viewer list must reach the paginated SQL"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_manager_write_methods_require_manage(pbac_env, method):
    """viewers (slug:view only) and nobody -> 404 on every write; nothing written."""
    client, pool, repo, app = pbac_env
    app["qs_definition_repository"] = repo
    for who in ("viewer", "nobody"):
        resp = await client.request(
            method,
            "/api/v1/management/queries/fixture_slug",
            json=_WRITE_BODY,
            headers=_h(who),
        )
        assert resp.status == 404, (method, who, resp.status)
        assert _untouched(pool, repo)
    if method in ("POST", "PUT"):
        # slug supplied only in the body on the collection route
        resp = await client.request(
            method, "/api/v1/management/queries", json=_WRITE_BODY, headers=_h("viewer")
        )
        assert resp.status == 404
        assert _untouched(pool, repo)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_manager_write_allowed_with_manage(pbac_env, method):
    """editors pass the gate: whatever the handler answers, it is not the PBAC 404."""
    client, pool, repo, app = pbac_env
    repo.get.return_value = None
    repo.upsert.return_value = (MagicMock(), True)
    app["qs_definition_repository"] = repo
    resp = await client.request(
        method,
        "/api/v1/management/queries/fixture_slug",
        json=_WRITE_BODY,
        headers=_h("editor"),
    )
    assert resp.status != 404, (method, resp.status)


@pytest.mark.asyncio
async def test_manager_single_get_allowed_for_viewer(pbac_env):
    """viewer (slug:view) gets past the gate on a single slug."""
    client, pool, _repo, _app = pbac_env
    pool.fetch_one_handler = None
    resp = await client.get("/api/v1/management/queries/some_slug", headers=_h("viewer"))
    # Passes the gate: the legacy single-slug path answers 204/X-STATUS=EMPTY
    # or an error from the (absent) DB, never the PBAC 404.
    assert resp.status != 404


@pytest.mark.asyncio
async def test_management_pbac_off_unchanged(fake_qs_connection):
    """No setup_pbac (no app['security']) -> no gate, no session needed."""
    app = web.Application()
    app["qs_connection"] = fake_qs_connection
    _routes(app)
    client = await _start(app)
    try:
        assert "security" not in app
        fake_qs_connection.fetchval_handler = 0
        fake_qs_connection.fetch_handler = []
        resp = await client.get("/api/v1/management/queries")
        assert resp.status in (200, 204)
        resp = await client.get("/api/v1/management/queries:meta")
        assert resp.status in (200, 204)
        assert fake_qs_connection.calls
    finally:
        await client.close()
