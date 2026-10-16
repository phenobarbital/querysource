"""Shared schedule handoff state, readiness and admission for live query moves.

The control plane lives in the existing PostgreSQL metadata database (see
``docs/sql/query_move_control.sql``); this module never runs DDL. It owns:

* canonical owner/slug/kind identity and the advisory-lock keys derived from it;
* read-only deployment readiness (control tables, rollout gate, worker protocol);
* connection-bound journal and schedule-state helpers, so the move cutover can
  run inside one caller-owned transaction instead of nesting public operations;
* ``inspect`` (read-only desired-schedule inventory) and ``prepare`` (freeze new
  admissions for an existing durable operation).

Nothing here executes a source query: migration verification is metadata only.
"""
import hashlib
import json
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from navconfig import config
from navconfig.logging import logging

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

logger = logging.getLogger("QSScheduler.Handoff")

PROTOCOL_VERSION = 1
HEARTBEAT_TTL_SECONDS = 90
SCHEDULE_KINDS = ("query", "multi", "cache")
CONTROL_TABLES = (
    "qs_query_moves",
    "qs_move_reservations",
    "qs_schedule_state",
    "qs_schedule_runs",
    "qs_scheduler_workers",
)
# Operation states in which no further orchestration happens.
TERMINAL_STATES = ("completed", "failed")

ConnectionFactory = Callable[[], Awaitable[AbstractAsyncContextManager[Any]]]


# -- canonical identity ----------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleOwner:
    """Canonical physical owner of one saved definition (never the contract or schema alone)."""

    namespace: str
    schema: str
    table: str
    slug: str

    @classmethod
    def from_identity(cls, identity: QueryIdentity) -> "ScheduleOwner":
        """Build the owner from a resolved identity."""
        store = identity.store
        return cls(store.database_namespace, store.schema, store.table, identity.slug)

    @classmethod
    def from_store(cls, store: QueryStore, slug: str) -> "ScheduleOwner":
        """Build the owner from a store and slug."""
        return cls(store.database_namespace, store.schema, store.table, slug)

    def as_tuple(self) -> tuple[str, str, str, str]:
        """Return the (namespace, schema, table, slug) key."""
        return (self.namespace, self.schema, self.table, self.slug)

    def lock_key(self) -> int:
        """Return the signed 64-bit advisory-lock key shared by every worker for this owner."""
        return _hash_key("qs-schedule-owner", *self.as_tuple())


def _uuid(value: Any) -> uuid.UUID | None:
    """Coerce an id to ``uuid.UUID`` (the driver rejects the textual form for uuid params)."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _hash_key(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def ordered_lock_keys(owners: Sequence[ScheduleOwner]) -> list[int]:
    """Return the distinct owner lock keys in the deterministic acquisition order."""
    return sorted({owner.lock_key() for owner in owners})


def is_default_store(registry: TenantRegistry, store: QueryStore) -> bool:
    """True only for the exact configured physical default (not merely a legacy contract)."""
    default = registry.resolve()
    return (
        store.database_namespace == default.database_namespace
        and store.schema == default.schema
        and store.table == default.table
    )


def selector_for(registry: TenantRegistry, store: QueryStore) -> str | None:
    """Map a physical store to its scheduler selector.

    ``None`` is returned only for the exact configured default; every other
    store (including literal public.queries next to a custom default) uses its
    registered schema. ``contract == "legacy"`` is never used to pick ``None``.
    """
    return None if is_default_store(registry, store) else store.schema


# -- schedule inventory (same predicates as QSScheduler registration) ---------------------


def schedule_inventory(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List the schedules QSScheduler would register for a definition row.

    Mirrors ``QSScheduler._register_query_row`` / ``_register_cache_row``: a
    nonzero ``cache_refresh`` alone is not a schedule.
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
                "schedule": _as_mapping(scheduler_def.get("schedule")),
            }
        )
    if provider != "multi" and row.get("is_cached", False):
        cache_options = _as_mapping(row.get("cache_options")) or {}
        if cache_options.get("schedule_type") and cache_options.get("schedule"):
            found.append(
                {
                    "kind": "cache",
                    "schedule_type": cache_options.get("schedule_type"),
                    "schedule": _as_mapping(cache_options.get("schedule")),
                }
            )
    return found


def has_external_schedule(row: Mapping[str, Any]) -> bool:
    """True when the definition relies on an external/DWH scheduler QSScheduler does not coordinate."""
    return bool(row.get("dwh") and _as_mapping(row.get("dwh_scheduler")))


def _as_mapping(value: Any) -> dict | None:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return dict(value) if isinstance(value, Mapping) else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _row_dict(row: Any) -> dict[str, Any]:
    """Normalize a driver record: uuid → str, json text → mapping."""
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, uuid.UUID):
            data[key] = str(value)
        elif key in ("trigger_spec", "plan_snapshot") and isinstance(value, (str, bytes)):
            data[key] = json.loads(value)
    return data


def config_digest(state: Mapping[str, Any]) -> str:
    """Digest the durable schedule configuration, excluding the advancing cursor.

    ``next_fire_at``/``last_claimed_fire_at`` and worker heartbeats are excluded
    on purpose: merely waiting for the next occurrence must not stale a preview.
    """
    material = {
        "kind": state.get("kind"),
        "config_revision": state.get("config_revision"),
        "trigger_spec": state.get("trigger_spec"),
        "timezone": state.get("timezone"),
        "anchor_at": _iso(state.get("anchor_at")),
        "paused": bool(state.get("paused")),
        "enabled": bool(state.get("enabled")),
        "max_instances": state.get("max_instances"),
        "coalesce_missed": state.get("coalesce_missed"),
        "misfire_grace_seconds": state.get("misfire_grace_seconds"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


# -- coordinator -------------------------------------------------------------------------


class ScheduleHandoffCoordinator:
    """Shared control-plane access for live schedule transfer."""

    def __init__(
        self,
        repository: DefinitionRepository,
        *,
        deployment_id: str,
        worker_id: str,
        protocol_version: int = PROTOCOL_VERSION,
        rollout_confirmed: bool | None = None,
        heartbeat_ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
        trigger_parser: Callable[[str, dict], Any] | None = None,
    ) -> None:
        """Use ``repository.registry`` / its connection factory for shared state and owners.

        Args:
            repository: Saved-definition repository (owner resolution + connections).
            deployment_id: Scope of idempotency keys and rollout.
            worker_id: This process' scheduler-worker identity.
            protocol_version: Handoff protocol this process speaks.
            rollout_confirmed: Deployment rollout gate. ``None`` reads the
                ``QS_SCHEDULE_HANDOFF_ROLLOUT_CONFIRMED`` setting (default off):
                heartbeats alone can never prove an unreported old process is gone.
            heartbeat_ttl_seconds: A worker heartbeat older than this is ignored.
            trigger_parser: ``(schedule_type, schedule) -> trigger | None``; defaults to
                the scheduler's own parser so validity matches registration.
        """
        self.repository = repository
        self.registry: TenantRegistry = repository.registry
        self.connection_factory: ConnectionFactory = repository.connection_factory
        self.deployment_id = deployment_id
        self.worker_id = worker_id
        self.protocol_version = protocol_version
        self._rollout_confirmed = rollout_confirmed
        self.heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self._trigger_parser = trigger_parser

    # -- readiness (read-only) -----------------------------------------------------------

    def rollout_confirmed(self) -> bool:
        """Return the deployment rollout gate (explicit operator confirmation)."""
        if self._rollout_confirmed is not None:
            return self._rollout_confirmed
        return bool(config.getboolean("QS_SCHEDULE_HANDOFF_ROLLOUT_CONFIRMED", fallback=False))

    async def readiness(self, conn: Any | None = None) -> dict[str, Any]:
        """Read whether live scheduled transfer is enabled; never mutates anything.

        Returns:
            ``{"ready": bool, "reasons": [code, ...], "workers": {...}}`` where reasons
            are ``control_tables_missing``, ``rollout_not_confirmed``,
            ``no_compatible_workers`` or ``incompatible_workers``.
        """
        if conn is None:
            async with await self.connection_factory() as own:
                return await self.readiness(own)
        reasons: list[str] = []
        missing = await self._missing_control_tables(conn)
        workers = {"compatible": 0, "incompatible": 0}
        if missing:
            reasons.append("control_tables_missing")
        else:
            rows = await conn.fetch_all(
                "SELECT protocol_version FROM management.qs_scheduler_workers "
                "WHERE heartbeat_at > now() - make_interval(secs => $1)",
                float(self.heartbeat_ttl_seconds),
            )
            for row in rows or []:
                key = "compatible" if int(row["protocol_version"]) >= self.protocol_version else "incompatible"
                workers[key] += 1
            if workers["incompatible"]:
                reasons.append("incompatible_workers")
            if not workers["compatible"]:
                reasons.append("no_compatible_workers")
        if not self.rollout_confirmed():
            reasons.append("rollout_not_confirmed")
        return {"ready": not reasons, "reasons": reasons, "missing": missing, "workers": workers}

    async def _missing_control_tables(self, conn: Any) -> list[str]:
        missing = []
        for table in CONTROL_TABLES:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"management.{table}", column=0)
            if not exists:
                missing.append(f"management.{table}")
        return missing

    # -- worker protocol -----------------------------------------------------------------

    async def register_worker(self, conn: Any | None = None) -> None:
        """Upsert this process' worker row (protocol version + heartbeat)."""
        sql = (
            "INSERT INTO management.qs_scheduler_workers (worker_id, protocol_version, process_id) "
            "VALUES ($1, $2, $3) ON CONFLICT (worker_id) DO UPDATE "
            "SET protocol_version = EXCLUDED.protocol_version, heartbeat_at = now()"
        )
        if conn is None:
            async with await self.connection_factory() as own:
                await own.fetch_all(sql + " RETURNING worker_id", self.worker_id, self.protocol_version, os.getpid())
            return
        await conn.fetch_all(sql + " RETURNING worker_id", self.worker_id, self.protocol_version, os.getpid())

    # -- connection-bound journal helpers --------------------------------------------------

    async def load_operation(self, conn: Any, operation_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
        """Read one journal row on the caller's connection (optionally locking it)."""
        suffix = " FOR UPDATE" if for_update else ""
        row = await conn.fetch_one(
            f"SELECT * FROM management.qs_query_moves WHERE operation_id = $1::uuid{suffix}", _uuid(operation_id)
        )
        return _row_dict(row) if row else None

    async def update_operation(self, conn: Any, operation_id: str, **fields: Any) -> dict[str, Any]:
        """Update journal columns on the caller's connection and return the new row."""
        allowed = {
            "state", "moved", "scheduler_sync", "error_code", "message", "cutover_at",
            "lease_owner", "lease_expires_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported journal columns: {sorted(unknown)}")
        sets = [f"{name} = ${i + 2}" for i, name in enumerate(fields)]
        sets.append("updated_at = now()")
        row = await conn.fetch_one(
            f"UPDATE management.qs_query_moves SET {', '.join(sets)} "
            "WHERE operation_id = $1::uuid RETURNING *",
            _uuid(operation_id), *fields.values(),
        )
        if row is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        return _row_dict(row)

    async def release_reservations(self, conn: Any, operation_id: str) -> None:
        """Release both identity reservations held by a finished operation."""
        await conn.fetch_all(
            "DELETE FROM management.qs_move_reservations WHERE operation_id = $1::uuid RETURNING query_slug",
            _uuid(operation_id),
        )

    # -- connection-bound schedule-state helpers -------------------------------------------

    async def load_schedule_states(self, conn: Any, owner: ScheduleOwner) -> list[dict[str, Any]]:
        """Read every durable schedule row for one canonical owner/slug."""
        rows = await conn.fetch_all(
            "SELECT * FROM management.qs_schedule_state WHERE owner_namespace = $1 AND owner_schema = $2 "
            "AND owner_table = $3 AND query_slug = $4 ORDER BY kind",
            *owner.as_tuple(),
        )
        return [_row_dict(row) for row in rows or []]

    async def ensure_schedule_state(
        self,
        conn: Any,
        owner: ScheduleOwner,
        kind: str,
        *,
        trigger_spec: Mapping[str, Any],
        timezone_name: str = "UTC",
        anchor_at: datetime | None = None,
        next_fire_at: datetime | None = None,
        paused: bool = False,
        max_instances: int = 1,
        coalesce_missed: bool = True,
        misfire_grace_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Create (once) the durable state of one owner/slug/kind and return it.

        Serialized per owner/slug/kind by a transaction-scoped advisory lock, so
        several processes backfilling at startup converge on a single row that
        keeps the first persisted anchor/cursor (it is never recomputed from now).
        An existing row whose trigger or paused state disagrees with the caller's
        is flagged ``mirror_conflict`` instead of being silently overwritten.
        """
        if kind not in SCHEDULE_KINDS:
            raise ValueError(f"Unknown schedule kind: {kind!r}")
        await conn.fetch_one(
            "SELECT pg_advisory_xact_lock($1) AS locked",
            _hash_key("qs-schedule-bootstrap", *owner.as_tuple(), kind),
        )
        # asyncdb json-encodes mapping parameters itself; passing text would double-encode.
        trigger_json = dict(trigger_spec)
        row = await conn.fetch_one(
            "INSERT INTO management.qs_schedule_state (owner_namespace, owner_schema, owner_table, "
            "query_slug, kind, trigger_spec, timezone, anchor_at, next_fire_at, paused, max_instances, "
            "coalesce_missed, misfire_grace_seconds) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13) "
            "ON CONFLICT (owner_namespace, owner_schema, owner_table, query_slug, kind) DO NOTHING "
            "RETURNING *",
            *owner.as_tuple(), kind, trigger_json, timezone_name, anchor_at, next_fire_at, paused,
            max_instances, coalesce_missed, misfire_grace_seconds,
        )
        if row is not None:
            return _row_dict(row)
        existing = await conn.fetch_one(
            "SELECT * FROM management.qs_schedule_state WHERE owner_namespace = $1 AND owner_schema = $2 "
            "AND owner_table = $3 AND query_slug = $4 AND kind = $5",
            *owner.as_tuple(), kind,
        )
        state = _row_dict(existing)
        same_trigger = _canonical(state.get("trigger_spec")) == _canonical(dict(trigger_spec))
        if (not same_trigger or bool(state.get("paused")) != paused) and not (
            state.get("trigger_spec") or {}
        ).get("mirror_conflict"):
            flagged = {**(state.get("trigger_spec") or {}), "mirror_conflict": True}
            updated = await conn.fetch_one(
                "UPDATE management.qs_schedule_state SET trigger_spec = $2::jsonb, updated_at = now() "
                "WHERE logical_schedule_id = $1::uuid RETURNING *",
                _uuid(state["logical_schedule_id"]), flagged,
            )
            state = _row_dict(updated)
        return state

    async def set_gate(
        self, conn: Any, owners: Sequence[ScheduleOwner], gate: str, operation_id: str | None
    ) -> int:
        """Open or freeze the shared dispatch gate for every schedule of the given owners."""
        if gate not in ("open", "frozen"):
            raise ValueError(f"Unknown gate: {gate!r}")
        changed = 0
        for owner in owners:
            rows = await conn.fetch_all(
                "UPDATE management.qs_schedule_state SET dispatch_gate = $5, gate_operation_id = $6::uuid, "
                "updated_at = now() WHERE owner_namespace = $1 AND owner_schema = $2 AND owner_table = $3 "
                "AND query_slug = $4 RETURNING logical_schedule_id",
                *owner.as_tuple(), gate, _uuid(operation_id) if gate == "frozen" else None,
            )
            changed += len(rows or [])
        return changed

    # -- read-only inventory -----------------------------------------------------------

    def _parse(self, schedule_type: str, schedule: dict) -> Any:
        parser = self._trigger_parser
        if parser is None:
            from querysource.scheduler.scheduler import QSScheduler

            parser = QSScheduler()._parse_trigger
        return parser(schedule_type, schedule)

    async def inspect(self, source: QueryIdentity, destination: QueryStore) -> dict:
        """Read readiness, desired schedules and blockers without any mutation.

        Returns:
            ``{"ready", "scheduler_required", "handoff_ready", "schedules", "blockers",
            "schedule_revision"}``. ``schedules`` follows the MovePlan shape; the digest
            covers configuration only (never the advancing cursor or heartbeats).
        """
        owner = ScheduleOwner.from_identity(source)
        blockers: list[dict[str, Any]] = []
        loaded = await self.repository.get(source)
        runtime = loaded.runtime
        row = {
            "attributes": getattr(runtime, "attributes", None),
            "cache_options": getattr(runtime, "cache_options", None),
            "provider": getattr(runtime, "provider", None),
            "is_cached": getattr(runtime, "is_cached", False),
            "dwh": getattr(runtime, "dwh", False),
            "dwh_scheduler": getattr(runtime, "dwh_scheduler", None),
        }
        inventory = schedule_inventory(row)
        scheduler_required = bool(inventory)

        if has_external_schedule(row):
            blockers.append(
                {
                    "code": "move_scheduler_unsupported",
                    "message": "An external/DWH scheduler is configured and is not coordinated by QSScheduler.",
                    "path": "dwh_scheduler",
                }
            )
            scheduler_required = True

        for item in inventory:
            trigger = self._parse(str(item["schedule_type"]), dict(item["schedule"] or {}))
            if trigger is None:
                blockers.append(
                    {
                        "code": "move_schedule_invalid",
                        "message": f"The {item['kind']} schedule cannot be parsed into a trigger.",
                        "path": f"schedule.{item['kind']}",
                    }
                )

        states: list[dict[str, Any]] = []
        ready_info: dict[str, Any] = {"ready": True, "reasons": []}
        if scheduler_required:
            async with await self.connection_factory() as conn:
                ready_info = await self.readiness(conn)
                if not ready_info["missing"]:
                    states = await self.load_schedule_states(conn, owner)
            if not ready_info["ready"]:
                blockers.append(
                    {
                        "code": "move_scheduler_not_ready",
                        "message": "Live schedule transfer is not ready: " + ", ".join(ready_info["reasons"]),
                    }
                )
            elif any(not any(s["kind"] == i["kind"] for s in states) for i in inventory):
                blockers.append(
                    {
                        "code": "move_scheduler_not_ready",
                        "message": "Durable schedule state has not been bootstrapped for every schedule.",
                    }
                )
            if any((s.get("trigger_spec") or {}).get("mirror_conflict") for s in states):
                blockers.append(
                    {
                        "code": "move_scheduler_not_ready",
                        "message": "Scheduler mirrors disagree on paused state or trigger anchor.",
                    }
                )
            if any(s.get("dispatch_gate") == "frozen" for s in states):
                blockers.append(
                    {"code": "move_in_progress", "message": "Another move already froze this schedule."}
                )

        schedules = [
            {
                "kind": s["kind"],
                "enabled": bool(s["enabled"]),
                "paused": bool(s["paused"]),
                "timezone": s["timezone"],
                "next_fire_at": _iso(s["next_fire_at"]),
                "run_as": "preserve-existing",
            }
            for s in states
        ]
        digest = hashlib.sha256(
            json.dumps(sorted(config_digest(s) for s in states)).encode()
        ).hexdigest() if states else None
        return {
            "ready": ready_info["ready"] and not blockers,
            "scheduler_required": scheduler_required,
            "handoff_ready": ready_info["ready"] and not any(
                b["code"].startswith("move_scheduler") or b["code"] == "move_schedule_invalid" for b in blockers
            ),
            "schedules": schedules,
            "blockers": blockers,
            "schedule_revision": digest,
        }

    # -- freeze ------------------------------------------------------------------------

    async def prepare(self, operation_id: str) -> Mapping[str, Any]:
        """Freeze new admissions for an existing durable operation, then move it to draining.

        Idempotent: an operation already past ``preparing`` is returned unchanged.
        Readiness is checked BEFORE any gate is frozen; a blocker found here is
        recorded on the same operation as ``failed, moved=false`` (never raised
        to a client that has already been answered 202), and source dispatch is
        left untouched.
        """
        async with await self.connection_factory() as conn:
            await conn.transaction()
            try:
                op = await self.load_operation(conn, operation_id, for_update=True)
                if op is None:
                    raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
                if op["state"] != "preparing":
                    await conn.commit()
                    return op
                slug = op["query_slug"]
                source = ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug)
                target = ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug)
                states = await self.load_schedule_states(conn, source)
                if not states:
                    op = await self.update_operation(
                        conn, operation_id, state="draining", scheduler_sync="not_configured"
                    )
                else:
                    readiness = await self.readiness(conn)
                    if not readiness["ready"]:
                        await self.release_reservations(conn, operation_id)
                        op = await self.update_operation(
                            conn, operation_id, state="failed", moved=False,
                            error_code="move_scheduler_not_ready",
                            message="Live schedule transfer is not ready: " + ", ".join(readiness["reasons"]),
                        )
                    else:
                        await self.set_gate(conn, [source, target], "frozen", operation_id)
                        op = await self.update_operation(
                            conn, operation_id, state="draining", scheduler_sync="pending"
                        )
                await conn.commit()
                return op
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    logger.exception("Rollback failed while preparing move %s", operation_id)
                raise


def _canonical(value: Any) -> str:
    base = dict(value or {})
    base.pop("mirror_conflict", None)
    return json.dumps(base, sort_keys=True, default=str)


def utcnow() -> datetime:
    """Return the current aware UTC time (single seam for tests)."""
    return datetime.now(timezone.utc)


__all__ = [
    "CONTROL_TABLES",
    "PROTOCOL_VERSION",
    "SCHEDULE_KINDS",
    "ScheduleHandoffCoordinator",
    "ScheduleOwner",
    "config_digest",
    "has_external_schedule",
    "is_default_store",
    "ordered_lock_keys",
    "schedule_inventory",
    "selector_for",
]
