"""Immutable store identities and catalog discovery for per-tenant queries."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qs, unquote, urlsplit

from asyncdb.drivers.pg import pg

from querysource.models import QueryModel
from querysource.tenant_errors import TenantError

logger = logging.getLogger("QS.TenantRegistry")

# System schemas to exclude from discovery
_SYSTEM_SCHEMAS = ("information_schema", "pg_catalog", "pg_toast", "pg_temp")

# Reserved schemas that cannot be used as tenant names
_RESERVED_SCHEMAS = ("management",)

# Schemas containing slashes are excluded
_SLASH_EXCLUDED = True


@dataclass(frozen=True)
class QueryStore:
    """Canonical physical identity plus its single shape flag.

    ``has_program_slug`` is ``"program_slug" in columns`` at discovery: when
    True the stored ``program_slug`` is persisted and used as-is; when False
    it is runtime-derived from ``schema`` and never persisted.
    """

    database_namespace: str
    schema: str
    table: str
    has_program_slug: bool
    columns: frozenset[str]


@dataclass(frozen=True)
class QueryIdentity:
    """A saved definition identity; aliases never replace its slug."""

    store: QueryStore
    slug: str


@dataclass(frozen=True)
class LoadedDefinition:
    """Detached runtime model plus immutable persisted-revision identity."""

    identity: QueryIdentity
    runtime: QueryModel
    revision: str


@dataclass(frozen=True)
class DefinitionPage:
    """Persisted projections and total matching the same validated filters."""

    rows: tuple[Mapping[str, Any], ...]
    total: int


class TenantOwnerEnvelope(TypedDict):
    """Versioned remote/job identity; no database secrets or connections.

    This is a WIRE contract: it is serialized to remote qworkers under the
    ``querysource.remote.tenant_query_handler_v1`` handler, so its v1 shape is
    frozen and keeps the ``contract`` key even though :class:`QueryStore` now
    models the same fact as ``has_program_slug``. Build it with
    :func:`owner_envelope`, never by hand, and bump to a ``_v2`` handler rather
    than renaming a key here.
    """

    version: Literal[1]
    database_namespace: str
    schema: str
    table: str
    contract: Literal["legacy", "tenant"]


def contract_of(has_program_slug: bool) -> str:
    """Map the internal shape flag to the frozen v1 wire name."""
    return "legacy" if has_program_slug else "tenant"


def owner_envelope(store: QueryStore) -> TenantOwnerEnvelope:
    """Build the frozen v1 owner envelope for a physical store.

    Args:
        store: The resolved physical store the job/query is bound to.

    Returns:
        The v1 envelope; never contains credentials, a DSN or a run-as identity.
    """
    return TenantOwnerEnvelope(
        version=1,
        database_namespace=store.database_namespace,
        schema=store.schema,
        table=store.table,
        contract=contract_of(store.has_program_slug),
    )


def is_configured_default(store: QueryStore) -> bool:
    """Return True when ``store`` is the configured QS_QUERIES_SCHEMA/QS_QUERIES_TABLE table."""
    return (store.schema, store.table) == (QueryModel.Meta.schema, QueryModel.Meta.name)


class TenantRegistry:
    """One immutable published snapshot per QuerySource initialization."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._stores: tuple[QueryStore, ...] = ()
        self._diagnostics: tuple[Mapping[str, Any], ...] = ()
        self._default_store: QueryStore | None = None
        # Real database_namespace captured during discover(), used by
        # resolve()'s legacy-fallback branches so a synthetic fallback
        # store never reports a different physical identity (and thus a
        # different cache-key/job-id digest) than the same logical
        # database's other, actually-discovered stores. None until
        # discover() has run at least once.
        self._database_namespace: str | None = None

    def _is_system_schema(self, schema: str) -> bool:
        """Check if a schema is a system schema."""
        return schema.lower() in _SYSTEM_SCHEMAS

    def _is_reserved_schema(self, schema: str) -> bool:
        """Check if a schema is reserved."""
        return schema.lower() in _RESERVED_SCHEMAS

    def _has_slash(self, schema: str) -> bool:
        """Check if a schema contains a slash."""
        return "/" in schema

    def _is_valid_identifier(self, value: str) -> bool:
        """Check if a value is a valid identifier (no NUL, no empty)."""
        return value and "\0" not in value

    def _quote_identifier(self, value: str) -> str:
        """Quote one validated identifier, preserving case and embedded quotes."""
        if not self._is_valid_identifier(value):
            raise ValueError(f"Invalid identifier: {value!r}")

        # Double embedded double quotes
        quoted = value.replace('"', '""')
        return f'"{quoted}"'

    def _build_database_namespace(self, host: str, port: int, database: str) -> str:
        """Build a database namespace from host, port, and database without secrets."""
        return f"{host}:{port}/{database}"

    def _deduplicate_stores(self, stores: Sequence[QueryStore]) -> tuple[QueryStore, ...]:
        """Deduplicate stores by their physical identity."""
        seen = set()
        unique_stores: list[QueryStore] = []
        for store in stores:
            key = (store.database_namespace, store.schema, store.table)
            if key not in seen:
                seen.add(key)
                unique_stores.append(store)
        return tuple(unique_stores)

    async def discover(
        self,
        conn: pg,
        *,
        allowlist: Sequence[str] | None = None,
    ) -> None:
        """Publish compatible stores atomically; scan failure publishes nothing."""
        # Start with empty registry
        self._stores = ()
        self._diagnostics = ()
        self._default_store = None
        # QS_QUERIES_SCHEMA / QS_QUERIES_TABLE name the default store.
        configured_key = (QueryModel.Meta.schema, QueryModel.Meta.name)

        # Get database connection info without secrets
        # Acquired pg wrappers keep their DSN on the owning pool.
        dsn = conn.get_dsn()
        if not dsn and conn.pool() is not None:
            dsn = conn.pool().get_dsn()
        db_info = urlsplit(dsn or "")
        options = parse_qs(db_info.query)
        host = options.get("host", [unquote(db_info.hostname or "localhost")])[-1]
        port = int(options.get("port", [db_info.port or 5432])[-1])
        database = options.get("dbname", [unquote(db_info.path.lstrip("/")) or "querysource"])[-1]
        database_namespace = self._build_database_namespace(host, port, database)
        # Capture the real connection's namespace so resolve()'s
        # legacy-fallback branches never diverge from it.
        self._database_namespace = database_namespace

        # Build allowlist set
        allowlist_set: set[str] | None = None
        if allowlist is not None:
            allowlist_set = set(allowlist)
            # Validate allowlist members
            for item in allowlist:
                if not isinstance(item, str) or not item:
                    raise TenantError(
                        f"Invalid allowlist member: {item!r}",
                        error_code="invalid_tenant",
                    )

        # Tenant tables are named 'queries'; the configured default may not be.
        configured_filter = (
            f"(table_schema = {_sql_literal(configured_key[0])} "
            f"AND table_name = {_sql_literal(configured_key[1])})"
        )
        tables_query = f"""
        SELECT
            table_schema,
            table_name,
            table_type
        FROM information_schema.tables
        WHERE (table_name = 'queries' OR {configured_filter})
        AND table_type = 'BASE TABLE'
        """

        try:
            result, error = await conn.query(tables_query)
        except Exception as exc:  # noqa: BLE001 - re-raised as TenantError below
            logger.error(
                "TenantRegistry.discover: catalog table scan failed: %s", exc
            )
            raise TenantError(
                f"Catalog table scan failed: {exc}",
                error_code="tenant_store_unavailable",
            ) from exc
        if error:
            logger.error(
                "TenantRegistry.discover: catalog table scan returned an error: %s",
                error,
            )
            raise TenantError(
                f"Catalog table scan failed: {error}",
                error_code="tenant_store_unavailable",
            )
        tables = result or []

        if not tables:
            # No queries tables found - no stores to register
            return

        # Read the full shape: program_slug presence sets has_program_slug.
        columns_query = f"""
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE (table_name = 'queries' OR {configured_filter})
        """

        try:
            result, error = await conn.query(columns_query)
        except Exception as exc:  # noqa: BLE001 - re-raised as TenantError below
            logger.error(
                "TenantRegistry.discover: catalog column scan failed: %s", exc
            )
            raise TenantError(
                f"Catalog column scan failed: {exc}",
                error_code="tenant_store_unavailable",
            ) from exc
        if error:
            logger.error(
                "TenantRegistry.discover: catalog column scan returned an error: %s",
                error,
            )
            raise TenantError(
                f"Catalog column scan failed: {error}",
                error_code="tenant_store_unavailable",
            )
        columns = result or []

        # Build a mapping of schema -> table -> columns
        schema_columns: dict[str, dict[str, frozenset[str]]] = {}
        for schema, table, col_name, _data_type, _is_nullable in columns:
            if schema not in schema_columns:
                schema_columns[schema] = {}
            schema_columns[schema][table] = schema_columns[schema].get(table, frozenset()) | {col_name}

        # Process each table
        discovered_stores: list[QueryStore] = []
        diagnostics: list[Mapping[str, Any]] = []

        for schema, table, _table_type in tables:
            is_configured = (schema, table) == configured_key
            if not is_configured and table != "queries":
                continue
            # Skip system schemas
            if self._is_system_schema(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "system schema excluded",
                    }
                )
                continue

            # Skip reserved schemas
            if self._is_reserved_schema(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "reserved schema excluded",
                    }
                )
                continue

            # Skip schemas with slashes
            if self._has_slash(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "schema contains slash",
                    }
                )
                continue

            # Check if table is in allowlist
            if allowlist_set is not None and schema not in allowlist_set:
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "not in allowlist",
                    }
                )
                continue

            # Get columns for this table
            if "query_slug" not in schema_columns.get(schema, {}).get(table, frozenset()):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "no query_slug column found",
                    }
                )
                continue

            columns_set = schema_columns[schema][table]
            store = QueryStore(
                database_namespace=database_namespace,
                schema=schema,
                table=table,
                has_program_slug="program_slug" in columns_set,
                columns=columns_set,
            )
            discovered_stores.append(store)
            if is_configured:
                self._default_store = store

        # Deduplicate stores
        self._stores = self._deduplicate_stores(discovered_stores)
        self._diagnostics = tuple(diagnostics)

        # No owner fallback: if the configured table was not discovered,
        # resolve(None) returns the synthetic configured store, never an
        # arbitrary tenant picked by catalog query result order.

    def _legacy_fallback_store(self) -> QueryStore:
        """Synthetic configured default store for when it was not discovered.

        Uses the REAL database_namespace captured by discover() (host/port/
        database from the actual metadata connection) whenever discovery has
        run at least once, so this fallback's cache-key/job-id digest never
        diverges from the namespace any actually-discovered store on the
        same physical database reports. Only falls back to the symbolic
        ``"localhost:5432/querysource"`` literal when discover() never ran
        at all (e.g. a bare, never-discovered TenantRegistry()).
        """
        return QueryStore(
            database_namespace=self._database_namespace or "localhost:5432/querysource",
            schema=QueryModel.Meta.schema,
            table=QueryModel.Meta.name,
            has_program_slug=True,
            columns=frozenset(["query_slug", "program_slug"]),
        )

    def resolve(self, tenant: str | None = None) -> QueryStore:
        """Resolve exact selector; never fall back from an explicit name."""
        default = self._default_store or self._legacy_fallback_store()
        if tenant is None:
            return default

        # A schema selector addresses that schema's store (its ``queries`` table first).
        matches = [store for store in self._stores if store.schema == tenant]
        if matches:
            return next((store for store in matches if store.table == "queries"), matches[0])

        # The configured default's schema also addresses the default store.
        if tenant == default.schema:
            return default

        # Tenant not found
        raise TenantError(
            f"Tenant not found: {tenant}",
            error_code="tenant_not_available",
        )

    def stores(self) -> tuple[QueryStore, ...]:
        """Return unique eligible physical stores, including configured default."""
        return self._stores

    def diagnostics(self) -> tuple[Mapping[str, Any], ...]:
        """Return administrative discovery reasons without secrets."""
        return self._diagnostics


def _sql_literal(value: str) -> str:
    """Render a catalog-filter string literal with embedded quotes doubled."""
    if "\0" in value:
        raise ValueError(f"Invalid literal: {value!r}")
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    """Quote one validated identifier, preserving case and embedded quotes."""
    registry = TenantRegistry()
    return registry._quote_identifier(value)
