"""Saved-definition persistence: sole storage boundary for tenant/legacy rows.

Every saved-definition SQL read goes through :class:`DefinitionRepository`.
Mutation (create/upsert/patch/delete) is implemented in TASK-719.

Note: this module intentionally does NOT use ``from __future__ import
annotations`` nor ``X | None``/``list[X]`` union syntax. See
``querysource/tenant_models.py`` for the verified reason: this project's
Cython ``datamodel`` validator breaks on both when constructing
``TenantQueryDefinition`` instances.
"""
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from asyncdb.drivers.pg import UndefinedTableError, pg

from querysource.cache_identity import definition_revision
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError
from querysource.tenant_models import TenantQueryDefinition
from querysource.tenants import (
    DefinitionPage,
    LoadedDefinition,
    QueryIdentity,
    QueryStore,
    TenantRegistry,
    quote_identifier,
)
from querysource.types.validators import Entity

# Retain current list defaults/limits (matches querysource/handlers/_pagination.py).
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200
_DEFAULT_SORT_FIELD = "updated_at"

# Every persistence column a tenant-contract store may project/filter/sort on.
# program_slug is never a member here: a tenant-contract store never has that
# column (see querysource/tenants.py TenantRegistry._is_compatible_store),
# and TenantQueryDefinition never declares it either.
_TENANT_COLUMNS: frozenset = frozenset(
    TenantQueryDefinition(query_slug="__probe__").columns().keys()
)

# Eligibility predicate preserved verbatim from the scheduler's existing
# hardcoded public.queries startup query (querysource/scheduler/scheduler.py,
# QSScheduler.startup), qualified to an arbitrary store instead of
# hardcoded public.queries.
_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"
_SCHEDULABLE_PREDICATE = (
    "(attributes IS NOT NULL AND attributes != '{}') "
    "OR (cache_options IS NOT NULL AND cache_options != '{}')"
)


class DefinitionRepository:
    """Use qualified SQL and per-call connections for every definition operation."""

    def __init__(
        self,
        registry: TenantRegistry,
        connection_factory: Callable[[], Awaitable[AbstractAsyncContextManager[pg]]],
    ) -> None:
        """Accept a loop-local connection factory, never a shared checked-out connection."""
        self.registry = registry
        self.connection_factory = connection_factory

    # -- identifiers / column policy -------------------------------------------------

    def _qualified_table(self, store: QueryStore) -> str:
        """Render the schema-qualified, quoted table name for this store."""
        return f"{quote_identifier(store.schema)}.{quote_identifier(store.table)}"

    def _allowed_columns(self, store: QueryStore) -> frozenset:
        """Return the projection/filter/sort allowlist for this store's contract.

        Tenant-contract stores are restricted to TenantQueryDefinition's
        fields (program_slug is never a member — AC-4 "Reject tenant
        program_slug requests"). Legacy-contract stores keep the full,
        existing QueryModel column set.
        """
        if store.contract == "tenant":
            return _TENANT_COLUMNS
        return frozenset(QueryModel(query_slug="__probe__", program_slug="__probe__").columns().keys())

    # -- row <-> model adaptation ------------------------------------------------

    def _row_to_persisted(self, row: Mapping[str, Any], store: QueryStore) -> tuple[dict, str | None]:
        """Validate a persisted row with TenantQueryDefinition.

        Returns the validated persisted fields (never including
        program_slug) plus, for a legacy-contract store only, the legacy
        stored program_slug value (kept as-is, never derived).

        ``TenantQueryDefinition.updated_at`` uses ``encoder=rigth_now``
        (matching ``QueryModel``), which unconditionally rewrites the
        field to ``datetime.now()`` at construction time — a write-time
        behavior, not appropriate for reading back what is actually
        persisted. AC-3 requires hashing "before runtime mutation", so the
        raw, as-stored ``updated_at`` is restored after validation; every
        other field has no encoder and round-trips unchanged.
        """
        data = dict(row)
        legacy_program_slug = data.pop("program_slug", None) if store.contract == "legacy" else None
        validated = TenantQueryDefinition(**data)
        persisted = validated.to_dict()
        if "updated_at" in data:
            persisted["updated_at"] = data["updated_at"]
        return persisted, legacy_program_slug

    def _runtime_model(self, persisted: dict, store: QueryStore, legacy_program_slug: str | None) -> QueryModel:
        """Construct a fresh, detached runtime QueryModel for provider/parser use.

        Tenant program_slug is always derived from the store's schema
        (structural ownership); legacy program_slug retains its stored
        value (AC-2).
        """
        program_slug = legacy_program_slug if store.contract == "legacy" else store.schema
        return QueryModel(**persisted, program_slug=program_slug)

    async def _fetch_row(self, store: QueryStore, slug: str) -> Mapping[str, Any] | None:
        """Read exactly one persisted row for this store/slug, or None."""
        table = self._qualified_table(store)
        sql = f"SELECT * FROM {table} WHERE query_slug = $1 LIMIT 1"
        async with await self.connection_factory() as conn:
            return await conn.fetch_one(sql, slug)

    def _translate_write_error(self, err: Exception, store: QueryStore) -> None:
        """Translate a mutation failure to the correct TenantError and raise it.

        AC-4: "Translate write grant failures to tenant_write_forbidden and
        runtime table loss to tenant_store_unavailable; do not turn
        validation or missing-row errors into fallback queries." Runtime
        table loss uses the driver's typed ``UndefinedTableError``
        (verified: ``asyncdb.drivers.pg.UndefinedTableError``); no typed
        exception is re-exported by the driver for a permission failure, so
        that branch matches the underlying error text — the same
        constraint the driver itself is subject to. Never swallows an
        unrelated error: anything that matches neither case re-raises.
        """
        if isinstance(err, UndefinedTableError):
            raise TenantError(
                f"Store table not available: {store.schema}.{store.table}",
                error_code="tenant_store_unavailable",
            ) from err
        error_msg = str(err).lower()
        if "permission denied" in error_msg or "must be owner" in error_msg:
            raise TenantError(
                f"Write permission denied for store {store.schema}",
                error_code="tenant_write_forbidden",
            ) from err
        if "relation" in error_msg and "does not exist" in error_msg:
            raise TenantError(
                f"Store table not available: {store.schema}.{store.table}",
                error_code="tenant_store_unavailable",
            ) from err
        raise err

    # -- public API ----------------------------------------------------------------

    async def get(self, identity: QueryIdentity) -> LoadedDefinition:
        """Read current persisted row and return detached runtime model plus revision."""
        store = identity.store
        row = await self._fetch_row(store, identity.slug)
        if row is None:
            raise TenantError(
                f"Query not found: {identity.slug!r}",
                error_code="query_not_found",
            )
        persisted, legacy_program_slug = self._row_to_persisted(row, store)
        revision = definition_revision(persisted)
        runtime = self._runtime_model(persisted, store, legacy_program_slug)
        return LoadedDefinition(identity=identity, runtime=runtime, revision=revision)

    async def list(self, store: QueryStore, params: Mapping[str, Any]) -> DefinitionPage:
        """Validate owner-specific projections/filter/sort; bind page/count values."""
        allowed = self._allowed_columns(store)
        table = self._qualified_table(store)

        page = max(int(params.get("page", 1)), 1)
        page_size = min(max(int(params.get("page_size", _DEFAULT_PAGE_SIZE)), 1), _MAX_PAGE_SIZE)
        sort_field = params.get("sort_field", _DEFAULT_SORT_FIELD)
        sort_direction = "DESC" if str(params.get("sort_direction", "desc")).lower() == "desc" else "ASC"

        requested_fields = params.get("fields")
        filters = dict(params.get("filters") or {})

        if sort_field == "program_slug" or "program_slug" in filters or (
            requested_fields is not None and "program_slug" in requested_fields
        ):
            raise TenantError(
                "program_slug is not a queryable field",
                error_code="invalid_tenant",
            )
        if sort_field not in allowed:
            raise TenantError(
                f"Invalid sort field: {sort_field!r}",
                error_code="invalid_tenant",
            )
        rejected = set(filters) - allowed
        if rejected:
            raise TenantError(
                f"Invalid filter field(s): {sorted(rejected)!r}",
                error_code="invalid_tenant",
            )
        if requested_fields is not None:
            rejected_fields = set(requested_fields) - allowed
            if rejected_fields:
                raise TenantError(
                    f"Invalid projection field(s): {sorted(rejected_fields)!r}",
                    error_code="invalid_tenant",
                )

        where_clauses = []
        bind_values: list = []
        for column, value in filters.items():
            bind_values.append(value)
            where_clauses.append(f"{quote_identifier(column)} = ${len(bind_values)}")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        async with await self.connection_factory() as conn:
            count_sql = f"SELECT COUNT(*) FROM {table} {where_sql}"
            total = await conn.fetchval(count_sql, *bind_values, column=0)

            offset = (page - 1) * page_size
            page_bind_values = list(bind_values) + [page_size, offset]
            limit_idx = len(bind_values) + 1
            offset_idx = len(bind_values) + 2
            page_sql = (
                f"SELECT * FROM {table} {where_sql} "
                f"ORDER BY {quote_identifier(sort_field)} {sort_direction} "
                f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
            )
            rows = await conn.fetch_all(page_sql, *page_bind_values)

        persisted_rows = tuple(self._row_to_persisted(row, store)[0] for row in (rows or []))
        return DefinitionPage(rows=persisted_rows, total=int(total or 0))

    def schema(self, store: QueryStore) -> Mapping[str, Any]:
        """Return persistence metadata, excluding tenant runtime-only program_slug."""
        return TenantQueryDefinition(query_slug="__probe__").schema(as_dict=True)

    async def export_insert(self, identity: QueryIdentity) -> str:
        """Render safely escaped SQL for this store using persisted fields only.

        Mirrors the existing ``QueryManager.get_query_insert`` pattern
        (``querysource/handlers/manager.py:47``): ``Entity.toSQL`` per
        column, then ``Entity.quoteString`` — the same SQL-safety helpers
        used elsewhere in the codebase. Never writes to the database.
        """
        store = identity.store
        row = await self._fetch_row(store, identity.slug)
        if row is None:
            raise TenantError(
                f"Query not found: {identity.slug!r}",
                error_code="query_not_found",
            )
        data = dict(row)
        data.pop("program_slug", None)
        validated = TenantQueryDefinition(**data)

        table = self._qualified_table(store)
        columns: list = []
        values: list = []
        for name, field in validated.columns().items():
            val = getattr(validated, field.name)
            field_type = field.type
            try:
                db_type = field.db_type()
            except Exception:  # noqa: BLE001 - mirror get_query_insert's defensive fallback
                db_type = None
            sql_val = Entity.toSQL(val, field_type, dbtype=db_type)
            columns.append(name)
            values.append(Entity.quoteString(str(sql_val), no_dblquoting=False))

        return "INSERT INTO {table} ({columns}) VALUES ({values});".format(
            table=table,
            columns=", ".join(columns),
            values=", ".join(values),
        )

    async def schedulable(self, store: QueryStore) -> tuple[Mapping[str, Any], ...]:
        """Return scheduler candidates from one store using existing eligibility rules."""
        table = self._qualified_table(store)
        sql = f"SELECT {_SCHEDULABLE_COLUMNS} FROM {table} WHERE {_SCHEDULABLE_PREDICATE}"
        async with await self.connection_factory() as conn:
            rows = await conn.fetch_all(sql)
        return tuple(dict(row) for row in (rows or []))

    # -- mutation methods ----------------------------------------------------------

    async def create(self, store: QueryStore, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate and insert persisted fields, retaining database constraints.

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        """
        # Validate input data with TenantQueryDefinition (rejects program_slug)
        validated = TenantQueryDefinition(**data)
        persisted = validated.to_dict()

        # Remove None values to let database defaults apply
        persisted = {k: v for k, v in persisted.items() if v is not None}
        # FEAT-151: an empty declaration never requires the migrated column.
        if not persisted.get("columns_definition"):
            persisted.pop("columns_definition", None)

        table = self._qualified_table(store)
        columns = list(persisted.keys())
        placeholders = [f"${i + 1}" for i in range(len(columns))]
        values = list(persisted.values())

        sql = f"INSERT INTO {table} ({', '.join(quote_identifier(c) for c in columns)}) VALUES ({', '.join(placeholders)}) RETURNING *"

        try:
            async with await self.connection_factory() as conn:
                row = await conn.fetch_one(sql, *values)
        except Exception as exc:
            self._translate_write_error(exc, store)
            raise

        if row is None:
            raise TenantError(
                "Failed to create query definition",
                error_code="tenant_store_unavailable",
            )

        persisted_result, _ = self._row_to_persisted(dict(row), store)
        return persisted_result

    async def upsert(
        self, identity: QueryIdentity, data: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], bool]:
        """Atomically upsert and return persisted row with created flag.

        Uses PostgreSQL ON CONFLICT for atomicity. The created flag accurately
        reflects whether a new row was inserted (True) or an existing row was
        updated (False).

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        """
        store = identity.store

        # Validate input data with TenantQueryDefinition (rejects program_slug)
        validated = TenantQueryDefinition(**data)
        persisted = validated.to_dict()

        # Remove None values to let database defaults apply
        persisted = {k: v for k, v in persisted.items() if v is not None}
        # FEAT-151: an empty declaration never requires the migrated column.
        if not persisted.get("columns_definition"):
            persisted.pop("columns_definition", None)

        table = self._qualified_table(store)
        columns = list(persisted.keys())
        placeholders = [f"${i + 1}" for i in range(len(columns))]
        values = list(persisted.values())

        # Build ON CONFLICT DO UPDATE using query_slug as the primary key
        update_clauses = []
        for col in columns:
            if col != "query_slug":
                update_clauses.append(f"{quote_identifier(col)} = EXCLUDED.{quote_identifier(col)}")

        # Add updated_at timestamp for the update case
        update_clauses.append(f"{quote_identifier('updated_at')} = now()")

        sql = f"""
            INSERT INTO {table} ({', '.join(quote_identifier(c) for c in columns)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT (query_slug) DO UPDATE SET {', '.join(update_clauses)}
            RETURNING *, (xmax = 0) AS is_inserted
        """

        try:
            async with await self.connection_factory() as conn:
                row = await conn.fetch_one(sql, *values)
        except Exception as exc:
            self._translate_write_error(exc, store)
            raise

        if row is None:
            raise TenantError(
                "Failed to upsert query definition",
                error_code="tenant_store_unavailable",
            )

        # PostgreSQL returns (xmax = 0) as True for inserted, False for updated
        is_created = bool(row.get("is_inserted", False))

        # Remove the is_inserted pseudo-column before validation
        row_data = dict(row)
        row_data.pop("is_inserted", None)

        persisted_result, _ = self._row_to_persisted(row_data, store)
        return persisted_result, is_created

    async def patch(
        self, identity: QueryIdentity, data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Update supplied mutable fields only; owner and slug cannot change.

        Null values in data are applied explicitly (different from omitted fields).
        Rejects any attempt to change query_slug or add program_slug.

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        Raises TenantError(query_not_found) if the row doesn't exist.
        """
        store = identity.store

        # Reject attempts to change the identity
        if "query_slug" in data and data["query_slug"] != identity.slug:
            raise TenantError(
                "Cannot change query_slug via PATCH",
                error_code="invalid_tenant",
            )

        # Reject program_slug (never allowed for tenants)
        if "program_slug" in data:
            raise TenantError(
                "program_slug cannot be modified",
                error_code="invalid_tenant",
            )

        # Build the SET clause with only the provided fields
        set_clauses = []
        values = []
        for key, value in data.items():
            # Skip query_slug (identity) and program_slug (forbidden)
            if key in ("query_slug", "program_slug"):
                continue
            set_clauses.append(f"{quote_identifier(key)} = ${len(values) + 1}")
            values.append(value)

        if not set_clauses:
            # Nothing to update - just return the current row
            row = await self._fetch_row(store, identity.slug)
            if row is None:
                raise TenantError(
                    f"Query not found: {identity.slug!r}",
                    error_code="query_not_found",
                )
            persisted_result, _ = self._row_to_persisted(dict(row), store)
            return persisted_result

        # Add updated_at timestamp
        set_clauses.append(f"{quote_identifier('updated_at')} = now()")

        table = self._qualified_table(store)
        sql = f"""
            UPDATE {table}
            SET {', '.join(set_clauses)}
            WHERE query_slug = ${len(values) + 1}
            RETURNING *
        """
        values.append(identity.slug)

        try:
            async with await self.connection_factory() as conn:
                row = await conn.fetch_one(sql, *values)
        except Exception as exc:
            self._translate_write_error(exc, store)
            raise

        if row is None:
            raise TenantError(
                f"Query not found: {identity.slug!r}",
                error_code="query_not_found",
            )

        persisted_result, _ = self._row_to_persisted(dict(row), store)
        return persisted_result

    async def delete(self, identity: QueryIdentity) -> bool:
        """Delete exactly this owner's row; report missing without fallback.

        Returns True if the row was deleted, raises TenantError(query_not_found)
        if the row doesn't exist.

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        """
        store = identity.store
        table = self._qualified_table(store)
        sql = f"DELETE FROM {table} WHERE query_slug = $1 RETURNING query_slug"

        try:
            async with await self.connection_factory() as conn:
                row = await conn.fetch_one(sql, identity.slug)
        except Exception as exc:
            self._translate_write_error(exc, store)
            raise

        if row is None:
            raise TenantError(
                f"Query not found: {identity.slug!r}",
                error_code="query_not_found",
            )

        return True
