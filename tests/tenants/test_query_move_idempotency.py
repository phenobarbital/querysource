"""Durable move submission: idempotency, reservations, stale plans and status reads."""
import asyncio
import uuid

import pytest
from test_handoff_state import NS, _factory, _reset, pg_dsn  # noqa: F401 - pg_dsn is a fixture
from test_query_moves import LEGACY, TENANT, _ident, _row, _service

from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM
from querysource.tenant_errors import TenantError

ACKS = {ACK_CONSUMERS: True, ACK_PROGRAM: True}


def _key() -> str:
    return str(uuid.uuid4())


async def _count(dsn, table):
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(f"SELECT count(*) FROM management.{table}", column=0)


async def _submit(svc, key, *, destination=TENANT, actor=7, acks=ACKS, revision=None, source=None):
    source = source or _ident()
    if revision is None:
        revision = (await svc.preview(source, destination, actor=actor))["plan_revision"]
    return await svc.move(
        source, destination, plan_revision=revision, idempotency_key=key, actor=actor,
        acknowledgements=acks, request_info={"ip": "10.0.0.1"},
    )


@pytest.mark.asyncio
async def test_submission_reserves_durable_operation_without_moving_anything(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, repo, _ = await _service(pg_dsn, {("public", "s"): _row()})
    op = await _submit(svc, _key())
    assert op["state"] == "preparing" and op["moved"] is False and op["scheduler_sync"] == "pending"
    assert op["source"]["tenant"] is None and op["destination"]["tenant"] == "wm_assembly"
    assert op["query_slug"] == "s" and op["version"] == 1
    assert "error_code" not in op and "10.0.0.1" not in str(op)
    assert await _count(pg_dsn, "qs_query_moves") == 1
    assert await _count(pg_dsn, "qs_move_reservations") == 2
    assert ("wm_assembly", "s") not in repo.rows  # the row move belongs to the worker, not submission


@pytest.mark.asyncio
async def test_same_key_returns_same_operation_even_after_source_changes(pg_dsn) -> None:
    await _reset(pg_dsn)
    rows = {("public", "s"): _row()}
    svc, _, _ = await _service(pg_dsn, rows)
    key = _key()
    plan_revision = (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"]
    first = await _submit(svc, key, revision=plan_revision)
    rows[("public", "s")] = _row(program_slug="edited-after-response-was-lost")
    again = await _submit(svc, key, revision=plan_revision)  # lost HTTP response: client retries
    assert again == first
    assert await _count(pg_dsn, "qs_query_moves") == 1
    assert await _count(pg_dsn, "qs_move_reservations") == 2


@pytest.mark.asyncio
async def test_key_reuse_for_different_request_conflicts_and_is_actor_scoped(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row()})
    key = _key()
    await _submit(svc, key)
    other = _store_other()
    with pytest.raises(TenantError) as err:
        await _submit(svc, key, destination=other)
    assert err.value.error_code == "move_idempotency_conflict"
    with pytest.raises(TenantError) as err:
        await _submit(svc, key, acks={ACK_CONSUMERS: True})  # different confirmed request
    assert err.value.error_code == "move_idempotency_conflict"
    # Another actor presenting the same key is NOT a replay: it is a new request that now collides.
    with pytest.raises(TenantError) as err:
        await _submit(svc, key, actor=8)
    assert err.value.error_code == "move_in_progress"
    assert await _count(pg_dsn, "qs_query_moves") == 1


def _store_other():
    from test_handoff_state import _store

    return _store("other_schema", "queries", "tenant")


@pytest.mark.asyncio
async def test_overlapping_and_opposite_moves_collide_on_reservations(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row(), ("wm_assembly", "t"): _row(query_slug="t")})
    await _submit(svc, _key())
    with pytest.raises(TenantError) as err:  # new key, same identities
        await _submit(svc, _key())
    assert err.value.error_code == "move_in_progress"
    # The preview itself already reports the reservation as a concrete blocker.
    plan = await svc.preview(_ident(), TENANT, actor=7)
    assert plan["can_move"] is False
    assert await _count(pg_dsn, "qs_query_moves") == 1


@pytest.mark.asyncio
async def test_concurrent_submissions_of_one_key_create_one_operation(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row()})
    key = _key()
    revision = (await svc.preview(_ident(), TENANT, actor=7))["plan_revision"]
    results = await asyncio.gather(*[_submit(svc, key, revision=revision) for _ in range(5)], return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict)]
    assert ok and len({r["operation_id"] for r in ok}) == 1
    for r in results:
        assert isinstance(r, dict) or (isinstance(r, TenantError) and r.error_code == "move_in_progress")
    assert await _count(pg_dsn, "qs_query_moves") == 1


@pytest.mark.asyncio
async def test_stale_plan_blockers_first_and_acknowledgements_exact(pg_dsn) -> None:
    await _reset(pg_dsn)
    rows = {("public", "s"): _row()}
    svc, _, _ = await _service(pg_dsn, rows)
    with pytest.raises(TenantError) as err:
        await _submit(svc, _key(), revision="0" * 64)
    assert err.value.error_code == "move_plan_stale" and err.value.code == 409
    # A concrete collision beats the generic stale-plan error.
    rows[("wm_assembly", "s")] = _row()
    with pytest.raises(TenantError) as err:
        await _submit(svc, _key(), revision="0" * 64)
    assert err.value.error_code == "move_target_exists"
    del rows[("wm_assembly", "s")]
    for acks in ({}, {ACK_CONSUMERS: True}, {ACK_CONSUMERS: True, ACK_PROGRAM: "true"}, {ACK_PROGRAM: True}):
        with pytest.raises(TenantError) as err:
            await _submit(svc, _key(), acks=acks)
        assert err.value.error_code == "move_acknowledgement_required" and err.value.code == 400
    assert await _count(pg_dsn, "qs_query_moves") == 0
    assert await _count(pg_dsn, "qs_move_reservations") == 0


@pytest.mark.asyncio
async def test_program_acknowledgement_not_required_when_program_unchanged(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("wm_assembly", "s"): _row()})
    op = await _submit(svc, _key(), source=_ident(store=TENANT), destination=LEGACY, acks={ACK_CONSUMERS: True})
    assert op["state"] == "preparing"


@pytest.mark.asyncio
async def test_invalid_inputs_are_rejected_before_any_write(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, _ = await _service(pg_dsn, {("public", "s"): _row()})
    for bad_key in ("", "not-a-uuid", None, 5):
        with pytest.raises(TenantError) as err:
            await _submit(svc, bad_key, revision="x")
        assert err.value.error_code == "move_validation_failed"
    for kwargs in ({"actor": 0}, {"revision": ""}):
        with pytest.raises(TenantError) as err:
            await svc.move(_ident(), TENANT, plan_revision=kwargs.get("revision", "x"), idempotency_key=_key(),
                           actor=kwargs.get("actor", 7), acknowledgements=ACKS)
        assert err.value.error_code == "move_validation_failed"
    with pytest.raises(TenantError) as err:
        await svc.move(_ident(), TENANT, plan_revision="x", idempotency_key=_key(), actor=7, acknowledgements=None)
    assert err.value.error_code == "move_validation_failed"
    assert await _count(pg_dsn, "qs_query_moves") == 0


@pytest.mark.asyncio
async def test_status_is_a_pure_read_and_represents_moved_tristate(pg_dsn) -> None:
    await _reset(pg_dsn)
    svc, _, coord = await _service(pg_dsn, {("public", "s"): _row()})
    op = await _submit(svc, _key())
    oid = op["operation_id"]
    before = await svc.status(oid)
    assert before == op
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="cutover", moved=None)
        await conn.commit()
    unknown = await svc.status(oid)
    assert unknown["state"] == "cutover" and unknown["moved"] is None
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="reconciling", moved=True, scheduler_sync="failed",
                                    error_code="move_scheduler_not_ready", message="Scheduler mirror update pending.")
        await conn.commit()
    committed = await svc.status(oid)
    assert committed["moved"] is True and committed["scheduler_sync"] == "failed"
    assert committed["error_code"] == "move_scheduler_not_ready"
    assert await svc.status(oid) == committed  # repeated reads do not mutate
    with pytest.raises(TenantError) as err:
        await svc.status(str(uuid.uuid4()))
    assert err.value.error_code == "query_not_found"
    with pytest.raises(TenantError) as err:
        await svc.status("nope")
    assert err.value.error_code == "move_validation_failed"
