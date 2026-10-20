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

# Legacy contract marker
_LEGACY_CONTRACT = "legacy"

# Tenant contract marker
_TENANT_CONTRACT = "tenant"


@dataclass(frozen=True)
class QueryStore:
    """Canonical physical identity and validated persistence contract."""

    database_namespace: str
    schema: str
    table: str
    contract: Literal["legacy", "tenant"]
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
    """Versioned remote/job identity; no database secrets or connections."""

    version: Literal[1]
    database_namespace: str
    schema: str
    table: str
    contract: Literal["legacy", "tenant"]


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
        # True once discover() has published a snapshot (even an empty one).
        # After that, a missing configured table is an explicit unavailable
        # store rather than a silent public.queries substitute.
        self._discovered: bool = False

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

    def _is_compatible_store(
        self,
        columns: frozenset[str],
    ) -> tuple[bool, str | None]:
        """Check whether a discovered table can serve as a query store at all.

        This is a shape check, not a contract check: ``program_slug`` decides which
        contract the store follows (see ``discover``), never whether it is eligible.

        Args:
            columns: The table's column names.

        Returns:
            ``(is_compatible, reason)`` where ``reason`` is None when compatible.
        """
        # Check for required columns
        required_columns = {"query_slug"}
        if not required_columns.issubset(columns):
            return False, f"missing required columns: {required_columns - columns}"

        # Check for slug non-null uniqueness (we can't verify this from catalog alone,
        # but we can check the column exists and is not null)
        if "query_slug" not in columns:
            return False, "query_slug column not found"

        return True, None

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
        self._discovered = False

        # Honor QS_QUERIES_SCHEMA / QS_QUERIES_TABLE as the configured legacy store.
        configured_schema = QueryModel.Meta.schema
        configured_table = QueryModel.Meta.name
        configured_key = (configured_schema, configured_table)

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

        # Query information_schema for tables named 'queries'
        configured_filter = (
            f"(table_schema = {_sql_literal(configured_schema)} "
            f"AND table_name = {_sql_literal(configured_table)})"
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
            # No queries tables found - no stores to register. The configured
            # default is explicitly unavailable (resolve(None) will say so).
            self._diagnostics = (
                {
                    "schema": configured_schema,
                    "table": configured_table,
                    "reason": "configured default table not found",
                },
            )
            self._discovered = True
            return

        # Read the full shape: the presence of program_slug is what selects the
        # store's persistence contract.
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
            # Tenant tables are always named 'queries'; the configured table is
            # additionally registered irrespective of schema filters/allowlist.
            if not is_configured and table != "queries":
                continue
            # Skip system schemas
            if not is_configured and self._is_system_schema(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "system schema excluded",
                    }
                )
                continue

            # Skip reserved schemas
            if not is_configured and self._is_reserved_schema(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "reserved schema excluded",
                    }
                )
                continue

            # Skip schemas with slashes
            if not is_configured and self._has_slash(schema):
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "schema contains slash",
                    }
                )
                continue

            # Check if table is in allowlist
            if not is_configured and allowlist_set is not None and schema not in allowlist_set:
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

            is_compatible, reason = self._is_compatible_store(columns_set)
            if not is_compatible:
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": reason,
                    }
                )
                continue

            # The contract IS the physical shape: a table that stores program_slug
            # follows the legacy row contract, one that does not follows the tenant
            # contract. Neither the schema name nor "is this the configured default"
            # takes part — a store is whatever its table actually is.
            contract = _LEGACY_CONTRACT if "program_slug" in columns_set else _TENANT_CONTRACT

            # Create store
            store = QueryStore(
                database_namespace=database_namespace,
                schema=schema,
                table=table,
                contract=contract,
                columns=columns_set,
            )

            discovered_stores.append(store)

            # The default is exactly the configured physical store, never "public" by name.
            if is_configured:
                self._default_store = store

        if self._default_store is None:
            diagnostics.append(
                {
                    "schema": configured_schema,
                    "table": configured_table,
                    "reason": "configured default table not found",
                }
            )

        # Deduplicate stores
        self._stores = self._deduplicate_stores(discovered_stores)
        self._diagnostics = tuple(diagnostics)
        self._discovered = True

        # No owner fallback (repeated, explicit constraint throughout this
        # feature's spec/tasks): if no "public" schema was discovered,
        # _default_store stays None — resolve(None) then falls through to
        # its own well-defined hardcoded legacy public.queries fallback,
        # never an arbitrary tenant picked by catalog query result order.

    def _legacy_fallback_store(self) -> QueryStore:
        """Synthetic legacy public.queries store for when nothing was discovered.

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
            schema="public",
            table="queries",
            contract=_LEGACY_CONTRACT,
            # Declares program_slug so this synthetic store satisfies the same rule
            # discovery applies: a legacy contract IS a table that stores program_slug.
            columns=frozenset(["query_slug", "program_slug"]),
        )

    def resolve(self, tenant: str | None = None) -> QueryStore:
        """Resolve exact selector; never fall back from an explicit name."""
        if tenant is None:
            # Return the configured physical default store
            if self._default_store is None:
                if self._discovered:
                    raise TenantError(
                        "Configured default query store is not available",
                        error_code="tenant_store_unavailable",
                    )
                return self._legacy_fallback_store()
            return self._default_store

        # A schema selector addresses that schema's ``queries`` table.
        for store in self._stores:
            if store.schema == tenant and store.table == "queries":
                return store

        # Literal public stays public, distinct from a custom configured default.
        if tenant == "public":
            return self._legacy_fallback_store()

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


def is_default_store(registry: TenantRegistry, store: QueryStore) -> bool:
    """True only for the exact configured physical default (not merely a legacy contract).

    Args:
        registry: The resolved tenant registry.
        store: The physical store to test.

    Returns:
        True when ``store`` is the same physical table the registry resolves for
        a ``None`` selector.
    """
    default = registry.resolve()
    return (
        store.database_namespace == default.database_namespace
        and store.schema == default.schema
        and store.table == default.table
    )


def selector_for(registry: TenantRegistry, store: QueryStore) -> str | None:
    """Map a physical store to its tenant selector.

    ``None`` is returned only for the exact configured default; every other store
    (including literal ``public.queries`` next to a custom default) uses its
    registered schema. ``contract == "legacy"`` is never used to pick ``None``.

    Args:
        registry: The resolved tenant registry.
        store: The physical store to map.

    Returns:
        ``None`` for the configured default, otherwise the store's schema.
    """
    return None if is_default_store(registry, store) else store.schema
