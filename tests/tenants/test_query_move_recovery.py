"""Lease-based orchestration and recovery of durable moves (disposable PostgreSQL)."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from test_query_moves_postgres import (  # noqa: E402,F401 - pg_dsn is a fixture
    ACKS,
    LEGACY,
    SCHEDULED,
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

from querysource.query_moves import MoveWorker  # noqa: E402
from querysource.scheduler.handoff import ScheduleOwner  # noqa: E402
from querysource.tenant_errors import TenantError  # noqa: E402

OWNER = ScheduleOwner.from_store(LEGACY, "s")
BOOT = {"provider": "db", "attributes": SCHEDULED}


async def _submit(svc, source=None, destination=TENANT):
    source = source or _ident()
    plan = await svc.preview(source, destination, actor=77)
    assert plan["can_move"], plan["blockers"]
    op = await svc.move(source, destination, plan_revision=plan["plan_revision"],
                        idempotency_key=str(uuid.uuid4()), actor=77, acknowledgements=ACKS)
    return op["operation_id"]


async def _sql(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        res = await conn.execute(sql, *args)
        assert res[1] is None, res


@pytest.mark.asyncio
async def test_advance_drives_an_unscheduled_move_to_completion(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _submit(svc)
    final = await svc.advance(oid)
    assert final["state"] == "completed" and final["moved"] is True and final["scheduler_sync"] == "not_configured"
    assert await _row(pg_dsn, LEGACY) is None and await _row(pg_dsn, TENANT) is not None
    assert await _scalar(pg_dsn, "SELECT lease_owner IS NULL FROM management.qs_query_moves")
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_move_reservations") == 0
    assert (await svc.advance(oid))["state"] == "completed"  # idempotent


@pytest.mark.asyncio
async def test_running_job_leaves_a_visible_pending_drain_then_completes(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn)
    await coord.bootstrap_row(OWNER, BOOT)
    oid = await _submit(svc)
    async with await _factory(pg_dsn)() as holder:
        await holder.fetchval("SELECT pg_advisory_lock_shared($1)", OWNER.lock_key(), column=0)
        pending = await svc.advance(oid, drain_timeout_seconds=0.3)
        assert pending["state"] == "draining" and pending["moved"] is False
        assert await _row(pg_dsn, LEGACY) is not None
        assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "frozen"
        await holder.fetchval("SELECT pg_advisory_unlock_shared($1)", OWNER.lock_key(), column=0)
    done = await svc.advance(oid)
    assert done["state"] == "completed" and await _row(pg_dsn, TENANT) is not None
    assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "open"


@pytest.mark.asyncio
async def test_unknown_run_outcome_is_not_replayed_and_stays_pending(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    await _sql(pg_dsn,
               "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at, "
               "owner_namespace, owner_schema, owner_table, query_slug, outcome) "
               "VALUES ($1::uuid, 1, now(), $2, $3, $4, $5, 'running')",
               uuid.UUID(state["logical_schedule_id"]), *OWNER.as_tuple())
    oid = await _submit(svc)
    pending = await svc.advance(oid, drain_timeout_seconds=0.3)
    assert pending["state"] == "draining" and "unknown outcome" in pending["message"]
    assert await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == "unknown"
    assert await _row(pg_dsn, LEGACY) is not None
    # Only an operator resolving the attempt lets the move proceed — never an automatic replay.
    await _sql(pg_dsn, "UPDATE management.qs_schedule_runs SET outcome = 'failed', error = 'operator reconciled'")
    assert (await svc.advance(oid))["state"] == "completed"
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1


@pytest.mark.asyncio
async def test_lease_blocks_a_second_worker_and_expiry_allows_takeover(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _submit(svc)
    await _sql(pg_dsn, "UPDATE management.qs_query_moves SET lease_owner = 'dead-worker', "
                       "lease_expires_at = now() + interval '5 min'")
    assert await coord.claim_operation_lease(oid) is None
    assert oid not in await coord.list_recoverable_operations()
    assert (await svc.advance(oid))["state"] == "preparing"  # live foreign lease: untouched
    assert await _row(pg_dsn, LEGACY) is not None
    await _sql(pg_dsn, "UPDATE management.qs_query_moves SET lease_expires_at = now() - interval '1 min'")
    assert oid in await coord.list_recoverable_operations()
    views = await svc.recover()
    assert [v["state"] for v in views] == ["completed"]
    assert await _scalar(pg_dsn, "SELECT count(*) FROM \"wm_assembly\".\"queries\"") == 1


@pytest.mark.asyncio
async def test_concurrent_advances_move_exactly_once(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, _ = await _build(pg_dsn)
    oid = await _submit(svc)
    await asyncio.gather(svc.advance(oid), svc.advance(oid), svc.advance(oid))
    assert (await svc.advance(oid))["state"] == "completed"
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "public"."queries"') == 0


@pytest.mark.asyncio
async def test_crash_after_cutover_only_reconciles_and_old_generation_stays_fenced(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED)
    svc, coord = await _build(pg_dsn)
    state = (await coord.bootstrap_row(OWNER, BOOT))["query"]
    await coord.set_desired(state["logical_schedule_id"], paused=True)
    oid = await _submit(svc)
    await coord.prepare(oid)
    assert (await svc.cutover(oid))["moved"] is True  # ... and the worker "crashes" here
    # The source generation is fenced even though no mirror was updated yet.
    assert await coord.admit(state["logical_schedule_id"], state["generation"]) is None

    class _Broken:
        calls = 0

        async def reconcile_owner_change(self, source, target):
            _Broken.calls += 1
            raise RuntimeError("scheduler restarting")

    coord.attach_mirror(_Broken())
    first = await svc.reconcile(oid)
    assert first["state"] == "reconciling" and first["scheduler_sync"] == "failed" and first["moved"] is True
    coord.attach_mirror(None)
    recovered = (await svc.recover())[0]
    assert recovered["state"] == "completed" and recovered["scheduler_sync"] == "ok"
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1
    assert await _scalar(pg_dsn, "SELECT paused FROM management.qs_schedule_state") is True  # never activated


@pytest.mark.asyncio
async def test_crash_before_the_cutover_transaction_resumes_without_a_second_move(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _submit(svc)
    await coord.prepare(oid)
    await _sql(pg_dsn, "UPDATE management.qs_query_moves SET state = 'cutover', moved = NULL")  # marker written, tx never ran
    final = (await svc.recover())[0]
    assert final["state"] == "completed" and final["moved"] is True
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1


@pytest.mark.asyncio
async def test_unknown_cutover_outcome_is_visible_and_resolves_when_locks_free(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _submit(svc)
    await coord.prepare(oid)
    await _sql(pg_dsn, "UPDATE management.qs_query_moves SET state = 'cutover', moved = NULL")
    async with await _factory(pg_dsn)() as holder:  # the original cutover transaction is still alive
        await holder.fetchval("SELECT pg_advisory_lock($1)", OWNER.lock_key(), column=0)
        view = await svc.advance(oid)
        assert view["state"] == "cutover" and view["moved"] is None
        assert "not yet known" in view["message"]
        assert await _row(pg_dsn, LEGACY) is not None
        await holder.fetchval("SELECT pg_advisory_unlock($1)", OWNER.lock_key(), column=0)
    assert (await svc.advance(oid))["state"] == "completed"


@pytest.mark.asyncio
async def test_invalidated_delegated_identity_fails_before_cutover_and_restores_dispatch(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY, attributes=SCHEDULED, scheduler_run_as_user_id=5)

    async def deny(run_as, raw):
        raise TenantError("identity revoked", error_code="move_stale_run_as")

    svc, coord = await _build(pg_dsn, grant_validator=deny)
    await coord.bootstrap_row(OWNER, BOOT)
    oid = await _submit(svc)
    final = await svc.advance(oid)
    assert final["state"] == "failed" and final["moved"] is False and final["error_code"] == "move_stale_run_as"
    assert await _scalar(pg_dsn, "SELECT dispatch_gate FROM management.qs_schedule_state") == "open"
    assert await _row(pg_dsn, LEGACY) is not None and await _row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_pool_exhaustion_is_a_recoverable_visible_state_and_worker_polls_without_notification(pg_dsn) -> None:
    await _setup(pg_dsn)
    await _insert(pg_dsn, LEGACY)
    svc, coord = await _build(pg_dsn)
    oid = await _submit(svc)
    real_factory = coord.connection_factory
    failures = {"left": 3}

    async def exhausted():
        if failures["left"] > 0:
            failures["left"] -= 1
            raise ConnectionError("connection pool exhausted")
        return await real_factory()

    coord.connection_factory = exhausted
    worker = MoveWorker(svc, poll_seconds=0.05)
    with pytest.raises(ConnectionError):
        await worker.run_once()  # listing itself failed: the loop's guard retries on the next pass
    coord.connection_factory = real_factory
    # Mid-operation exhaustion: advance records a message and leaves the operation recoverable.
    failures["left"] = 0
    mid = {"n": 0}

    async def flaky():
        mid["n"] += 1
        if mid["n"] == 3:
            raise ConnectionError("lost lock connection")
        return await real_factory()

    coord.connection_factory = flaky
    view = await svc.advance(oid)
    coord.connection_factory = real_factory
    assert view["state"] in {"preparing", "draining", "cutover", "completed"}
    # No wake() is ever called: the durable poll alone completes it.
    task = asyncio.create_task(worker.run())
    for _ in range(100):
        if await _scalar(pg_dsn, "SELECT state FROM management.qs_query_moves") == "completed":
            break
        await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(task, 5)
    assert await _scalar(pg_dsn, "SELECT state FROM management.qs_query_moves") == "completed"
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "wm_assembly"."queries"') == 1
    assert await _scalar(pg_dsn, 'SELECT count(*) FROM "public"."queries"') == 0
