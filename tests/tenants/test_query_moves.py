"""Read-only move preview: collisions, schedules, dependency analysis, program and run-as rules."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from test_handoff_state import (  # noqa: F401 - pg_dsn is a fixture
    NS,
    _factory,
    _Registry,
    _reset,
    _store,
    pg_dsn,
)

from querysource.query_moves import (
    ACK_CONSUMERS,
    ACK_PROGRAM,
    QueryMoveService,
    analyze_references,
    store_descriptor,
)
from querysource.repositories.definitions import RUN_AS_COLUMN
from querysource.scheduler.handoff import ScheduleHandoffCoordinator, ScheduleOwner
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
LEGACY = _store("public", "queries", "legacy")
TENANT = _store("wm_assembly", "queries", "tenant")
SCHEDULED = {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}


def _parse(schedule_type, schedule):
    return IntervalTrigger(start_date=T0, timezone="UTC", minutes=5)


class _Repo:
    """Minimal DefinitionRepository stand-in: one dict of raw rows per physical store."""

    def __init__(self, dsn, rows):
        self.registry = _Registry(LEGACY)
        self.connection_factory = _factory(dsn)
        self.rows = rows
        self.fetches = 0

    async def _fetch_row(self, store, slug):
        self.fetches += 1
        return self.rows.get((store.schema, slug))

    async def get(self, identity):
        row = self.rows.get((identity.store.schema, identity.slug))
        if row is None:
            raise TenantError("missing", error_code="query_not_found")
        runtime = SimpleNamespace(
            attributes=row.get("attributes"), cache_options=row.get("cache_options"),
            provider=row.get("provider"), is_cached=row.get("is_cached", False),
            dwh=row.get("dwh", False), dwh_scheduler=row.get("dwh_scheduler"),
            program_slug=row.get("program_slug") if identity.store.contract == "legacy" else identity.store.schema,
            query_raw=row.get("query_raw"),
        )
        return SimpleNamespace(runtime=runtime, revision="r")

    def _run_as_audit_table(self, store):
        return f'"{store.schema}"."{store.table}_run_as_audit"'


def _row(**over):
    base = {"query_slug": "s", "provider": "db", "attributes": {}, "program_slug": "prog", "is_cached": False,
            "cache_options": {}, "query_raw": "SELECT 1", RUN_AS_COLUMN: None}
    base.update(over)
    return base


async def _reset_workers(coord):
    await coord.register_worker()


async def _service(dsn, rows):
    repo = _Repo(dsn, rows)
    coord = ScheduleHandoffCoordinator(
        repo, deployment_id="d", worker_id="w", rollout_confirmed=True, trigger_parser=_parse, clock=lambda: T0
    )
    await _reset_workers(coord)
    return QueryMoveService(repo, coord, registry=repo.registry), repo, coord


def _ident(slug="s", store=LEGACY):
    return QueryIdentity(store=store, slug=slug)


def _codes(plan):
    return {b["code"] for b in plan["blockers"]}


# -- pure dependency analysis -------------------------------------------------------------


def test_inherited_vs_pinned_vs_explicit_null_children() -> None:
    pipeline = {"queries": {
        "inherit": {"slug": "a"},
        "pinned": {"slug": "b", "tenant": "wm_assembly"},
        "legacy_null": {"slug": "c", "tenant": None},
        "raw": {"query": "SELECT 1", "driver": "pg"},
    }}
    inherited, blockers = analyze_references("multi", json.dumps(pipeline))
    assert inherited == [{"path": "queries.inherit", "slug": "a"}]
    assert [b["code"] for b in blockers] == ["move_inherited_references"]
    assert blockers[0]["path"] == "queries.inherit.tenant"
    assert analyze_references("db", "SELECT 1") == ([], [])  # SQL text is never scanned


def test_unknown_and_dynamic_forms_block_with_a_path() -> None:
    _, blockers = analyze_references("multi", "{not json")
    assert blockers[0]["code"] == "move_dependency_analysis_unsupported" and blockers[0]["path"] == "query_raw"
    _, blockers = analyze_references("multi", {"queries": {"x": {"slug": "{{ tenant }}_q"}, "y": "oops"}})
    assert {(b["code"], b["path"]) for b in blockers} == {
        ("move_dependency_analysis_unsupported", "queries.x"),
        ("move_dependency_analysis_unsupported", "queries.y"),
    }
    _, blockers = analyze_references("multi", {"sources": [{"SomeSource": {"slug": "q"}}]})
    assert blockers[0]["path"] == "sources[0].SomeSource"
    assert analyze_references("multi", None) == ([], [])


def test_descriptor_uses_physical_default_and_hides_connection_details() -> None:
    registry = _Registry(_store("legacy_qs", "saved", "legacy"))
    custom = store_descriptor(registry, _store("legacy_qs", "saved", "legacy"))
    literal = store_descriptor(registry, _store("public", "queries", "legacy"))
    assert custom == {"tenant": None, "schema": "legacy_qs", "table": "saved", "contract": "legacy",
                      "is_default": True}
    assert literal["tenant"] == "public" and literal["is_default"] is False
    assert set(custom) == {"tenant", "schema", "table", "contract", "is_default"}


# -- preview -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_plain_move_is_read_only_and_reports_consumers_and_program(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, repo, _ = await _service(pg_dsn, {("public", "s"): _row()})
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is True and plan["blockers"] == []
    assert plan["version"] == 1 and plan["schedule_policy"] == "live-transfer"
    assert plan["consumer_review_required"] is True
    assert plan["runtime_program"] == {"before": "prog", "after": "wm_assembly"}
    assert {w["code"] for w in plan["warnings"]} == {"external_consumers", "runtime_program_change"}
    assert plan["source"]["tenant"] is None and plan["destination"]["tenant"] == "wm_assembly"
    assert len(plan["plan_revision"]) == 64
    assert svc.required_acknowledgements(plan) == [ACK_CONSUMERS, ACK_PROGRAM]
    async with await _factory(pg_dsn)() as conn:
        assert await conn.fetchval("SELECT count(*) FROM management.qs_schedule_state", column=0) == 0


@pytest.mark.asyncio
async def test_program_acknowledgement_only_required_when_program_differs(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("wm_assembly", "s"): _row()})
    plan = await svc.preview(_ident(store=TENANT), LEGACY, actor=7)
    # tenant -> legacy: program is taken from the source runtime value (the schema), so it is unchanged
    assert plan["runtime_program"]["before"] == plan["runtime_program"]["after"] == "wm_assembly"
    assert svc.required_acknowledgements(plan) == [ACK_CONSUMERS]


@pytest.mark.asyncio
async def test_preview_errors_missing_same_store_and_invalid_actor(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {})
    with pytest.raises(TenantError) as err:
        await svc.preview(_ident(), TENANT, actor=7)
    assert err.value.error_code == "query_not_found" and err.value.code == 404
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row()})
    with pytest.raises(TenantError) as err:
        await svc.preview(_ident(), LEGACY, actor=7)
    assert err.value.error_code == "move_same_store"
    for bad in (0, None, "7", True):
        with pytest.raises(TenantError) as err:
            await svc.preview(_ident(), TENANT, actor=bad)
        assert err.value.error_code == "move_validation_failed"


@pytest.mark.asyncio
async def test_collision_never_overwrites_and_is_a_blocker(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row(), ("wm_assembly", "s"): _row()})
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is False and "move_target_exists" in _codes(plan)


@pytest.mark.asyncio
async def test_valid_schedules_are_not_blockers_but_readiness_is(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, coord = await _service(pg_dsn, {("public", "s"): _row(attributes=SCHEDULED)})
    # Not bootstrapped yet: an explicit readiness blocker, never a silent success.
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is False and plan["handoff_ready"] is False
    assert _codes(plan) == {"move_scheduler_not_ready"}
    # After the durable backfill the same live schedule is transferable.
    await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"), _row(attributes=SCHEDULED))
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is True and plan["handoff_ready"] is True
    assert [s["kind"] for s in plan["schedules"]] == ["query"]
    assert plan["schedules"][0]["run_as"] == "preserve-existing"


@pytest.mark.asyncio
async def test_revision_ignores_cursor_but_tracks_source_and_schedule_changes(pg_dsn) -> None:
    await _reset(pg_dsn)
    rows = {("public", "s"): _row(attributes=SCHEDULED)}
    svc, _, coord = await _service(pg_dsn, rows)
    owner = ScheduleOwner.from_store(LEGACY, "s")
    await coord.bootstrap_row(owner, rows[("public", "s")])
    first = (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"]
    async with await _factory(pg_dsn)() as conn:  # naturally advancing cursor + heartbeat
        await conn.execute("UPDATE management.qs_schedule_state SET next_fire_at = next_fire_at + interval '5 min'")
    await coord.register_worker()
    assert (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"] == first
    # Only the legacy program changed: the digest moves.
    rows[("public", "s")] = _row(attributes=SCHEDULED, program_slug="other")
    assert (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"] != first
    # Only the run-as column changed: the digest moves.
    rows[("public", "s")] = _row(attributes=SCHEDULED, **{RUN_AS_COLUMN: 5})
    changed = (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"]
    assert changed != first


@pytest.mark.asyncio
async def test_external_scheduler_is_unsupported(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row(dwh=True, dwh_scheduler={"cron": "* * * * *"})})
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert "move_scheduler_unsupported" in _codes(plan) and plan["can_move"] is False


@pytest.mark.asyncio
async def test_stale_unscheduled_run_as_is_separate_from_valid_scheduled_run_as(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, coord = await _service(pg_dsn, {("public", "s"): _row(**{RUN_AS_COLUMN: 5})})
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert _codes(plan) == {"move_stale_run_as"}

    rows = {("public", "s"): _row(attributes=SCHEDULED, **{RUN_AS_COLUMN: 5})}
    svc, _, coord = await _service(pg_dsn, rows)
    await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"), rows[("public", "s")])
    plan = await svc.preview(_ident(), TENANT, actor=7)
    # Valid scheduled run-as is transferable, but missing audit infrastructure blocks readiness.
    assert "move_stale_run_as" not in _codes(plan)
    assert _codes(plan) == {"move_scheduler_not_ready"}
    async with await _factory(pg_dsn)() as conn:
        for store in (LEGACY, TENANT):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{store.schema}"')
            await conn.execute(f'CREATE TABLE "{store.schema}"."{store.table}_run_as_audit" (id int)')
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is True


@pytest.mark.asyncio
async def test_inherited_children_block_the_plan_with_paths(pg_dsn) -> None:
    await _reset(pg_dsn)
    pipeline = json.dumps({"queries": {"child": {"slug": "c"}, "pinned": {"slug": "d", "tenant": None}}})
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row(provider="multi", query_raw=pipeline)})
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is False
    assert plan["inherited_references"] == [{"path": "queries.child", "slug": "c"}]
    assert any(b["path"] == "queries.child.tenant" for b in plan["blockers"])


@pytest.mark.asyncio
async def test_reserved_identity_blocks_preview(pg_dsn) -> None:
    from test_handoff_scheduler_controls import _reserve

    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row()})
    await _reserve(pg_dsn, ScheduleOwner.from_store(LEGACY, "s"), ScheduleOwner.from_store(TENANT, "s"))
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert "move_in_progress" in _codes(plan) and plan["can_move"] is False
