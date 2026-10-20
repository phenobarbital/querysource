"""Explicit single-definition moves between query stores (FEAT-176).

A move is a supervised administrative operation, not a distributed protocol:
pause the slug's scheduled jobs, move the row in ONE transaction, re-register the
jobs under the new owner. QSScheduler runs in a single process with an in-memory
job store, so ``set_slug_jobs_paused`` is authoritative — no shared control plane,
no durable operation journal and no recovery worker are needed.

``QueryMoveService.preview`` never mutates anything and never executes a stored
query: verification is metadata, raw-row and reference analysis only. The plan
digest (``plan_revision``) covers the raw persisted source row (including a legacy
program and the run-as column), the canonical endpoints and the deterministic
blocker/warning inventory, so a plan confirmed by an operator is revalidated
against exactly what they saw.
"""
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from navconfig.logging import logging

from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import (
    QueryIdentity,
    QueryStore,
    TenantRegistry,
    is_default_store,
    selector_for,
)

logger = logging.getLogger("QS.QueryMoves")

PLAN_VERSION = 1
# A selector/slug that is not a literal string (templated, computed) cannot be proven safe.
_DYNAMIC = re.compile(r"\{\{|\{%|\$\{|\$\(")
ACK_CONSUMERS = "acknowledge_external_consumers"
ACK_PROGRAM = "acknowledge_runtime_program_change"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _iso(value: Any) -> str | None:
    """Render a timestamp as ISO-8601 so the HTTP layer can serialize it."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _lock_key(store: QueryStore, slug: str) -> int:
    """Stable 64-bit advisory-lock key for one physical owner/slug identity."""
    material = "\x1f".join((store.database_namespace, store.schema, store.table, slug))
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big", signed=True)


def store_descriptor(registry: TenantRegistry, store: QueryStore) -> dict[str, Any]:
    """Render the credential-free ``StoreDescriptor`` of a registered physical store."""
    return {
        "tenant": selector_for(registry, store),
        "schema": store.schema,
        "table": store.table,
        "contract": store.contract,
        "is_default": is_default_store(registry, store),
    }


def schedule_inventory(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List the schedules QSScheduler would register for a definition row.

    Mirrors ``QSScheduler._register_query_row`` / ``_register_cache_row``: a nonzero
    ``cache_refresh`` alone is not a schedule.

    Args:
        row: The raw persisted definition row.

    Returns:
        One entry per schedule QSScheduler would register.
    """
    found: list[dict[str, Any]] = []
    attributes = _as_mapping(row.get("attributes")) or {}
    scheduler_def = _as_mapping(attributes.get("scheduler"))
    provider = row.get("provider")
    if scheduler_def:
        found.append(
            {
                "kind": "multi" if provider == "multi" else "query",
                "schedule_type": scheduler_def.get("schedule_type"),
                "enabled": True,
            }
        )
    if provider != "multi" and row.get("is_cached", False):
        cache_options = _as_mapping(row.get("cache_options")) or {}
        if cache_options.get("schedule_type") and cache_options.get("schedule"):
            found.append(
                {
                    "kind": "cache",
                    "schedule_type": cache_options.get("schedule_type"),
                    "enabled": True,
                }
            )
    return found


def has_external_schedule(row: Mapping[str, Any]) -> bool:
    """True when the definition relies on an external/DWH scheduler QSScheduler does not drive."""
    return bool(row.get("dwh") and _as_mapping(row.get("dwh_scheduler")))


def analyze_references(provider: Any, query_raw: Any) -> tuple[list[dict], list[dict]]:
    """Find saved-child references whose owner is inherited from the moved definition.

    A saved child in a multi-query pipeline that omits ``tenant`` resolves against the
    parent's owner, so moving the parent would silently re-point it. An explicit ``tenant``
    key (including an explicit ``null``) is a pinned reference and never blocks.
    Unknown or dynamic reference forms are reported, never guessed at; ``query_raw`` is
    never rewritten and SQL text is never scanned with a regex.

    Args:
        provider: The definition's provider (only ``multi`` has saved children).
        query_raw: The raw pipeline definition.

    Returns:
        ``(inherited_references, blockers)``.
    """
    if provider != "multi":
        return [], []
    inherited: list[dict] = []
    blockers: list[dict] = []
    pipeline = _as_mapping(query_raw)
    if pipeline is None:
        if query_raw in (None, "", b""):
            return [], []
        return [], [
            {
                "code": "move_dependency_analysis_unsupported",
                "message": "The pipeline definition cannot be parsed, so its saved-child references cannot be verified.",
                "path": "query_raw",
            }
        ]
    queries = pipeline.get("queries")
    if queries is not None and not isinstance(queries, Mapping):
        blockers.append(
            {
                "code": "move_dependency_analysis_unsupported",
                "message": "'queries' has an unknown shape; saved-child references cannot be verified.",
                "path": "queries",
            }
        )
        queries = {}
    for alias, cfg in (queries or {}).items():
        path = f"queries.{alias}"
        if not isinstance(cfg, Mapping):
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": f"Entry {alias!r} has an unknown shape.",
                    "path": path,
                }
            )
            continue
        if "slug" not in cfg:
            continue  # raw child: no saved-definition owner involved
        slug = cfg.get("slug")
        tenant = cfg.get("tenant", "<omitted>")
        if not isinstance(slug, str) or not slug or _DYNAMIC.search(slug) or (
            isinstance(tenant, str) and _DYNAMIC.search(tenant)
        ) or (tenant != "<omitted>" and tenant is not None and not isinstance(tenant, str)):
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": f"Entry {alias!r} uses a dynamic slug/tenant form that cannot be analysed.",
                    "path": path,
                }
            )
            continue
        if "tenant" not in cfg:
            inherited.append({"path": path, "slug": slug})
            blockers.append(
                {
                    "code": "move_inherited_references",
                    "message": (
                        f"Child {alias!r} ({slug!r}) inherits its owner; pin 'tenant' explicitly "
                        "(null selects the configured default) before moving."
                    ),
                    "path": f"{path}.tenant",
                }
            )
    sources = pipeline.get("sources")
    entries: list[tuple[str, Any]] = []
    if isinstance(sources, list):
        for index, entry in enumerate(sources):
            if isinstance(entry, Mapping):
                entries.extend((f"sources[{index}].{kind}", cfg) for kind, cfg in entry.items())
    elif isinstance(sources, Mapping):
        entries.extend((f"sources.{alias}", cfg) for alias, cfg in sources.items())
    for path, cfg in entries:
        if isinstance(cfg, Mapping) and "slug" in cfg:
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": "A source component references a saved slug in a form that cannot be analysed.",
                    "path": path,
                }
            )
    return inherited, blockers


class QueryMoveService:
    """Preview and execute explicit single-definition moves."""

    _STAMPED = frozenset({"updated_at", "updated_by"})

    def __init__(
        self,
        repository: DefinitionRepository,
        *,
        registry: TenantRegistry | None = None,
        scheduler: Any = None,
        grant_validator: Callable[[int | None, Mapping[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Bind the repository boundary and the live scheduler.

        Args:
            repository: Saved-definition repository (connection factory and store policy).
            registry: Tenant registry (defaults to the repository's).
            scheduler: The running ``QSScheduler``, or None when this process runs no
                scheduler; a move of a scheduled definition then reports
                ``move_scheduler_unsupported`` instead of silently orphaning its jobs.
            grant_validator: Optional ``async (run_as_user_id, raw_row)`` hook run inside the
                move transaction; it must raise ``TenantError`` when the preserved delegated
                identity or its scheduled-output grants are no longer valid.
        """
        self.repository = repository
        self.registry = registry or repository.registry
        self.scheduler = scheduler
        self._grant_validator = grant_validator

    # -- helpers ---------------------------------------------------------------------

    def descriptor(self, store: QueryStore) -> dict[str, Any]:
        """``StoreDescriptor`` for a store (no host, credentials or diagnostics)."""
        return store_descriptor(self.registry, store)

    @staticmethod
    def _same_store(a: QueryStore, b: QueryStore) -> bool:
        return (a.database_namespace, a.schema, a.table) == (b.database_namespace, b.schema, b.table)

    def _program_change(self, destination: QueryStore, runtime: Any) -> dict:
        before = getattr(runtime, "program_slug", None)
        # Moving into legacy sets its program from the source runtime value; into tenant it is the schema.
        after = before if destination.contract == "legacy" else destination.schema
        return {"before": before, "after": after}

    @staticmethod
    def required_acknowledgements(plan: Mapping[str, Any]) -> list[str]:
        """Exact acknowledgements a submission must carry for this plan.

        External-consumer review is always required; the program acknowledgement only when
        the runtime program differs before/after. A client acknowledgement is never
        authorization.
        """
        required = [ACK_CONSUMERS] if plan.get("consumer_review_required") else []
        program = plan.get("runtime_program") or {}
        if program.get("before") != program.get("after"):
            required.append(ACK_PROGRAM)
        return required

    async def _audit_ready(self, store: QueryStore) -> bool:
        """True when the store's run-as audit table exists (needed to transfer a nonnull run-as)."""
        table = self.repository._run_as_audit_table(store)
        async with await self.repository.connection_factory() as conn:
            row = await conn.fetch_one("SELECT to_regclass($1) AS found", table)
        return bool(row and row.get("found"))

    def _in_flight(self, store: QueryStore, slug: str) -> bool:
        """True when a scheduled run of this identity is executing in this process right now."""
        from querysource.scheduler.jobs import in_flight

        return in_flight(selector_for(self.registry, store), slug)

    # -- preview ----------------------------------------------------------------------

    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Return the read-only ``MovePlan`` for moving one definition (see ``_build_plan``)."""
        plan, _ = await self._build_plan(source, destination, actor=actor)
        return plan

    async def _build_plan(
        self, source: QueryIdentity, destination: QueryStore, *, actor: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the ``MovePlan`` plus the internal digest a submission must re-verify.

        Args:
            source: The resolved source identity.
            destination: The resolved destination store.
            actor: The numeric authenticated actor (audit attribution).

        Returns:
            ``(plan, internal)`` where ``internal`` carries the raw-row digest and is never
            returned to clients.

        Raises:
            TenantError: ``move_same_store`` (400) for identical canonical stores,
                ``move_validation_failed`` (400) for a non-numeric actor,
                ``query_not_found`` (404) for a missing source; store/permission errors keep
                their existing mappings. Every other actionable condition is a plan with
                ``can_move: false`` and concrete blockers.
        """
        if not isinstance(actor, int) or isinstance(actor, bool) or actor <= 0:
            raise TenantError("A numeric authenticated actor is required", error_code="move_validation_failed")
        src_store = source.store
        if self._same_store(src_store, destination):
            raise TenantError("Source and destination are the same store", error_code="move_same_store")

        raw = await self.repository._fetch_row(src_store, source.slug)
        if raw is None:
            raise TenantError(f"Query not found: {source.slug!r}", error_code="query_not_found")
        raw = dict(raw)
        loaded = await self.repository.get(source)  # validates the persisted row
        runtime = loaded.runtime
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        # Destination collision: never overwrite.
        existing = await self.repository._fetch_row(destination, source.slug)
        if existing is not None:
            blockers.append(
                {
                    "code": "move_target_exists",
                    "message": f"Destination already holds {source.slug!r}; nothing is overwritten.",
                    "path": "destination_tenant",
                }
            )

        schedules = schedule_inventory(raw)
        if has_external_schedule(raw):
            blockers.append(
                {
                    "code": "move_scheduler_unsupported",
                    "message": (
                        "The definition is scheduled by an external/DWH scheduler QuerySource does not "
                        "drive; re-point it manually after the move."
                    ),
                    "path": "dwh_scheduler",
                }
            )
        if schedules and self.scheduler is None:
            blockers.append(
                {
                    "code": "move_scheduler_unsupported",
                    "message": (
                        "The definition is scheduled but no QSScheduler runs in this process; "
                        "move it from the process that owns the scheduler."
                    ),
                    "path": "schedules",
                }
            )
        if schedules and self._in_flight(src_store, source.slug):
            blockers.append(
                {
                    "code": "move_run_in_flight",
                    "message": "A scheduled run of this definition is executing right now; retry when it finishes.",
                    "path": "schedules",
                }
            )

        inherited, ref_blockers = analyze_references(getattr(runtime, "provider", None), raw.get("query_raw"))
        blockers.extend(ref_blockers)

        # Run-as: a scheduled source transfers its original identity; an unscheduled stale one blocks.
        run_as = raw.get(RUN_AS_COLUMN)
        if run_as is not None:
            if not schedules:
                blockers.append(
                    {
                        "code": "move_stale_run_as",
                        "message": (
                            "The definition is unscheduled but still stores a run-as identity; clear it with "
                            "the attributed repair procedure before moving."
                        ),
                        "path": "run_as",
                    }
                )
            else:
                for label, store in (("source", src_store), ("destination", destination)):
                    if not await self._audit_ready(store):
                        blockers.append(
                            {
                                "code": "move_validation_failed",
                                "message": f"The {label} store lacks the run-as audit table needed to transfer run-as.",
                                "path": "run_as_audit",
                            }
                        )

        program = self._program_change(destination, runtime)
        if program["before"] != program["after"]:
            warnings.append(
                {
                    "code": "runtime_program_change",
                    "message": f"Runtime program changes from {program['before']!r} to {program['after']!r}.",
                }
            )
        warnings.append(
            {
                "code": "external_consumers",
                "message": "External consumers are not redirected; review and re-point them after the move.",
            }
        )
        if schedules:
            warnings.append(
                {
                    "code": "schedule_pause_window",
                    "message": (
                        "The schedule is paused for the duration of the move and resumed under the new "
                        "owner; run it outside peak hours."
                    ),
                }
            )

        blockers.extend(await self._destination_blockers(raw, destination, program["before"], actor))

        blockers.sort(key=lambda b: (b["code"], b.get("path") or ""))
        warnings.sort(key=lambda w: w["code"])
        digest = hashlib.sha256(
            _canonical_json(
                {
                    "v": PLAN_VERSION,
                    "slug": source.slug,
                    "source": self.descriptor(src_store),
                    "destination": self.descriptor(destination),
                    "raw": raw,
                    "blockers": [[b["code"], b.get("path")] for b in blockers],
                    "warnings": [w["code"] for w in warnings],
                }
            ).encode()
        ).hexdigest()
        plan = {
            "version": PLAN_VERSION,
            "source": self.descriptor(src_store),
            "destination": self.descriptor(destination),
            "query_slug": source.slug,
            "plan_revision": digest,
            "can_move": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "inherited_references": inherited,
            "runtime_program": program,
            "consumer_review_required": True,
            "schedule_policy": "pause-and-transfer",
            "schedules": schedules,
        }
        internal = {"raw_sha256": hashlib.sha256(_canonical_json(raw).encode()).hexdigest()}
        return plan, internal

    async def _destination_blockers(
        self, raw: Mapping[str, Any], destination: QueryStore, program: Any, actor: int
    ) -> list[dict[str, Any]]:
        """Dry-run the move's column mapping so preview never promises an impossible move.

        Uses the same ``_destination_columns``/``_insert_plan`` the move uses (read-only:
        catalog introspection only), so ``can_move: true`` implies the destination table
        exists and has a counterpart for every persisted source field.
        """
        async with await self.repository.connection_factory() as conn:
            dest_cols = await self._destination_columns(conn, destination)
        if not dest_cols:
            return [{
                "code": "move_validation_failed",
                "message": f"Destination table {destination.schema}.{destination.table} is not available.",
                "path": "destination_tenant",
            }]
        try:
            self._insert_plan(raw, destination, dest_cols, program, actor)
        except TenantError as exc:
            return [{"code": "move_validation_failed", "message": str(exc), "path": "destination_columns"}]
        return []

    # -- row projection ------------------------------------------------------------------

    @staticmethod
    def _normalize(value: Any) -> Any:
        """Comparable form of a column value (jsonb text and mappings compare equal)."""
        if isinstance(value, (str, bytes)):
            try:
                loaded = json.loads(value)
            except ValueError:
                return value
            return loaded if isinstance(loaded, (dict, list)) else value
        return value

    @staticmethod
    def _param(value: Any, udt: str) -> Any:
        """Bind value for a destination column (jsonb text is decoded so it is not double-encoded)."""
        if udt in ("jsonb", "json") and isinstance(value, (str, bytes)):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    @staticmethod
    def _ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    async def _destination_columns(self, conn: Any, store: QueryStore) -> dict[str, str]:
        rows = await conn.fetch_all(
            "SELECT column_name, udt_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2",
            store.schema, store.table,
        )
        return {r["column_name"]: r["udt_name"] for r in rows or []}

    def _insert_plan(
        self, raw: Mapping[str, Any], dest: QueryStore, dest_cols: Mapping[str, str], program: Any, actor: int
    ) -> tuple[dict[str, Any], list[str]]:
        """Columns/values for the plain destination INSERT, preserving every business field.

        Tenant persistence omits ``program_slug``; moving into legacy sets it from the source
        runtime value. ``updated_at``/``updated_by`` are stamped with the migration actor/time;
        creation provenance (``created_*``) is preserved verbatim.
        """
        allowed = set(self.repository._allowed_columns(dest)) | {RUN_AS_COLUMN}
        values: dict[str, Any] = {}
        for name, value in raw.items():
            if name == "program_slug":
                continue
            if name == RUN_AS_COLUMN and value is None:
                continue
            if name not in allowed:
                raise TenantError(
                    f"Source column {name!r} has no counterpart in the destination contract",
                    error_code="move_validation_failed",
                    details={"column": name},
                )
            values[name] = value
        if dest.contract == "legacy":
            values["program_slug"] = program
        stamped = [c for c in self._STAMPED if c in dest_cols]
        missing = sorted(c for c in values if c not in dest_cols)
        if missing:
            raise TenantError(
                "Destination table lacks column(s): " + ", ".join(missing),
                error_code="move_validation_failed",
                details={"columns": missing},
            )
        values.pop("updated_at", None)
        values.pop("updated_by", None)
        if "updated_by" in stamped:
            values["updated_by"] = actor
        return values, stamped

    # -- move ----------------------------------------------------------------------------

    async def move(
        self,
        source: QueryIdentity,
        destination: QueryStore,
        *,
        plan_revision: str,
        actor: int,
        acknowledgements: Mapping[str, bool],
        request_info: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Pause, move in one transaction, then re-register the schedules under the new owner.

        The row never exists in both stores and never in neither: the INSERT, the readback,
        the ``DELETE`` that must affect exactly one row and the run-as audit events all share
        one transaction. The schedule is paused before it opens and is always resumed —
        under the destination on success, under the source on any failure.

        Args:
            source: The resolved source identity.
            destination: The resolved destination store.
            plan_revision: The ``plan_revision`` of the confirmed preview.
            actor: The numeric authenticated actor.
            acknowledgements: The operator's acknowledgement flags.
            request_info: Optional request context recorded in the run-as audit events.

        Returns:
            The ``MoveResult``.

        Raises:
            TenantError: ``move_plan_stale`` (409) when the definition changed after the
                preview, ``move_acknowledgement_required`` (400), ``move_target_exists``
                (409), ``move_run_in_flight`` (409) and the plan's own blocker codes.
        """
        plan, internal = await self._build_plan(source, destination, actor=actor)
        if plan["plan_revision"] != plan_revision:
            raise TenantError(
                "The definition changed after the plan was confirmed; review a fresh preview",
                error_code="move_plan_stale",
            )
        if plan["blockers"]:
            first = plan["blockers"][0]
            raise TenantError(first["message"], error_code=first["code"], details={"blockers": plan["blockers"]})
        missing = [name for name in self.required_acknowledgements(plan) if not acknowledgements.get(name)]
        if missing:
            raise TenantError(
                "Missing acknowledgement(s): " + ", ".join(sorted(missing)),
                error_code="move_acknowledgement_required",
                details={"required": sorted(missing)},
            )

        src_store = source.store
        slug = source.slug
        schedules = plan["schedules"]
        src_selector = selector_for(self.registry, src_store)
        dst_selector = selector_for(self.registry, destination)
        paused: list[str] = []
        if schedules and self.scheduler is not None:
            paused = self.scheduler.set_slug_jobs_paused(slug, src_store, True)
            # Pausing stops future dispatches, not one already running.
            if self._in_flight(src_store, slug):
                self.scheduler.set_slug_jobs_paused(slug, src_store, False)
                raise TenantError(
                    "A scheduled run of this definition started while the move was being prepared; retry it",
                    error_code="move_run_in_flight",
                )
        moved = False
        try:
            moved_row = await self._transfer(
                src_store, destination, slug,
                expected_digest=internal["raw_sha256"],
                program=plan["runtime_program"]["before"],
                actor=actor,
                request_info=request_info,
            )
            moved = True
        finally:
            if not moved and paused:
                # Nothing committed: the source keeps its schedule exactly as it was.
                self.scheduler.set_slug_jobs_paused(slug, src_store, False)

        scheduler_sync = "not_configured"
        if self.scheduler is not None:
            # The source row is gone, so this removes its jobs and registers none; the
            # destination registration then rebuilds them from the moved row.
            await self.scheduler.register_slug(slug, tenant=src_selector)
            registered = await self.scheduler.register_slug(slug, tenant=dst_selector)
            scheduler_sync = "ok"
            logger.info(
                "Moved %r from %s.%s to %s.%s; schedules re-registered: %s",
                slug, src_store.schema, src_store.table,
                destination.schema, destination.table, registered.get("registered"),
            )
        return {
            "version": PLAN_VERSION,
            "query_slug": slug,
            "source": self.descriptor(src_store),
            "destination": self.descriptor(destination),
            "moved": True,
            "schedules_transferred": len(schedules),
            "scheduler_sync": scheduler_sync,
            "updated_at": _iso(moved_row.get("updated_at")),
        }

    async def _transfer(
        self,
        src: QueryStore,
        dst: QueryStore,
        slug: str,
        *,
        expected_digest: str,
        program: Any,
        actor: int,
        request_info: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        """Move the row in ONE transaction: lock, verify, insert, read back, delete, audit.

        Ordered exclusive advisory locks serialize two concurrent moves touching either
        identity. The source row is taken ``FOR UPDATE`` and re-digested against the plan,
        so a definition edited between preview and submission aborts instead of moving a
        row the operator never reviewed. There is no upsert: a destination collision is a
        unique-violation that rolls the whole transaction back and leaves the source intact.

        Returns:
            The persisted destination row.
        """
        src_table = self.repository._qualified_table(src)
        dst_table = self.repository._qualified_table(dst)
        conn_cm = await self.repository.connection_factory()
        async with conn_cm as conn:
            await conn.transaction()
            try:
                for key in sorted({_lock_key(src, slug), _lock_key(dst, slug)}):
                    await conn.fetchval("SELECT pg_advisory_xact_lock($1)", key, column=0)

                raw_row = await conn.fetch_one(
                    f"SELECT * FROM {src_table} WHERE query_slug = $1 FOR UPDATE", slug
                )
                if raw_row is None:
                    raise TenantError(f"Query not found: {slug!r}", error_code="query_not_found")
                raw = dict(raw_row)
                if hashlib.sha256(_canonical_json(raw).encode()).hexdigest() != expected_digest:
                    raise TenantError(
                        "The source changed after the plan was confirmed", error_code="move_plan_stale"
                    )
                run_as = raw.get(RUN_AS_COLUMN)
                if self._grant_validator is not None:
                    await self._grant_validator(run_as, raw)
                if await conn.fetch_one(f"SELECT 1 AS present FROM {dst_table} WHERE query_slug = $1", slug):
                    raise TenantError("Destination already holds this slug", error_code="move_target_exists")

                dest_cols = await self._destination_columns(conn, dst)
                if not dest_cols:
                    raise TenantError(
                        f"Store table not available: {dst.schema}.{dst.table}",
                        error_code="tenant_store_unavailable",
                    )
                values, stamped = self._insert_plan(raw, dst, dest_cols, program, actor)
                names = list(values)
                placeholders = [f"${i + 1}" for i in range(len(names))]
                columns = [self._ident(n) for n in names]
                if "updated_at" in stamped:
                    columns.append(self._ident("updated_at"))
                    placeholders.append("now()")
                params = [self._param(values[n], dest_cols[n]) for n in names]
                inserted = await conn.fetch_one(
                    f"INSERT INTO {dst_table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(placeholders)}) RETURNING *",
                    *params,
                )
                if inserted is None:
                    raise TenantError(
                        "The destination insert returned no row", error_code="move_validation_failed"
                    )
                readback = await conn.fetch_one(f"SELECT * FROM {dst_table} WHERE query_slug = $1", slug)
                if readback is None:
                    raise TenantError(
                        "The destination row could not be read back", error_code="move_validation_failed"
                    )
                mismatched = [
                    n for n in names
                    if n not in self._STAMPED and self._normalize(readback.get(n)) != self._normalize(values[n])
                ]
                if mismatched:
                    raise TenantError(
                        "The destination row does not match the source: " + ", ".join(sorted(mismatched)),
                        error_code="move_validation_failed",
                    )
                deleted = await conn.fetch_all(
                    f"DELETE FROM {src_table} WHERE query_slug = $1 RETURNING query_slug", slug
                )
                if len(deleted or []) != 1:
                    raise TenantError(
                        "The source delete did not affect exactly one row",
                        error_code="move_validation_failed",
                    )
                if run_as is not None:
                    context = json.dumps(dict(request_info or {}))
                    for store, old, new, operation in ((src, run_as, None, "clear"), (dst, None, run_as, "set")):
                        await conn.fetch_one(
                            f"INSERT INTO {self.repository._run_as_audit_table(store)} "
                            "(query_slug, old_user_id, new_user_id, operation, changed_by, request_info) "
                            "VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING audit_id",
                            slug, old, new, operation, actor, context,
                        )
                await conn.commit()
                return dict(readback)
            except BaseException:
                await conn.rollback()
                raise
