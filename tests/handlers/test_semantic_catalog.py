"""Tests for the semantic catalog endpoint (FEAT-175 M9)."""
from unittest.mock import MagicMock

from aiohttp import web

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
