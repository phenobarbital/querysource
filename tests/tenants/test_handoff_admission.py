"""Due-occurrence admission: one claim per slot, shared fence retained, cadence preserved."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from test_handoff_state import (  # noqa: F401 - pg_dsn is a fixture
    NS,
    _coordinator,
    _factory,
    _reset,
    _store,
    pg_dsn,
)

from querysource.scheduler.handoff import ScheduleOwner

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
SPEC = {"schedule_type": "interval", "schedule": {"minutes": 5}}


def _parse(schedule_type, schedule):
    assert schedule_type == "interval"
    return IntervalTrigger(start_date=T0, timezone="UTC", **schedule)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _admission_coordinator(dsn, clock, **kw):
    return _coordinator(dsn, trigger_parser=_parse, clock=clock, **kw)


async def _seed(dsn, coord, *, cursor=T0, kind="query", slug="s", **kw):
    owner = ScheduleOwner(NS, "public", "queries", slug)
    async with await _factory(dsn)() as conn:
        await conn.transaction()
        state = await coord.ensure_schedule_state(
            conn, owner, kind, trigger_spec=SPEC, anchor_at=T0, next_fire_at=cursor,
            misfire_grace_seconds=kw.pop("misfire_grace_seconds", 600), **kw,
        )
        await conn.commit()
    return state


async def _scalar(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(sql, *args, column=0)


@pytest.mark.asyncio
async def test_concurrent_wakeups_admit_each_slot_once(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    state = await _seed(pg_dsn, coord)
    sid = state["logical_schedule_id"]

    results = await asyncio.gather(*[coord.admit(sid, 1) for _ in range(8)])
    admitted = [r for r in results if r is not None]
    assert len(admitted) == 1
    run = admitted[0]
    assert run["scheduled_fire_at"] == T0 and run["generation"] == 1 and run["kind"] == "query"
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1
    # Cursor advanced by the trigger's own cadence, never reset to now.
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == T0 + timedelta(minutes=5)
    assert await _scalar(pg_dsn, "SELECT last_claimed_fire_at FROM management.qs_schedule_state") == T0
    await run.finish()
    # Same slot cannot be re-admitted even after finish (no replay); next slot is not due yet.
    assert await coord.admit(sid, 1) is None
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1


@pytest.mark.asyncio
async def test_rejections_stale_paused_frozen_not_due_and_exclusive_holder(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord))["logical_schedule_id"]
    runs = "SELECT count(*) FROM management.qs_schedule_runs"

    assert await coord.admit(sid, 2) is None  # stale generation
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET paused = true")
    assert await coord.admit(sid, 1) is None
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET paused = false, dispatch_gate = 'frozen'")
    assert await coord.admit(sid, 1) is None
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET dispatch_gate = 'open'")
    clock.now = T0 - timedelta(minutes=1)
    assert await coord.admit(sid, 1) is None  # not due
    clock.now = T0 + timedelta(seconds=1)

    # An exclusive holder (a move draining/cutting over) rejects new admissions.
    key = ScheduleOwner(NS, "public", "queries", "s").lock_key()
    async with await _factory(pg_dsn)() as holder:
        await holder.fetchval("SELECT pg_advisory_lock($1)", key, column=0)
        assert await coord.admit(sid, 1) is None
        await holder.fetchval("SELECT pg_advisory_unlock($1)", key, column=0)
    assert await _scalar(pg_dsn, runs) == 0
    run = await coord.admit(sid, 1)
    assert run is not None
    await run.finish()


@pytest.mark.asyncio
async def test_shared_fence_retained_until_finish(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord))["logical_schedule_id"]
    run = await coord.admit(sid, 1)
    key = ScheduleOwner(NS, "public", "queries", "s").lock_key()
    async with await _factory(pg_dsn)() as other:
        # A mover's exclusive lock cannot be taken while the admitted run is alive...
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0) is False
        await run.mark_running()
        await run.finish("failed", "boom")
        # ...and drain completes as soon as the run finishes.
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0) is True
        await other.fetchval("SELECT pg_advisory_unlock($1)", key, column=0)
    row = await _scalar(pg_dsn, "SELECT outcome || ':' || error FROM management.qs_schedule_runs")
    assert row == "failed:boom"
    await run.finish()  # idempotent


@pytest.mark.asyncio
async def test_coalescing_misfire_and_cadence(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(minutes=12))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, misfire_grace_seconds=3600))["logical_schedule_id"]

    # 08:00, 08:05 and 08:10 are all due: coalesce to the latest, one run, cursor at 08:15.
    run = await coord.admit(sid, 1)
    assert run["scheduled_fire_at"] == T0 + timedelta(minutes=10)
    await run.finish()
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == T0 + timedelta(minutes=15)

    # Default misfire grace (1s): a slot that is late is consumed without running.
    await _reset(pg_dsn)
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, misfire_grace_seconds=None))["logical_schedule_id"]
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET misfire_grace_seconds = NULL")
    assert await coord.admit(sid, 1) is None
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 0
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == T0 + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_without_coalescing_runs_each_missed_slot_in_order(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(minutes=7))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, coalesce_missed=False, max_instances=3))["logical_schedule_id"]
    first = await coord.admit(sid, 1)
    second = await coord.admit(sid, 1)
    third = await coord.admit(sid, 1)
    assert [first["scheduled_fire_at"], second["scheduled_fire_at"]] == [T0, T0 + timedelta(minutes=5)]
    assert third is None  # caught up
    for run in (first, second):
        await run.finish()


@pytest.mark.asyncio
async def test_max_instances_consumes_slot_and_unknown_runs_are_never_replayed(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, max_instances=1))["logical_schedule_id"]
    first = await coord.admit(sid, 1)
    clock.now = T0 + timedelta(minutes=5, seconds=1)
    assert await coord.admit(sid, 1) is None  # still running: slot skipped, cursor advances
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == T0 + timedelta(minutes=10)
    await first.finish("unknown", "remote outcome could not be established")

    # The skipped/unknown slots are never retried; only the next slot admits.
    assert await coord.admit(sid, 1) is None
    clock.now = T0 + timedelta(minutes=10, seconds=1)
    nxt = await coord.admit(sid, 1)
    assert nxt["scheduled_fire_at"] == T0 + timedelta(minutes=10)
    await nxt.finish()
    outcomes = sorted(r for r in await _all(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs"))
    assert outcomes == ["completed", "unknown"]


async def _all(dsn, sql):
    async with await _factory(dsn)() as conn:
        return [r["outcome"] for r in await conn.fetch_all(sql)]


@pytest.mark.asyncio
async def test_timezone_and_biweekly_anchor_survive_via_real_trigger_parser(pg_dsn) -> None:
    """The default parser is the scheduler's own, so timezone/anchor match registration."""
    from querysource.scheduler.scheduler import QSScheduler

    await _reset(pg_dsn)
    sched = QSScheduler()
    spec = {"schedule_type": "biweekly",
            "schedule": {"day_of_week": "mon", "hour": 9, "minute": 30,
                         "start_date": "2026-01-05", "timezone": "Europe/Madrid"}}
    trigger = sched._parse_trigger(spec["schedule_type"], spec["schedule"])
    first = trigger.get_next_fire_time(None, datetime(2026, 1, 1, tzinfo=timezone.utc))
    clock = _Clock(first + timedelta(seconds=1))
    coord = _coordinator(pg_dsn, clock=clock, trigger_parser=sched._parse_trigger)
    owner = ScheduleOwner(NS, "public", "queries", "bw")
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        state = await coord.ensure_schedule_state(
            conn, owner, "query", trigger_spec=spec, timezone_name="Europe/Madrid",
            anchor_at=first, next_fire_at=first,
        )
        await conn.commit()
    run = await coord.admit(state["logical_schedule_id"], 1)
    assert run is not None and run["scheduled_fire_at"] == first
    assert await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state") == first + timedelta(weeks=2)
    await run.finish()


async def _orphan_run(dsn, sid, slot, *, outcome="running", slug="s"):
    """A run row whose worker died (SIGKILL/OOM): no session holds the owner fence any more."""
    import uuid as _uuid

    async with await _factory(dsn)() as conn:
        await conn.fetch_one(
            "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at, "
            "owner_namespace, owner_schema, owner_table, query_slug, worker_id, outcome) "
            "VALUES ($1::uuid, 1, $2, $3, 'public', 'queries', $4, 'dead-worker', $5) RETURNING run_id",
            _uuid.UUID(sid), slot, NS, slug, outcome,
        )


@pytest.mark.asyncio
async def test_orphaned_open_run_is_expired_unknown_and_never_blocks_future_occurrences(pg_dsn) -> None:
    """B1: a dead worker's open run must not consume every future occurrence as max_instances."""
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(minutes=5, seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, cursor=T0 + timedelta(minutes=5), max_instances=1))["logical_schedule_id"]
    await _orphan_run(pg_dsn, sid, T0)

    run = await coord.admit(sid, 1)
    assert run is not None and run["scheduled_fire_at"] == T0 + timedelta(minutes=5)
    await run.finish()
    rows = await _all(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs ORDER BY scheduled_fire_at")
    assert rows == ["unknown", "completed"]
    error = await _scalar(pg_dsn, "SELECT error FROM management.qs_schedule_runs WHERE outcome = 'unknown'")
    assert "worker lost" in error
    assert await _scalar(pg_dsn, "SELECT finished_at IS NOT NULL FROM management.qs_schedule_runs "
                                 "WHERE outcome = 'unknown'")


@pytest.mark.asyncio
async def test_open_run_with_a_live_fence_holder_is_not_expired(pg_dsn) -> None:
    """A run whose session still holds the shared owner fence is alive: it keeps max_instances."""
    await _reset(pg_dsn)
    clock = _Clock(T0 + timedelta(minutes=5, seconds=1))
    coord = _admission_coordinator(pg_dsn, clock)
    sid = (await _seed(pg_dsn, coord, cursor=T0 + timedelta(minutes=5), max_instances=1))["logical_schedule_id"]
    await _orphan_run(pg_dsn, sid, T0)
    key = ScheduleOwner(NS, "public", "queries", "s").lock_key()
    async with await _factory(pg_dsn)() as live:
        await live.fetchval("SELECT pg_advisory_lock_shared($1)", key, column=0)
        assert await coord.admit(sid, 1) is None  # max_instances reached by a LIVE run
        await live.fetchval("SELECT pg_advisory_unlock_shared($1)", key, column=0)
    assert await _all(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == ["running"]


@pytest.mark.asyncio
async def test_run_outcome_is_recorded_even_when_cancelled_twice() -> None:
    """``finish`` is shielded: a second cancellation cannot skip the outcome/fence release."""
    from querysource.scheduler.jobs import _fenced

    started, done = asyncio.Event(), asyncio.Event()
    recorded: list = []

    class _Run(dict):
        async def mark_running(self) -> None:
            return None

        async def finish(self, outcome, error=None) -> None:
            started.set()
            await asyncio.sleep(0.1)
            recorded.append(outcome)
            done.set()

    class _Coord:
        async def admit(self, sid, generation):
            return _Run()

    async def execute() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_fenced(
        label="job", slug="s", owner=None, job_id="j", notification_manager=None, coordinator=_Coord(),
        logical_schedule_id="x", generation=1, execute=execute,
    ))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(done.wait(), 1)
    assert recorded == ["unknown"]
