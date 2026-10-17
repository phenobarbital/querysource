"""Multi-process live transfer regression against a disposable PostgreSQL (no business data touched)."""
import asyncio
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_query_moves_postgres import (  # noqa: E402,F401 - pg_dsn is a fixture
    ACK_CONSUMERS,
    ACK_PROGRAM,
    ACKS,
    LEGACY,
    RUN_AS,
    RUN_AS_COLUMN,
    SCHEDULED,
    TENANT,
    _build,
    _factory,
    _Faults,
    _ident,
    _insert,
    _row,
    _scalar,
    _setup,
    pg_dsn,
)

from querysource.scheduler.handoff import ScheduleOwner  # noqa: E402
from querysource.tenant_errors import TenantError  # noqa: E402

OWNER = ScheduleOwner.from_store(LEGACY, "s")
BOOT = {"provider": "db", "attributes": SCHEDULED}

# A scheduler "process": its own interpreter, connections and handoff coordinator.
CHILD = r"""
import asyncio, sys, time
from datetime import datetime, timezone
from types import SimpleNamespace
from apscheduler.triggers.interval import IntervalTrigger
from asyncdb import AsyncDB
from querysource.scheduler.handoff import ScheduleHandoffCoordinator

dsn, lid, generation, start_at, window, label = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]

async def factory():
    return await AsyncDB("pg", dsn=dsn).connection()

def parse(schedule_type, schedule):
    return IntervalTrigger(start_date=datetime(2026, 1, 5, tzinfo=timezone.utc), timezone="UTC", seconds=1)

async def main():
    repo = SimpleNamespace(registry=SimpleNamespace(), connection_factory=factory)
    coord = ScheduleHandoffCoordinator(repo, deployment_id="d", worker_id=label, rollout_confirmed=True, trigger_parser=parse)
    while time.time() < start_at:
        await asyncio.sleep(0.005)
    admitted = 0
    end = time.time() + window
    while time.time() < end:
        run = await coord.admit(lid, generation)
        if run is not None:
            admitted += 1
            await run.finish()
        await asyncio.sleep(0.01)
    print("ADMITTED", admitted)

asyncio.run(main())
"""


async def _spawn(dsn, lid, generation, start_at, window, label):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", CHILD, dsn, lid, str(generation), str(start_at), str(window), label,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )


async def _finish(proc):
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-2000:]
    return int(out.decode().split("ADMITTED")[-1].split()[0])


async def _arm(dsn, lid, *, seconds_ago=0.1, interval_seconds=1, grace=600, coalesce=True):
    """Make the schedule due right now and use a 1-second cadence (real clock in the child processes)."""
    async with await _factory(dsn)() as conn:
        res = await conn.execute(
            "UPDATE management.qs_schedule_state SET next_fire_at = now() - make_interval(secs => $2), "
            "misfire_grace_seconds = $3, coalesce_missed = $4, "
            "trigger_spec = jsonb_set(trigger_spec, '{schedule}', $5::jsonb) WHERE logical_schedule_id = $1::uuid",
            uuid.UUID(lid), seconds_ago, grace, coalesce, {"seconds": interval_seconds},
        )
        assert res[1] is None, res


async def _rows(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        return await conn.fetch_all(sql, *args)


@pytest.mark.asyncio
async def test_two_real_processes_admit_each_due_occurrence_once(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    lid = state["logical_schedule_id"]
    await _arm(pg_dsn, lid)
    start = time.time() + 6  # both interpreters finish importing before the shared start instant
    procs = [await _spawn(pg_dsn, lid, 1, start, 2.5, f"proc-{i}") for i in range(2)]
    counts = [await _finish(p) for p in procs]
    runs = await _rows(pg_dsn, "SELECT scheduled_fire_at, worker_id FROM management.qs_schedule_runs")
    slots = [r["scheduled_fire_at"] for r in runs]
    assert len(slots) == len(set(slots)) == sum(counts) >= 2  # no slot was ever admitted twice
    assert {r["worker_id"] for r in runs} <= {"proc-0", "proc-1"}


@pytest.mark.asyncio
async def test_transfer_racing_two_processes_never_admits_the_old_generation_afterwards(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, **{RUN_AS_COLUMN: RUN_AS})
    svc, coord = await _build(pg_dsn, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    lid = state["logical_schedule_id"]
    await _arm(pg_dsn, lid)
    plan = await svc.preview(_ident(), TENANT, actor=77)
    assert plan["can_move"], plan["blockers"]
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    start = time.time() + 6
    procs = [await _spawn(pg_dsn, lid, 1, start, 4.0, f"proc-{i}") for i in range(2)]
    await asyncio.sleep(max(0, start + 1.0 - time.time()))  # let both processes run for a while first
    final = await svc.advance(oid)
    assert final["state"] == "completed" and final["moved"] is True
    cutover_at = await _scalar(pg_dsn, "SELECT cutover_at FROM management.qs_query_moves")
    [await _finish(p) for p in procs]
    runs = await _rows(pg_dsn, "SELECT generation, scheduled_fire_at, admitted_at FROM management.qs_schedule_runs")
    assert runs and len({r["scheduled_fire_at"] for r in runs}) == len(runs)  # one admission per occurrence
    assert all(r["generation"] == 1 and r["admitted_at"] <= cutover_at for r in runs)  # nothing after the fence
    assert await _scalar(pg_dsn, "SELECT owner_schema FROM management.qs_schedule_state") == "wm_assembly"
    assert await _scalar(pg_dsn, "SELECT generation FROM management.qs_schedule_state") == 2
    assert await _row(pg_dsn, LEGACY) is None and (await _row(pg_dsn, TENANT))[RUN_AS_COLUMN] == RUN_AS
    # The stale source generation stays fenced; only the new generation can admit.
    assert await coord.admit(lid, 1) is None
    await _arm(pg_dsn, lid)
    fresh = await coord.admit(lid, 2)
    assert fresh is not None and fresh["generation"] == 2
    await fresh.finish()


@pytest.mark.asyncio
async def test_held_source_run_blocks_destination_until_it_finishes_and_cadence_is_kept(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    lid = state["logical_schedule_id"]
    await _arm(pg_dsn, lid, seconds_ago=0.05, interval_seconds=1)
    await coord.set_desired(lid, paused=False)
    running = await coord.admit(lid, 1)  # an admitted source run holds the shared owner lock
    assert running is not None
    plan = await svc.preview(_ident(), TENANT, actor=77)
    assert plan["can_move"], plan["blockers"]
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    pending = await svc.advance(oid, drain_timeout_seconds=0.4)
    assert pending["state"] == "draining" and await _row(pg_dsn, TENANT) is None
    cursor = await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state")
    await asyncio.sleep(2.5)  # slots become due while the source run is still executing
    assert await coord.admit(lid, 1) is None  # frozen: the late wakeup admits nothing
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == cursor
    await running.finish()
    done = await svc.advance(oid)
    assert done["state"] == "completed"
    # The slots missed during the drain are handled ONCE at the destination (coalescing), not replayed.
    await _arm(pg_dsn, lid, seconds_ago=3.0, interval_seconds=1)
    first = await coord.admit(lid, 2)
    assert first is not None
    await first.finish()
    assert await coord.admit(lid, 2) is None
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 2


@pytest.mark.asyncio
async def test_query_multi_and_cache_schedules_move_together_and_other_stores_are_untouched(pg_dsn) -> None:
    await _setup(pg_dsn)
    both = {
        "provider": "db", "attributes": SCHEDULED, "is_cached": True,
        "cache_options": {"schedule_type": "interval", "schedule": {"minutes": 10}},
    }
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, is_cached=True,
                  cache_options={"schedule_type": "interval", "schedule": {"minutes": 10}},
                  **{RUN_AS_COLUMN: RUN_AS})
    await _insert(pg_dsn, TENANT, slug="s_other")
    svc, coord = await _build(pg_dsn, real_clock=True)
    states = await coord.bootstrap_row(OWNER, both)
    assert set(states) == {"query", "cache"}
    # The same slug text scheduled in a THIRD store is a different identity and must stay untouched.
    other = ScheduleOwner(OWNER.namespace, "other_schema", "queries", "s")
    other_states = await coord.bootstrap_row(other, {"provider": "db", "attributes": SCHEDULED})
    plan = await svc.preview(_ident(), TENANT, actor=77)
    assert plan["can_move"], plan["blockers"]
    assert sorted(s["kind"] for s in plan["schedules"]) == ["cache", "query"]
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    assert (await svc.advance(oid))["state"] == "completed"
    moved = await _rows(pg_dsn, "SELECT kind, logical_schedule_id::text AS lid, owner_schema, generation "
                                "FROM management.qs_schedule_state WHERE owner_schema = 'wm_assembly' ORDER BY kind")
    assert [(m["kind"], m["owner_schema"], m["generation"]) for m in moved] == [
        ("cache", "wm_assembly", 2), ("query", "wm_assembly", 2)]
    assert {m["lid"] for m in moved} == {s["logical_schedule_id"] for s in states.values()}  # ids survive
    assert other_states["query"]["logical_schedule_id"] not in {m["lid"] for m in moved}
    untouched = await _rows(pg_dsn, "SELECT generation FROM management.qs_schedule_state WHERE owner_schema = $1",
                            "other_schema")
    assert len(untouched) == 1 and untouched[0]["generation"] == 1
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_state") == 3


@pytest.mark.asyncio
async def test_enabled_schedule_already_at_the_destination_identity_refuses_the_cutover(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn, real_clock=True)
    await coord.bootstrap_row(OWNER, BOOT)
    plan = await svc.preview(_ident(), TENANT, actor=77)
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    await coord.prepare(oid)
    await coord.bootstrap_row(ScheduleOwner.from_store(TENANT, "s"), BOOT)  # a stray enabled state appears
    failed = await svc.cutover(oid)
    assert failed["state"] == "failed" and failed["error_code"] == "move_scheduler_not_ready"
    assert await _row(pg_dsn, LEGACY) is not None and await _row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_lost_commit_ack_with_schedules_resolves_to_one_atomic_owner_change(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, **{RUN_AS_COLUMN: RUN_AS})
    faults = _Faults()
    svc, coord = await _build(pg_dsn, faults, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    plan = await svc.preview(_ident(), TENANT, actor=77)
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    await coord.prepare(oid)
    faults.commit = "post"  # COMMIT executes, the acknowledgement is lost
    view = await svc.cutover(oid)
    assert view["moved"] is True
    # Row, owner/generation, run-as audit and journal all changed together (or none would have).
    assert await _row(pg_dsn, LEGACY) is None and await _row(pg_dsn, TENANT) is not None
    assert await _scalar(pg_dsn, "SELECT generation FROM management.qs_schedule_state") == 2
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries_run_as_audit"') == 1
    faults.commit = None
    assert (await svc.advance(oid))["state"] == "completed"
    assert await coord.admit(state["logical_schedule_id"], 1) is None


@pytest.mark.asyncio
async def test_workers_crashed_at_each_phase_recover_without_replay_or_second_move(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    lid = state["logical_schedule_id"]
    plan = await svc.preview(_ident(), TENANT, actor=77)
    oid = (await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                          actor=77, acknowledgements=ACKS))["operation_id"]
    # 1. crashed before freeze: stale foreign lease, nothing frozen yet.
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_query_moves SET lease_owner = 'dead', "
                           "lease_expires_at = now() - interval '1 min'")
    assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "open"
    # 2. crashed during drain: gate frozen, an admitted run whose worker died (unknown outcome).
    await coord.prepare(oid)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute(
            "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at, "
            "owner_namespace, owner_schema, owner_table, query_slug, outcome) VALUES ($1::uuid, 1, now(), $2, $3, $4, $5, "
            "'running')", uuid.UUID(lid), *OWNER.as_tuple())
        await conn.execute("UPDATE management.qs_query_moves SET lease_owner = 'dead', "
                           "lease_expires_at = now() - interval '1 min'")
    stuck = (await svc.recover())[0]
    assert stuck["state"] == "draining" and "unknown outcome" in stuck["message"]
    assert await _row(pg_dsn, LEGACY) is not None  # never replayed, never forced
    async with await _factory(pg_dsn)() as conn:  # operator reconciles the attempt
        await conn.execute("UPDATE management.qs_schedule_runs SET outcome = 'failed', error = 'operator'")
        await conn.execute("UPDATE management.qs_query_moves SET lease_owner = NULL, lease_expires_at = NULL")
    # 3. crashed after cutover but before the mirror update: recover only reconciles.
    assert (await svc.cutover(oid))["moved"] is True
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_query_moves SET lease_owner = 'dead', "
                           "lease_expires_at = now() - interval '1 min'")
    assert await coord.admit(lid, 1) is None  # stale mirror fenced before any reconciliation
    done = (await svc.recover())[0]
    assert done["state"] == "completed"
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1  # no replay


@pytest.mark.asyncio
async def test_control_pool_exhaustion_fails_admission_closed(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn, real_clock=True)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    await _arm(pg_dsn, state["logical_schedule_id"])
    real = coord.connection_factory

    async def exhausted():
        raise ConnectionError("control pool exhausted")

    coord.connection_factory = exhausted
    with pytest.raises(ConnectionError):
        await coord.admit(state["logical_schedule_id"], 1)  # the caller must treat this as "do not run"
    coord.connection_factory = real
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 0
    assert (await coord.admit(state["logical_schedule_id"], 1)) is not None  # the slot was not consumed
