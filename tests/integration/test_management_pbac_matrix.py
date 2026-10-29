"""FEAT-182 TASK-948: role x endpoint PBAC matrix (real evaluator, no ``*:*`` floor).

The app is the real one: ``QuerySource(lazy=True).setup(app)`` with
``QS_PBAC_ENABLED`` on registers every production route and bootstraps the
real PBAC stack from a ``tmp_path`` policy set. That set is the shipped
``policies/defaults.yaml`` (the ``admin`` role) plus two narrow role policies;
there is NO ``*:*`` baseline. Only I/O is patched: the ``qs_connection`` pool,
``get_source`` and the startup hooks. The matrix asserts authorization, not
query execution: allowed means the handler was reached (anything but the PBAC
404), denied means 404.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from navigator_auth.conf import AUTH_SESSION_OBJECT

from querysource.handlers import describe as describe_mod
from querysource.handlers.service import QueryService

SHIPPED_DEFAULTS = Path(__file__).resolve().parents[2] / "policies" / "defaults.yaml"

_ROLE_POLICIES = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: qsteam_management
    effect: allow
    resources: ["slug:*", "driver:*", "raw_query:*"]
    actions: ["slug:view", "slug:manage", "raw_query:execute", "driver:query", "driver:use"]
    subjects:
      groups: ["qsteam"]
    priority: 30
  - name: plain_execution
    effect: allow
    resources: ["slug:*", "datasource:*", "driver:*"]
    actions: ["slug:execute", "datasource:use", "driver:use"]
    subjects:
      groups: ["plain"]
    priority: 20
  - name: plain_direct_http
    effect: allow
    resources: ["driver:http"]
    actions: ["driver:query"]
    subjects:
      groups: ["plain"]
    priority: 20
"""

_USERS = {
    "admin": {"username": "ada", "user_id": "ada", "groups": ["admin"], "roles": [], "programs": ["acme"]},
    "qsteam": {"username": "quinn", "user_id": "quinn", "groups": ["qsteam"], "roles": [], "programs": ["acme"]},
    "plain": {"username": "pat", "user_id": "pat", "groups": ["plain"], "roles": [], "programs": ["acme"]},
}


class _Conn:
    """asyncdb connection stand-in: empty results, SQL recorded."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def fetchval(self, sql, *args):
        self._pool.calls.append(sql)
        return 0

    async def fetch_all(self, sql, *args):
        self._pool.calls.append(sql)
        return [{"query_slug": "s1", "provider": "db", "description": "d",
                 "program_slug": "acme", "updated_at": "2026-01-01"}]

    async def fetch_one(self, sql, *args):
        self._pool.calls.append(sql)
        return None

    async def fetchrow(self, sql, *args):
        self._pool.calls.append(sql)
        return None


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class _Pool:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def acquire(self):
        return _Acquire(_Conn(self))


@web.middleware
async def _session_mw(request: web.Request, handler):
    """Install the session the way navigator-session does, picked by header."""
    who = request.headers.get("X-Test-Role")
    if who:
        request["user_session"] = {AUTH_SESSION_OBJECT: _USERS[who]}
    return await handler(request)


@pytest.fixture
def pbac_app(tmp_path, monkeypatch):
    """The real QuerySource app with real PBAC on; startup hooks dropped (no DB)."""
    from querysource.services import QuerySource

    shutil.copy(SHIPPED_DEFAULTS, tmp_path / "defaults.yaml")
    (tmp_path / "roles.yaml").write_text(_ROLE_POLICIES)
    monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", True)
    monkeypatch.setattr("querysource.services.QS_POLICY_PATH", str(tmp_path))
    monkeypatch.setattr("querysource.services.QS_PBAC_STRICT_BOOT", True)
    app = web.Application(middlewares=[_session_mw])
    QuerySource(lazy=True).setup(app)
    assert app.get("security") is not None, "real PBAC bootstrap failed"
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    app["qs_connection"] = _Pool()
    return app


@pytest_asyncio.fixture
async def client(pbac_app):
    get_source = AsyncMock(return_value=None)  # gate passed -> handler's own 400
    with patch.object(QueryService, "get_source", get_source):
        c = TestClient(TestServer(pbac_app))
        await c.start_server()
        try:
            c.get_source = get_source
            yield c
        finally:
            await c.close()


_BODY = {"query_slug": "s1", "description": "x", "provider": "db"}
_MOVE = {"tenant": None, "destination_tenant": "x"}
_SQL = {"query": "SELECT 1", "driver": "pg"}

# (role, method, path, json body, allowed)
_ROWS = [
    ("mgmt-list", "GET", "/api/v1/management/queries", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("mgmt-meta", "GET", "/api/v1/management/queries:meta", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("mgmt-get", "GET", "/api/v1/management/queries/s1", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("mgmt-post", "POST", "/api/v1/management/queries/s1", _BODY, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("mgmt-delete", "DELETE", "/api/v1/management/queries/s1", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("mgmt-move", "POST", "/api/v1/management/queries/s1/move", _MOVE, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("catalogue", "GET", "/api/v1/management/query-stores", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("driver-http", "GET", "/api/v2/queries/http/rssapp/get_bundle", None, {"admin": 1, "qsteam": 1, "plain": 1}),
    ("driver-pg", "GET", "/api/v2/queries/pg/some_source/some_attr", None, {"admin": 1, "qsteam": 1, "plain": 0}),
    ("test-queries", "POST", "/api/v2/test/queries", _SQL, {"admin": 1, "qsteam": 1, "plain": 0}),
]

MATRIX = [
    pytest.param(role, method, path, body, bool(allowed[role]), id=f"{name}-{role}")
    for (name, method, path, body, allowed) in _ROWS
    for role in ("admin", "qsteam", "plain")
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,method,path,body,allowed", MATRIX)
async def test_management_routes_policy_matrix(client, role, method, path, body, allowed):
    """allowed -> handler reached (not the PBAC 404); denied -> 404."""
    resp = await client.request(method, path, json=body, headers={"X-Test-Role": role})
    if allowed:
        assert resp.status != 404, (role, method, path)
    else:
        assert resp.status == 404, (role, method, path, resp.status)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [(r[1], r[2], r[3]) for r in _ROWS])
async def test_management_routes_sessionless_denied(client, method, path, body):
    """No session -> 404 everywhere (fail-closed), nothing resolved."""
    resp = await client.request(method, path, json=body)
    assert resp.status == 404, (method, path)
    client.get_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_describe_list_fallback_flag(client, monkeypatch):
    """plain holds only slug:execute: sees the list with the fallback on, nothing with it off.

    admin holds slug:list via the shipped policy and is unaffected either way.
    """
    url = "/api/v1/queries/describe"
    resp = await client.get(url, headers={"X-Test-Role": "plain"})
    assert resp.status == 200
    assert [r["query_slug"] for r in (await resp.json())["data"]] == ["s1"]

    monkeypatch.setattr(describe_mod, "QS_DESCRIBE_EXECUTE_FALLBACK", False)
    assert (await client.get(url, headers={"X-Test-Role": "plain"})).status == 204
    assert (await client.get(url, headers={"X-Test-Role": "admin"})).status == 200
