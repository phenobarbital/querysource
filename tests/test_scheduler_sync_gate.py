"""FEAT-160 / TASK-830: scheduler sync endpoint gate for scheduled write-capable multis."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.handlers.scheduler import SchedulerJobsView
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry

_PBAC = "querysource.handlers.scheduler.enforce_request_pbac"

WRITE_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]})
READ_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}})
SCHEDULE = {"scheduler": {"schedule_type": "cron", "schedule": {"hour": 1}}}


def _registry() -> TenantRegistry:
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource", schema="tenant1", table="queries",
        contract="tenant", columns=frozenset({"query_slug"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


def _repo(stored: dict | None) -> MagicMock:
    repo = MagicMock()
    if stored is None:
        repo.get = AsyncMock(side_effect=TenantError("Query not found", error_code="query_not_found"))
    else:
        repo.get = AsyncMock(return_value=SimpleNamespace(runtime=SimpleNamespace(**stored)))
    return repo


def _view(*, repo, pbac: bool = True, body: dict | None = None):
    scheduler = MagicMock()
    scheduler.register_slug = AsyncMock(return_value={"slug": "s1", "registered": [], "removed": []})
    request = MagicMock()
    request.json = AsyncMock(return_value=body or {"slug": "s1"})
    request.app = {"qs_scheduler": scheduler, "qs_tenant_registry": _registry()}
    if repo is not None:
        request.app["qs_definition_repository"] = repo
    if pbac:
        request.app["security"] = MagicMock()
    view = SchedulerJobsView.__new__(SchedulerJobsView)
    view._request = request
    return view, scheduler


async def test_scheduler_sync_gate_denies_write_multi(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE}))
    with pytest.raises(web.HTTPNotFound):
        await view.post()
    scheduler.register_slug.assert_not_awaited()
    assert deny.await_args.args[1:] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")


async def test_scheduler_sync_gate_allows_with_grant(monkeypatch):
    allow = AsyncMock(return_value=None)
    monkeypatch.setattr(_PBAC, allow)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE}))
    response = await view.post()
    assert response.status == 200
    scheduler.register_slug.assert_awaited_once_with("s1", tenant=None)


async def test_scheduler_sync_gate_read_only_registered(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": READ_RAW, "attributes": SCHEDULE}))
    response = await view.post()
    assert response.status == 200
    pbac.assert_not_awaited()
    scheduler.register_slug.assert_awaited_once()


@pytest.mark.parametrize("case", ["missing_row", "no_repo", "pbac_off", "unknown_tenant"])
async def test_scheduler_sync_gate_unchanged_behaviour(case, monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = None if case == "no_repo" else _repo(None if case == "missing_row" else {
        "provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE,
    })
    body = {"slug": "s1", "tenant": "nope"} if case == "unknown_tenant" else None
    view, scheduler = _view(repo=repo, pbac=case != "pbac_off", body=body)
    await view.post()
    pbac.assert_not_awaited()
    scheduler.register_slug.assert_awaited_once()


async def test_scheduler_sync_gate_store_failure_fails_closed(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=TenantError("down", error_code="tenant_store_unavailable"))
    view, scheduler = _view(repo=repo)
    with pytest.raises(web.HTTPNotFound):
        await view.post()
    scheduler.register_slug.assert_not_awaited()
