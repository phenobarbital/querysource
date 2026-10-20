"""Read-only move preview: collisions, schedules, dependency analysis, program and run-as rules."""
import pytest
from _movefixtures import (  # noqa: F401 - pg_dsn is a fixture
    ACTOR,
    LEGACY,
    RUN_AS,
    SCHEDULED,
    TENANT,
    FakeRepo,
    FakeScheduler,
    _factory,
    _store,
    ident,
    pg_dsn,
    raw_row,
    setup_stores,
)

from querysource.query_moves import (
    ACK_CONSUMERS,
    ACK_PROGRAM,
    QueryMoveService,
    analyze_references,
    schedule_inventory,
    store_descriptor,
)
from querysource.tenant_errors import TenantError


async def _service(dsn, rows, *, scheduler=None):
    """A move service over the fake repository, with the real destination tables present."""
    await setup_stores(dsn)
    repo = FakeRepo(dsn, rows)
    scheduler = scheduler if scheduler is not None else FakeScheduler()
    return QueryMoveService(repo, registry=repo.registry, scheduler=scheduler), repo


# -- dependency analysis (pure) ----------------------------------------------------------


def test_inherited_vs_pinned_vs_explicit_null_children() -> None:
    pipeline = {"queries": {
        "a": {"slug": "child_a"},                      # inherits -> blocks
        "b": {"slug": "child_b", "tenant": "wm"},      # pinned   -> fine
        "c": {"slug": "child_c", "tenant": None},      # explicit null -> fine
        "d": {"raw": "SELECT 1"},                      # raw child, no owner
    }}
    inherited, blockers = analyze_references("multi", pipeline)
    assert inherited == [{"path": "queries.a", "slug": "child_a"}]
    assert [b["code"] for b in blockers] == ["move_inherited_references"]
    assert blockers[0]["path"] == "queries.a.tenant"


def test_unknown_and_dynamic_forms_block_with_a_path() -> None:
    _, blockers = analyze_references("multi", {"queries": {"a": {"slug": "{{ var }}"}}})
    assert [b["code"] for b in blockers] == ["move_dependency_analysis_unsupported"]
    assert blockers[0]["path"] == "queries.a"
    _, unparsable = analyze_references("multi", "not-json")
    assert unparsable[0]["path"] == "query_raw"
    # A non-multi provider has no saved children at all.
    assert analyze_references("db", {"queries": {"a": {"slug": "x"}}}) == ([], [])


def test_schedule_inventory_matches_the_scheduler_predicates() -> None:
    assert schedule_inventory(raw_row()) == []
    # A nonzero cache_refresh alone is not a schedule.
    assert schedule_inventory(raw_row(is_cached=True, cache_refresh=300)) == []
    assert [s["kind"] for s in schedule_inventory(raw_row(attributes=SCHEDULED))] == ["query"]
    assert [s["kind"] for s in schedule_inventory(raw_row(provider="multi", attributes=SCHEDULED))] == ["multi"]
    cached = raw_row(is_cached=True, cache_options={"schedule_type": "interval", "schedule": {"minutes": 5}})
    assert [s["kind"] for s in schedule_inventory(cached)] == ["cache"]


def test_descriptor_uses_physical_default_and_hides_connection_details() -> None:
    from _movefixtures import _Registry

    custom = _store("ops", "qs_queries", "legacy")
    registry = _Registry(custom)
    assert store_descriptor(registry, custom) == {
        "tenant": None, "schema": "ops", "table": "qs_queries", "contract": "legacy", "is_default": True,
    }
    # Literal public.queries beside a custom default keeps its own selector.
    public = store_descriptor(registry, LEGACY)
    assert public["tenant"] == "public" and public["is_default"] is False
    assert not {"database_namespace", "dsn", "host"} & set(public)


# -- preview -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_plain_move_is_read_only_and_reports_consumers_and_program(pg_dsn) -> None:
    svc, repo = await _service(pg_dsn, {("public", "s"): raw_row()})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert plan["can_move"] is True and plan["blockers"] == []
    assert plan["version"] == 1 and plan["schedule_policy"] == "pause-and-transfer"
    assert plan["consumer_review_required"] is True
    assert plan["runtime_program"] == {"before": "prog", "after": "wm_assembly"}
    assert {w["code"] for w in plan["warnings"]} == {"external_consumers", "runtime_program_change"}
    assert QueryMoveService.required_acknowledgements(plan) == [ACK_CONSUMERS, ACK_PROGRAM]
    # Preview mutated nothing: the row is still only in the source.
    assert repo.rows[("public", "s")]["query_slug"] == "s"


@pytest.mark.asyncio
async def test_program_acknowledgement_only_required_when_program_differs(pg_dsn) -> None:
    svc, _ = await _service(pg_dsn, {("wm_assembly", "s"): raw_row(program_slug=None)})
    plan = await svc.preview(ident(store=TENANT), LEGACY, actor=ACTOR)
    assert plan["runtime_program"] == {"before": "wm_assembly", "after": "wm_assembly"}
    assert QueryMoveService.required_acknowledgements(plan) == [ACK_CONSUMERS]


@pytest.mark.asyncio
async def test_preview_errors_missing_same_store_and_invalid_actor(pg_dsn) -> None:
    svc, _ = await _service(pg_dsn, {("public", "s"): raw_row()})
    with pytest.raises(TenantError) as missing:
        await svc.preview(ident("absent"), TENANT, actor=ACTOR)
    assert missing.value.error_code == "query_not_found" and missing.value.code == 404

    with pytest.raises(TenantError) as same:
        await svc.preview(ident(), LEGACY, actor=ACTOR)
    assert same.value.error_code == "move_same_store" and same.value.code == 400

    for bad in (None, 0, -1, True, "7"):
        with pytest.raises(TenantError) as invalid:
            await svc.preview(ident(), TENANT, actor=bad)
        assert invalid.value.error_code == "move_validation_failed"


@pytest.mark.asyncio
async def test_collision_never_overwrites_and_is_a_blocker(pg_dsn) -> None:
    rows = {("public", "s"): raw_row(), ("wm_assembly", "s"): raw_row()}
    svc, _ = await _service(pg_dsn, rows)
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert plan["can_move"] is False
    assert [b["code"] for b in plan["blockers"]] == ["move_target_exists"]


@pytest.mark.asyncio
async def test_a_valid_schedule_is_not_a_blocker_and_is_inventoried(pg_dsn) -> None:
    svc, _ = await _service(pg_dsn, {("public", "s"): raw_row(attributes=SCHEDULED)})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert plan["can_move"] is True, plan["blockers"]
    assert [s["kind"] for s in plan["schedules"]] == ["query"]
    # The operator is told the schedule pauses for the move, not asked to disable it.
    assert "schedule_pause_window" in {w["code"] for w in plan["warnings"]}


@pytest.mark.asyncio
async def test_a_scheduled_definition_needs_a_scheduler_in_this_process(pg_dsn) -> None:
    svc, _ = await _service(pg_dsn, {("public", "s"): raw_row(attributes=SCHEDULED)}, scheduler=None)
    svc.scheduler = None
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert [b["code"] for b in plan["blockers"]] == ["move_scheduler_unsupported"]
    assert plan["blockers"][0]["path"] == "schedules"


@pytest.mark.asyncio
async def test_external_scheduler_is_unsupported(pg_dsn) -> None:
    row = raw_row(dwh=True, dwh_scheduler={"cron": "0 * * * *"})
    svc, _ = await _service(pg_dsn, {("public", "s"): row})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert "move_scheduler_unsupported" in {b["code"] for b in plan["blockers"]}


@pytest.mark.asyncio
async def test_an_in_flight_run_blocks_the_plan(pg_dsn) -> None:
    from querysource.scheduler import jobs

    svc, _ = await _service(pg_dsn, {("public", "s"): raw_row(attributes=SCHEDULED)})
    with jobs._running(None, "s"):  # the source is the configured default -> selector None
        plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert "move_run_in_flight" in {b["code"] for b in plan["blockers"]}
    # Once it finishes the plan is clean again.
    assert (await svc.preview(ident(), TENANT, actor=ACTOR))["can_move"] is True


@pytest.mark.asyncio
async def test_revision_tracks_source_changes(pg_dsn) -> None:
    rows = {("public", "s"): raw_row()}
    svc, _ = await _service(pg_dsn, rows)
    first = (await svc.preview(ident(), TENANT, actor=ACTOR))["plan_revision"]
    assert (await svc.preview(ident(), TENANT, actor=ACTOR))["plan_revision"] == first
    rows[("public", "s")] = raw_row(query_raw="SELECT 2")
    assert (await svc.preview(ident(), TENANT, actor=ACTOR))["plan_revision"] != first


@pytest.mark.asyncio
async def test_stale_unscheduled_run_as_is_separate_from_valid_scheduled_run_as(pg_dsn) -> None:
    unscheduled = raw_row(scheduler_run_as_user_id=RUN_AS)
    svc, _ = await _service(pg_dsn, {("public", "s"): unscheduled})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert "move_stale_run_as" in {b["code"] for b in plan["blockers"]}

    scheduled = raw_row(scheduler_run_as_user_id=RUN_AS, attributes=SCHEDULED)
    svc, _ = await _service(pg_dsn, {("public", "s"): scheduled})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert "move_stale_run_as" not in {b["code"] for b in plan["blockers"]}
    assert plan["can_move"] is True, plan["blockers"]


@pytest.mark.asyncio
async def test_missing_run_as_audit_table_blocks_a_run_as_transfer(pg_dsn) -> None:
    row = raw_row(scheduler_run_as_user_id=RUN_AS, attributes=SCHEDULED)
    svc, _ = await _service(pg_dsn, {("public", "s"): row})
    async with await _factory(pg_dsn)() as conn:
        await conn.execute('DROP TABLE "wm_assembly"."queries_run_as_audit"')
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    blocker = next(b for b in plan["blockers"] if b.get("path") == "run_as_audit")
    assert blocker["code"] == "move_validation_failed"


@pytest.mark.asyncio
async def test_inherited_children_block_the_plan_with_paths(pg_dsn) -> None:
    row = raw_row(provider="multi", query_raw={"queries": {"a": {"slug": "child"}}})
    svc, _ = await _service(pg_dsn, {("public", "s"): row})
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert plan["can_move"] is False
    assert plan["inherited_references"] == [{"path": "queries.a", "slug": "child"}]
    assert "move_inherited_references" in {b["code"] for b in plan["blockers"]}


@pytest.mark.asyncio
async def test_preview_blocks_when_the_destination_cannot_hold_the_row(pg_dsn) -> None:
    svc, _ = await _service(pg_dsn, {("public", "s"): raw_row()})
    async with await _factory(pg_dsn)() as conn:
        await conn.execute('ALTER TABLE "wm_assembly"."queries" DROP COLUMN query_raw')
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    blocker = next(b for b in plan["blockers"] if b["code"] == "move_validation_failed")
    assert "query_raw" in blocker["message"]

    async with await _factory(pg_dsn)() as conn:
        await conn.execute('DROP TABLE "wm_assembly"."queries"')
    plan = await svc.preview(ident(), TENANT, actor=ACTOR)
    assert any(b["path"] == "destination_tenant" for b in plan["blockers"])
