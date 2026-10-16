"""Durable schedule bootstrap and scheduler-mirror reconciliation (isolated PostgreSQL, no production writes)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from test_handoff_state import (  # noqa: F401 - pg_dsn is a fixture
    NS,
    _coordinator,
    _factory,
    _reset,
    pg_dsn,
)

from querysource.scheduler.handoff import ScheduleOwner
from querysource.tenant_errors import TenantError

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _parse(schedule_type, schedule):
    assert schedule_type == "interval"
    kwargs = {k: v for k, v in schedule.items() if k in ("minutes", "hours", "seconds")}
    start = schedule.get("start_date")
    start = datetime.fromisoformat(start) if isinstance(start, str) else T0
    return IntervalTrigger(start_date=start, timezone="UTC", **kwargs)


def _row(minutes: int = 5, *, start: str | None = None) -> dict:
    schedule = {"minutes": minutes}
    if start:
        schedule["start_date"] = start
    return {
        "provider": "db",
        "attributes": {"scheduler": {"schedule_type": "interval", "schedule": schedule}},
    }


def _coord(dsn, clock):
    return _coordinator(dsn, trigger_parser=_parse, clock=clock)


async def _scalar(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(sql, *args, column=0)


@pytest.mark.asyncio
async def test_bootstrap_persists_anchor_once_and_is_idempotent(pg_dsn) -> None:
    await _reset(pg_dsn)
    clock = _Clock(T0)
    coord = _coord(pg_dsn, clock)
    owner = ScheduleOwner(NS, "public", "queries", "s")
    first = (await coord.bootstrap_row(owner, _row()))["query"]
    clock.now = T0 + timedelta(hours=3)  # a later process must not re-anchor from now
    second = (await coord.bootstrap_row(owner, _row()))["query"]
    assert first["logical_schedule_id"] == second["logical_schedule_id"]
    assert second["anchor_at"] == first["anchor_at"] == T0
    assert second["next_fire_at"] == first["next_fire_at"]
    assert second["config_revision"] == first["config_revision"]
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_state") == 1


@pytest.mark.asyncio
async def test_bootstrap_preserves_paused_state_and_flags_conflicting_mirror(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn, _Clock(T0))
    owner = ScheduleOwner(NS, "public", "queries", "s")
    state = (await coord.bootstrap_row(owner, _row()))["query"]
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET paused = true")
    again = (await coord.bootstrap_row(owner, _row()))["query"]
    assert again["paused"] is True  # a starting process has no paused opinion
    assert again["logical_schedule_id"] == state["logical_schedule_id"]
    # A process that observed a different paused state is flagged, never overwritten.
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        flagged = await coord.ensure_schedule_state(
            conn, owner, "query", trigger_spec=again["trigger_spec"], anchor_at=T0, next_fire_at=T0,
            observed_paused=False,
        )
        await conn.commit()
    assert flagged["trigger_spec"]["mirror_conflict"] is True
    assert flagged["paused"] is True


@pytest.mark.asyncio
async def test_configuration_change_replaces_trigger_and_disappearance_disables(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn, _Clock(T0))
    owner = ScheduleOwner(NS, "public", "queries", "s")
    first = (await coord.bootstrap_row(owner, _row(5)))["query"]
    changed = (await coord.bootstrap_row(owner, _row(10)))["query"]
    assert changed["logical_schedule_id"] == first["logical_schedule_id"]
    assert changed["config_revision"] == first["config_revision"] + 1
    assert changed["trigger_spec"]["schedule"]["minutes"] == 10
    assert changed["generation"] == first["generation"]  # an ordinary save never changes the fence
    assert await coord.bootstrap_row(owner, None) == {}
    assert await _scalar(pg_dsn, "SELECT enabled FROM management.qs_schedule_state") is False
    back = (await coord.bootstrap_row(owner, _row(10)))["query"]
    assert back["enabled"] is True


async def _moved_operation(dsn, source, target, *, moved=True, state="reconciling") -> str:
    op = uuid.uuid4()
    async with await _factory(dsn)() as conn:
        res = await conn.execute(
            "INSERT INTO management.qs_query_moves (operation_id, deployment_id, actor_id, idempotency_key,"
            " request_digest, query_slug, source_namespace, source_schema, source_table, source_contract,"
            " target_namespace, target_schema, target_table, target_contract, plan_snapshot, state, moved)"
            " VALUES ($1::uuid, 'd', 1, $2::uuid, 'x', $3, $4, $5, $6, 'tenant', $7, $8, $9, 'tenant', '{}'::jsonb,"
            " $10, $11)",
            op, uuid.uuid4(), source.slug, source.namespace, source.schema, source.table,
            target.namespace, target.schema, target.table, state, moved,
        )
        assert res[1] is None, res
    return str(op)


class _Mirror:
    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple] = []
        self.fail_times = fail_times

    async def reconcile_owner_change(self, source, target) -> None:
        self.calls.append((source, target))
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("scheduler unavailable")


async def _seed_target(coord, target):
    return (await coord.bootstrap_row(target, _row()))["query"]


@pytest.mark.asyncio
async def test_reconcile_is_idempotent_retryable_and_completes(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn, _Clock(T0))
    source = ScheduleOwner(NS, "public", "queries", "s")
    target = ScheduleOwner(NS, "wm", "queries", "s")
    await _seed_target(coord, target)
    mirror = _Mirror(fail_times=1)
    coord.attach_mirror(mirror)
    op = await _moved_operation(pg_dsn, source, target)

    failed = await coord.reconcile(op)
    assert failed["state"] == "reconciling" and failed["scheduler_sync"] == "failed"
    assert failed["error_code"] == "move_scheduler_not_ready"

    done = await coord.reconcile(op)  # the SAME operation retries; nothing is re-migrated
    assert done["state"] == "completed" and done["scheduler_sync"] == "ok"
    assert len(mirror.calls) == 2

    again = await coord.reconcile(op)  # terminal: a repeat is a no-op
    assert again["state"] == "completed"
    assert len(mirror.calls) == 2
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_state") == 1


@pytest.mark.asyncio
async def test_reconcile_requires_durable_cutover_and_known_operation(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn, _Clock(T0))
    source = ScheduleOwner(NS, "public", "queries", "s")
    target = ScheduleOwner(NS, "wm", "queries", "s")
    mirror = _Mirror()
    coord.attach_mirror(mirror)
    op = await _moved_operation(pg_dsn, source, target, moved=False, state="draining")
    untouched = await coord.reconcile(op)
    assert untouched["state"] == "draining" and mirror.calls == []
    with pytest.raises(TenantError) as err:
        await coord.reconcile(str(uuid.uuid4()))
    assert err.value.error_code == "query_not_found"


@pytest.mark.asyncio
async def test_reconcile_without_schedules_reports_not_configured(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn, _Clock(T0))
    source = ScheduleOwner(NS, "public", "queries", "s")
    target = ScheduleOwner(NS, "wm", "queries", "s")
    mirror = _Mirror()
    coord.attach_mirror(mirror)
    op = await _moved_operation(pg_dsn, source, target)
    done = await coord.reconcile(op)
    assert done["state"] == "completed" and done["scheduler_sync"] == "not_configured"
    assert mirror.calls == []


@pytest.mark.asyncio
async def test_wakeup_after_cutover_cannot_add_catch_up_run(pg_dsn) -> None:
    """Cursor and occurrence ids live in shared state: a stale mirror admits nothing extra."""
    await _reset(pg_dsn)
    clock = _Clock(T0)
    coord = _coord(pg_dsn, clock)
    owner = ScheduleOwner(NS, "public", "queries", "s")
    state = (await coord.bootstrap_row(owner, _row(5, start=T0.isoformat())))["query"]
    clock.now = T0 + timedelta(seconds=1)
    sid = state["logical_schedule_id"]
    run = await coord.admit(sid, int(state["generation"]))
    assert run is not None
    await run.finish()
    cursor = await _scalar(pg_dsn, "SELECT next_fire_at FROM management.qs_schedule_state")
    # A restart/reconcile re-bootstraps from the same definition: cursor retained, no second admission.
    again = (await coord.bootstrap_row(owner, _row(5, start=T0.isoformat())))["query"]
    assert again["next_fire_at"] == cursor
    assert await coord.admit(sid, int(again["generation"])) is None
    # A mirror holding a stale generation is fenced.
    assert await coord.admit(sid, int(again["generation"]) + 1) is None
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1
