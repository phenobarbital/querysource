"""Read-only move plans and dependency analysis for explicit query moves (FEAT-176).

``QueryMoveService.preview`` never mutates anything and never executes a stored
query: verification is metadata, raw-row and reference analysis only. The plan
digest (``plan_revision``) covers the raw persisted source row (including a
legacy program and the run-as column), the canonical endpoints, the schedule
*configuration* revision and the deterministic blocker/warning inventory. It
excludes the naturally advancing schedule cursor and worker heartbeats, so
merely waiting for the next scheduled run never stales an unchanged preview.
"""
import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from navconfig.logging import logging

from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository
from querysource.scheduler.handoff import (
    TERMINAL_STATES,
    ScheduleHandoffCoordinator,
    ScheduleOwner,
    _iso,
    _row_dict,
    _uuid,
    config_digest,
    is_default_store,
    ordered_lock_keys,
    selector_for,
)
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

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


def store_descriptor(registry: TenantRegistry, store: QueryStore) -> dict[str, Any]:
    """Render the credential-free ``StoreDescriptor`` of a registered physical store."""
    return {
        "tenant": selector_for(registry, store),
        "schema": store.schema,
        "table": store.table,
        "contract": store.contract,
        "is_default": is_default_store(registry, store),
    }


def analyze_references(provider: Any, query_raw: Any) -> tuple[list[dict], list[dict]]:
    """Find saved-child references whose owner is inherited from the moved definition.

    A saved child in a multi-query pipeline that omits ``tenant`` resolves against the
    parent's owner, so moving the parent would silently re-point it. An explicit ``tenant``
    key (including an explicit ``null``) is a pinned reference and never blocks.
    Unknown or dynamic reference forms are reported, never guessed at; ``query_raw`` is
    never rewritten and SQL text is never scanned with a regex.

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
    """Plan (and, in later stages, submit) explicit single-definition moves."""

    def __init__(
        self,
        repository: DefinitionRepository,
        coordinator: ScheduleHandoffCoordinator,
        *,
        registry: TenantRegistry | None = None,
        grant_validator: Callable[[int | None, Mapping[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Bind the repository boundary and the shared schedule coordinator.

        Args:
            repository: Saved-definition repository (connection factory and store policy).
            coordinator: Shared live-transfer coordinator and journal helpers.
            registry: Tenant registry (defaults to the repository's).
            grant_validator: Optional ``async (run_as_user_id, raw_row)`` hook run inside the
                cutover transaction; it must raise ``TenantError`` when the preserved delegated
                identity or its scheduled-output grants are no longer valid. The background
                worker has no request, so the management boundary supplies this check.
        """
        self.repository = repository
        self.coordinator = coordinator
        self.registry = registry or repository.registry
        self._grant_validator = grant_validator

    # -- helpers ---------------------------------------------------------------------

    def descriptor(self, store: QueryStore) -> dict[str, Any]:
        """``StoreDescriptor`` for a store (no host, credentials or diagnostics)."""
        return store_descriptor(self.registry, store)

    @staticmethod
    def _same_store(a: QueryStore, b: QueryStore) -> bool:
        return (a.database_namespace, a.schema, a.table) == (b.database_namespace, b.schema, b.table)

    def _program_change(self, source: QueryStore, destination: QueryStore, raw: Mapping[str, Any], runtime: Any) -> dict:
        before = getattr(runtime, "program_slug", None)
        # Moving into legacy sets its program from the source runtime value; into tenant it is the schema.
        after = before if destination.contract == "legacy" else destination.schema
        return {"before": before, "after": after}

    @staticmethod
    def required_acknowledgements(plan: Mapping[str, Any]) -> list[str]:
        """Exact acknowledgements a submission must carry for this plan.

        External-consumer review is always required; the program acknowledgement only when the
        runtime program differs before/after. A client acknowledgement is never authorization.
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

    # -- preview ----------------------------------------------------------------------

    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Return the read-only ``MovePlan`` for moving one definition (see ``_build_plan``)."""
        plan, _ = await self._build_plan(source, destination, actor=actor)
        return plan

    async def _build_plan(
        self, source: QueryIdentity, destination: QueryStore, *, actor: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the ``MovePlan`` plus the internal facts a durable operation must re-verify.

        The second element (raw source digest and schedule configuration revision) is only
        ever stored in the immutable journal snapshot, never returned to clients.

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

        owners = [ScheduleOwner.from_store(src_store, source.slug), ScheduleOwner.from_store(destination, source.slug)]
        async with await self.coordinator.connection_factory() as conn:
            readiness = await self.coordinator.readiness(conn)
            held = None if readiness["missing"] else await self.coordinator.find_reservation(conn, owners)
        if held is not None:
            blockers.append(
                {
                    "code": "move_in_progress",
                    "message": "Another unfinished move holds this source or destination identity.",
                }
            )

        inspection = await self.coordinator.inspect(source, destination)
        for blocker in inspection["blockers"]:
            if not any(b["code"] == blocker["code"] and b.get("path") == blocker.get("path") for b in blockers):
                blockers.append(dict(blocker))

        inherited, ref_blockers = analyze_references(getattr(runtime, "provider", None), raw.get("query_raw"))
        blockers.extend(ref_blockers)

        # Run-as: a scheduled source transfers its original identity; an unscheduled stale one blocks.
        run_as = raw.get(RUN_AS_COLUMN)
        if run_as is not None:
            if not inspection["schedules"] and not inspection["scheduler_required"]:
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
                                "code": "move_scheduler_not_ready",
                                "message": f"The {label} store lacks the run-as audit table needed to transfer run-as.",
                                "path": "run_as_audit",
                            }
                        )

        program = self._program_change(src_store, destination, raw, runtime)
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
                    "schedule_revision": inspection["schedule_revision"],
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
            "schedule_policy": "live-transfer",
            "schedules": inspection["schedules"],
            "handoff_ready": bool(inspection["handoff_ready"]),
        }
        internal = {
            "raw_sha256": hashlib.sha256(_canonical_json(raw).encode()).hexdigest(),
            "schedule_revision": inspection["schedule_revision"],
        }
        return plan, internal

    async def _destination_blockers(
        self, raw: Mapping[str, Any], destination: QueryStore, program: Any, actor: int
    ) -> list[dict[str, Any]]:
        """Dry-run the cutover's column mapping so preview never promises an impossible move.

        Uses the same ``_destination_columns``/``_insert_plan`` the cutover uses (read-only:
        catalog introspection only), so ``can_move: true`` implies the destination table exists
        and has a counterpart for every persisted source field.
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

    # -- submission ----------------------------------------------------------------------

    @staticmethod
    def _parse_key(idempotency_key: Any) -> str:
        try:
            if not isinstance(idempotency_key, (str, uuid.UUID)):
                raise ValueError("not a string")
            return str(uuid.UUID(str(idempotency_key)))
        except ValueError as exc:
            raise TenantError(
                "idempotency_key must be a UUID", error_code="move_validation_failed"
            ) from exc

    @staticmethod
    def _physical(store: QueryStore) -> list[str]:
        return [store.database_namespace, store.schema, store.table]

    def request_digest(
        self, source: QueryIdentity, destination: QueryStore, plan_revision: str, acknowledgements: Mapping[str, bool]
    ) -> str:
        """Digest of the full confirmed request (never of the idempotency key itself)."""
        return hashlib.sha256(
            _canonical_json(
                {
                    "slug": source.slug,
                    "source": self._physical(source.store),
                    "destination": self._physical(destination),
                    "plan_revision": plan_revision,
                    "acknowledgements": sorted(k for k, v in acknowledgements.items() if v is True),
                }
            ).encode()
        ).hexdigest()

    def operation_view(self, op: Mapping[str, Any]) -> dict[str, Any]:
        """Render a journal row as the ``MoveOperation`` payload (no internals, no secrets)."""
        def store_of(prefix: str) -> QueryStore:
            return QueryStore(
                op[f"{prefix}_namespace"], op[f"{prefix}_schema"], op[f"{prefix}_table"],
                op[f"{prefix}_contract"], frozenset(),
            )

        view: dict[str, Any] = {
            "version": PLAN_VERSION,
            "operation_id": str(op["operation_id"]),
            "source": self.descriptor(store_of("source")),
            "destination": self.descriptor(store_of("target")),
            "query_slug": op["query_slug"],
            "state": op["state"],
            "moved": op["moved"],
            "scheduler_sync": op["scheduler_sync"],
            "updated_at": _iso(op["updated_at"]),
        }
        if op.get("error_code"):
            view["error_code"] = op["error_code"]
        if op.get("message"):
            view["message"] = op["message"]
        return view

    async def _find_by_key(self, conn: Any, actor: int, key: str) -> dict[str, Any] | None:
        row = await conn.fetch_one(
            "SELECT * FROM management.qs_query_moves WHERE deployment_id = $1 AND actor_id = $2 "
            "AND idempotency_key = $3::uuid",
            self.coordinator.deployment_id, actor, _uuid(key),
        )
        return _row_dict(row) if row else None

    def _replay(self, existing: Mapping[str, Any], digest: str) -> dict[str, Any]:
        if existing["request_digest"] != digest:
            raise TenantError(
                "This idempotency key was already used for a different move request",
                error_code="move_idempotency_conflict",
            )
        return self.operation_view(existing)

    async def move(
        self,
        source: QueryIdentity,
        destination: QueryStore,
        *,
        plan_revision: str,
        idempotency_key: str,
        actor: int,
        acknowledgements: Mapping[str, bool],
        request_info: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Reserve (or return) the durable operation for one confirmed move request.

        The key is bound to deployment + authenticated actor; the same authorized request
        returns the SAME operation (a lost HTTP response never causes a new key or a second row
        migration), a different request under the same key is ``move_idempotency_conflict``.
        A new operation is only created after the preview is re-run: concrete blockers
        (source/collision/readiness/dependency) take precedence over the generic
        ``move_plan_stale``; required acknowledgements are then enforced exactly. Both
        canonical identities are reserved in one transaction (a database primary key makes
        overlapping/opposite-direction moves collide). Nothing is moved here: a bounded
        worker advances the operation. ``request_info`` is accepted for audit context but is
        deliberately not persisted (no client addresses or headers in the journal).

        Raises:
            TenantError: ``move_validation_failed``, ``move_idempotency_conflict``, any preview
                blocker code, ``move_plan_stale``, ``move_acknowledgement_required`` or
                ``move_in_progress``.
        """
        key = self._parse_key(idempotency_key)
        if not isinstance(actor, int) or isinstance(actor, bool) or actor <= 0:
            raise TenantError("A numeric authenticated actor is required", error_code="move_validation_failed")
        if not isinstance(plan_revision, str) or not plan_revision:
            raise TenantError("plan_revision is required", error_code="move_validation_failed")
        if not isinstance(acknowledgements, Mapping):
            raise TenantError("acknowledgements must be a mapping", error_code="move_validation_failed")
        digest = self.request_digest(source, destination, plan_revision, acknowledgements)

        async with await self.coordinator.connection_factory() as conn:
            existing = await self._find_by_key(conn, actor, key)
        if existing is not None:
            return self._replay(existing, digest)

        plan, internal = await self._build_plan(source, destination, actor=actor)
        if plan["blockers"]:
            first = plan["blockers"][0]
            raise TenantError(first["message"], error_code=first["code"], details={"blockers": plan["blockers"]})
        if plan["plan_revision"] != plan_revision:
            raise TenantError("The preview is out of date; review a fresh plan", error_code="move_plan_stale")
        missing = [name for name in self.required_acknowledgements(plan) if acknowledgements.get(name) is not True]
        if missing:
            raise TenantError(
                "Required acknowledgements are missing: " + ", ".join(missing),
                error_code="move_acknowledgement_required",
                details={"required": self.required_acknowledgements(plan)},
            )

        src_owner = ScheduleOwner.from_store(source.store, source.slug)
        dst_owner = ScheduleOwner.from_store(destination, source.slug)
        async with await self.coordinator.connection_factory() as conn:
            await conn.transaction()
            try:
                row = await conn.fetch_one(
                    "INSERT INTO management.qs_query_moves (deployment_id, actor_id, idempotency_key, "
                    "request_digest, query_slug, source_namespace, source_schema, source_table, source_contract, "
                    "target_namespace, target_schema, target_table, target_contract, plan_snapshot, state, moved) "
                    "VALUES ($1,$2,$3::uuid,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,'preparing',false) "
                    "ON CONFLICT (deployment_id, actor_id, idempotency_key) DO NOTHING RETURNING *",
                    self.coordinator.deployment_id, actor, _uuid(key), digest, source.slug,
                    *src_owner.as_tuple()[:3], source.store.contract,
                    *dst_owner.as_tuple()[:3], destination.contract, {**plan, "_internal": internal},
                )
                if row is None:  # a concurrent submission of the same key won the race
                    existing = await self._find_by_key(conn, actor, key)
                    await conn.rollback()
                    return self._replay(existing, digest)
                op = _row_dict(row)
                for role, owner in (("source", src_owner), ("target", dst_owner)):
                    reserved = await conn.fetch_one(
                        "INSERT INTO management.qs_move_reservations (namespace_id, schema_name, table_name, "
                        "query_slug, operation_id, role) VALUES ($1,$2,$3,$4,$5::uuid,$6) "
                        "ON CONFLICT DO NOTHING RETURNING query_slug",
                        *owner.as_tuple(), _uuid(op["operation_id"]), role,
                    )
                    if reserved is None:
                        await conn.rollback()
                        raise TenantError(
                            f"Another move already reserves {source.slug!r} in this source or destination",
                            error_code="move_in_progress",
                        )
                await conn.commit()
            except TenantError:
                raise
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    logger.exception("Rollback failed while reserving a move for %s", source.slug)
                raise
        return self.operation_view(op)

    async def operation_actor(self, operation_id: str) -> int:
        """Return the numeric actor that submitted an operation (for per-operation authorization).

        Raises:
            TenantError: ``move_validation_failed`` for a non-UUID id, ``query_not_found`` when missing.
        """
        try:
            op_id = _uuid(operation_id)
        except (ValueError, AttributeError) as exc:
            raise TenantError("operation_id must be a UUID", error_code="move_validation_failed") from exc
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, str(op_id))
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        return int(op["actor_id"])

    async def status(self, operation_id: str) -> Mapping[str, Any]:
        """Return the persisted operation without mutating it (caller reauthorizes)."""
        try:
            op_id = _uuid(operation_id)
        except (ValueError, AttributeError) as exc:
            raise TenantError("operation_id must be a UUID", error_code="move_validation_failed") from exc
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, str(op_id))
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        return self.operation_view(op)

    # -- atomic cutover ------------------------------------------------------------------

    _STAMPED = frozenset({"updated_at", "updated_by"})

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

    async def _view_of(self, operation_id: str) -> dict[str, Any]:
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, operation_id)
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        return self.operation_view(op)

    async def _fail_pre_cutover(self, op: Mapping[str, Any], error_code: str, message: str) -> None:
        """Record a PROVEN pre-cutover failure: source dispatch is restored, no target row remains."""
        slug = op["query_slug"]
        owners = [
            ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug),
            ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug),
        ]
        async with await self.coordinator.connection_factory() as conn:
            await conn.transaction()
            try:
                # Compare-and-set: only an attempt that is STILL indeterminate may be failed. A
                # concurrent resolver/orchestrator that already committed or reset it wins.
                changed = await conn.fetch_one(
                    "UPDATE management.qs_query_moves SET state = 'failed', moved = false, error_code = $2, "
                    "message = $3, updated_at = now() WHERE operation_id = $1::uuid AND state = 'cutover' "
                    "AND moved IS NULL RETURNING operation_id",
                    _uuid(str(op["operation_id"])), error_code, message[:500],
                )
                if changed is not None:
                    await self.coordinator.set_gate(conn, owners, "open", str(op["operation_id"]))
                    await self.coordinator.release_reservations(conn, str(op["operation_id"]))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def _revert_to_draining(self, operation_id: str, message: str) -> None:
        """Proven not-committed attempt that is still retryable: back to ``draining``, moved=false.

        Compare-and-set on ``state='cutover' AND moved IS NULL`` so it can never overwrite a
        committed (``moved=true``) or already-resolved journal.
        """
        async with await self.coordinator.connection_factory() as conn:
            await conn.transaction()
            try:
                await conn.fetch_all(
                    "UPDATE management.qs_query_moves SET state = 'draining', moved = false, message = $2, "
                    "updated_at = now() WHERE operation_id = $1::uuid AND state = 'cutover' AND moved IS NULL "
                    "RETURNING operation_id",
                    _uuid(operation_id), message[:500],
                )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def resolve_cutover(self, operation_id: str) -> Mapping[str, Any]:
        """Resolve an indeterminate cutover (``moved`` null) from the durable journal.

        The journal row is part of the cutover transaction, so ``moved = true`` proves the
        commit. While the cutover transaction still holds the identities' advisory locks the
        outcome is genuinely unknown (``move_outcome_unknown``): a connection error is never
        read as a rollback. Once both locks are free and the journal is still null the
        transaction is gone and provably did not commit; the operation returns to
        ``draining`` and may be retried — it can never produce a second row move.
        """
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, operation_id)
            if op is None:
                raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
            if op["moved"] is not None or op["state"] in TERMINAL_STATES:
                return self.operation_view(op)
            slug = op["query_slug"]
            owners = [
                ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug),
                ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug),
            ]
            await conn.transaction()
            try:
                for key in ordered_lock_keys(owners):
                    if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", key, column=0):
                        await conn.rollback()
                        raise TenantError(
                            "The cutover outcome is not yet known; retry the same operation",
                            error_code="move_outcome_unknown",
                        )
                op = await self.coordinator.load_operation(conn, operation_id)
                if op["moved"] is None:
                    await self.coordinator.update_operation(
                        conn, operation_id, state="draining", moved=False,
                        message="A previous cutover attempt did not commit; it will be retried.",
                    )
                    op = await self.coordinator.load_operation(conn, operation_id)
                await conn.commit()
            except TenantError:
                raise
            except BaseException:
                await conn.rollback()
                raise
        return self.operation_view(op)

    async def cutover(
        self, operation_id: str, *, lock_timeout_seconds: float = 30.0, poll_seconds: float = 0.25
    ) -> Mapping[str, Any]:
        """Move the row and the live schedule ownership in ONE transaction.

        Valid for a drained operation (``draining``): the exclusive identity fences taken here
        are the final proof that no admitted run is still active. On one connection and in one
        transaction: ordered exclusive advisory locks, source ``SELECT ... FOR UPDATE``,
        revalidation against the confirmed plan, a plain destination ``INSERT`` (no upsert),
        persisted readback, a source ``DELETE`` that must affect exactly one row, run-as audit
        events for a nonnull transfer, the durable schedule owner/generation change (the
        logical id and cursor are kept) and the journal cutover record. Known pre-commit
        failures roll back and are recorded as ``failed, moved=false``. An ambiguous COMMIT is
        resolved from the same journal (``resolve_cutover``); it is never reported as a
        rollback and never repeated. ``moved=true`` operations are returned unchanged.

        Returns:
            The ``MoveOperation`` view after the attempt.
        """
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, operation_id)
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        if op["moved"] is True or op["state"] in TERMINAL_STATES:
            return self.operation_view(op)
        if op["state"] == "cutover" and op["moved"] is None:
            resolved = await self.resolve_cutover(operation_id)
            if resolved["state"] != "draining":
                return resolved
            async with await self.coordinator.connection_factory() as conn:
                op = await self.coordinator.load_operation(conn, operation_id)
        if op["state"] != "draining":
            return self.operation_view(op)

        # Durable "outcome indeterminate" marker BEFORE the transaction (committed on its own).
        # Compare-and-set: a concurrent abort/orchestrator that already changed the operation wins.
        async with await self.coordinator.connection_factory() as conn:
            await conn.transaction()
            try:
                marked = await self.coordinator.cas_operation(
                    conn, operation_id, expect_states=("draining",), expect_moved=False, state="cutover", moved=None
                )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        if marked is None:
            return await self._view_of(operation_id)
        op = marked

        conn_cm = await self.coordinator.connection_factory()
        committed = False
        async with conn_cm as conn:
            await conn.transaction()
            try:
                result = await self._cutover_statements(conn, op, lock_timeout_seconds, poll_seconds)
            except _NotReady as pending:
                await self._rollback_quietly(conn, operation_id)
                await self._revert_to_draining(operation_id, str(pending))
                return await self._view_of(operation_id)
            except BaseException as exc:  # noqa: BLE001 - classified below
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    # Cancellation (shutdown, startup timeout) must leave the operation recoverable,
                    # never a terminal failure: roll back and let resolve_cutover decide later.
                    await asyncio.shield(self._rollback_quietly(conn, operation_id))
                    raise
                if await self._rollback_quietly(conn, operation_id):
                    code, message = self._classify(exc)
                    await self._fail_pre_cutover(op, code, message)
                    return await self._view_of(operation_id)
                return await self._resolve_after_error(operation_id)
            try:
                await conn.commit()
                committed = True
            except asyncio.CancelledError:
                raise
            except BaseException:  # noqa: BLE001 - ambiguous acknowledgement
                logger.warning("COMMIT acknowledgement lost for move %s; resolving from the journal", operation_id)
        if committed:
            return result
        return await self._resolve_after_error(operation_id)

    async def _resolve_after_error(self, operation_id: str) -> Mapping[str, Any]:
        try:
            return await self.resolve_cutover(operation_id)
        except TenantError:
            raise
        except Exception as exc:  # noqa: BLE001 - journal unreachable: honest unknown
            raise TenantError(
                "The cutover outcome could not be established; retry the same operation",
                error_code="move_outcome_unknown",
            ) from exc

    async def _rollback_quietly(self, conn: Any, operation_id: str) -> bool:
        """Roll back; True only when the rollback was acknowledged (a proven non-commit)."""
        try:
            await conn.rollback()
            return True
        except Exception:  # noqa: BLE001 - the caller resolves the outcome from the journal
            logger.exception("Rollback failed for move %s", operation_id)
            return False

    @staticmethod
    def _classify(exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, TenantError):
            return exc.error_code, str(exc)
        text = str(exc).lower()
        if "duplicate key" in text or "unique" in text:
            return "move_target_exists", "Destination already holds this slug; nothing was overwritten."
        if "permission denied" in text:
            return "move_validation_failed", "A database permission is missing for the move."
        return "move_validation_failed", f"The move failed before cutover ({type(exc).__name__})."

    async def _cutover_statements(
        self, conn: Any, op: Mapping[str, Any], timeout: float, poll: float
    ) -> dict[str, Any]:
        """Everything inside the single cutover transaction (never commits)."""
        operation_id = str(op["operation_id"])
        slug = op["query_slug"]
        src_owner = ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug)
        dst_owner = ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug)
        src = QueryStore(op["source_namespace"], op["source_schema"], op["source_table"], op["source_contract"], frozenset())
        dst = QueryStore(op["target_namespace"], op["target_schema"], op["target_table"], op["target_contract"], frozenset())
        snapshot = op["plan_snapshot"] or {}
        internal = snapshot.get("_internal") or {}
        actor = int(op["actor_id"])

        deadline = time.monotonic() + timeout
        for key in ordered_lock_keys([src_owner, dst_owner]):
            while not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", key, column=0):
                if time.monotonic() >= deadline:
                    raise _NotReady("Admitted runs have not finished; cutover will be retried.")
                await asyncio.sleep(poll)
        open_runs = await self.coordinator._open_run_ids(conn, [src_owner, dst_owner])
        if open_runs:
            raise _NotReady("Admitted run(s) with an unknown outcome must be reconciled by an operator.")

        current = await self.coordinator.load_operation(conn, operation_id, for_update=True)
        if current["moved"] is not None or current["state"] != "cutover":
            raise _NotReady("The operation changed state concurrently.")

        src_table = self.repository._qualified_table(src)
        dst_table = self.repository._qualified_table(dst)
        raw_row = await conn.fetch_one(f"SELECT * FROM {src_table} WHERE query_slug = $1 FOR UPDATE", slug)
        if raw_row is None:
            raise TenantError(f"Query not found: {slug!r}", error_code="query_not_found")
        raw = dict(raw_row)
        if hashlib.sha256(_canonical_json(raw).encode()).hexdigest() != internal.get("raw_sha256"):
            raise TenantError("The source changed after the plan was confirmed", error_code="move_plan_stale")
        states = await self.coordinator.load_schedule_states(conn, src_owner)
        revision = (
            hashlib.sha256(json.dumps(sorted(config_digest(st) for st in states)).encode()).hexdigest()
            if states else None
        )
        if revision != internal.get("schedule_revision"):
            raise TenantError("The schedule configuration changed after the plan was confirmed",
                              error_code="move_plan_stale")
        run_as = raw.get(RUN_AS_COLUMN)
        if self._grant_validator is not None:
            await self._grant_validator(run_as, raw)
        if await conn.fetch_one(f"SELECT 1 AS present FROM {dst_table} WHERE query_slug = $1", slug) is not None:
            raise TenantError("Destination already holds this slug", error_code="move_target_exists")
        for leftover in await self.coordinator.load_schedule_states(conn, dst_owner):
            if leftover["enabled"]:
                raise TenantError("The destination identity already has an enabled schedule",
                                  error_code="move_scheduler_not_ready")

        dest_cols = await self._destination_columns(conn, dst)
        if not dest_cols:
            raise TenantError(f"Store table not available: {dst.schema}.{dst.table}",
                              error_code="tenant_store_unavailable")
        program = (snapshot.get("runtime_program") or {}).get("before")
        values, stamped = self._insert_plan(raw, dst, dest_cols, program, actor)
        names = list(values)
        placeholders = [f"${i + 1}" for i in range(len(names))]
        columns = [self._ident(n) for n in names]
        if "updated_at" in stamped:
            columns.append(self._ident("updated_at"))
            placeholders.append("now()")
        params = [self._param(values[n], dest_cols[n]) for n in names]
        inserted = await conn.fetch_one(
            f"INSERT INTO {dst_table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING *",
            *params,
        )
        if inserted is None:
            raise TenantError("The destination insert returned no row", error_code="move_validation_failed")

        readback = await conn.fetch_one(f"SELECT * FROM {dst_table} WHERE query_slug = $1", slug)
        if readback is None:
            raise TenantError("The destination row could not be read back", error_code="move_validation_failed")
        mismatched = [
            n for n in names
            if n not in self._STAMPED and self._normalize(readback.get(n)) != self._normalize(values[n])
        ]
        if mismatched:
            raise TenantError(
                "The destination row does not match the source: " + ", ".join(sorted(mismatched)),
                error_code="move_validation_failed",
            )

        deleted = await conn.fetch_all(f"DELETE FROM {src_table} WHERE query_slug = $1 RETURNING query_slug", slug)
        if len(deleted or []) != 1:
            raise TenantError("The source delete did not affect exactly one row", error_code="move_validation_failed")

        if run_as is not None:
            for store, old, new, operation in ((src, run_as, None, "clear"), (dst, None, run_as, "set")):
                await conn.fetch_one(
                    f"INSERT INTO {self.repository._run_as_audit_table(store)} "
                    "(query_slug, old_user_id, new_user_id, operation, changed_by, request_info) "
                    "VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING audit_id",
                    slug, old, new, operation, actor,
                    json.dumps({"move_operation_id": operation_id}),
                )

        await conn.fetch_all(
            "DELETE FROM management.qs_schedule_state WHERE owner_namespace = $1 AND owner_schema = $2 "
            "AND owner_table = $3 AND query_slug = $4 RETURNING logical_schedule_id",
            *dst_owner.as_tuple(),
        )
        await conn.fetch_all(
            "UPDATE management.qs_schedule_state SET owner_namespace = $5, owner_schema = $6, owner_table = $7, "
            "generation = generation + 1, dispatch_gate = 'frozen', gate_operation_id = $8::uuid, updated_at = now() "
            "WHERE owner_namespace = $1 AND owner_schema = $2 AND owner_table = $3 AND query_slug = $4 "
            "RETURNING logical_schedule_id",
            *src_owner.as_tuple(), *dst_owner.as_tuple()[:3], _uuid(operation_id),
        )
        moved = await self.coordinator.update_operation(
            conn, operation_id, state="cutover", moved=True, cutover_at=self.coordinator._clock(),
            error_code=None, message=None,
        )
        return self.operation_view(moved)

    @staticmethod
    def _ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'


    # -- orchestration and recovery ------------------------------------------------------

    async def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        """Idempotently repair the SAME operation; never copies or deletes a row.

        After a durable cutover it mirrors the owner/generation into the schedulers and
        completes the operation. An indeterminate cutover is first resolved from the journal.
        An operation that has not cut over is returned unchanged: reconcile cannot start a move.
        """
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, operation_id)
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        if op["moved"] is None and op["state"] == "cutover":
            return await self.resolve_cutover(operation_id)
        if op["moved"] is True and op["state"] not in TERMINAL_STATES:
            return self.operation_view(await self.coordinator.reconcile(operation_id))
        return self.operation_view(op)

    async def abort(
        self, operation_id: str, *, actor: int, resolve_unknown_runs: bool = False
    ) -> Mapping[str, Any]:
        """Explicitly abandon an operation that has NOT cut over (``preparing``/``draining``).

        Compare-and-set on ``moved IS FALSE AND state IN ('preparing', 'draining')``: the
        operation becomes ``failed, moved=false`` (``move_aborted``), the gates it froze are
        re-opened and both reservations are released, in one transaction. Nothing is copied or
        deleted. Once the cutover marker is written (``cutover``, or any committed state) an
        abort is refused: an indeterminate or committed cutover is only ever reconciled.
        An already failed operation is returned unchanged (idempotent).

        Args:
            operation_id: The operation to abort.
            actor: Numeric authenticated actor (recorded in the journal message).
            resolve_unknown_runs: Also close the ``unknown`` run outcomes of both identities that
                block a drain (outcome becomes ``failed`` with an attributed operator note). Only
                runs already recorded as ``unknown`` are touched — never an ``admitted``/
                ``running`` attempt — and nothing is replayed.

        Raises:
            TenantError: ``move_validation_failed`` (bad id), ``query_not_found``, or
                ``move_in_progress`` (409) when the operation already reached cutover.
        """
        try:
            op_id = str(_uuid(operation_id))
        except (ValueError, AttributeError) as exc:
            raise TenantError("operation_id must be a UUID", error_code="move_validation_failed") from exc
        async with await self.coordinator.connection_factory() as conn:
            op = await self.coordinator.load_operation(conn, op_id)
            if op is None:
                raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
            if op["state"] == "failed":
                return self.operation_view(op)
            await conn.transaction()
            try:
                aborted = await self.coordinator.cas_operation(
                    conn, op_id, expect_states=("preparing", "draining"), expect_moved=False,
                    state="failed", moved=False, error_code="move_aborted",
                    message=f"Aborted before cutover by user {int(actor)}; source dispatch restored.",
                )
                if aborted is None:
                    await conn.rollback()
                    current = await self.coordinator.load_operation(conn, op_id)
                    if current is not None and current["state"] == "failed":
                        return self.operation_view(current)
                    raise TenantError(
                        "The move already reached cutover and can no longer be aborted; reconcile it instead",
                        error_code="move_in_progress",
                    )
                slug = op["query_slug"]
                owners = [
                    ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug),
                    ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug),
                ]
                await self.coordinator.set_gate(conn, owners, "open", op_id)
                await self.coordinator.release_reservations(conn, op_id)
                if resolve_unknown_runs:
                    await self.coordinator.resolve_unknown_runs(
                        conn, owners, note=f"resolved by user {int(actor)} while aborting move {op_id}"
                    )
                await conn.commit()
            except TenantError:
                raise
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    logger.exception("Rollback failed while aborting move %s", op_id)
                raise
        logger.info("Move %s aborted before cutover by user %s", op_id, actor)
        return self.operation_view(aborted)

    async def _note(self, operation_id: str, message: str) -> None:
        """Best-effort visible message on a still-unfinished operation (never changes state)."""
        try:
            async with await self.coordinator.connection_factory() as conn:
                await conn.fetch_all(
                    "UPDATE management.qs_query_moves SET message = $2, updated_at = now() "
                    "WHERE operation_id = $1::uuid AND state NOT IN ('completed', 'failed') RETURNING operation_id",
                    _uuid(operation_id), message[:500],
                )
        except Exception:  # noqa: BLE001 - the metadata service itself may be unreachable
            logger.warning("Could not record a status message for move %s", operation_id)

    async def advance(
        self,
        operation_id: str,
        *,
        lease_ttl_seconds: float = 60.0,
        drain_timeout_seconds: float = 20.0,
    ) -> Mapping[str, Any]:
        """Advance one operation as far as the durable state allows, under a recovery lease.

        Every stage re-reads the journal, so a lease takeover resumes exactly where the previous
        owner stopped: a committed cutover is only ever reconciled, never repeated; drain stays
        pending (visibly) while admitted work or an unknown run outcome remains; an
        indeterminate cutover is resolved before anything else. Unexpected infrastructure
        errors leave the operation recoverable with an actionable message.
        """
        lease = await self.coordinator.claim_operation_lease(operation_id, ttl_seconds=lease_ttl_seconds)
        if lease is None:
            return await self._view_of(operation_id)
        try:
            for _ in range(8):
                async with await self.coordinator.connection_factory() as conn:
                    op = await self.coordinator.load_operation(conn, operation_id)
                if op is None or op["state"] in TERMINAL_STATES:
                    break
                if await self.coordinator.claim_operation_lease(operation_id, ttl_seconds=lease_ttl_seconds) is None:
                    # Lease lost (expired and taken over, or the operation finished): stop orchestrating.
                    logger.warning("Move %s: recovery lease lost; another worker continues it", operation_id)
                    break
                state = op["state"]
                if state == "preparing":
                    await self.coordinator.prepare(operation_id)
                elif state == "draining":
                    drained = await self.coordinator.drain(operation_id, timeout_seconds=drain_timeout_seconds)
                    if not drained.get("drained"):
                        if drained.get("pending_runs") and not drained.get("message"):
                            await self._note(operation_id, "Waiting for admitted run(s) to finish.")
                        break
                    result = await self.cutover(operation_id, lock_timeout_seconds=drain_timeout_seconds)
                    if result["state"] == "draining":
                        break
                elif op["moved"] is None:  # cutover, outcome indeterminate
                    resolved = await self.resolve_cutover(operation_id)
                    if resolved["state"] == "cutover" and resolved["moved"] is None:
                        break
                elif op["moved"] is True:  # cutover/reconciling after a durable commit
                    after = await self.coordinator.reconcile(operation_id)
                    if after["state"] == "reconciling":
                        break  # scheduler_sync failed: retried on the next pass, never re-moved
                else:  # pragma: no cover - moved=false outside draining cannot occur
                    break
        except TenantError as exc:
            if exc.error_code == "move_outcome_unknown":
                await self._note(operation_id, "The cutover outcome is not yet known; resolving from the journal.")
            else:
                raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - pool exhaustion, lost connection...: recoverable
            logger.warning("Move %s paused by an infrastructure error: %s", operation_id, exc)
            await self._note(operation_id, f"Paused by an infrastructure error ({type(exc).__name__}); will retry.")
        finally:
            await self.coordinator.release_operation_lease(operation_id)
        return await self._view_of(operation_id)

    async def recover(self, *, limit: int = 50) -> list[Mapping[str, Any]]:
        """Resume unfinished operations (startup, BEFORE affected dispatch is enabled, and by polling).

        Operations whose lease is held by a live worker are skipped; an expired lease is taken
        over from the journal. Returns the resulting views.
        """
        views: list[Mapping[str, Any]] = []
        for operation_id in await self.coordinator.list_recoverable_operations(limit=limit):
            try:
                views.append(await self.advance(operation_id))
            except TenantError as exc:
                logger.warning("Move %s could not be advanced: %s", operation_id, exc)
        return views


class MoveWorker:
    """Bounded background worker that polls the durable journal (no notification required)."""

    def __init__(self, service: QueryMoveService, *, poll_seconds: float = 5.0, max_concurrent: int = 2) -> None:
        """Bound concurrency so long-lived run locks and moves cannot starve the control pool."""
        self.service = service
        self.poll_seconds = poll_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._wake = asyncio.Event()
        self._stopping = False

    def wake(self) -> None:
        """Optional accelerator after a submission; losing it only delays the next poll."""
        self._wake.set()

    async def run_once(self) -> int:
        """One polling pass; a failure of one operation never stops the others."""
        handled = 0
        for operation_id in await self.service.coordinator.list_recoverable_operations():
            async with self._semaphore:
                try:
                    await self.service.advance(operation_id)
                    handled += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - polled again on the next pass
                    logger.warning("Move %s failed this pass: %s", operation_id, exc)
        return handled

    async def run(self) -> None:
        """Poll until cancelled; independent of ENABLE_QS_SCHEDULER."""
        while not self._stopping:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - e.g. metadata service down: retry later
                logger.warning("Move worker pass failed: %s", exc)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def stop(self) -> None:
        """Ask the loop to end after the current pass."""
        self._stopping = True
        self._wake.set()


class _NotReady(Exception):
    """The cutover cannot start yet (runs still draining); the operation stays retryable."""
