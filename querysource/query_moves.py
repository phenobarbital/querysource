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
from collections.abc import Mapping
from typing import Any

from navconfig.logging import logging

from querysource.repositories.definitions import DefinitionRepository
from querysource.scheduler.handoff import (
    ScheduleHandoffCoordinator,
    ScheduleOwner,
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
