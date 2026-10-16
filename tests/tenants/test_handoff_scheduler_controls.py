"""Scheduler pause/resume/removal/sync through durable desired state and move reservations."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from aiohttp import web
from apscheduler.triggers.interval import IntervalTrigger
from test_handoff_state import (  # noqa: F401 - pg_dsn is a fixture
    NS,
    _coordinator,
    _factory,
    _reset,
    _store,
    pg_dsn,
)

from querysource.handlers.scheduler import SchedulerJobsView
from querysource.scheduler.handoff import ScheduleOwner
from querysource.tenant_errors import TenantError

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
SPEC = {"schedule_type": "interval", "schedule": {"minutes": 5}}


def _parse(schedule_type, schedule):
    return IntervalTrigger(start_date=T0, timezone="UTC", minutes=5)


def _coord(dsn):
    return _coordinator(dsn, trigger_parser=_parse, clock=lambda: T0)


async def _seed(dsn, coord, owner, kind="query"):
    async with await _factory(dsn)() as conn:
        await conn.transaction()
        state = await coord.ensure_schedule_state(
            conn, owner, kind, trigger_spec=SPEC, anchor_at=T0, next_fire_at=T0
        )
        await conn.commit()
    return state


async def _reserve(dsn, source: ScheduleOwner, target: ScheduleOwner) -> str:
    op = uuid.uuid4()
    async with await _factory(dsn)() as conn:
        res = await conn.execute(
            "INSERT INTO management.qs_query_moves (operation_id, deployment_id, actor_id, idempotency_key,"
            " request_digest, query_slug, source_namespace, source_schema, source_table, source_contract,"
            " target_namespace, target_schema, target_table, target_contract, plan_snapshot)"
            " VALUES ($1::uuid, 'd', 1, $2::uuid, 'x', $3, $4, $5, $6, 'tenant', $7, $8, $9, 'tenant', '{}'::jsonb)",
            op, uuid.uuid4(), source.slug, source.namespace, source.schema, source.table,
            target.namespace, target.schema, target.table,
        )
        assert res[1] is None, res
        for role, owner in (("source", source), ("target", target)):
            res = await conn.execute(
                "INSERT INTO management.qs_move_reservations (namespace_id, schema_name, table_name, query_slug,"
                " operation_id, role) VALUES ($1, $2, $3, $4, $5::uuid, $6)",
                *owner.as_tuple(), op, role,
            )
            assert res[1] is None, res
    return str(op)


async def _state(dsn, lid):
    async with await _factory(dsn)() as conn:
        return await conn.fetch_one("SELECT * FROM management.qs_schedule_state WHERE logical_schedule_id = $1::uuid",
                                    uuid.UUID(str(lid)))


@pytest.mark.asyncio
async def test_pause_resume_changes_revision_only_on_real_change(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    state = await _seed(pg_dsn, coord, ScheduleOwner(NS, "public", "queries", "s"))
    lid = state["logical_schedule_id"]
    paused = await coord.set_desired(lid, paused=True)
    assert paused["paused"] is True and paused["config_revision"] == state["config_revision"] + 1
    again = await coord.set_desired(lid, paused=True)  # idempotent: no revision churn
    assert again["config_revision"] == paused["config_revision"]
    resumed = await coord.set_desired(lid, paused=False)
    assert resumed["paused"] is False and resumed["config_revision"] == paused["config_revision"] + 1
    assert resumed["generation"] == state["generation"]
    assert await coord.set_desired(str(uuid.uuid4()), paused=True) is None


@pytest.mark.asyncio
async def test_cursor_advancement_does_not_bump_configuration_revision(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    state = await _seed(pg_dsn, coord, ScheduleOwner(NS, "public", "queries", "s"))
    run = await coord.admit(state["logical_schedule_id"], 1)
    assert run is not None
    await run.finish()
    after = await _state(pg_dsn, state["logical_schedule_id"])
    assert after["next_fire_at"] > state["next_fire_at"]
    assert after["config_revision"] == state["config_revision"]


@pytest.mark.asyncio
async def test_reserved_or_frozen_schedule_rejects_controls_with_move_in_progress(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    source = ScheduleOwner(NS, "public", "queries", "s")
    target = ScheduleOwner(NS, "wm", "queries", "s")
    state = await _seed(pg_dsn, coord, source)
    other = await _seed(pg_dsn, coord, ScheduleOwner(NS, "public", "queries", "unrelated"))
    op = await _reserve(pg_dsn, source, target)

    for owner in (source, target):
        with pytest.raises(TenantError) as err:
            await coord.assert_not_moving([owner])
        assert err.value.error_code == "move_in_progress" and err.value.code == 409
    with pytest.raises(TenantError) as err:
        await coord.set_desired(state["logical_schedule_id"], paused=True)
    assert err.value.error_code == "move_in_progress"
    assert (await _state(pg_dsn, state["logical_schedule_id"]))["paused"] is False
    assert (await coord.set_desired(other["logical_schedule_id"], paused=True))["paused"] is True

    # Reservation released but the shared generation still frozen: a local control cannot bypass it.
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("DELETE FROM management.qs_move_reservations WHERE operation_id = $1::uuid",
                           uuid.UUID(op))
        await conn.execute("UPDATE management.qs_schedule_state SET dispatch_gate = 'frozen'")
    await coord.assert_not_moving([source, target])  # no reservation any more
    with pytest.raises(TenantError):
        await coord.set_desired(state["logical_schedule_id"], enabled=False)


@pytest.mark.asyncio
async def test_same_slug_in_custom_default_and_literal_public_are_distinct_owners(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    custom = _store("legacy_qs", "saved_queries", "legacy")
    literal = _store("public", "queries", "legacy")
    a = await _seed(pg_dsn, coord, ScheduleOwner.from_store(custom, "same"))
    b = await _seed(pg_dsn, coord, ScheduleOwner.from_store(literal, "same"))
    assert a["logical_schedule_id"] != b["logical_schedule_id"]
    await coord.set_desired(a["logical_schedule_id"], paused=True)
    assert (await _state(pg_dsn, b["logical_schedule_id"]))["paused"] is False
    # Reserving the literal-public owner never blocks the custom default.
    await _reserve(pg_dsn, ScheduleOwner.from_store(literal, "same"), ScheduleOwner(NS, "wm", "queries", "same"))
    await coord.assert_not_moving([ScheduleOwner.from_store(custom, "same")])
    with pytest.raises(TenantError):
        await coord.assert_not_moving([ScheduleOwner.from_store(literal, "same")])


# -- HTTP view ----------------------------------------------------------------------------------


class _Aps:
    def __init__(self, job) -> None:
        self.job = job

    def get_job(self, job_id):
        return self.job if self.job is not None and job_id == self.job.id else None


class _Sched:
    def __init__(self, coordinator, job) -> None:
        self._coordinator = coordinator
        self._scheduler = _Aps(job)
        self._registry = SimpleNamespace()
        self.calls: list[tuple] = []

    def set_job_paused(self, job_id, paused):
        self.calls.append(("pause", job_id, paused))
        return True

    def remove_job(self, job_id):
        self.calls.append(("remove", job_id))
        return True


def _view(sched, body=None, job_id="query_s"):
    request = mock.MagicMock(spec=web.Request)

    async def _json():
        return body

    request.json = _json
    view = SchedulerJobsView(request)
    view._get_scheduler = lambda: sched
    view.match_parameters = lambda request: {"job_id": job_id}
    view.json_response = lambda response, status=200, headers=None: web.json_response(
        response, status=status, headers=headers
    )
    return view


@pytest.mark.asyncio
async def test_http_pause_and_delete_route_through_durable_state(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    state = await _seed(pg_dsn, coord, ScheduleOwner(NS, "public", "queries", "s"))
    lid = state["logical_schedule_id"]
    job = SimpleNamespace(id="query_s", kwargs={"logical_schedule_id": lid})
    sched = _Sched(coord, job)

    resp = await _view(sched, {"action": "pause"}).patch()
    assert resp.status == 200  # response convention unchanged
    assert sched.calls == [("pause", "query_s", True)]
    assert (await _state(pg_dsn, lid))["paused"] is True

    resp = await _view(sched).delete()
    assert resp.status == 200
    assert sched.calls[-1] == ("remove", "query_s")
    assert (await _state(pg_dsn, lid))["enabled"] is False


@pytest.mark.asyncio
async def test_http_controls_on_moving_query_return_409_and_leave_local_job_alone(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    source = ScheduleOwner(NS, "public", "queries", "s")
    state = await _seed(pg_dsn, coord, source)
    await _reserve(pg_dsn, source, ScheduleOwner(NS, "wm", "queries", "s"))
    job = SimpleNamespace(id="query_s", kwargs={"logical_schedule_id": state["logical_schedule_id"]})
    sched = _Sched(coord, job)

    for resp in (await _view(sched, {"action": "pause"}).patch(), await _view(sched).delete()):
        assert resp.status == 409
        assert b"move_in_progress" in resp.body
    assert sched.calls == []  # the local mirror was not touched
    assert (await _state(pg_dsn, state["logical_schedule_id"]))["paused"] is False


@pytest.mark.asyncio
async def test_http_sync_of_reserved_identity_is_rejected_before_registration(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    store = _store("public", "queries", "legacy")
    owner = ScheduleOwner.from_store(store, "s")
    await _reserve(pg_dsn, owner, ScheduleOwner(NS, "wm", "queries", "s"))
    registry = SimpleNamespace(resolve=lambda tenant=None: store)
    sched = _Sched(coord, None)
    sched.register_slug = mock.AsyncMock(return_value={"registered": [], "removed": []})
    request = mock.MagicMock(spec=web.Request)

    async def _json():
        return {"slug": "s"}

    request.json = _json
    request.app = {"qs_tenant_registry": registry}
    view = SchedulerJobsView(request)
    view._get_scheduler = lambda: sched
    view.json_response = lambda response, status=200, headers=None: web.json_response(
        response, status=status, headers=headers
    )
    resp = await view.post()
    assert resp.status == 409
    sched.register_slug.assert_not_awaited()
