"""The move transaction against a real PostgreSQL: row, run-as audit and atomicity."""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Appended, never inserted at 0: prepending would shadow tests/integration/conftest.py
sys.path.append(str(Path(__file__).resolve().parents[1] / "tenants"))
from _movefixtures import (  # noqa: E402,F401 - pg_dsn is a fixture
    ACKS,
    ACTOR,
    LEGACY,
    RUN_AS,
    SCHEDULED,
    TENANT,
    FakeScheduler,
    _factory,
    _Registry,
    fetch_row,
    ident,
    insert_row,
    pg_dsn,
    scalar,
    setup_stores,
)

from querysource.query_moves import QueryMoveService  # noqa: E402
from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository  # noqa: E402
from querysource.tenant_errors import TenantError  # noqa: E402


class _Faults:
    """Fault plan applied to connections created by the faulty factory."""

    def __init__(self) -> None:
        self.on_sql: dict[str, object] = {}  # substring -> exception | callable(row)->row | "empty"
        self.commit: str | None = None  # "pre" raises instead of committing


class _FaultConn:
    def __init__(self, real, faults: _Faults) -> None:
        self._real, self._faults = real, faults

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
        return self._apply(sql, await self._real.fetch_one(sql, *args, **kw))

    async def fetch_all(self, sql, *args, **kw):
        return self._apply(sql, await self._real.fetch_all(sql, *args, **kw), many=True)

    async def commit(self):
        if self._faults.commit == "pre":
            raise ConnectionResetError("connection lost before COMMIT was acknowledged")
        await self._real.commit()


async def _build(dsn, faults: _Faults | None = None, grant_validator=None, scheduler=None):
    """A move service over the real repository, optionally with injected faults."""
    base = _factory(dsn)

    async def factory():
        conn = await base()
        return _FaultConn(conn, faults) if faults is not None else conn

    repo = DefinitionRepository(_Registry(LEGACY), factory)
    scheduler = scheduler if scheduler is not None else FakeScheduler()
    return QueryMoveService(repo, scheduler=scheduler, grant_validator=grant_validator)


async def _move(svc, source=None, destination=TENANT, acks=ACKS):
    """Preview then move, asserting the plan was clean."""
    source = source or ident()
    plan = await svc.preview(source, destination, actor=ACTOR)
    assert plan["can_move"], plan["blockers"]
    return await svc.move(
        source, destination, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements=acks
    )


# -- success -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_to_tenant_moves_all_fields_exactly_and_stamps_the_mover(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY, description="keep me", query_raw="SELECT 42", program_id=9)
    svc = await _build(pg_dsn)

    result = await _move(svc)

    assert result["moved"] is True and result["scheduler_sync"] == "ok"
    assert await fetch_row(pg_dsn, LEGACY) is None, "the source row must be gone"
    moved = await fetch_row(pg_dsn, TENANT)
    assert moved["description"] == "keep me" and moved["query_raw"] == "SELECT 42"
    assert moved["program_id"] == 9
    # Creation provenance preserved verbatim; the mover is stamped on updated_by.
    assert moved["created_by"] == 3
    assert moved["created_at"] == datetime(2025, 5, 5, tzinfo=timezone.utc)
    assert moved["updated_by"] == ACTOR
    # Tenant persistence never carries program_slug.
    assert "program_slug" not in moved


@pytest.mark.asyncio
async def test_tenant_to_legacy_sets_program_from_source_runtime_value(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, TENANT)
    svc = await _build(pg_dsn)

    await _move(svc, source=ident(store=TENANT), destination=LEGACY)

    assert await fetch_row(pg_dsn, TENANT) is None
    # The runtime program of a tenant row is its schema; that is what legacy persists.
    assert (await fetch_row(pg_dsn, LEGACY))["program_slug"] == "wm_assembly"


@pytest.mark.asyncio
async def test_scheduled_move_pauses_the_source_and_reregisters_the_destination(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY, attributes=SCHEDULED, scheduler_run_as_user_id=RUN_AS)
    scheduler = FakeScheduler()
    svc = await _build(pg_dsn, scheduler=scheduler)

    result = await _move(svc)

    assert result["schedules_transferred"] == 1
    # Paused on the source before the transaction, never resumed there (the row left).
    assert scheduler.paused == [("s", "public", True)]
    # The source is re-synced (its jobs disappear with the row), then the destination registers.
    assert scheduler.registered == [None, "wm_assembly"]
    # The ORIGINAL stored run-as moves; it is never rebound to the mover.
    assert (await fetch_row(pg_dsn, TENANT))[RUN_AS_COLUMN] == RUN_AS


@pytest.mark.asyncio
async def test_run_as_transfer_writes_both_attributed_audit_events(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY, attributes=SCHEDULED, scheduler_run_as_user_id=RUN_AS)
    svc = await _build(pg_dsn)

    await _move(svc)

    cleared = await scalar(
        pg_dsn, 'SELECT operation FROM "public"."queries_run_as_audit" WHERE query_slug = $1', "s")
    assert cleared == "clear"
    row = await scalar(
        pg_dsn, 'SELECT new_user_id FROM "wm_assembly"."queries_run_as_audit" WHERE query_slug = $1', "s")
    assert row == RUN_AS
    changed_by = await scalar(
        pg_dsn, 'SELECT changed_by FROM "wm_assembly"."queries_run_as_audit" WHERE query_slug = $1', "s")
    assert changed_by == ACTOR, "the audit event is attributed to the mover"


@pytest.mark.asyncio
async def test_an_unscheduled_move_needs_no_scheduler(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc = await _build(pg_dsn)
    svc.scheduler = None

    result = await _move(svc)

    assert result["moved"] is True and result["scheduler_sync"] == "not_configured"
    assert await fetch_row(pg_dsn, TENANT) is not None


# -- atomicity -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,fault",
    [
        ("DELETE FROM", RuntimeError("delete exploded")),
        ("INSERT INTO", RuntimeError("insert exploded")),
        ("queries_run_as_audit", RuntimeError("audit exploded")),
    ],
)
@pytest.mark.asyncio
async def test_a_failure_anywhere_rolls_the_whole_move_back(pg_dsn, pattern, fault) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY, attributes=SCHEDULED, scheduler_run_as_user_id=RUN_AS)
    faults = _Faults()
    faults.on_sql[pattern] = fault
    scheduler = FakeScheduler()
    svc = await _build(pg_dsn, faults, scheduler=scheduler)

    with pytest.raises(Exception):  # noqa: B017 - the injected fault propagates
        await _move(svc)

    # The source is intact and the destination is untouched.
    assert await fetch_row(pg_dsn, LEGACY) is not None
    assert await fetch_row(pg_dsn, TENANT) is None
    # The schedule was paused for the attempt and resumed afterwards.
    assert scheduler.paused == [("s", "public", True), ("s", "public", False)]


@pytest.mark.asyncio
async def test_a_lost_commit_leaves_the_source_intact(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    faults = _Faults()
    faults.commit = "pre"
    svc = await _build(pg_dsn, faults)

    with pytest.raises(ConnectionResetError):
        await _move(svc)

    assert await fetch_row(pg_dsn, LEGACY) is not None
    assert await fetch_row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_source_changed_after_plan_is_stale_and_changes_nothing(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc = await _build(pg_dsn)
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    async with await _factory(pg_dsn)() as conn:
        await conn.execute('UPDATE "public"."queries" SET description = $1 WHERE query_slug = $2', "edited", "s")

    with pytest.raises(TenantError) as exc:
        await svc.move(ident(), TENANT, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements=ACKS)

    assert exc.value.error_code == "move_plan_stale" and exc.value.code == 409
    assert await fetch_row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_target_created_concurrently_is_never_overwritten(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc = await _build(pg_dsn)
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    await insert_row(pg_dsn, TENANT, description="mine")

    with pytest.raises(TenantError) as exc:
        await svc.move(ident(), TENANT, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements=ACKS)

    assert exc.value.error_code in ("move_target_exists", "move_plan_stale")
    assert (await fetch_row(pg_dsn, TENANT))["description"] == "mine", "never overwritten"
    assert await fetch_row(pg_dsn, LEGACY) is not None, "the source survives"


@pytest.mark.asyncio
async def test_missing_acknowledgement_is_refused_before_anything_moves(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc = await _build(pg_dsn)
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)

    with pytest.raises(TenantError) as exc:
        await svc.move(ident(), TENANT, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements={})

    assert exc.value.error_code == "move_acknowledgement_required" and exc.value.code == 400
    assert await fetch_row(pg_dsn, LEGACY) is not None


@pytest.mark.asyncio
async def test_revoked_run_as_grants_block_the_move(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY, attributes=SCHEDULED, scheduler_run_as_user_id=RUN_AS)

    async def revoked(run_as, raw):
        raise TenantError("the delegated identity lost its grant", error_code="tenant_write_forbidden")

    svc = await _build(pg_dsn, grant_validator=revoked)

    with pytest.raises(TenantError) as exc:
        await _move(svc)

    assert exc.value.error_code == "tenant_write_forbidden"
    assert await fetch_row(pg_dsn, LEGACY) is not None
    assert await fetch_row(pg_dsn, TENANT) is None


@pytest.mark.asyncio
async def test_concurrent_moves_of_the_same_slug_move_it_exactly_once(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc_a, svc_b = await _build(pg_dsn), await _build(pg_dsn)
    plan = await svc_a.preview(ident(), TENANT, actor=ACTOR)

    results = await asyncio.gather(
        svc_a.move(ident(), TENANT, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements=ACKS),
        svc_b.move(ident(), TENANT, plan_revision=plan["plan_revision"], actor=ACTOR, acknowledgements=ACKS),
        return_exceptions=True,
    )

    moved = [r for r in results if not isinstance(r, BaseException)]
    assert len(moved) == 1, f"exactly one move may succeed, got {results}"
    assert await fetch_row(pg_dsn, LEGACY) is None
    assert await fetch_row(pg_dsn, TENANT) is not None


@pytest.mark.asyncio
async def test_a_move_and_a_concurrent_write_do_not_resurrect_the_source(pg_dsn) -> None:
    await setup_stores(pg_dsn)
    await insert_row(pg_dsn, LEGACY)
    svc = await _build(pg_dsn)
    await _move(svc)

    # An ordinary write to the (now empty) source store affects no row and creates nothing.
    async with await _factory(pg_dsn)() as conn:
        affected = await conn.fetch_all(
            'UPDATE "public"."queries" SET description = $1 WHERE query_slug = $2 RETURNING query_slug',
            "resurrect", "s",
        )
    assert not affected
    assert await fetch_row(pg_dsn, LEGACY) is None
