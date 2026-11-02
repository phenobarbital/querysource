"""FEAT-182 TASK-946: QS_DESCRIBE_EXECUTE_FALLBACK.

Real ``setup_pbac`` + ``PolicyEvaluator`` from YAML in ``tmp_path``; the real
``can_access`` / ``filter_visible`` run (neither is mocked). Only the
principal resolution and the slug loader are pinned, as in the sibling
describe tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.auth import setup_pbac
from querysource.auth.slug_visibility import Principal, PrincipalKind
from querysource.handlers import describe as describe_mod
from querysource.handlers.describe import QueryDescribe
from querysource.models import QueryModel

_POLICIES = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: runners_execute_only
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:execute"]
    subjects:
      groups: ["runners"]
    priority: 20
  - name: describers
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:list", "slug:describe"]
    subjects:
      groups: ["describers"]
    priority: 20
"""


def _principal(group: str) -> Principal:
    info = {"username": group, "user_id": group, "groups": [group], "roles": []}
    return Principal(kind=PrincipalKind.PROGRAMS, userinfo=info, groups=(group,),
                     programs=("acme",), session={"userinfo": info})


def _pin(principal: Principal):
    return patch.object(QueryDescribe, "_principal", new_callable=AsyncMock, return_value=principal)


@pytest_asyncio.fixture
async def client(tmp_path, fake_qs_connection):
    (tmp_path / "defaults.yaml").write_text(_POLICIES)
    app = web.Application()
    app["qs_connection"] = fake_qs_connection
    setup_pbac(app, policy_dir=str(tmp_path))
    assert app.get("security") is not None
    dh = QueryDescribe()
    app.router.add_get("/api/v1/queries/describe", dh.describe_list)
    app.router.add_get("/api/v1/queries/{slug}/describe", dh.describe)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


def _rows():
    return [
        {"query_slug": "s1", "provider": "db", "description": "d1",
         "program_slug": "acme", "updated_at": "2026-01-01"},
    ]


def _model() -> QueryModel:
    return QueryModel(query_slug="s1", provider="db", description="d", program_slug="acme")


async def _list(client, fake, who):
    fake.fetch_handler = lambda sql, *args: _rows()
    with _pin(_principal(who)):
        return await client.get("/api/v1/queries/describe")


async def _detail(client, fake, who):
    fake.fetch_one_handler = lambda sql, *args: {"exists": 1}
    with _pin(_principal(who)), \
            patch.object(QueryModel, "get", new_callable=AsyncMock, return_value=_model()):
        return await client.get("/api/v1/queries/s1/describe")


@pytest.mark.asyncio
async def test_describe_fallback_default(client, fake_qs_connection):
    """Default (flag True): an execute-only user still sees list and detail."""
    assert describe_mod.QS_DESCRIBE_EXECUTE_FALLBACK is True
    resp = await _list(client, fake_qs_connection, "runners")
    assert resp.status == 200
    assert [r["query_slug"] for r in (await resp.json())["data"]] == ["s1"]
    assert (await _detail(client, fake_qs_connection, "runners")).status == 200


@pytest.mark.asyncio
async def test_describe_fallback_disabled(client, fake_qs_connection, monkeypatch):
    """Flag False: execute-only user sees no list rows and gets 404 on detail; describers unaffected."""
    monkeypatch.setattr(describe_mod, "QS_DESCRIBE_EXECUTE_FALLBACK", False)
    resp = await _list(client, fake_qs_connection, "runners")
    assert resp.status == 204
    assert resp.headers["X-Total-Count"] == "0"
    assert (await _detail(client, fake_qs_connection, "runners")).status == 404

    # a user holding the real describe grants keeps working
    resp = await _list(client, fake_qs_connection, "describers")
    assert resp.status == 200
    assert (await _detail(client, fake_qs_connection, "describers")).status == 200


def test_flag_defaults_to_true():
    """conf default keeps today's behaviour."""
    from querysource import conf
    assert conf.QS_DESCRIBE_EXECUTE_FALLBACK is True
    assert describe_mod._execute_fallback() == "slug:execute"
