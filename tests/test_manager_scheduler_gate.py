"""FEAT-160 / TASK-829: QueryManager gate for scheduled write-capable multis."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

_PBAC = "querysource.handlers.manager.enforce_request_pbac"

WRITE_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]})
READ_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"Table": {"table": "t"}}]})
SCHEDULE = {"schedule_type": "cron", "schedule": {"hour": 1}}


def _registry() -> TenantRegistry:
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource", schema="tenant1", table="queries",
        contract="tenant", columns=frozenset({"query_slug", "description"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


class _Repo:
    """Fake DefinitionRepository: ``stored`` None -> query_not_found on get."""

    def __init__(self, stored: dict | None = None):
        self.stored = stored
        self.calls: list[tuple[str, QueryIdentity, dict]] = []

    async def get(self, identity: QueryIdentity):
        if self.stored is None:
            raise TenantError("Query not found", error_code="query_not_found")
        return SimpleNamespace(runtime=SimpleNamespace(**self.stored))

    async def upsert(self, identity: QueryIdentity, data: dict):
        self.calls.append(("upsert", identity, data))
        return {"query_slug": identity.slug}, self.stored is None

    async def patch(self, identity: QueryIdentity, data: dict):
        self.calls.append(("patch", identity, data))
        return {"query_slug": identity.slug}


def _manager(repo: _Repo, *, json_data: dict, match_info: dict | None = None, pbac: bool = True):
    from querysource.handlers.manager import QueryManager

    request = MagicMock(spec=web.Request)
    request.method = "POST"
    request.query = {}
    request.match_info = match_info or {}
    request.app = {"qs_tenant_registry": _registry(), "qs_definition_repository": repo}
    if pbac:
        request.app["security"] = MagicMock()
    manager = QueryManager(request)

    async def fake_json_data(req=None):
        return dict(json_data)

    manager.json_data = fake_json_data
    manager.get_arguments = lambda: request.match_info
    return manager


def _write_multi(**extra) -> dict:
    return {"query_slug": "s1", "provider": "multi", "query_raw": WRITE_RAW,
            "attributes": {"scheduler": SCHEDULE}, **extra}


def _assert_pg_admin_call(mock: AsyncMock) -> None:
    args = mock.await_args.args
    assert args[1:] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")


@pytest.mark.parametrize("verb", ["put", "post"])
async def test_manager_gate_denies_scheduled_write_multi(verb, monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(), match_info={"slug": "s1"} if verb == "post" else {})
    with pytest.raises(web.HTTPNotFound):
        await getattr(manager, verb)()
    assert repo.calls == []
    _assert_pg_admin_call(deny)


@pytest.mark.parametrize("verb", ["put", "post"])
async def test_manager_gate_allows_with_grant(verb, monkeypatch):
    allow = AsyncMock(return_value=None)
    monkeypatch.setattr(_PBAC, allow)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(), match_info={"slug": "s1"} if verb == "post" else {})
    response = await getattr(manager, verb)()
    assert response.status == 201
    assert repo.calls[0][0] == "upsert"
    _assert_pg_admin_call(allow)


async def test_manager_gate_read_only_multi_no_pbac_call(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(query_raw=READ_RAW))
    response = await manager.put()
    assert response.status == 201
    pbac.assert_not_awaited()


async def test_manager_gate_pbac_disabled_skips_stored_read(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored=None)
    repo.get = AsyncMock(side_effect=AssertionError("stored row must not be read"))
    manager = _manager(repo, json_data=_write_multi(), pbac=False)
    response = await manager.put()
    assert response.status == 201
    pbac.assert_not_awaited()


async def test_manager_gate_patch_adding_write_step_is_gated(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored={"provider": "multi", "query_raw": READ_RAW, "attributes": {"scheduler": SCHEDULE}})
    manager = _manager(repo, json_data={"query_raw": WRITE_RAW}, match_info={"slug": "s1"})
    with pytest.raises(web.HTTPNotFound):
        await manager.patch()
    assert repo.calls == []


async def test_manager_gate_patch_adding_schedule_is_gated(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored={"provider": "multi", "query_raw": WRITE_RAW, "attributes": {"owner": "x"}})
    manager = _manager(repo, json_data={"attributes": {"scheduler": SCHEDULE}}, match_info={"slug": "s1"})
    with pytest.raises(web.HTTPNotFound):
        await manager.patch()
    assert repo.calls == []


async def test_manager_gate_patch_unscheduled_write_multi_not_gated(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored={"provider": "multi", "query_raw": READ_RAW, "attributes": None})
    manager = _manager(repo, json_data={"query_raw": WRITE_RAW}, match_info={"slug": "s1"})
    response = await manager.patch()
    assert response.status == 200
    assert repo.calls[0][0] == "patch"
    pbac.assert_not_awaited()


@pytest.mark.parametrize("verb", ["put", "patch"])
async def test_manager_gate_null_attributes_does_not_bypass(verb, monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored={"provider": "db", "query_raw": "SELECT 1", "attributes": {"scheduler": SCHEDULE}})
    payload = {"query_slug": "s1", "provider": "multi", "attributes": None, "query_raw": WRITE_RAW}
    manager = _manager(repo, json_data=payload, match_info={"slug": "s1"})
    with pytest.raises(web.HTTPNotFound):
        await getattr(manager, verb)()
    assert repo.calls == []
