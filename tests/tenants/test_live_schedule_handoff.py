"""Job fences and drain: gate before QS construction, lock retained, no cancel/replay."""
import asyncio
import importlib
import types
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from test_handoff_state import (  # noqa: F401 - pg_dsn is a fixture
    NS,
    _coordinator,
    _factory,
    _journal,
    _reset,
    _store,
    pg_dsn,
)

from querysource.models import QueryModel
from querysource.scheduler import jobs
from querysource.scheduler.handoff import ScheduleOwner

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
SPEC = {"schedule_type": "interval", "schedule": {"minutes": 5}}
ENVELOPE = {"version": 1, "database_namespace": NS, "schema": "public", "table": "queries", "contract": "legacy"}


def _parse(schedule_type, schedule):
    return IntervalTrigger(start_date=T0, timezone="UTC", **schedule)


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def notify(self, **kw) -> None:
        self.calls.append(kw)


class _Registry:
    def __init__(self, store) -> None:
        self.store = store
        self.asked: list = []

    def resolve(self, tenant=None):
        self.asked.append(tenant)
        return self.store


def _install_fake_qs(monkeypatch, *, store=None, action=None):
    """Patch QS so no business query ever runs; record construction and execution."""
    log = {"built": [], "ran": 0}
    registry = _Registry(store or _store("public", contract="legacy"))

    class FakeQS:
        def __init__(self, slug=None, tenant=None, **kw):
            log["built"].append({"slug": slug, "tenant": tenant, **kw})

        async def get_definition_repository(self):
            return types.SimpleNamespace(registry=registry)

        async def query(self):
            log["ran"] += 1
            if action is not None:
                await action()

    module = importlib.import_module("querysource.queries.qs")
    monkeypatch.setattr(module, "QS", FakeQS)
    return log, registry


async def _seed(dsn, coord, slug="s", cursor=T0, schema="public", table="queries", kind="query"):
    owner = ScheduleOwner(NS, schema, table, slug)
    async with await _factory(dsn)() as conn:
        await conn.transaction()
        state = await coord.ensure_schedule_state(
            conn, owner, kind, trigger_spec=SPEC, anchor_at=T0, next_fire_at=cursor, misfire_grace_seconds=600,
        )
        await conn.commit()
    return state["logical_schedule_id"]


def _coord(dsn, now=T0 + timedelta(seconds=1)):
    return _coordinator(dsn, trigger_parser=_parse, clock=lambda: now)


async def _scalar(dsn, sql, *args):
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(sql, *args, column=0)


@pytest.mark.asyncio
async def test_stale_or_frozen_generation_never_builds_a_query(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    log, _ = _install_fake_qs(monkeypatch)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord)
    for job in (jobs.scheduled_query_job, jobs.scheduled_multiqs_job, jobs.cache_refresh_job):
        await job("s", None, owner=ENVELOPE, job_id="j", coordinator=coord, logical_schedule_id=sid, generation=7)
    assert log["built"] == [] and log["ran"] == 0
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 0

    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET dispatch_gate = 'frozen'")
    await jobs.scheduled_query_job("s", None, owner=ENVELOPE, job_id="j", coordinator=coord,
                                   logical_schedule_id=sid, generation=1)
    assert log["built"] == []


@pytest.mark.asyncio
async def test_admitted_job_holds_owner_lock_until_work_finishes(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord)
    key = ScheduleOwner(NS, "public", "queries", "s").lock_key()
    observed = {}

    async def while_running():
        async with await _factory(pg_dsn)() as other:
            observed["lock_free"] = await other.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0)
        observed["outcome"] = await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs")

    log, _ = _install_fake_qs(monkeypatch, action=while_running)
    await jobs.scheduled_query_job("s", None, owner=ENVELOPE, job_id="j", coordinator=coord,
                                   logical_schedule_id=sid, generation=1)
    assert log["ran"] == 1 and observed == {"lock_free": False, "outcome": "running"}
    assert await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == "completed"
    async with await _factory(pg_dsn)() as other:
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0) is True


@pytest.mark.asyncio
async def test_failure_notifies_with_real_job_id_and_cancel_records_unknown(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord)

    async def boom():
        raise RuntimeError("provider down")

    _install_fake_qs(monkeypatch, action=boom)
    notifier = _Notifier()
    await jobs.scheduled_query_job("s", notifier, owner=ENVELOPE, job_id="qsj2-query-abc-s",
                                   coordinator=coord, logical_schedule_id=sid, generation=1)
    assert [c["job_id"] for c in notifier.calls] == ["qsj2-query-abc-s"]
    assert await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == "failed"

    await _reset(pg_dsn)
    coord = _coord(pg_dsn, now=T0 + timedelta(minutes=5, seconds=1))
    sid = await _seed(pg_dsn, coord, cursor=T0 + timedelta(minutes=5))

    async def hang():
        await asyncio.sleep(60)

    _install_fake_qs(monkeypatch, action=hang)
    task = asyncio.create_task(jobs.scheduled_query_job(
        "s", None, owner=ENVELOPE, job_id="j", coordinator=coord, logical_schedule_id=sid, generation=1))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Interrupted work is retained as unknown (operator reconciliation), never replayed.
    assert await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == "unknown"
    key = ScheduleOwner(NS, "public", "queries", "s").lock_key()
    async with await _factory(pg_dsn)() as other:
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", key, column=0) is True


@pytest.mark.asyncio
async def test_admission_outage_fails_closed_with_notification(monkeypatch) -> None:
    class Broken:
        async def admit(self, sid, generation):
            raise ConnectionError("metadata database unreachable")

    log, _ = _install_fake_qs(monkeypatch)
    notifier = _Notifier()
    await jobs.cache_refresh_job("s", notifier, owner=ENVELOPE, job_id="cache_s", coordinator=Broken(),
                                 logical_schedule_id="x", generation=1)
    assert log["built"] == [] and len(notifier.calls) == 1 and notifier.calls[0]["job_id"] == "cache_s"


@pytest.mark.asyncio
async def test_uncoordinated_job_behaves_as_before(monkeypatch) -> None:
    log, _ = _install_fake_qs(monkeypatch)
    await jobs.scheduled_query_job("s", None, owner=ENVELOPE, job_id="j")
    assert log["ran"] == 1


@pytest.mark.asyncio
async def test_owner_selector_uses_physical_default_not_schema(monkeypatch) -> None:
    monkeypatch.setattr(QueryModel.Meta, "schema", "legacy_qs")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    custom = {**ENVELOPE, "schema": "legacy_qs", "table": "saved_queries"}
    public = {**ENVELOPE, "schema": "public", "table": "queries"}
    assert jobs._owner_selector(custom) is None
    assert jobs._owner_selector(public) == "public"  # literal public is NOT the default here
    assert jobs._owner_selector({**ENVELOPE, "schema": "wm", "contract": "tenant"}) == "wm"

    # Same slug in both stores: each job builds its OWN selector and revalidates against it.
    log, registry = _install_fake_qs(monkeypatch, store=_store("legacy_qs", "saved_queries", "legacy"))
    await jobs.scheduled_query_job("dup", None, owner=custom, job_id="query_dup")
    assert log["built"][-1]["tenant"] is None and registry.asked[-1] is None
    log2, registry2 = _install_fake_qs(monkeypatch, store=_store("public", "queries", "legacy"))
    await jobs.scheduled_query_job("dup", None, owner=public, job_id="qsj2-query-x-dup")
    assert log2["built"][-1]["tenant"] == "public" and registry2.asked[-1] == "public"

    # A registry that now resolves to a different physical store is rejected, never run.
    notifier = _Notifier()
    _install_fake_qs(monkeypatch, store=_store("legacy_qs", "other_table", "legacy"))
    await jobs.scheduled_query_job("dup", notifier, owner=custom, job_id="query_dup")
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_multi_job_preserves_delegated_run_as(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord, kind="multi")
    seen = {}

    class FakeMulti:
        def __init__(self, slug=None, tenant=None, identity_context=None, **kw):
            seen.update(slug=slug, tenant=tenant, ctx=identity_context)

        async def get_definition_repository(self):
            return types.SimpleNamespace(registry=_Registry(_store("public", contract="legacy")))

        async def query(self):
            seen["ran"] = True

    import querysource.queries as queries_pkg

    monkeypatch.setattr(queries_pkg, "MultiQS", FakeMulti)
    from querysource.auth import identity_tokens

    monkeypatch.setattr(
        identity_tokens.SourceIdentityContext, "for_scheduler",
        classmethod(lambda cls, uid, auth: ("scheduler-ctx", uid)),
    )
    await jobs.scheduled_multiqs_job(
        "s", None, owner=ENVELOPE, job_id="multi_s", run_as_user_id=42, identity_auth=object(),
        coordinator=coord, logical_schedule_id=sid, generation=1,
    )
    assert seen["ctx"] == ("scheduler-ctx", 42) and seen["ran"] is True  # the ORIGINAL run-as, nobody else


# -- drain ------------------------------------------------------------------------------------


async def _prepared_operation(dsn, coord, slug="s"):
    src, dst = _store("public", contract="legacy"), _store("wm")
    await coord.register_worker()
    op = await _journal(dsn, slug, src, dst)
    result = await coord.prepare(op)
    assert result["state"] == "draining"
    return op


@pytest.mark.asyncio
async def test_drain_waits_for_admitted_run_without_cancelling_it(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord)
    release = asyncio.Event()

    async def slow():
        await release.wait()

    _install_fake_qs(monkeypatch, action=slow)
    run_task = asyncio.create_task(jobs.scheduled_query_job(
        "s", None, owner=ENVELOPE, job_id="j", coordinator=coord, logical_schedule_id=sid, generation=1))
    await asyncio.sleep(0.5)
    op = await _prepared_operation(pg_dsn, coord)

    pending = await coord.drain(op, timeout_seconds=0.6, poll_seconds=0.1)
    assert pending["drained"] is False and len(pending["pending_runs"]) == 1
    assert not run_task.done()  # drain never cancels the source run

    waiter = asyncio.create_task(coord.drain(op, timeout_seconds=10, poll_seconds=0.1))
    await asyncio.sleep(0.4)
    assert not waiter.done()
    release.set()
    await run_task
    done = await waiter
    assert done["drained"] is True and done["state"] == "draining"
    # New admissions stayed frozen throughout (and nothing extra ran).
    assert await _scalar(pg_dsn, "SELECT count(*) FROM management.qs_schedule_runs") == 1


@pytest.mark.asyncio
async def test_drain_never_trusts_a_lost_worker_and_keeps_pending(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    sid = await _seed(pg_dsn, coord)
    async with await _factory(pg_dsn)() as conn:  # a run whose process vanished: row open, no lock
        await conn.execute(
            "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at,"
            " owner_namespace, owner_schema, owner_table, query_slug, worker_id, outcome) "
            "VALUES ($1::uuid, 1, $2, $3, 'public', 'queries', 's', 'dead', 'running')",
            __import__("uuid").UUID(sid), T0, NS,
        )
    op = await _prepared_operation(pg_dsn, coord)
    result = await coord.drain(op, timeout_seconds=2, poll_seconds=0.1)
    assert result["drained"] is False and result["state"] == "draining"
    assert result["error_code"] == "move_scheduler_not_ready" and "operator" in result["message"]
    assert await _scalar(pg_dsn, "SELECT outcome FROM management.qs_schedule_runs") == "unknown"

    # Still pending until an operator resolves it; then drain completes and clears the error.
    again = await coord.drain(op, timeout_seconds=2, poll_seconds=0.1)
    assert again["drained"] is False
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_runs SET outcome = 'failed'")
    final = await coord.drain(op, timeout_seconds=2, poll_seconds=0.1)
    assert final["drained"] is True and final["error_code"] is None


@pytest.mark.asyncio
async def test_unscheduled_drain_completes_immediately(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coord(pg_dsn)
    src, dst = _store("public", contract="legacy"), _store("wm")
    op = await _journal(pg_dsn, "plain", src, dst)
    await coord.prepare(op)
    assert (await coord.drain(op, timeout_seconds=1))["drained"] is True
