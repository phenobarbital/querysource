"""Tests for the semantic catalog endpoint (FEAT-175 M9)."""
from unittest.mock import MagicMock

from aiohttp import web
from semantic.conftest import fake_evaluator, sidemantic_available  # noqa: F401

from querysource.handlers.semantic import SemanticCatalog
from querysource.queries.describe import DescribeGrants, describe_slug


def test_catalog_route_registered():
    """Both legacy and tenant catalog routes are registered with the handler."""
    app = web.Application()
    handler = SemanticCatalog()
    app.router.add_get("/api/v1/semantic/{slug}/catalog", handler.catalog)
    app.router.add_get("/api/v1/{tenant}/semantic/{slug}/catalog", handler.catalog)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/v1/semantic/{slug}/catalog") in routes
    assert ("GET", "/api/v1/{tenant}/semantic/{slug}/catalog") in routes


def test_describe_semantic_derived():
    """Semantic describe payload contains compiler-free resource metadata."""
    definition = MagicMock()
    definition.query_slug = "orders"
    definition.provider = "semantic"
    definition.query_raw = """
name: orders
kind: semantic
connection: warehouse
semantic:
  format: ossie
  definition:
    models: {}
governance:
  row_filters:
    default: []
    analyst: []
"""
    definition.conditions = {}
    definition.cond_definition = {}
    definition.fields = []
    definition.filtering = {}
    definition.ordering = []
    definition.grouping = []
    definition.h_filtering = False
    definition.qry_options = {}
    for name in ("description", "source", "params", "attributes", "columns_definition", "is_raw", "is_cached",
                 "parser", "cache_timeout", "cache_refresh", "program_id", "program_slug", "dwh", "dwh_driver",
                 "dwh_info", "dwh_scheduler", "created_at", "created_by", "updated_at", "updated_by"):
        setattr(definition, name, None)
    payload = describe_slug(definition, DescribeGrants(raw=True, admin=True), columns_link="/columns", vocabulary_link="/vocabulary")
    assert payload["derived"]["semantic"] == {
        "format": "ossie",
        "connection": "warehouse",
        "definition_kind": "inline",
        "governance_roles": ["analyst"],
        "catalog": "/api/v1/semantic/orders/catalog",
    }


# --- real handler behaviour (AC-16 / AC-26) -------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402

CATALOG_RESOURCE = """
name: s
kind: semantic
semantic:
  format: sidemantic
  definition:
    models:
      - name: orders
        table: orders
        metrics:
          - {name: revenue, agg: sum, sql: amount, description: Total}
          - {name: margin, agg: sum, sql: margin}
        dimensions:
          - {name: region, type: categorical}
"""


def _handler(monkeypatch, *, provider="semantic", raw=CATALOG_RESOURCE):
    handler = SemanticCatalog()
    model = SimpleNamespace(provider=provider, query_raw=raw)
    monkeypatch.setattr(handler, "_principal", AsyncMock(return_value=object()))
    monkeypatch.setattr(handler, "_store", AsyncMock(return_value=object()))
    monkeypatch.setattr(handler, "_load_visible", AsyncMock(return_value=SimpleNamespace(runtime=model, revision="r1")))
    monkeypatch.setattr(handler, "_enforce_pbac", AsyncMock())
    monkeypatch.setattr(handler, "_get_user_session", AsyncMock(return_value=None))
    return handler


def _request():
    return make_mocked_request("GET", "/api/v1/semantic/s/catalog", match_info={"slug": "s"})


@pytest.mark.asyncio
async def test_catalog_returns_payload_and_redacts_denied_fields(monkeypatch, fake_evaluator, sidemantic_available):
    import json

    from querysource.auth.principal import QSPrincipal
    from querysource.semantic import governance

    principal = QSPrincipal(user_id="1", username="a")
    monkeypatch.setattr(governance, "principal_from_session", lambda session: principal)
    fake_evaluator.deny.add("s:metric:margin")
    response = await _handler(monkeypatch).catalog(_request())
    assert response.status == 200
    body = json.loads(response.text)
    assert body["slug"] == "s" and body["dialect"] == "postgres"
    metrics = {m["name"]: m for m in body["metrics"]}
    assert metrics["orders.revenue"]["granted"] is True
    assert metrics["orders.revenue"]["description"] == "Total"
    assert metrics["orders.margin"]["granted"] is False
    assert metrics["orders.margin"].get("description") is None


@pytest.mark.asyncio
async def test_catalog_404_for_non_semantic_slug(monkeypatch):
    with pytest.raises(web.HTTPNotFound):
        await _handler(monkeypatch, provider="db").catalog(_request())


@pytest.mark.asyncio
async def test_catalog_unknown_datasource_is_400(monkeypatch):
    resource = CATALOG_RESOURCE.replace("kind: semantic\n", "kind: semantic\nconnection: nope\n", 1)
    with pytest.raises(web.HTTPBadRequest):
        await _handler(monkeypatch, raw=resource).catalog(_request())


@pytest.mark.asyncio
async def test_catalog_denied_datasource_is_404(monkeypatch, fake_evaluator):
    from querysource.auth.principal import QSPrincipal
    from querysource.semantic import governance

    monkeypatch.setattr(governance, "principal_from_session", lambda session: QSPrincipal(user_id="1", username="a"))
    fake_evaluator.deny.add("default")
    with pytest.raises(web.HTTPNotFound):
        await _handler(monkeypatch).catalog(_request())


def test_describe_semantic_non_raw_hides_connection_roles_and_errors():
    definition = MagicMock()
    definition.query_slug = "orders"
    definition.provider = "semantic"
    definition.query_raw = "name: orders\nkind: semantic\nconnection: warehouse\nsemantic:\n  definition: {models: {}}\ngovernance:\n  row_filters:\n    analyst: []\n"
    definition.conditions = {}
    definition.cond_definition = {}
    for name in ("fields", "ordering", "grouping"):
        setattr(definition, name, [])
    for name in ("filtering", "qry_options"):
        setattr(definition, name, {})
    definition.h_filtering = False
    payload = describe_slug(definition, DescribeGrants(raw=False, admin=False), columns_link="/c", vocabulary_link="/v")
    semantic = payload["derived"]["semantic"]
    assert "connection" not in semantic and "governance_roles" not in semantic
    definition.query_raw = "not: [valid"
    payload = describe_slug(definition, DescribeGrants(raw=False, admin=False), columns_link="/c", vocabulary_link="/v")
    assert payload["derived"]["semantic"] == {"error": "invalid semantic resource"}
