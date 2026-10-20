"""Saved-definition persistence: sole storage boundary for tenant/legacy rows.

Every saved-definition SQL read goes through :class:`DefinitionRepository`.
Mutation (create/upsert/patch/delete) is implemented in TASK-719.

Note: this module intentionally does NOT use ``from __future__ import
annotations`` nor ``X | None``/``list[X]`` union syntax. See
``querysource/tenant_models.py`` for the verified reason: this project's
Cython ``datamodel`` validator breaks on both when constructing
``TenantQueryDefinition`` instances.
"""
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from asyncdb.drivers.pg import UndefinedColumnError, UndefinedTableError, pg

from querysource.cache_identity import definition_revision
from querysource.models import QueryModel
from querysource.query_moves import analyze_references
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
# Established QueryModel default, applied only when a program_slug row is inserted.
_DEFAULT_PROGRAM = "default"

# Every persistence column a store without program_slug may project/filter/sort on.
# program_slug is never a member here: TenantQueryDefinition never declares it.
_TENANT_COLUMNS: frozenset = frozenset(
    TenantQueryDefinition(query_slug="__probe__").columns().keys()
)

# Eligibility predicate preserved verbatim from the scheduler's existing
# hardcoded public.queries startup query (querysource/scheduler/scheduler.py,
# QSScheduler.startup), qualified to an arbitrary store instead of
# hardcoded public.queries.
RUN_AS_COLUMN: str = "scheduler_run_as_user_id"
_RUN_AS_FALLBACK_WARNED: set = set()
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunAsChange:
    """One run-as transition recorded in the append-only audit table."""

    query_slug: str
    old_user_id: int | None
    new_user_id: int | None
    operation: str  # "set" | "change" | "clear"


def _scheduler_of(attributes: Any) -> Any:
    """Return the ``scheduler`` entry of an attributes value (dict or JSON text)."""
    if isinstance(attributes, (str, bytes)):
        try:
            attributes = json.loads(attributes)
        except ValueError:
            return None
    if not isinstance(attributes, Mapping):
        return None
    return attributes.get("scheduler")


def _is_missing_run_as_column(exc: Exception) -> bool:
    """Return True when exc means the store has no run-as column (not migrated)."""
    if isinstance(exc, UndefinedColumnError):
        return True
    msg = str(exc).lower()
    return RUN_AS_COLUMN in msg and "does not exist" in msg


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
        """Return the projection/filter/sort allowlist for this store's shape.

        Stores without program_slug are restricted to TenantQueryDefinition's
        fields; stores with program_slug keep the full QueryModel column set.
        """
        if not store.has_program_slug:
            return _TENANT_COLUMNS
        return frozenset(QueryModel(query_slug="__probe__", program_slug="__probe__").columns().keys())

    # -- row <-> model adaptation ------------------------------------------------

    def _row_to_persisted(self, row: Mapping[str, Any], store: QueryStore) -> tuple[dict, str | None]:
        """Validate a persisted row with TenantQueryDefinition.

        Returns the validated persisted fields (never including
        program_slug) plus, for a store with program_slug only, the
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
        # FEAT-159: repository-only column; never enters models, revisions or API output.
        data.pop(RUN_AS_COLUMN, None)
        legacy_program_slug = data.pop("program_slug", None) if store.has_program_slug else None
        validated = TenantQueryDefinition(**data)
        persisted = validated.to_dict()
        if "updated_at" in data:
            persisted["updated_at"] = data["updated_at"]
        return persisted, legacy_program_slug

    def _project(self, row: Mapping[str, Any], store: QueryStore) -> dict:
        """Return the persisted projection, keeping a stored program_slug when the store has one."""
        persisted, legacy_program_slug = self._row_to_persisted(row, store)
        if store.has_program_slug and legacy_program_slug is not None:
            persisted["program_slug"] = legacy_program_slug
        return persisted

    def _runtime_model(self, persisted: dict, store: QueryStore, legacy_program_slug: str | None) -> QueryModel:
        """Construct a fresh, detached runtime QueryModel for provider/parser use.

        Without a program_slug column it is derived from the store's schema
        (structural ownership); otherwise the stored value is kept (AC-2).
        """
        program_slug = legacy_program_slug if store.has_program_slug else store.schema
        return QueryModel(**persisted, program_slug=program_slug)

    async def _fetch_row(self, store: QueryStore, slug: str) -> Mapping[str, Any] | None:
        """Read exactly one persisted row for this store/slug, or None."""
        table = self._qualified_table(store)
        sql = f"SELECT * FROM {table} WHERE query_slug = $1 LIMIT 1"
        async with await self.connection_factory() as conn:
            return await conn.fetch_one(sql, slug)

    async def exists(self, store: QueryStore, slug: str) -> bool:
        """Return True when this store has a row for ``slug`` (cheap ``SELECT 1``)."""
        sql = f"SELECT 1 AS present FROM {self._qualified_table(store)} WHERE query_slug = $1"
        async with await self.connection_factory() as conn:
            return await conn.fetch_one(sql, slug) is not None

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
        # A stored program_slug changes query meaning, so it is part of the revision.
        if store.has_program_slug:
            revision = definition_revision({**persisted, "program_slug": legacy_program_slug})
        else:
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

        persisted_rows = tuple(self._project(row, store) for row in (rows or []))
        return DefinitionPage(rows=persisted_rows, total=int(total or 0))

    def schema(self, store: QueryStore) -> Mapping[str, Any]:
        """Return persistence metadata; program_slug only when the store persists it."""
        if store.has_program_slug:
            return QueryModel.schema(as_dict=True)
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
        data.pop(RUN_AS_COLUMN, None)
        if store.has_program_slug:
            validated: Any = QueryModel(**data)
        else:
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
        sql = f"SELECT {_SCHEDULABLE_COLUMNS}, {RUN_AS_COLUMN} FROM {table} WHERE {_SCHEDULABLE_PREDICATE}"
        try:
            async with await self.connection_factory() as conn:
                rows = await conn.fetch_all(sql)
        except Exception as exc:
            if not _is_missing_run_as_column(exc):
                raise
            key = (store.schema, store.table)
            if key not in _RUN_AS_FALLBACK_WARNED:
                _RUN_AS_FALLBACK_WARNED.add(key)
                _logger.warning(
                    "Store %s.%s has no %s column; run migration to enable run-as",
                    store.schema, store.table, RUN_AS_COLUMN,
                )
            sql = (
                f"SELECT {_SCHEDULABLE_COLUMNS}, NULL AS {RUN_AS_COLUMN} "
                f"FROM {table} WHERE {_SCHEDULABLE_PREDICATE}"
            )
            async with await self.connection_factory() as conn:
                rows = await conn.fetch_all(sql)
        return tuple(dict(row) for row in (rows or []))

    # -- mutation methods ----------------------------------------------------------

    @staticmethod
    def _program_slug_of(data: Mapping[str, Any]) -> str | None:
        """Return a validated ``program_slug`` from a payload, or None if omitted."""
        if "program_slug" not in data:
            return None
        value = data["program_slug"]
        if not isinstance(value, str) or not value:
            raise TenantError("program_slug must be a non-empty string", error_code="invalid_tenant")
        return value

    def _validate_write(self, store: QueryStore, data: Mapping[str, Any]) -> tuple[dict, str | None]:
        """Validate a create/upsert payload against the store's shape.

        Stores without program_slug reject it before any SQL. Stores with it
        validate the shared fields and return the payload's program_slug
        separately (None = omitted: preserved on conflict, defaulted on insert).
        """
        payload = dict(data)
        if not store.has_program_slug:
            if "program_slug" in payload:
                raise TenantError("program_slug cannot be set on this store", error_code="invalid_tenant")
            program_slug = None
        else:
            program_slug = self._program_slug_of(payload)
            payload.pop("program_slug", None)
        persisted = TenantQueryDefinition(**payload).to_dict()
        # Remove None values to let database defaults apply
        persisted = {k: v for k, v in persisted.items() if v is not None}
        # FEAT-151: an empty declaration never requires the migrated column.
        if not persisted.get("columns_definition"):
            persisted.pop("columns_definition", None)
        if store.has_program_slug:
            persisted["program_slug"] = program_slug or _DEFAULT_PROGRAM
        return persisted, program_slug

    async def create(self, store: QueryStore, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate and insert persisted fields, retaining database constraints.

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        """
        self._reject_run_as(data)
        persisted, _ = self._validate_write(store, data)

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

        return self._project(dict(row), store)

    async def upsert(
        self,
        identity: QueryIdentity,
        data: Mapping[str, Any],
        *,
        run_as_actor: int | None = None,
        request_info: Mapping[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], bool]:
        """Atomically upsert and return persisted row with created flag.

        Uses PostgreSQL ON CONFLICT for atomicity. The created flag accurately
        reflects whether a new row was inserted (True) or an existing row was
        updated (False).

        Raises TenantError(tenant_write_forbidden) on permission failure.
        Raises TenantError(tenant_store_unavailable) if the store table is missing.
        """
        store = identity.store
        self._reject_run_as(data)
        persisted, program_slug = self._validate_write(store, data)

        table = self._qualified_table(store)
        columns = list(persisted.keys())
        placeholders = [f"${i + 1}" for i in range(len(columns))]
        values = list(persisted.values())

        # Build ON CONFLICT DO UPDATE using query_slug as the primary key
        update_clauses = []
        for col in columns:
            if col == "program_slug" and program_slug is None:
                continue  # omitted: keep the stored value on conflict
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
            row = await self._write_with_run_as(
                store, identity.slug, sql, values, run_as_actor, request_info
            )
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

        return self._project(row_data, store), is_created

    async def patch(
        self,
        identity: QueryIdentity,
        data: Mapping[str, Any],
        *,
        run_as_actor: int | None = None,
        request_info: Mapping[str, Any] | None = None,
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

        self._reject_run_as(data)

        # program_slug is runtime-derived on stores without the column.
        if not store.has_program_slug and "program_slug" in data:
            raise TenantError(
                "program_slug cannot be modified",
                error_code="invalid_tenant",
            )
        if store.has_program_slug:
            self._program_slug_of(data)
        unknown = set(data) - self._allowed_columns(store)
        if unknown:
            raise TenantError(
                f"Unknown or immutable field(s): {sorted(unknown)!r}",
                error_code="invalid_tenant",
            )

        # Build the SET clause with only the provided fields
        set_clauses = []
        values = []
        for key, value in data.items():
            # Skip identity and the server-managed timestamp
            if key in ("query_slug", "updated_at"):
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
            return self._project(dict(row), store)

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
            row = await self._write_with_run_as(
                store, identity.slug, sql, values, run_as_actor, request_info
            )
        except Exception as exc:
            self._translate_write_error(exc, store)
            raise

        if row is None:
            raise TenantError(
                f"Query not found: {identity.slug!r}",
                error_code="query_not_found",
            )

        return self._project(dict(row), store)

    @staticmethod
    def _reject_run_as(data: Mapping[str, Any]) -> None:
        """Refuse API payloads that try to set the run-as user (G6)."""
        if RUN_AS_COLUMN in data:
            raise TenantError(
                f"{RUN_AS_COLUMN} cannot be set via the API",
                error_code="invalid_tenant",
            )

    async def get_run_as(self, identity: QueryIdentity) -> int | None:
        """Return the stored run-as user id.

        Returns None when unset, when the row is missing, or when the column
        is absent (store not migrated).
        """
        table = self._qualified_table(identity.store)
        sql = f"SELECT {RUN_AS_COLUMN} FROM {table} WHERE query_slug = $1"
        try:
            async with await self.connection_factory() as conn:
                row = await conn.fetch_one(sql, identity.slug)
        except Exception as exc:
            if _is_missing_run_as_column(exc):
                return None
            raise
        if not row:
            return None
        value = row[RUN_AS_COLUMN]
        return None if value is None else int(value)

    def _run_as_audit_table(self, store: QueryStore) -> str:
        """Return the quoted ``{schema}.{table}_run_as_audit`` name for this store."""
        return f"{quote_identifier(store.schema)}.{quote_identifier(f'{store.table}_run_as_audit')}"

    async def _write_with_run_as(
        self,
        store: QueryStore,
        slug: str,
        sql: str,
        values: list,
        actor: int | None,
        request_info: Mapping[str, Any] | None,
    ) -> Mapping[str, Any] | None:
        """Run a definition write plus any run-as/audit change in one transaction.

        Locks and reads the previous ``attributes``, performs the write, applies
        the run-as change from the RETURNING row and commits; any error rolls
        the whole transaction back and is re-raised.
        """
        table = self._qualified_table(store)
        async with await self.connection_factory() as conn:
            # Fail closed: without real transaction support the row lock, SAVEPOINT and
            # audit write would run in autocommit and could split from the definition write.
            missing = [
                name for name in ("transaction", "commit", "rollback")
                if not callable(getattr(conn, name, None))
            ]
            if missing:
                raise TenantError(
                    f"Store connection lacks transaction support ({', '.join(missing)})",
                    error_code="tenant_store_unavailable",
                )
            await conn.transaction()
            try:
                previous_row = await conn.fetch_one(
                    f"SELECT attributes FROM {table} WHERE query_slug = $1 for update", slug
                )
                previous = previous_row.get("attributes") if previous_row else None
                row = await conn.fetch_one(sql, *values)
                if row is not None:
                    await self._apply_run_as(
                        conn, store, slug, previous, row.get("attributes"), actor, request_info
                    )
                await conn.commit()
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    _logger.exception("Rollback failed after run-as write error on %s", table)
                raise
        return row

    async def _apply_run_as(
        self,
        conn: Any,
        store: QueryStore,
        slug: str,
        previous: Any,
        current: Any,
        actor: int | None,
        request_info: Mapping[str, Any] | None,
    ) -> "RunAsChange | None":
        """Set/change/clear run-as and write one audit row in the caller's transaction."""
        prev = _scheduler_of(previous)
        cur = _scheduler_of(current)
        if not prev and not cur:
            return None
        if prev and cur and prev == cur:
            return None
        if actor is None:
            # Fail closed: a schedule change must be attributable (audit changed_by is
            # NOT NULL), otherwise a stale run-as user could keep running it.
            raise TenantError(
                f"Schedule change on {slug!r} requires an authenticated user",
                error_code="tenant_write_forbidden",
            )

        table = self._qualified_table(store)
        audit = self._run_as_audit_table(store)
        await conn.fetch_one("SAVEPOINT qs_run_as")
        try:
            row = await conn.fetch_one(
                f"SELECT {RUN_AS_COLUMN} FROM {table} WHERE query_slug = $1", slug
            )
            old = row.get(RUN_AS_COLUMN) if row else None
            old = None if old is None else int(old)
            if cur:
                if old == actor:
                    await conn.fetch_one("RELEASE SAVEPOINT qs_run_as")
                    return None
                new: int | None = actor
                operation = "set" if old is None else "change"
            else:
                if old is None:
                    await conn.fetch_one("RELEASE SAVEPOINT qs_run_as")
                    return None
                new = None
                operation = "clear"
            await conn.fetch_one(
                f"UPDATE {table} SET {RUN_AS_COLUMN} = $1 WHERE query_slug = $2 "
                "RETURNING query_slug",
                new, slug,
            )
            await conn.fetch_one(
                f"INSERT INTO {audit} "
                "(query_slug, old_user_id, new_user_id, operation, changed_by, request_info) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING audit_id",
                slug, old, new, operation, actor,
                None if request_info is None else json.dumps(dict(request_info), default=str),
            )
            await conn.fetch_one("RELEASE SAVEPOINT qs_run_as")
        except Exception as exc:
            if _is_missing_run_as_column(exc) or isinstance(exc, UndefinedTableError):
                await conn.fetch_one("ROLLBACK TO SAVEPOINT qs_run_as")
                key = (store.schema, store.table)
                if key not in _RUN_AS_FALLBACK_WARNED:
                    _RUN_AS_FALLBACK_WARNED.add(key)
                    _logger.warning(
                        "Store %s.%s is not migrated for run-as (%s); definition written without it",
                        store.schema, store.table, exc,
                    )
                return None
            raise
        return RunAsChange(query_slug=slug, old_user_id=old, new_user_id=new, operation=operation)

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

    # -- move ----------------------------------------------------------------------

    @staticmethod
    def _project_move(row: Mapping[str, Any], origin: QueryStore, dest_columns: frozenset) -> dict:
        """Project one origin row onto the destination's columns.

        A missing ``program_slug`` on the destination is dropped (it becomes
        runtime-derived from the destination schema); a destination that
        persists it receives the origin's runtime value. Any other origin
        column without a destination counterpart must be null or empty.

        Raises:
            TenantError: ``move_validation_failed`` with ``details.columns``.
        """
        projected: dict = {}
        incompatible: list = []
        for column, value in row.items():
            if column in dest_columns:
                projected[column] = value
            elif column != "program_slug" and value not in (None, "", [], {}):
                incompatible.append(column)
        if incompatible:
            raise TenantError(
                "The destination store has no column for some of this definition's values",
                error_code="move_validation_failed",
                details={"columns": sorted(incompatible)},
            )
        if "program_slug" in dest_columns and "program_slug" not in row:
            projected["program_slug"] = origin.schema
        return projected

    async def _audit_move_run_as(
        self,
        conn: Any,
        raw: Mapping[str, Any],
        origin: QueryStore,
        destination: QueryStore,
        actor: int | None,
        request_info: Mapping[str, Any] | None,
    ) -> None:
        """Record a transferred run-as identity on both stores, inside the move transaction.

        A delegated execution identity changing owner store with no trail is not
        auditable, so the origin gets a ``clear`` event and the destination a ``set``
        event, both attributed to the mover. A null run-as emits nothing. A missing
        audit table is fatal here: the move must not commit a silent transfer.
        """
        run_as = raw.get(RUN_AS_COLUMN)
        if run_as is None:
            return
        slug = raw.get("query_slug")
        for store in (origin, destination):
            table = self._run_as_audit_table(store)
            found = await conn.fetch_one("SELECT to_regclass($1) AS found", table)
            if not (found and found.get("found")):
                raise TenantError(
                    f"{store.schema}.{store.table}_run_as_audit is missing, so the delegated "
                    "run-as identity cannot be transferred with an audit trail",
                    error_code="move_validation_failed",
                    details={"run_as_audit": f"{store.schema}.{store.table}_run_as_audit"},
                )
        context = json.dumps(dict(request_info or {}))
        for store, old, new, operation in (
            (origin, run_as, None, "clear"),
            (destination, None, run_as, "set"),
        ):
            await conn.fetch_one(
                f"INSERT INTO {self._run_as_audit_table(store)} "
                "(query_slug, old_user_id, new_user_id, operation, changed_by, request_info) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING audit_id",
                slug, old, new, operation, actor, context,
            )

    async def move(
        self,
        slug: str,
        origin: QueryStore,
        destination: QueryStore,
        *,
        actor: int | None = None,
        request_info: Mapping[str, Any] | None = None,
    ) -> None:
        """Move one definition row between two stores in ONE transaction.

        Locks the origin row (``FOR UPDATE``), refuses a pipeline whose saved
        children would be silently re-pointed, checks the destination has no row,
        projects the row onto the destination's live columns, inserts it, deletes
        exactly one origin row and records the move: ``updated_by``/``updated_at``
        are stamped with the mover, and a transferred run-as identity gets the
        paired ``clear``/``set`` audit events. Any failure rolls everything back.

        Args:
            slug: The definition slug.
            origin: The store currently holding the row.
            destination: The store receiving the row (same database).
            actor: The authenticated user performing the move, for attribution.
            request_info: Optional request context recorded on the audit events.

        Raises:
            TenantError: ``query_not_found``, ``move_target_exists``,
                ``move_inherited_references``, ``move_dependency_analysis_unsupported``,
                ``move_validation_failed``, ``tenant_write_forbidden`` or
                ``tenant_store_unavailable``.
        """
        if origin.database_namespace != destination.database_namespace:
            raise TenantError(
                "Both stores must belong to the same database",
                error_code="move_validation_failed",
            )
        src = self._qualified_table(origin)
        dst = self._qualified_table(destination)
        try:
            async with await self.connection_factory() as conn:
                await conn.transaction()
                try:
                    row = await conn.fetch_one(f"SELECT * FROM {src} WHERE query_slug = $1 FOR UPDATE", slug)
                    if row is None:
                        raise TenantError(f"Query not found: {slug!r}", error_code="query_not_found")
                    raw = dict(row)
                    # A saved child that omits ``tenant`` inherits the parent's owner, so moving
                    # the parent would re-point it with nothing failing. Refuse until it is pinned.
                    _inherited, ref_blockers = analyze_references(raw.get("provider"), raw.get("query_raw"))
                    if ref_blockers:
                        first = ref_blockers[0]
                        raise TenantError(
                            first["message"],
                            error_code=first["code"],
                            details={"blockers": ref_blockers},
                        )
                    if await conn.fetch_one(f"SELECT 1 AS present FROM {dst} WHERE query_slug = $1", slug):
                        raise TenantError(
                            f"The destination store already has {slug!r}", error_code="move_target_exists"
                        )
                    found = await conn.fetch_all(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = $1 AND table_name = $2",
                        destination.schema, destination.table,
                    )
                    dest_columns = frozenset(r["column_name"] for r in found or [])
                    if not dest_columns:
                        raise TenantError(
                            f"Store table not available: {destination.schema}.{destination.table}",
                            error_code="tenant_store_unavailable",
                        )
                    await self._audit_move_run_as(conn, raw, origin, destination, actor, request_info)
                    projected = self._project_move(raw, origin, dest_columns)
                    # Stamp the move so the destination row is not indistinguishable from
                    # one that was never touched.
                    if actor is not None and "updated_by" in dest_columns:
                        projected["updated_by"] = actor
                    projected.pop("updated_at", None)
                    columns = list(projected)
                    placeholders = [f"${i + 1}" for i in range(len(columns))]
                    names = [quote_identifier(c) for c in columns]
                    if "updated_at" in dest_columns:
                        names.append(quote_identifier("updated_at"))
                        placeholders.append("now()")
                    await conn.fetch_one(
                        f"INSERT INTO {dst} ({', '.join(names)}) "
                        f"VALUES ({', '.join(placeholders)}) RETURNING query_slug",
                        *projected.values(),
                    )
                    deleted = await conn.fetch_all(f"DELETE FROM {src} WHERE query_slug = $1 RETURNING query_slug", slug)
                    if len(deleted or []) != 1:
                        raise TenantError(
                            "The origin row could not be removed exactly once", error_code="tenant_store_unavailable"
                        )
                    await conn.commit()
                except BaseException:
                    try:
                        await conn.rollback()
                    except Exception:  # noqa: BLE001 - never mask the original error
                        _logger.exception("Rollback failed after a move error on %s", src)
                    raise
        except TenantError:
            raise
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                raise TenantError(
                    f"The destination store already has {slug!r}", error_code="move_target_exists"
                ) from exc
            self._translate_write_error(exc, destination)
            raise
