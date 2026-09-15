"""Immutable store identities and catalog discovery for per-tenant queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, Tuple

from asyncdb.drivers.pg import pg
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError

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

    rows: Tuple[Mapping[str, Any], ...]
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
        self._stores: Tuple[QueryStore, ...] = ()
        self._diagnostics: Tuple[Mapping[str, Any], ...] = ()
        self._default_store: QueryStore | None = None

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

    def _deduplicate_stores(self, stores: Sequence[QueryStore]) -> Tuple[QueryStore, ...]:
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
        has_program_slug: bool,
    ) -> Tuple[bool, str | None]:
        """Check if a store is compatible with the tenant contract."""
        # Check for program_slug - incompatible with tenant contract
        if has_program_slug:
            return False, "contains program_slug column (incompatible with tenant contract)"

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

        # Get database connection info without secrets
        db_info = conn.config
        host = db_info.get("host", "localhost")
        port = db_info.get("port", 5432)
        database = db_info.get("database", "querysource")
        database_namespace = self._build_database_namespace(host, port, database)

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
        tables_query = """
        SELECT
            table_schema,
            table_name,
            table_type
        FROM information_schema.tables
        WHERE table_name = 'queries'
        AND table_type = 'BASE TABLE'
        """

        async with conn.cursor() as cur:
            await cur.execute(tables_query)
            tables = await cur.fetchall()

        if not tables:
            # No queries tables found - no stores to register
            return

        # Query information_schema.columns for query_slug column
        columns_query = """
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE table_name = 'queries'
        AND column_name = 'query_slug'
        """

        async with conn.cursor() as cur:
            await cur.execute(columns_query)
            columns = await cur.fetchall()

        # Build a mapping of schema -> table -> columns
        schema_columns: dict[str, dict[str, frozenset[str]]] = {}
        for schema, table, col_name, data_type, is_nullable in columns:
            if schema not in schema_columns:
                schema_columns[schema] = {}
            schema_columns[schema][table] = schema_columns[schema].get(table, frozenset()) | {col_name}

        # Process each table
        discovered_stores: list[QueryStore] = []
        diagnostics: list[Mapping[str, Any]] = []

        for schema, table, table_type in tables:
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
            if schema not in schema_columns or table not in schema_columns[schema]:
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": "no query_slug column found",
                    }
                )
                continue

            columns_set = schema_columns[schema][table]

            # Check for program_slug column
            has_program_slug = "program_slug" in columns_set

            # Check compatibility
            is_compatible, reason = self._is_compatible_store(columns_set, has_program_slug)
            if not is_compatible:
                diagnostics.append(
                    {
                        "schema": schema,
                        "table": table,
                        "reason": reason,
                    }
                )
                continue

            # Determine contract
            contract = _LEGACY_CONTRACT if has_program_slug else _TENANT_CONTRACT

            # Create store
            store = QueryStore(
                database_namespace=database_namespace,
                schema=schema,
                table=table,
                contract=contract,
                columns=columns_set,
            )

            discovered_stores.append(store)

            # Track default store (public schema or configured default)
            if schema == "public":
                self._default_store = store

        # Deduplicate stores
        self._stores = self._deduplicate_stores(discovered_stores)
        self._diagnostics = tuple(diagnostics)

        # If no default store found, use the first discovered store
        if self._default_store is None and self._stores:
            self._default_store = self._stores[0]

    def resolve(self, tenant: str | None = None) -> QueryStore:
        """Resolve exact selector; never fall back from an explicit name."""
        if tenant is None:
            # Return configured default store
            if self._default_store is None:
                # Fallback to legacy public.queries
                return QueryStore(
                    database_namespace="localhost:5432/querysource",
                    schema="public",
                    table="queries",
                    contract=_LEGACY_CONTRACT,
                    columns=frozenset(["query_slug"]),
                )
            return self._default_store

        # Check if tenant is in allowlist
        for store in self._stores:
            if store.schema == tenant:
                return store

        # Check if tenant is public (literal)
        if tenant == "public":
            if self._default_store is not None and self._default_store.schema == "public":
                return self._default_store
            # Return legacy public.queries
            return QueryStore(
                database_namespace="localhost:5432/querysource",
                schema="public",
                table="queries",
                contract=_LEGACY_CONTRACT,
                columns=frozenset(["query_slug"]),
            )

        # Tenant not found
        raise TenantError(
            f"Tenant not found: {tenant}",
            error_code="tenant_not_available",
        )

    def stores(self) -> Tuple[QueryStore, ...]:
        """Return unique eligible physical stores, including configured default."""
        return self._stores

    def diagnostics(self) -> Tuple[Mapping[str, Any], ...]:
        """Return administrative discovery reasons without secrets."""
        return self._diagnostics


def quote_identifier(value: str) -> str:
    """Quote one validated identifier, preserving case and embedded quotes."""
    registry = TenantRegistry()
    return registry._quote_identifier(value)
