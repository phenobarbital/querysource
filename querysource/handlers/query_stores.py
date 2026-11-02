"""Credential-free catalog of registered query stores (FEAT-176).

``GET /api/v1/management/query-stores`` publishes the registry snapshot taken
at QuerySource initialization. It never rediscovers, never exposes the
database host, DSN, credentials or diagnostics, and listing a store does not
authorize writing to it.
"""
from collections.abc import Mapping
from typing import Any

from aiohttp import web
from navconfig.logging import logging

from querysource.auth import ResourceType
from querysource.auth.request_gate import enforce_request_pbac
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry

logger = logging.getLogger("QS.QueryStoreCatalog")

CATALOG_VERSION = 1


def tenant_error_response(exc: TenantError) -> web.Response:
    """Render a TenantError (client-safe message) as ``{error_code, message, details?}``."""
    body: dict[str, Any] = {"error_code": exc.error_code, "message": str(exc)}
    if exc.details:
        body["details"] = dict(exc.details)
    return web.json_response(body, status=exc.code)


def _same(left: QueryStore, right: QueryStore) -> bool:
    """Return True when both are the same physical table."""
    return (left.database_namespace, left.schema, left.table) == (
        right.database_namespace, right.schema, right.table,
    )


def store_selector(registry: TenantRegistry, store: QueryStore) -> str | None:
    """Return the selector addressing ``store``: None for the default, else its schema."""
    return None if _same(store, registry.resolve()) else store.schema


def store_descriptor(registry: TenantRegistry, store: QueryStore) -> Mapping[str, Any]:
    """Return the public descriptor of one store (no namespace, host or DSN)."""
    tenant = store_selector(registry, store)
    return {
        "tenant": tenant,
        "schema": store.schema,
        "table": store.table,
        "has_program_slug": store.has_program_slug,
        "is_default": tenant is None,
    }


def catalog_descriptors(registry: TenantRegistry) -> list[Mapping[str, Any]]:
    """Return one descriptor per discovered, addressable store, default first."""
    descriptors = []
    for store in sorted(registry.stores(), key=lambda s: (store_selector(registry, s) is not None, s.schema, s.table)):
        try:
            resolved = registry.resolve(store_selector(registry, store))
        except TenantError:
            continue  # not addressable by any selector: never offer it
        if _same(resolved, store):
            descriptors.append(store_descriptor(registry, store))
    return descriptors


class QueryStoreCatalog:
    """Management store catalog (same authenticated boundary as the CRUD routes)."""

    async def list(self, request: web.Request) -> web.Response:
        """Return ``200 {version: 1, data: [descriptor, ...]}`` or 503 without a registry."""
        # FEAT-182: the catalogue is part of the management boundary.
        await enforce_request_pbac(
            request, ResourceType.SLUG, "*", "slug:view", logger=logger,
        )
        registry = request.app.get("qs_tenant_registry")
        if registry is None:
            return tenant_error_response(
                TenantError("The query store registry is not available", error_code="tenant_store_unavailable")
            )
        return web.json_response({"version": CATALOG_VERSION, "data": catalog_descriptors(registry)})
