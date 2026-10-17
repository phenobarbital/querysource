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
import asyncio
import hashlib
import json
import os
import time
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
# Journal columns orchestration may write (never identity, actor or plan snapshot).
_JOURNAL_COLUMNS = frozenset({
    "state", "moved", "scheduler_sync", "error_code", "message", "cutover_at",
    "lease_owner", "lease_expires_at",
})
# APScheduler's own default misfire grace (QSScheduler sets only coalesce/max_instances).
DEFAULT_MISFIRE_GRACE_SECONDS = 1

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


def _anchor_of(schedule: Mapping[str, Any]) -> datetime | None:
    """Return the persisted anchor (``start_date``) of an interval-style schedule, if any."""
    value = schedule.get("start_date")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


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


class AdmittedRun(dict):
    """One admitted occurrence; retains the shared owner lock until :meth:`finish`.

    A ``dict`` of the run row (``run_id``, ``logical_schedule_id``, ``generation``,
    ``scheduled_fire_at``, owner fields, ``kind``). The dedicated lock connection is
    released in ``finish`` — callers MUST call it from a ``finally``.
    """

    def __init__(self, data: Mapping[str, Any], *, conn: Any, cm: Any, lock_key: int) -> None:
        super().__init__(data)
        self._conn = conn
        self._cm = cm
        self._lock_key = lock_key
        self._finished = False

    async def mark_running(self) -> None:
        """Record that business work has started (still counts toward max_instances)."""
        await self._conn.fetch_one(
            "UPDATE management.qs_schedule_runs SET outcome = 'running' WHERE run_id = $1::uuid "
            "AND outcome = 'admitted' RETURNING run_id", _uuid(self["run_id"]),
        )

    async def finish(self, outcome: str = "completed", error: str | None = None) -> None:
        """Record the outcome, then release the shared lock and the connection (idempotent).

        ``outcome`` is ``completed``, ``failed`` or ``unknown`` (external effect could not
        be established — retained for operator reconciliation, never replayed).
        """
        if self._finished:
            return
        self._finished = True
        try:
            await self._conn.fetch_one(
                "UPDATE management.qs_schedule_runs SET outcome = $2, error = $3, finished_at = now() "
                "WHERE run_id = $1::uuid RETURNING run_id", _uuid(self["run_id"]), outcome, error,
            )
        except Exception:  # noqa: BLE001 - the lock must still be released
            logger.exception("Could not record outcome for run %s", self.get("run_id"))
        finally:
            try:
                await self._conn.fetchval("SELECT pg_advisory_unlock_shared($1)", self._lock_key, column=0)
            except Exception:  # noqa: BLE001 - a lost connection already dropped the session lock
                logger.warning("Could not release shared owner lock %s", self._lock_key)
            finally:
                await self._cm.__aexit__(None, None, None)


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
        clock: Callable[[], datetime] | None = None,
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
            clock: Aware-UTC time source (a test seam; defaults to the wall clock).
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
        self._clock = clock or utcnow
        self._mirror: Any = None

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
        unknown = set(fields) - _JOURNAL_COLUMNS
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

    async def cas_operation(
        self,
        conn: Any,
        operation_id: str,
        *,
        expect_states: Sequence[str],
        expect_moved: bool | None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Compare-and-set journal columns: apply ``fields`` only while state/moved still match.

        Args:
            conn: Caller-owned connection (inside its transaction).
            operation_id: Journal row to update.
            expect_states: The operation must currently be in one of these states.
            expect_moved: The current ``moved`` value the caller observed (``None`` means SQL NULL).
            **fields: Journal columns to set (same allowlist as :meth:`update_operation`).

        Returns:
            The updated row, or ``None`` when a concurrent writer already changed the operation.
        """
        unknown = set(fields) - _JOURNAL_COLUMNS
        if unknown:
            raise ValueError(f"Unsupported journal columns: {sorted(unknown)}")
        sets = [f"{name} = ${i + 3}" for i, name in enumerate(fields)]
        sets.append("updated_at = now()")
        moved_sql = {None: "moved IS NULL", True: "moved IS TRUE", False: "moved IS FALSE"}[expect_moved]
        row = await conn.fetch_one(
            f"UPDATE management.qs_query_moves SET {', '.join(sets)} "
            f"WHERE operation_id = $1::uuid AND state = ANY($2::text[]) AND {moved_sql} RETURNING *",
            _uuid(operation_id), list(expect_states), *fields.values(),
        )
        return _row_dict(row) if row else None

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
        observed_paused: bool | None = None,
        flag_trigger_change: bool = True,
    ) -> dict[str, Any]:
        """Create (once) the durable state of one owner/slug/kind and return it.

        Serialized per owner/slug/kind by a transaction-scoped advisory lock, so
        several processes backfilling at startup converge on a single row that
        keeps the first persisted anchor/cursor (it is never recomputed from now).
        An existing row whose trigger definition disagrees with the caller's — or,
        when ``observed_paused`` is given, whose paused state does — is flagged
        ``mirror_conflict`` instead of being silently overwritten. (A starting
        process has no paused opinion: it passes ``None`` and durable state wins. The
        saved-definition bootstrap passes ``flag_trigger_change=False``: there a changed
        trigger is an ordinary save the caller applies, not a conflicting mirror.)
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
        paused_differs = observed_paused is not None and bool(state.get("paused")) != observed_paused
        if ((not same_trigger and flag_trigger_change) or paused_differs) and not (
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
        """Open or freeze the shared dispatch gate for every schedule of the given owners.

        Opening only touches gates that are open already or were frozen by ``operation_id``
        itself: one operation can never release a gate another operation froze.
        """
        if gate not in ("open", "frozen"):
            raise ValueError(f"Unknown gate: {gate!r}")
        changed = 0
        for owner in owners:
            if gate == "frozen":
                rows = await conn.fetch_all(
                    "UPDATE management.qs_schedule_state SET dispatch_gate = 'frozen', "
                    "gate_operation_id = $5::uuid, updated_at = now() WHERE owner_namespace = $1 "
                    "AND owner_schema = $2 AND owner_table = $3 AND query_slug = $4 RETURNING logical_schedule_id",
                    *owner.as_tuple(), _uuid(operation_id),
                )
            else:
                rows = await conn.fetch_all(
                    "UPDATE management.qs_schedule_state SET dispatch_gate = 'open', gate_operation_id = NULL, "
                    "updated_at = now() WHERE owner_namespace = $1 AND owner_schema = $2 AND owner_table = $3 "
                    "AND query_slug = $4 AND (gate_operation_id IS NULL OR gate_operation_id = $5::uuid) "
                    "RETURNING logical_schedule_id",
                    *owner.as_tuple(), _uuid(operation_id),
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

    # -- bootstrap / sync of durable schedule state -------------------------------------------

    def attach_mirror(self, mirror: Any) -> None:
        """Register the local scheduler (``QSScheduler``) whose APScheduler jobs mirror durable state."""
        self._mirror = mirror

    async def load_all_states(self, conn: Any) -> list[dict[str, Any]]:
        """Read every durable schedule row (used by the mirror poller)."""
        rows = await conn.fetch_all("SELECT * FROM management.qs_schedule_state ORDER BY logical_schedule_id")
        return [_row_dict(row) for row in rows or []]

    async def bootstrap_row(
        self, owner: ScheduleOwner, row: Mapping[str, Any] | None, *, default_timezone: str = "UTC"
    ) -> dict[str, dict[str, Any]]:
        """Persist/refresh the durable state of every schedule of one definition (serialized).

        For a definition seen for the first time the existing trigger anchor and the next
        unclaimed fire time are persisted — an interval without a ``start_date`` is anchored
        once, here, and never recomputed from a later migration time. A definition whose
        schedule configuration changed (an ordinary save) bumps ``config_revision``, replaces
        the trigger and re-derives the cursor. Schedules that disappeared are disabled.
        ``row is None`` (definition deleted/moved away) disables everything of this owner.

        Returns:
            ``{kind: state_row}`` for every schedule the definition currently declares.
        """
        from querysource.conf import QS_SCHEDULER_COALESCE, QS_SCHEDULER_MAX_INSTANCES

        inventory = schedule_inventory(row or {}) if row is not None else []
        now = self._clock()
        result: dict[str, dict[str, Any]] = {}
        async with await self.connection_factory() as conn:
            await conn.transaction()
            try:
                for item in inventory:
                    definition = {"schedule_type": item["schedule_type"], "schedule": dict(item["schedule"] or {})}
                    effective = {**definition, "schedule": dict(definition["schedule"])}
                    if item["schedule_type"] == "interval" and "start_date" not in effective["schedule"]:
                        effective["schedule"]["start_date"] = now.isoformat()
                    trigger = self._parse(str(item["schedule_type"]), dict(effective["schedule"]))
                    if trigger is None:
                        logger.warning("Schedule %s for %s is not parseable; not coordinated", item["kind"], owner.slug)
                        continue
                    next_fire = trigger.get_next_fire_time(None, now)
                    tz = (effective["schedule"].get("timezone") or default_timezone)
                    spec = {**effective, "definition": definition}
                    state = await self.ensure_schedule_state(
                        conn, owner, item["kind"], trigger_spec=spec, timezone_name=str(tz),
                        anchor_at=_anchor_of(effective["schedule"]) or now, next_fire_at=next_fire,
                        max_instances=int(QS_SCHEDULER_MAX_INSTANCES), coalesce_missed=bool(QS_SCHEDULER_COALESCE),
                        flag_trigger_change=False,
                    )
                    stored_definition = (state.get("trigger_spec") or {}).get("definition")
                    if stored_definition is not None and _canonical(stored_definition) != _canonical(definition) \
                            and not (state.get("trigger_spec") or {}).get("mirror_conflict"):
                        state = await self._replace_config(conn, state, spec, str(tz), next_fire)
                    elif not state["enabled"]:
                        state = await self._set_enabled(conn, state, True)
                    result[item["kind"]] = state
                kinds = {item["kind"] for item in inventory if item["kind"] in result}
                await self._disable_missing(conn, owner, kinds)
                await conn.commit()
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    logger.exception("Rollback failed while bootstrapping %s", owner.slug)
                raise
        return result

    async def _replace_config(
        self, conn: Any, state: Mapping[str, Any], spec: Mapping[str, Any], tz: str, next_fire: datetime | None
    ) -> dict[str, Any]:
        row = await conn.fetch_one(
            "UPDATE management.qs_schedule_state SET trigger_spec = $2, timezone = $3, next_fire_at = $4, "
            "enabled = true, config_revision = config_revision + 1, updated_at = now() "
            "WHERE logical_schedule_id = $1::uuid RETURNING *",
            _uuid(state["logical_schedule_id"]), dict(spec), tz, next_fire,
        )
        return _row_dict(row)

    async def _set_enabled(self, conn: Any, state: Mapping[str, Any], enabled: bool) -> dict[str, Any]:
        row = await conn.fetch_one(
            "UPDATE management.qs_schedule_state SET enabled = $2, config_revision = config_revision + 1, "
            "updated_at = now() WHERE logical_schedule_id = $1::uuid RETURNING *",
            _uuid(state["logical_schedule_id"]), enabled,
        )
        return _row_dict(row)

    async def _disable_missing(self, conn: Any, owner: ScheduleOwner, keep_kinds: set[str]) -> None:
        for state in await self.load_schedule_states(conn, owner):
            if state["kind"] not in keep_kinds and state["enabled"]:
                await self._set_enabled(conn, state, False)

    # -- durable desired-state controls (pause / resume / removal) --------------------------

    async def find_reservation(self, conn: Any, owners: Sequence[ScheduleOwner]) -> dict[str, Any] | None:
        """Return the unfinished move that reserves any of the given canonical identities."""
        for owner in owners:
            row = await conn.fetch_one(
                "SELECT operation_id, role FROM management.qs_move_reservations WHERE namespace_id = $1 "
                "AND schema_name = $2 AND table_name = $3 AND query_slug = $4",
                *owner.as_tuple(),
            )
            if row is not None:
                return _row_dict(row)
        return None

    async def assert_not_moving(self, owners: Sequence[ScheduleOwner]) -> None:
        """Reject a write to an identity reserved by an unfinished move.

        Raises:
            TenantError: ``move_in_progress`` (409) while any identity is reserved.
        """
        async with await self.connection_factory() as conn:
            held = await self.find_reservation(conn, owners)
        if held is not None:
            raise TenantError(
                f"Query {owners[0].slug!r} is being moved; retry when the move finishes",
                error_code="move_in_progress",
            )

    async def set_desired(
        self, logical_schedule_id: str, *, paused: bool | None = None, enabled: bool | None = None
    ) -> dict[str, Any] | None:
        """Persist a desired-state change (pause/resume/removal) of one logical schedule.

        The change is refused with ``move_in_progress`` while the owner is reserved by a move or
        its shared dispatch gate is frozen, so a local control can never bypass a frozen
        generation. ``config_revision`` advances only when the desired state actually changes
        (never on ordinary cursor advancement). Returns ``None`` for an unknown schedule.
        """
        async with await self.connection_factory() as conn:
            await conn.transaction()
            try:
                row = await conn.fetch_one(
                    "SELECT * FROM management.qs_schedule_state WHERE logical_schedule_id = $1::uuid FOR UPDATE",
                    _uuid(logical_schedule_id),
                )
                if row is None:
                    await conn.rollback()
                    return None
                state = _row_dict(row)
                owner = ScheduleOwner(
                    state["owner_namespace"], state["owner_schema"], state["owner_table"], state["query_slug"]
                )
                held = await self.find_reservation(conn, [owner])
                if held is not None or state.get("dispatch_gate") == "frozen":
                    await conn.rollback()
                    raise TenantError(
                        f"Query {owner.slug!r} is being moved; its schedule cannot be changed right now",
                        error_code="move_in_progress",
                    )
                new_paused = state["paused"] if paused is None else bool(paused)
                new_enabled = state["enabled"] if enabled is None else bool(enabled)
                if new_paused != state["paused"] or new_enabled != state["enabled"]:
                    row = await conn.fetch_one(
                        "UPDATE management.qs_schedule_state SET paused = $2, enabled = $3, "
                        "config_revision = config_revision + 1, updated_at = now() "
                        "WHERE logical_schedule_id = $1::uuid RETURNING *",
                        _uuid(logical_schedule_id), new_paused, new_enabled,
                    )
                    state = _row_dict(row)
                await conn.commit()
                return state
            except TenantError:
                raise
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - never mask the original error
                    logger.exception("Rollback failed while changing schedule %s", logical_schedule_id)
                raise

    # -- reconciliation ---------------------------------------------------------------------

    async def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        """Idempotently mirror the durable owner/generation locally; never redo the row move.

        Valid only once the cutover is durable (``moved`` is true). The old source mirrors are
        removed and the target mirrors installed from durable state by this process' scheduler;
        other processes converge through the mirror poller, and every stale mirror is fenced by
        generation at admission regardless. On success the operation completes and its
        reservations are released; a failure leaves ``reconciling`` with
        ``scheduler_sync='failed'`` so the SAME operation can be retried.
        """
        async with await self.connection_factory() as conn:
            op = await self.load_operation(conn, operation_id)
        if op is None:
            raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
        if op["moved"] is not True or op["state"] in TERMINAL_STATES:
            return op
        slug = op["query_slug"]
        source = ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug)
        target = ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug)
        async with await self.connection_factory() as conn:
            scheduled = bool(await self.load_schedule_states(conn, target))
        sync, message = "not_configured", None
        if scheduled:
            sync = "ok"
            try:
                if self._mirror is not None:
                    await self._mirror.reconcile_owner_change(source, target)
            except Exception as exc:  # noqa: BLE001 - recorded so the same operation can be retried
                logger.exception("Mirror reconciliation failed for operation %s", operation_id)
                sync, message = "failed", f"Scheduler mirror update failed: {exc}"
        async with await self.connection_factory() as conn:
            await conn.transaction()
            try:
                # Compare-and-set: only a committed, still-unfinished operation is finalized here.
                if sync == "failed":
                    changed = await self.cas_operation(
                        conn, operation_id, expect_states=("cutover", "reconciling"), expect_moved=True,
                        state="reconciling", scheduler_sync="failed",
                        error_code="move_scheduler_not_ready", message=message,
                    )
                else:
                    changed = await self.cas_operation(
                        conn, operation_id, expect_states=("cutover", "reconciling"), expect_moved=True,
                        state="completed", scheduler_sync=sync, error_code=None, message=None,
                    )
                    if changed is not None:
                        await self.set_gate(conn, [target], "open", operation_id)
                        await self.release_reservations(conn, operation_id)
                if changed is None:
                    changed = await self.load_operation(conn, operation_id)
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        return changed

    async def heartbeat_loop(self, interval_seconds: float = 30.0) -> None:
        """Refresh this worker's heartbeat until cancelled (best effort; failures are logged)."""
        while True:
            try:
                await self.register_worker()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - heartbeats are advisory
                logger.warning("Scheduler worker heartbeat failed: %s", exc)
            await asyncio.sleep(interval_seconds)

    async def unregister_worker(self) -> None:
        """Remove this worker's row on clean shutdown."""
        try:
            async with await self.connection_factory() as conn:
                await conn.fetch_all(
                    "DELETE FROM management.qs_scheduler_workers WHERE worker_id = $1 RETURNING worker_id",
                    self.worker_id,
                )
        except Exception as exc:  # noqa: BLE001 - shutdown must not fail on this
            logger.warning("Could not unregister scheduler worker %s: %s", self.worker_id, exc)

    # -- operation leases (durable recovery) ---------------------------------------------------

    async def claim_operation_lease(self, operation_id: str, *, ttl_seconds: float = 60.0) -> dict[str, Any] | None:
        """Take (or renew) the recovery lease of an unfinished operation, or return ``None``.

        A lease is only an exclusivity token for ORCHESTRATION: it expires on its own and lets
        another worker resume from the durable journal. It never proves that a run finished and
        never authorizes replaying an admitted attempt or repeating a committed cutover.
        """
        async with await self.connection_factory() as conn:
            row = await conn.fetch_one(
                "UPDATE management.qs_query_moves SET lease_owner = $2, "
                "lease_expires_at = now() + make_interval(secs => $3), updated_at = now() "
                "WHERE operation_id = $1::uuid AND state NOT IN ('completed', 'failed') "
                "AND (lease_owner IS NULL OR lease_owner = $2 OR lease_expires_at IS NULL "
                "OR lease_expires_at < now()) RETURNING *",
                _uuid(operation_id), self.worker_id, float(ttl_seconds),
            )
        return _row_dict(row) if row else None

    async def release_operation_lease(self, operation_id: str) -> None:
        """Drop this worker's lease (best effort; expiry covers a crash)."""
        try:
            async with await self.connection_factory() as conn:
                await conn.fetch_all(
                    "UPDATE management.qs_query_moves SET lease_owner = NULL, lease_expires_at = NULL "
                    "WHERE operation_id = $1::uuid AND lease_owner = $2 RETURNING operation_id",
                    _uuid(operation_id), self.worker_id,
                )
        except Exception as exc:  # noqa: BLE001 - expiry releases it anyway
            logger.warning("Could not release the lease of move %s: %s", operation_id, exc)

    async def list_recoverable_operations(self, *, limit: int = 50) -> list[str]:
        """Unfinished operations whose lease is free or expired, oldest first (polled; never notified)."""
        async with await self.connection_factory() as conn:
            rows = await conn.fetch_all(
                "SELECT operation_id FROM management.qs_query_moves WHERE state NOT IN ('completed', 'failed') "
                "AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at < now() "
                "OR lease_owner = $1) ORDER BY created_at LIMIT $2",
                self.worker_id, int(limit),
            )
        return [str(r["operation_id"]) for r in rows or []]

    # -- drain ---------------------------------------------------------------------------

    async def drain(
        self, operation_id: str, *, timeout_seconds: float = 30.0, poll_seconds: float = 0.25
    ) -> Mapping[str, Any]:
        """Wait for already-admitted runs to finish; never cancel or replay them.

        New admissions are already frozen (``prepare``). Every admitted run holds the
        shared owner lock until its query/pipeline and awaited remote work finish, so
        taking the exclusive locks of both identities — in deterministic order — proves
        the drain. Lock waits are polled (bounded by ``timeout_seconds``) so the durable
        operation stays resumable instead of blocking a worker forever.

        A run row still open (``admitted``/``running``/``unknown``) while its lock is
        acquirable means its process died without recording an outcome: it is marked
        ``unknown`` and drain stays pending with an actionable message — an expired
        heartbeat is never trusted as proof that the business work completed.

        Returns:
            The journal row plus ``drained`` (bool) and, when pending, ``pending_runs``.
        """
        async with await self.connection_factory() as conn:
            op = await self.load_operation(conn, operation_id)
            if op is None:
                raise TenantError(f"Move operation not found: {operation_id}", error_code="query_not_found")
            if op["state"] != "draining":
                return {**op, "drained": op["state"] not in ("preparing",)}
            slug = op["query_slug"]
            owners = [
                ScheduleOwner(op["source_namespace"], op["source_schema"], op["source_table"], slug),
                ScheduleOwner(op["target_namespace"], op["target_schema"], op["target_table"], slug),
            ]
            deadline = time.monotonic() + timeout_seconds
            held: list[int] = []
            try:
                for key in ordered_lock_keys(owners):
                    while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0):
                        if time.monotonic() >= deadline:
                            return {**op, "drained": False, "pending_runs": await self._open_run_ids(conn, owners)}
                        await asyncio.sleep(poll_seconds)
                    held.append(key)
                leftovers = await self._open_run_ids(conn, owners)
                if leftovers:
                    await conn.fetch_all(
                        "UPDATE management.qs_schedule_runs SET outcome = 'unknown', finished_at = now(), "
                        "error = coalesce(error, 'worker lost before the outcome was recorded') "
                        "WHERE run_id = ANY($1::uuid[]) AND outcome IN ('admitted', 'running') RETURNING run_id",
                        [_uuid(r) for r in leftovers],
                    )
                    noted = await self.cas_operation(
                        conn, operation_id, expect_states=("draining",), expect_moved=False,
                        error_code="move_scheduler_not_ready",
                        message="Admitted run(s) with an unknown outcome must be resolved by an operator "
                        "(resolve them, or abort the move): " + ", ".join(leftovers),
                    )
                    return {**(noted or op), "drained": False, "pending_runs": leftovers}
                if op.get("error_code"):
                    cleared = await self.cas_operation(
                        conn, operation_id, expect_states=("draining",), expect_moved=False, error_code=None, message=None
                    )
                    if cleared is None:  # aborted/changed concurrently: never report it as drained
                        return {**await self.load_operation(conn, operation_id), "drained": False}
                    op = cleared
                return {**op, "drained": True}
            finally:
                for key in reversed(held):
                    try:
                        await conn.fetchval("SELECT pg_advisory_unlock($1)", key, column=0)
                    except Exception:  # noqa: BLE001 - a lost connection already dropped the lock
                        logger.warning("Could not release drain lock %s", key)

    async def resolve_unknown_runs(self, conn: Any, owners: Sequence[ScheduleOwner], *, note: str) -> list[str]:
        """Close ``unknown`` run outcomes of the given identities after an operator decision.

        Only rows already recorded as ``unknown`` change (to ``failed`` with an attributed
        note); a live ``admitted``/``running`` attempt is never touched and nothing is replayed.

        Returns:
            The resolved run ids.
        """
        resolved: list[str] = []
        for owner in owners:
            rows = await conn.fetch_all(
                "UPDATE management.qs_schedule_runs SET outcome = 'failed', "
                "error = left(coalesce(error, 'unknown outcome') || ' | ' || $5, 1000), "
                "finished_at = coalesce(finished_at, now()) WHERE owner_namespace = $1 AND owner_schema = $2 "
                "AND owner_table = $3 AND query_slug = $4 AND outcome = 'unknown' RETURNING run_id",
                *owner.as_tuple(), note,
            )
            resolved.extend(str(r["run_id"]) for r in rows or [])
        if resolved:
            logger.warning("Operator resolved %d unknown run outcome(s): %s", len(resolved), ", ".join(resolved))
        return resolved

    async def _open_run_ids(self, conn: Any, owners: Sequence[ScheduleOwner]) -> list[str]:
        ids: list[str] = []
        for owner in owners:
            rows = await conn.fetch_all(
                "SELECT run_id FROM management.qs_schedule_runs WHERE owner_namespace = $1 AND owner_schema = $2 "
                "AND owner_table = $3 AND query_slug = $4 AND outcome IN ('admitted', 'running', 'unknown') "
                "ORDER BY admitted_at",
                *owner.as_tuple(),
            )
            ids.extend(str(r["run_id"]) for r in rows or [])
        return ids

    # -- due admission -------------------------------------------------------------------

    async def admit(self, logical_schedule_id: str, generation: int) -> Mapping[str, Any] | None:
        """Claim one due occurrence once, holding the shared owner fence until the run ends.

        The shared owner advisory lock is taken FIRST (on a dedicated connection);
        only then are gate/generation/owner/pause/due state revalidated and the
        occurrence claimed in a short transaction that persists the attempt and
        atomically advances the durable next-fire cursor. APScheduler is only a
        wakeup: several processes waking for the same slot admit it once because
        ``(logical_schedule_id, scheduled_fire_at)`` is unique.

        Cadence is never reset to now: the cursor advances with the trigger's own
        ``get_next_fire_time`` (timezone, interval/biweekly anchor preserved), and
        coalescing/misfire follow the stored policy (default: the scheduler's own,
        coalesce on and a 1 s misfire grace).

        Returns:
            An :class:`AdmittedRun` that retains the lock connection — call
            ``finish()`` in a ``finally`` — or ``None`` when the occurrence was
            rejected (stale generation, frozen gate, paused, not due, misfired,
            ``max_instances`` reached, or already claimed elsewhere).
        """
        cm = await self.connection_factory()
        conn = await cm.__aenter__()
        lock_key: int | None = None
        keep = False
        try:
            sid = _uuid(logical_schedule_id)
            peek = await conn.fetch_one(
                "SELECT owner_namespace, owner_schema, owner_table, query_slug, generation "
                "FROM management.qs_schedule_state WHERE logical_schedule_id = $1::uuid", sid,
            )
            if peek is None or int(peek["generation"]) != int(generation):
                return None
            owner = ScheduleOwner(
                peek["owner_namespace"], peek["owner_schema"], peek["owner_table"], peek["query_slug"]
            )
            lock_key = owner.lock_key()
            got = await conn.fetchval("SELECT pg_try_advisory_lock_shared($1)", lock_key, column=0)
            if not got:
                lock_key = None  # an exclusive holder (a move) owns the fence: frozen
                return None
            run = await self._claim(conn, sid, int(generation), owner)
            if run is None:
                return None
            keep = True
            return AdmittedRun(run, conn=conn, cm=cm, lock_key=lock_key)
        finally:
            if not keep:
                await self._unlock_and_close(cm, conn, lock_key)

    async def _claim(
        self, conn: Any, sid: uuid.UUID, generation: int, owner: ScheduleOwner
    ) -> dict[str, Any] | None:
        """Revalidate under the shared fence and claim the due slot in one short transaction."""
        await conn.transaction()
        try:
            row = await conn.fetch_one(
                "SELECT * FROM management.qs_schedule_state WHERE logical_schedule_id = $1::uuid FOR UPDATE", sid
            )
            state = _row_dict(row) if row else None
            claim = self._evaluate_claim(state, generation, owner)
            if claim is None or claim.get("claim") is None:
                if claim is not None:  # misfire / max_instances: the slot is consumed, nothing runs
                    await self._advance(conn, sid, claim["slot"], claim["next"])
                await conn.commit()
                return None
            slot = claim["slot"]
            await self._expire_orphaned_runs(conn, sid, owner)
            open_runs = await conn.fetchval(
                "SELECT count(*) FROM management.qs_schedule_runs WHERE logical_schedule_id = $1::uuid "
                "AND outcome IN ('admitted', 'running')", sid, column=0,
            )
            if int(open_runs or 0) >= int(state["max_instances"]):
                await self._advance(conn, sid, slot, claim["next"])
                await conn.commit()
                return None
            inserted = await conn.fetch_one(
                "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at, "
                "owner_namespace, owner_schema, owner_table, query_slug, worker_id) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (logical_schedule_id, scheduled_fire_at) DO NOTHING RETURNING *",
                sid, generation, slot, *owner.as_tuple(), self.worker_id,
            )
            if inserted is None:  # another process already admitted this occurrence
                await conn.commit()
                return None
            await self._advance(conn, sid, slot, claim["next"])
            await conn.commit()
            data = _row_dict(inserted)
            data["kind"] = state["kind"]
            return data
        except BaseException:
            try:
                await conn.rollback()
            except Exception:  # noqa: BLE001 - never mask the original error
                logger.exception("Rollback failed while admitting %s", sid)
            raise

    async def _expire_orphaned_runs(self, conn: Any, sid: uuid.UUID, owner: ScheduleOwner) -> list[str]:
        """Mark open runs whose process died (no live session holds the owner fence) ``unknown``.

        Every live admitted run keeps the shared owner lock on its own session until
        ``finish``. This connection already holds that lock shared, and a session's own locks
        never conflict with each other, so an exclusive *try* succeeds exactly when no OTHER
        session holds the key — proving that every still-open run of this owner lost its
        process (SIGKILL/OOM) before recording an outcome. Those rows are closed as
        ``unknown`` (never replayed) so they stop consuming ``max_instances`` forever.

        Returns:
            The run ids that were expired (empty when another session still holds the fence).
        """
        key = owner.lock_key()
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0):
            return []
        # Release the probe at once: runs of this schedule are only inserted under the state row
        # lock this transaction holds, so no new live run can appear before the UPDATE below.
        await conn.fetchval("SELECT pg_advisory_unlock($1)", key, column=0)
        rows = await conn.fetch_all(
            "UPDATE management.qs_schedule_runs SET outcome = 'unknown', finished_at = now(), "
            "error = coalesce(error, 'worker lost before the outcome was recorded (detected at admission)') "
            "WHERE logical_schedule_id = $1::uuid AND outcome IN ('admitted', 'running') RETURNING run_id",
            sid,
        )
        expired = [str(r["run_id"]) for r in rows or []]
        if expired:
            logger.warning(
                "Schedule %s: %d run(s) lost their worker before recording an outcome; marked unknown: %s",
                sid, len(expired), ", ".join(expired),
            )
        return expired

    def _evaluate_claim(
        self, state: Mapping[str, Any] | None, generation: int, owner: ScheduleOwner
    ) -> dict[str, Any] | None:
        """Decide, without side effects, which slot (if any) this wakeup may claim.

        Returns ``None`` for a plain rejection, ``{"claim": None, "slot", "next"}`` when a
        due slot must be consumed without running (misfire), or ``{"claim": True, ...}``.
        """
        if state is None or int(state["generation"]) != generation:
            return None
        if (state["owner_namespace"], state["owner_schema"], state["owner_table"], state["query_slug"]) != owner.as_tuple():
            return None  # ownership moved between the peek and the lock
        if state["dispatch_gate"] != "open" or state["paused"] or not state["enabled"]:
            return None
        cursor = state["next_fire_at"]
        now = self._clock()
        if cursor is None or cursor > now:
            return None
        spec = state.get("trigger_spec") or {}
        trigger = self._parse(str(spec.get("schedule_type")), dict(spec.get("schedule") or {}))
        if trigger is None:
            logger.error("Schedule %s has an unparsable stored trigger; refusing admission", state["logical_schedule_id"])
            return None
        due = [cursor]
        following = trigger.get_next_fire_time(cursor, now)
        while following is not None and following <= now and len(due) < 10000:
            due.append(following)
            following = trigger.get_next_fire_time(following, now)
        slot = due[-1] if state["coalesce_missed"] else due[0]
        next_fire = following if state["coalesce_missed"] else trigger.get_next_fire_time(slot, now)
        grace = state["misfire_grace_seconds"]
        grace = DEFAULT_MISFIRE_GRACE_SECONDS if grace is None else grace
        if (now - slot).total_seconds() > grace:
            return {"claim": None, "slot": slot, "next": next_fire}
        return {"claim": True, "slot": slot, "next": next_fire}

    async def _advance(self, conn: Any, sid: uuid.UUID, slot: datetime, next_fire: datetime | None) -> None:
        await conn.fetch_one(
            "UPDATE management.qs_schedule_state SET last_claimed_fire_at = $2, next_fire_at = $3, "
            "updated_at = now() WHERE logical_schedule_id = $1::uuid RETURNING logical_schedule_id",
            sid, slot, next_fire,
        )

    async def _unlock_and_close(self, cm: Any, conn: Any, lock_key: int | None) -> None:
        try:
            if lock_key is not None:
                await conn.fetchval("SELECT pg_advisory_unlock_shared($1)", lock_key, column=0)
        except Exception:  # noqa: BLE001 - a lost connection already dropped the session lock
            logger.warning("Could not release shared owner lock %s", lock_key)
        finally:
            await cm.__aexit__(None, None, None)


def _canonical(value: Any) -> str:
    """Canonical text of a trigger *definition* (the anchored copy and conflict flag are excluded)."""
    base = dict(value or {})
    base.pop("mirror_conflict", None)
    base = dict(base.get("definition", base))
    return json.dumps(base, sort_keys=True, default=str)


def utcnow() -> datetime:
    """Return the current aware UTC time (single seam for tests)."""
    return datetime.now(timezone.utc)


__all__ = [
    "AdmittedRun",
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
