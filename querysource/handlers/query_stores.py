"""Credential-free catalog of registered query stores (FEAT-176, M2).

``GET /api/v1/management/query-stores`` publishes the immutable registry snapshot taken at
QuerySource initialization. It never rediscovers, never creates tables, never infers write
grants and never exposes the database host, DSN, credentials, internal diagnostics or run-as
identities. Listing a store does not authorize writing to it.
"""
from collections.abc import Mapping
from typing import Any

from aiohttp import web
from navconfig.logging import logging

from querysource.query_moves import store_descriptor
from querysource.scheduler.handoff import selector_for
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry

logger = logging.getLogger("QS.QueryStoreCatalog")

CATALOG_VERSION = 1


def _error(code: str, message: str, status: int) -> web.Response:
    return web.json_response({"error_code": code, "message": message}, status=status)


def catalog_descriptors(registry: TenantRegistry) -> list[Mapping[str, Any]]:
    """Return the deduplicated ``StoreDescriptor`` list, configured default first.

    Aliases of one physical store collapse to a single entry that prefers ``tenant: null`` for
    the configured default. Only selectors that provably resolve back to the same physical
    store are offered (a schema selector addresses that schema's ``queries`` table only).

    Raises:
        TenantError: ``tenant_store_unavailable`` when the configured default store is not
            available — readiness is never claimed by substituting public or a tenant.
    """
    default: QueryStore = registry.resolve()  # raises tenant_store_unavailable when missing
    seen: set[tuple[str, str, str]] = set()
    ordered: list[QueryStore] = []

    def add(store: QueryStore) -> None:
        key = (store.database_namespace, store.schema, store.table)
        if key not in seen:
            seen.add(key)
            ordered.append(store)

    add(default)
    for store in sorted(registry.stores(), key=lambda s: (s.schema, s.table)):
        add(store)

    descriptors: list[Mapping[str, Any]] = []
    for store in ordered:
        selector = selector_for(registry, store)
        try:
            resolved = registry.resolve(selector)
        except TenantError:
            continue  # not addressable by any selector: never offer it
        if (resolved.database_namespace, resolved.schema, resolved.table) != (
            store.database_namespace, store.schema, store.table,
        ):
            continue
        descriptors.append(store_descriptor(registry, store))
    return descriptors


class QueryStoreCatalog:
    """Management store catalog (same authenticated management boundary as the CRUD routes)."""

    async def list(self, request: web.Request) -> web.Response:
        """Return ``200 {version: 1, data: StoreDescriptor[]}`` or an explicit 503.

        The registry comes from ``app['qs_tenant_registry']`` (published once at startup).
        An unpublished registry or an unavailable configured default yields 503 with a stable
        error code instead of a fabricated catalog.
        """
        registry = request.app.get("qs_tenant_registry")
        if registry is None:
            return _error("tenant_store_unavailable", "The query store registry is not available.", 503)
        try:
            data = catalog_descriptors(registry)
        except TenantError as exc:
            logger.warning("Query store catalog unavailable: %s", exc.error_code)
            return _error(exc.error_code, "The configured default query store is not available.", exc.code)
        return web.json_response({"version": CATALOG_VERSION, "data": data})
