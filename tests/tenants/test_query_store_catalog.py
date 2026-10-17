"""Management store catalog: canonical descriptors, default preference, no secrets, explicit 503."""
import json
from pathlib import Path

import pytest
from aiohttp import web
from test_tenant_registry import _catalog_conn  # noqa: F401 - catalog double

from querysource.handlers.query_stores import QueryStoreCatalog, catalog_descriptors
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_move_contract_v1.json"
NS = "prod-db.internal:5432/querysource"


async def _registry(tables, allowlist=None):
    registry = TenantRegistry()
    await registry.discover(_catalog_conn(tables), allowlist=allowlist)
    return registry


def _request(registry):
    app = web.Application()
    if registry is not None:
        app["qs_tenant_registry"] = registry
    request = type("R", (), {"app": app})()
    return request


async def _call(registry):
    response = await QueryStoreCatalog().list(_request(registry))
    return response.status, json.loads(response.body)


@pytest.mark.asyncio
async def test_default_public_and_tenants_match_the_shared_v1_fixture() -> None:
    registry = await _registry([("public", "queries", ("program_slug",)), ("wm_assembly", "queries", ())])
    status, body = await _call(registry)
    assert status == 200
    expected = json.loads(FIXTURE.read_text())["catalog"]["response"]
    assert body == expected  # exact keys, order, null selector for the default


@pytest.mark.asyncio
async def test_custom_default_table_is_listed_with_null_selector_and_public_stays_literal(monkeypatch) -> None:
    monkeypatch.setattr(QueryModel.Meta, "schema", "legacy_qs")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    registry = await _registry([
        ("legacy_qs", "saved_queries", ("program_slug",)),
        ("public", "queries", ("program_slug",)),
        ("tenant1", "queries", ()),
    ])
    status, body = await _call(registry)
    assert status == 200
    by_schema = {d["schema"]: d for d in body["data"]}
    assert body["data"][0] == {"tenant": None, "schema": "legacy_qs", "table": "saved_queries",
                               "contract": "legacy", "is_default": True}
    assert by_schema["public"] == {"tenant": "public", "schema": "public", "table": "queries",
                                   "contract": "legacy", "is_default": False}
    assert by_schema["tenant1"]["tenant"] == "tenant1" and by_schema["tenant1"]["contract"] == "tenant"
    assert sum(1 for d in body["data"] if d["is_default"]) == 1


@pytest.mark.asyncio
async def test_same_schema_custom_default_and_tenant_queries_are_distinct_entries(monkeypatch) -> None:
    monkeypatch.setattr(QueryModel.Meta, "schema", "shared")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    registry = await _registry([("shared", "saved_queries", ("program_slug",)), ("shared", "queries", ())])
    status, body = await _call(registry)
    assert status == 200
    pairs = [(d["schema"], d["table"], d["tenant"], d["is_default"]) for d in body["data"]]
    assert pairs == [("shared", "saved_queries", None, True), ("shared", "queries", "shared", False)]


@pytest.mark.asyncio
async def test_allowlist_limits_tenants_but_keeps_the_configured_default() -> None:
    tables = [("public", "queries", ("program_slug",)), ("a", "queries", ()), ("b", "queries", ())]
    status, body = await _call(await _registry(tables, allowlist=["a"]))
    assert [d["schema"] for d in body["data"]] == ["public", "a"]
    status, body = await _call(await _registry(tables, allowlist=[]))
    assert [d["schema"] for d in body["data"]] == ["public"]


def test_physical_aliases_are_deduplicated_preferring_null_selector() -> None:
    default = QueryStore(NS, "public", "queries", "legacy", frozenset({"query_slug"}))
    registry = TenantRegistry()
    registry._default_store = default
    registry._stores = (default, QueryStore(NS, "public", "queries", "legacy", frozenset({"query_slug"})))
    registry._discovered = True
    data = catalog_descriptors(registry)
    assert data == [{"tenant": None, "schema": "public", "table": "queries", "contract": "legacy",
                     "is_default": True}]


def test_unaddressable_stores_are_not_offered() -> None:
    default = QueryStore(NS, "public", "queries", "legacy", frozenset({"query_slug"}))
    odd = QueryStore(NS, "tenant9", "other_table", "tenant", frozenset({"query_slug"}))
    registry = TenantRegistry()
    registry._default_store, registry._stores, registry._discovered = default, (default, odd), True
    assert [d["schema"] for d in catalog_descriptors(registry)] == ["public"]


@pytest.mark.asyncio
async def test_missing_default_or_registry_is_an_explicit_503_never_public() -> None:
    registry = await _registry([("tenant1", "queries", ())])  # no public.queries discovered
    status, body = await _call(registry)
    assert status == 503 and body["error_code"] == "tenant_store_unavailable"
    with pytest.raises(TenantError):
        catalog_descriptors(registry)
    status, body = await _call(None)
    assert status == 503 and body["error_code"] == "tenant_store_unavailable"


@pytest.mark.asyncio
async def test_payload_never_contains_hosts_credentials_or_diagnostics() -> None:
    registry = await _registry([("public", "queries", ("program_slug",)), ("wm", "queries", ())])
    _, body = await _call(registry)
    text = json.dumps(body).lower()
    for forbidden in ("localhost", "5432", "postgresql://", "password", "dsn", "secret", "run_as", "diagnostic",
                      "columns"):
        assert forbidden not in text
    assert set(body) == {"version", "data"} and body["version"] == 1
    assert all(set(d) == {"tenant", "schema", "table", "contract", "is_default"} for d in body["data"])
