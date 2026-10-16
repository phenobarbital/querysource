"""Atomic cutover against a disposable PostgreSQL: row move + live schedule ownership in one transaction."""
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from apscheduler.triggers.interval import IntervalTrigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tenants"))
from test_handoff_state import NS, _factory, _Registry, _reset, _store, pg_dsn  # noqa: E402,F401 - fixture

from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM, QueryMoveService  # noqa: E402
from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository  # noqa: E402
from querysource.scheduler.handoff import ScheduleHandoffCoordinator, ScheduleOwner  # noqa: E402
from querysource.tenant_errors import TenantError  # noqa: E402
from querysource.tenants import QueryIdentity  # noqa: E402

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
LEGACY = _store("public", "queries", "legacy")
TENANT = _store("wm_assembly", "queries", "tenant")
ACKS = {ACK_CONSUMERS: True, ACK_PROGRAM: True}
SCHEDULED = {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}
ACTOR = 77
RUN_AS = 5

TENANT_DDL = """
CREATE TABLE {t} (
  query_slug text PRIMARY KEY, description text, source text,
  params jsonb DEFAULT '{{}}', attributes jsonb DEFAULT '{{}}', conditions jsonb DEFAULT '{{}}',
  cond_definition jsonb DEFAULT '{{}}', fields text[] DEFAULT '{{}}', filtering jsonb DEFAULT '{{}}',
  ordering text[] DEFAULT '{{}}', grouping text[] DEFAULT '{{}}', columns_definition text[] DEFAULT '{{}}',
  qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false, query_raw text, is_raw boolean DEFAULT false,
  is_cached boolean DEFAULT true, provider text DEFAULT 'db', parser text DEFAULT 'SQLParser',
  cache_timeout int NOT NULL DEFAULT 3600, cache_refresh int NOT NULL DEFAULT 0, cache_options jsonb DEFAULT '{{}}',
  program_id int NOT NULL DEFAULT 1, dwh boolean NOT NULL DEFAULT false, dwh_driver text, dwh_info jsonb,
  dwh_scheduler jsonb, created_at timestamptz DEFAULT now(), created_by int, updated_at timestamptz DEFAULT now(),
  updated_by int, {extra} scheduler_run_as_user_id int{legacy}
);
CREATE TABLE {a} (
  audit_id bigserial PRIMARY KEY, query_slug varchar NOT NULL, old_user_id int, new_user_id int,
  operation varchar NOT NULL, changed_by int NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
  request_info jsonb
);
"""


class _Faults:
    """Shared fault plan for connections created by the faulty factory."""

    def __init__(self) -> None:
        self.on_sql: dict[str, object] = {}  # substring -> exception | callable(row)->row | "empty"
        self.commit: str | None = None  # "pre" (raise before COMMIT) | "post" (COMMIT, then raise)


class _FaultConn:
    def __init__(self, real, faults: _Faults) -> None:
        self._real, self._faults, self._cutover = real, faults, False

    async def __aenter__(self):
        await self._real.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._real.__aexit__(*exc)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _apply(self, sql, result, many=False):
        for pattern, fault in self._faults.on_sql.items():
            if pattern in sql:
                if isinstance(fault, BaseException):
                    raise fault
                if fault == "empty":
                    return [] if many else None
                return fault(result)
        return result

    async def fetch_one(self, sql, *args, **kw):
        if "FOR UPDATE" in sql and "qs_query_moves" not in sql:
            self._cutover = True
        return self._apply(sql, await self._real.fetch_one(sql, *args, **kw))

    async def fetch_all(self, sql, *args, **kw):
        return self._apply(sql, await self._real.fetch_all(sql, *args, **kw), many=True)

    async def commit(self):
        if self._cutover and self._faults.commit == "pre":
            raise ConnectionResetError("connection lost before COMMIT was acknowledged")
        await self._real.commit()
        if self._cutover and self._faults.commit == "post":
            raise ConnectionResetError("connection lost after COMMIT")


async def _setup(dsn: str) -> None:
    await _reset(dsn)
    async with await _factory(dsn)() as conn:
        await conn.execute('DROP TABLE IF EXISTS "public"."queries", "public"."queries_run_as_audit" CASCADE')
        await conn.execute('DROP SCHEMA IF EXISTS "wm_assembly" CASCADE')
        for store, legacy in ((LEGACY, ", program_slug text"), (TENANT, "")):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{store.schema}"')
            res = await conn.execute(TENANT_DDL.format(
                t=f'"{store.schema}"."{store.table}"',
                a=f'"{store.schema}"."{store.table}_run_as_audit"', extra="", legacy=legacy))
            assert res[1] is None, res


async def _insert(dsn, store, slug="s", **cols):
    base = {"query_slug": slug, "description": "d", "query_raw": "SELECT 1", "created_by": 3,
            "created_at": datetime(2025, 5, 5, tzinfo=timezone.utc), "attributes": {}}
    if store.contract == "legacy":
        base["program_slug"] = "prog"
    base.update(cols)
    names = list(base)
    sql = (f'INSERT INTO "{store.schema}"."{store.table}" ({", ".join(names)}) VALUES '
           f'({", ".join(f"${i + 1}" for i in range(len(names)))})')
    async with await _factory(dsn)() as conn:
        res = await conn.execute(sql, *base.values())
        assert res[1] is None, res


async def _row(dsn, store, slug="s"):
    async with await _factory(dsn)() as conn:
        return await conn.fetch_one(f'SELECT * FROM "{store.schema}"."{store.table}" WHERE query_slug = $1', slug)


async def _scalar(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(sql, *args, column=0)


def _parse(schedule_type, schedule):
    return IntervalTrigger(start_date=T0, timezone="UTC", minutes=5)


async def _build(dsn, faults: _Faults | None = None, grant_validator=None, *, real_clock: bool = False):
    base = _factory(dsn)

    async def factory():
        conn = await base()
        return _FaultConn(conn, faults) if faults is not None else conn

    repo = DefinitionRepository(_Registry(LEGACY), factory)
    coord = ScheduleHandoffCoordinator(
        repo, deployment_id="d", worker_id="w", rollout_confirmed=True, trigger_parser=_parse,
        clock=None if real_clock else (lambda: T0),
    )
    await coord.register_worker()
    return QueryMoveService(repo, coord, grant_validator=grant_validator), coord


def _ident(slug="s", store=LEGACY):
    return QueryIdentity(store=store, slug=slug)


async def _draining(svc, coord, source=None, destination=TENANT, acks=ACKS):
    source = source or _ident()
    plan = await svc.preview(source, destination, actor=ACTOR)
    assert plan["can_move"], plan["blockers"]
    op = await svc.move(source, destination, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                        actor=ACTOR, acknowledgements=acks)
    prepared = await coord.prepare(op["operation_id"])
    assert prepared["state"] == "draining"
    return op["operation_id"]


# -- success ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_to_tenant_moves_all_fields_exactly_and_stamps_the_mover(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, params={"a": [1, 2]}, fields=["x", "y"], is_cached=False, cache_timeout=99,
                  dwh_info=None, h_filtering=True)
    before = dict(await _row(pg_dsn, LEGACY))
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    op = await svc.cutover(oid)
    assert op["state"] == "cutover" and op["moved"] is True
    assert await _row(pg_dsn, LEGACY) is None
    after = dict(await _row(pg_dsn, TENANT))
    assert "program_slug" not in after
    for name, value in before.items():
        if name in ("program_slug", "updated_at", "updated_by"):
            continue
        assert after[name] == value, name
    assert after["updated_by"] == ACTOR and after["updated_at"] is not None
    assert after["created_by"] == 3 and after["created_at"] == before["created_at"]  # provenance preserved
    done = await coord.reconcile(oid)
    assert done["state"] == "completed" and done["scheduler_sync"] == "not_configured"
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_move_reservations") == 0
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "public"."queries_run_as_audit"') == 0  # no null->null events
    # Repeating cutover/status never repeats the row move.
    assert (await svc.cutover(oid))["state"] == "completed"
    assert await _row(pg_dsn, LEGACY) is None


@pytest.mark.asyncio
async def test_tenant_to_legacy_sets_program_from_source_runtime_value(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, TENANT)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord, source=_ident(store=TENANT), destination=LEGACY, acks={ACK_CONSUMERS: True})
    assert (await svc.cutover(oid))["moved"] is True
    assert (await _row(pg_dsn, LEGACY))["program_slug"] == "wm_assembly"
    assert await _row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_scheduled_move_keeps_logical_id_cursor_pause_and_original_run_as(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, **{RUN_AS_COLUMN: RUN_AS})
    async with await _factory(pg_dsn)() as conn:
        for store in (LEGACY, TENANT):  # run-as audit already exists from the DDL
            assert await conn.fetchval(f"SELECT to_regclass('\"{store.schema}\".\"{store.table}_run_as_audit\"')",
                                       column=0)
    svc, coord = await _build(pg_dsn)
    state = (await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"),
                                       {"provider": "db", "attributes": SCHEDULED}))["query"]
    await coord.set_desired(state["logical_schedule_id"], paused=True)
    oid = await _draining(svc, coord)
    assert (await svc.cutover(oid))["moved"] is True
    moved = await _scalar(pg_dsn, "SELECT owner_schema FROM management.qs_schedule_state")
    assert moved == "wm_assembly"
    async with await _factory(pg_dsn)() as conn:
        after = await conn.fetch_one("SELECT * FROM management.qs_schedule_state")
    assert str(after["logical_schedule_id"]) == state["logical_schedule_id"]
    assert after["generation"] == state["generation"] + 1
    assert after["next_fire_at"] == state["next_fire_at"] and after["paused"] is True
    assert after["dispatch_gate"] == "frozen"  # nothing dispatches until reconciliation opens it
    assert (await _row(pg_dsn, TENANT))[RUN_AS_COLUMN] == RUN_AS  # NOT rebound to the mover
    src_audit = await _scalar(pg_dsn, "SELECT operation || ':' || changed_by FROM \"public\".\"queries_run_as_audit\"")
    dst_audit = await _scalar(pg_dsn, "SELECT operation || ':' || changed_by FROM \"wm_assembly\".\"queries_run_as_audit\"")
    assert (src_audit, dst_audit) == (f"clear:{ACTOR}", f"set:{ACTOR}")
    done = await coord.reconcile(oid)
    assert done["state"] == "completed"
    assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "open"
    assert await _scalar(pg_dsn, "SELECT paused FROM management.qs_schedule_state") is True
    # The stale source generation can no longer admit.
    assert await coord.admit(state["logical_schedule_id"], state["generation"]) is None


# -- pre-cutover failures: nothing partial, source stays active -------------------------------------


async def _assert_untouched(dsn, op_state="failed"):
    assert await _row(dsn, LEGACY) is not None and await _row(dsn, TENANT) is None
    assert await _scalar(dsn, "SELECT state FROM management.qs_query_moves") == op_state


@pytest.mark.parametrize(
    "pattern,fault",
    [
        ('INSERT INTO "wm_assembly"."queries"', RuntimeError("injected insert failure")),
        ('SELECT * FROM "wm_assembly"."queries" WHERE', lambda row: {**dict(row), "description": "tampered"}),
        ('DELETE FROM "public"."queries"', RuntimeError("injected delete failure")),
        ('DELETE FROM "public"."queries"', "empty"),
        ("INSERT INTO \"wm_assembly\".\"queries_run_as_audit\"", RuntimeError("audit down")),
    ],
)
@pytest.mark.asyncio
async def test_known_precommit_failures_roll_back_everything(pg_dsn, pattern, fault) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, **{RUN_AS_COLUMN: RUN_AS})
    faults = _Faults()
    svc, coord = await _build(pg_dsn, faults)
    await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"), {"provider": "db", "attributes": SCHEDULED})
    oid = await _draining(svc, coord)
    faults.on_sql[pattern] = fault
    op = await svc.cutover(oid)
    assert op["state"] == "failed" and op["moved"] is False and op["error_code"]
    await _assert_untouched(pg_dsn)
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "public"."queries_run_as_audit"') == 0
    # Source schedule untouched, dispatch restored, reservations released.
    assert await _scalar(pg_dsn, "SELECT owner_schema FROM management.qs_schedule_state") == "public"
    assert await _scalar(pg_dsn, "SELECT generation FROM management.qs_schedule_state") == 1
    assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "open"
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_move_reservations") == 0


@pytest.mark.asyncio
async def test_source_changed_after_plan_is_stale_and_changes_nothing(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    async with await _factory(pg_dsn)() as conn:  # only the legacy program changed
        await conn.execute("UPDATE public.queries SET program_slug = 'other'")
    op = await svc.cutover(oid)
    assert op["state"] == "failed" and op["error_code"] == "move_plan_stale" and op["moved"] is False
    await _assert_untouched(pg_dsn)
    assert (await _row(pg_dsn, LEGACY))["program_slug"] == "other"


@pytest.mark.asyncio
async def test_target_created_concurrently_is_never_overwritten(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    await _insert(pg_dsn, TENANT, description="someone else's definition")
    op = await svc.cutover(oid)
    assert op["error_code"] == "move_target_exists" and op["moved"] is False
    assert (await _row(pg_dsn, TENANT))["description"] == "someone else's definition"
    assert await _row(pg_dsn, LEGACY) is not None


@pytest.mark.asyncio
async def test_schedule_change_after_plan_is_stale(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn)
    state = (await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"),
                                       {"provider": "db", "attributes": SCHEDULED}))["query"]
    oid = await _draining(svc, coord)
    async with await _factory(pg_dsn)() as conn:  # a pause slipped in (e.g. a direct DB edit)
        await conn.execute("UPDATE management.qs_schedule_state SET paused = true, config_revision = config_revision + 1")
    op = await svc.cutover(oid)
    assert op["error_code"] == "move_plan_stale" and op["moved"] is False
    assert state["logical_schedule_id"] == await _scalar(pg_dsn, "SELECT logical_schedule_id::text FROM management.qs_schedule_state")


@pytest.mark.asyncio
async def test_missing_destination_column_fails_before_the_source_is_touched(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute('ALTER TABLE "wm_assembly"."queries" DROP COLUMN dwh_driver')
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    op = await svc.cutover(oid)
    assert op["error_code"] == "move_validation_failed" and op["moved"] is False
    await _assert_untouched(pg_dsn)


@pytest.mark.asyncio
async def test_revoked_run_as_grants_block_cutover(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, **{RUN_AS_COLUMN: RUN_AS})

    async def deny(run_as, raw):
        raise TenantError("delegated identity is no longer valid", error_code="move_stale_run_as")

    svc, coord = await _build(pg_dsn, grant_validator=deny)
    await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"), {"provider": "db", "attributes": SCHEDULED})
    oid = await _draining(svc, coord)
    op = await svc.cutover(oid)
    assert op["error_code"] == "move_stale_run_as" and op["moved"] is False
    await _assert_untouched(pg_dsn)


# -- drain not finished / ambiguous COMMIT --------------------------------------------------------


@pytest.mark.asyncio
async def test_running_job_keeps_cutover_pending_until_it_finishes(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    key = ScheduleOwner.from_store(LEGACY, "s").lock_key()
    async with await _factory(pg_dsn)() as holder:  # an admitted run holds the shared owner lock
        await holder.fetchval("SELECT pg_advisory_lock_shared($1)", key, column=0)
        pending = await svc.cutover(oid, lock_timeout_seconds=0.3, poll_seconds=0.05)
        assert pending["state"] == "draining" and pending["moved"] is False
        assert await _row(pg_dsn, LEGACY) is not None and await _row(pg_dsn, TENANT) is None
        await holder.fetchval("SELECT pg_advisory_unlock_shared($1)", key, column=0)
    assert (await svc.cutover(oid))["moved"] is True


@pytest.mark.asyncio
async def test_commit_failure_before_acknowledgement_is_resolved_not_assumed(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    faults = _Faults()
    svc, coord = await _build(pg_dsn, faults)
    oid = await _draining(svc, coord)
    faults.commit = "pre"
    op = await svc.cutover(oid)
    # Journal proves the transaction did not commit: retryable, never reported as failed-by-guess.
    assert op["state"] == "draining" and op["moved"] is False
    assert await _row(pg_dsn, LEGACY) is not None and await _row(pg_dsn, TENANT) is None
    faults.commit = None
    assert (await svc.cutover(oid))["moved"] is True  # the retry moves exactly once
    assert await _row(pg_dsn, LEGACY) is None and await _row(pg_dsn, TENANT) is not None


@pytest.mark.asyncio
async def test_commit_acknowledgement_lost_after_commit_reports_committed(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    faults = _Faults()
    svc, coord = await _build(pg_dsn, faults)
    oid = await _draining(svc, coord)
    faults.commit = "post"
    op = await svc.cutover(oid)
    assert op["moved"] is True and op["state"] == "cutover"  # resolved from the durable journal
    faults.commit = None
    assert (await svc.cutover(oid))["moved"] is True
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1


@pytest.mark.asyncio
async def test_in_flight_cutover_is_unknown_until_its_locks_are_gone(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_query_moves SET state = 'cutover', moved = NULL")
    key = ScheduleOwner.from_store(LEGACY, "s").lock_key()
    async with await _factory(pg_dsn)() as holder:  # stands in for the still-running cutover transaction
        await holder.fetchval("SELECT pg_advisory_lock($1)", key, column=0)
        with pytest.raises(TenantError) as err:
            await svc.resolve_cutover(oid)
        assert err.value.error_code == "move_outcome_unknown" and err.value.code == 503
        assert await _scalar(pg_dsn, "SELECT moved IS NULL FROM management.qs_query_moves")
        await holder.fetchval("SELECT pg_advisory_unlock($1)", key, column=0)
    resolved = await svc.resolve_cutover(oid)
    assert resolved["state"] == "draining" and resolved["moved"] is False


@pytest.mark.asyncio
async def test_concurrent_cutovers_move_exactly_once(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _draining(svc, coord)
    results = await asyncio.gather(svc.cutover(oid), svc.cutover(oid), return_exceptions=True)
    assert [r for r in results if isinstance(r, dict) and r["moved"] is True]
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "public"."queries"') == 0
