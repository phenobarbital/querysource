"""Lifecycle: the durable move worker starts/stops independently of ENABLE_QS_SCHEDULER and recovers first."""
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from test_query_moves_postgres import (  # noqa: E402,F401 - pg_dsn is a fixture
    ACKS,
    LEGACY,
    TENANT,
    _build,
    _factory,
    _ident,
    _insert,
    _row,
    _scalar,
    _setup,
    pg_dsn,
)

from querysource.services import QuerySource  # noqa: E402


async def _submit(svc):
    plan = await svc.preview(_ident(), TENANT, actor=77)
    op = await svc.move(_ident(), TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                        actor=77, acknowledgements=ACKS)
    return op["operation_id"]


def _qs(repo):
    """A stand-in for the QuerySource singleton (the lifecycle methods only read these attributes)."""
    return SimpleNamespace(_definition_repository=repo, _scheduler=None, _move_worker_task=None)


@pytest.mark.asyncio
async def test_missing_control_tables_disable_moves_without_ddl_or_failure(pg_dsn) -> None:
    await _setup(pg_dsn)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS management CASCADE")
    from test_query_moves import _Repo

    repo = _Repo(pg_dsn, {})
    app: dict = {}
    qs = _qs(repo)
    await QuerySource._start_move_service(qs, app)
    assert "qs_move_service" not in app and "qs_move_worker" not in app and qs._move_worker_task is None
    # Nothing was created: startup never runs DDL.
    assert await _scalar(pg_dsn, "SELECT to_regclass('management.qs_query_moves') IS NULL")


@pytest.mark.asyncio
async def test_startup_recovers_unfinished_operations_then_worker_polls_and_stops_cleanly(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)  # control tables deployed; ENABLE_QS_SCHEDULER is not involved
    oid = await _submit(svc)
    assert await _row(pg_dsn, LEGACY) is not None  # reserved, not yet moved
    app: dict = {}
    qs = _qs(svc.repository)
    await QuerySource._start_move_service(qs, app)
    try:
        assert app["qs_move_service"] is not None and app["qs_move_worker"] is not None
        # Recovery ran inside startup, before any scheduler hook could enable dispatch.
        assert await _scalar(pg_dsn, "SELECT state FROM management.qs_query_moves") == "completed"
        assert await _row(pg_dsn, LEGACY) is None and await _row(pg_dsn, TENANT) is not None
        # A later operation is picked up by polling alone (no notification, no wake()).
        await _insert(pg_dsn, LEGACY, slug="t")
        plan = await app["qs_move_service"].preview(_ident("t"), TENANT, actor=77)
        await app["qs_move_service"].move(_ident("t"), TENANT, plan_revision=plan["plan_revision"],
                                          idempotency_key=str(uuid.uuid4()), actor=77, acknowledgements=ACKS)
        app["qs_move_worker"].poll_seconds = 0.05
        for _ in range(200):
            if await _row(pg_dsn, TENANT, "t") is not None:
                break
            await asyncio.sleep(0.05)
        assert await _row(pg_dsn, TENANT, "t") is not None and await _row(pg_dsn, LEGACY, "t") is None
    finally:
        task = qs._move_worker_task
        await QuerySource.qs_stop(qs, app)
    assert task.done() and qs._move_worker_task is None


@pytest.mark.asyncio
async def test_scheduler_mirror_is_attached_only_when_a_scheduler_exists(pg_dsn) -> None:
    await _setup(pg_dsn)
    svc, coord = await _build(pg_dsn)
    app = {"qs_move_service": svc}
    qs = _qs(svc.repository)
    await QuerySource._attach_move_mirror(qs, app)  # scheduler disabled: nothing to mirror
    assert coord._mirror is None
    qs._scheduler = object()
    await QuerySource._attach_move_mirror(qs, app)
    assert coord._mirror is qs._scheduler
    await QuerySource._attach_move_mirror(qs, {})  # no service published: no-op


@pytest.mark.asyncio
async def test_unscheduled_move_never_needs_a_scheduler_worker(pg_dsn) -> None:
    """With the scheduler disabled there is no compatible worker heartbeat, yet an unscheduled move proceeds."""
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("DELETE FROM management.qs_scheduler_workers")
    plan = await svc.preview(_ident(), TENANT, actor=77)
    assert plan["can_move"] is True and plan["schedules"] == []
    oid = await _submit(svc)
    assert (await svc.advance(oid))["state"] == "completed"
