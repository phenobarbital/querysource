"""Read-only move plans and dependency analysis for explicit query moves (FEAT-176).

``QueryMoveService.preview`` never mutates anything and never executes a stored
query: verification is metadata, raw-row and reference analysis only. The plan
digest (``plan_revision``) covers the raw persisted source row (including a
legacy program and the run-as column), the canonical endpoints, the schedule
*configuration* revision and the deterministic blocker/warning inventory. It
excludes the naturally advancing schedule cursor and worker heartbeats, so
merely waiting for the next scheduled run never stales an unchanged preview.
"""
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from navconfig.logging import logging

from querysource.repositories.definitions import DefinitionRepository
from querysource.scheduler.handoff import (
    ScheduleHandoffCoordinator,
    ScheduleOwner,
    _iso,
    _row_dict,
    _uuid,
    is_default_store,
    selector_for,
)
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

logger = logging.getLogger("QS.QueryMoves")

PLAN_VERSION = 1
RUN_AS_COLUMN = "run_as_user_id"
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
    ) -> None:
        """Bind the repository boundary and the shared schedule coordinator."""
        self.repository = repository
        self.coordinator = coordinator
        self.registry = registry or repository.registry

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
        """Return the read-only ``MovePlan`` for moving one definition.

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
                    "message": f"Another move (operation {held['operation_id']}) holds this identity.",
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
                        "path": RUN_AS_COLUMN,
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
        return {
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

        plan = await self.preview(source, destination, actor=actor)
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
                    *dst_owner.as_tuple()[:3], destination.contract, dict(plan),
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
