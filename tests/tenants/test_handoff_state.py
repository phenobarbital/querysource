"""Shared handoff state, readiness and freeze against a disposable PostgreSQL."""
import asyncio
import glob
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from asyncdb import AsyncDB

from querysource.scheduler.handoff import (
    ScheduleHandoffCoordinator,
    ScheduleOwner,
    config_digest,
    ordered_lock_keys,
    schedule_inventory,
    selector_for,
)
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

DDL = Path(__file__).resolve().parents[2] / "docs" / "sql" / "query_move_control.sql"
NS = "h:5432/db"


def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"), reverse=True):
        return candidate
    return None


@pytest.fixture(scope="module")
def pg_dsn():
    dsn = os.environ.get("QS_TEST_PG_DSN")
    if dsn:
        yield dsn
        return
    initdb, pg_ctl = _find_bin("initdb"), _find_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("no PostgreSQL binaries and QS_TEST_PG_DSN unset")
    root = tempfile.mkdtemp(prefix="qs_handoff_pg_")
    data = os.path.join(root, "data")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "qs"], check=True, capture_output=True)
    subprocess.run(
        [pg_ctl, "-D", data, "-o", f"-p {port} -k {root} -c listen_addresses=127.0.0.1 -c max_connections=100",
         "-l", os.path.join(root, "log"), "-w", "start"],
        check=True, capture_output=True,
    )
    try:
        yield f"postgresql://qs@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def _factory(dsn: str):
    async def factory():
        return await AsyncDB("pg", dsn=dsn).connection()

    return factory


async def _reset(dsn: str, *, install: bool = True) -> None:
    async with await _factory(dsn)() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS management CASCADE")
        if install:
            await conn.execute(DDL.read_text())


def _store(schema: str, table: str = "queries", contract: str = "tenant") -> QueryStore:
    return QueryStore(NS, schema, table, contract, frozenset({"query_slug"}))


class _Registry(TenantRegistry):
    def __init__(self, default: QueryStore) -> None:
        super().__init__()
        self._default_store = default
        self._discovered = True


def _coordinator(dsn: str, *, rollout: bool = True, definition=None, default=None, **kw):
    default = default or _store("public", contract="legacy")
    repo = SimpleNamespace(registry=_Registry(default), connection_factory=_factory(dsn))

    async def get(identity):
        return SimpleNamespace(runtime=definition, revision="r")

    repo.get = get
    return ScheduleHandoffCoordinator(
        repo, deployment_id="d", worker_id=kw.pop("worker_id", "w1"), rollout_confirmed=rollout,
        trigger_parser=kw.pop("trigger_parser", lambda t, s: object()), **kw,
    )


def _definition(**over):
    base = dict(attributes={}, cache_options={}, provider="db", is_cached=True, dwh=False, dwh_scheduler=None)
    base.update(over)
    return SimpleNamespace(**base)


SCHEDULED = {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}


# -- pure helpers --------------------------------------------------------------------------


def test_owner_identity_is_physical_not_contract() -> None:
    public = ScheduleOwner(NS, "public", "queries", "s")
    custom = ScheduleOwner(NS, "public", "saved_queries", "s")
    assert public.lock_key() != custom.lock_key()
    assert public.lock_key() == ScheduleOwner(NS, "public", "queries", "s").lock_key()
    assert ordered_lock_keys([custom, public, public]) == sorted({public.lock_key(), custom.lock_key()})


def test_selector_uses_physical_default_not_legacy_contract() -> None:
    custom_default = _store("legacy_qs", "saved_queries", "legacy")
    registry = _Registry(custom_default)
    assert selector_for(registry, custom_default) is None
    # literal public.queries is also legacy-contract, but is NOT the default.
    assert selector_for(registry, _store("public", "queries", "legacy")) == "public"
    assert selector_for(registry, _store("wm_assembly")) == "wm_assembly"


def test_inventory_uses_registration_predicates() -> None:
    assert schedule_inventory({"provider": "db", "is_cached": True, "cache_refresh": 60}) == []
    kinds = [
        i["kind"]
        for i in schedule_inventory(
            {"provider": "db", "is_cached": True, "attributes": SCHEDULED,
             "cache_options": {"schedule_type": "interval", "schedule": {"hours": 1}}}
        )
    ]
    assert kinds == ["query", "cache"]
    assert [i["kind"] for i in schedule_inventory({"provider": "multi", "attributes": SCHEDULED})] == ["multi"]
    assert schedule_inventory({"provider": "multi", "is_cached": True,
                               "cache_options": {"schedule_type": "interval", "schedule": {"hours": 1}}}) == []


def test_config_digest_ignores_cursor_but_not_configuration() -> None:
    state = {"kind": "query", "config_revision": 1, "trigger_spec": {"a": 1}, "timezone": "UTC",
             "paused": False, "enabled": True, "max_instances": 1, "coalesce_missed": True,
             "next_fire_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    advanced = {**state, "next_fire_at": datetime(2027, 1, 1, tzinfo=timezone.utc), "last_claimed_fire_at": "x"}
    assert config_digest(state) == config_digest(advanced)
    assert config_digest(state) != config_digest({**state, "paused": True})
    assert config_digest(state) != config_digest({**state, "config_revision": 2})


# -- readiness -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_is_read_only_and_requires_ddl_gate_and_workers(pg_dsn) -> None:
    await _reset(pg_dsn, install=False)
    coord = _coordinator(pg_dsn, rollout=False)
    info = await coord.readiness()
    assert not info["ready"] and "control_tables_missing" in info["reasons"]
    assert "rollout_not_confirmed" in info["reasons"]

    await _reset(pg_dsn)
    info = await _coordinator(pg_dsn, rollout=True).readiness()
    assert info["reasons"] == ["no_compatible_workers"]

    coord = _coordinator(pg_dsn, rollout=True)
    await coord.register_worker()
    assert (await coord.readiness())["ready"] is True
    assert (await _coordinator(pg_dsn, rollout=False).readiness())["reasons"] == ["rollout_not_confirmed"]

    # A fresh worker on an older protocol blocks; a stale one is ignored.
    async with await _factory(pg_dsn)() as conn:
        await conn.execute(
            "INSERT INTO management.qs_scheduler_workers (worker_id, protocol_version) VALUES ('old', 0 + 1)"
        )
        await conn.execute("UPDATE management.qs_scheduler_workers SET protocol_version = 1 WHERE worker_id='old'")
    newer = _coordinator(pg_dsn, rollout=True, protocol_version=2, worker_id="w2")
    info = await newer.readiness()
    assert "incompatible_workers" in info["reasons"] and "no_compatible_workers" in info["reasons"]
    async with await _factory(pg_dsn)() as conn:
        await conn.execute(
            "UPDATE management.qs_scheduler_workers SET heartbeat_at = now() - interval '1 hour'")
    assert (await _coordinator(pg_dsn, rollout=True).readiness())["reasons"] == ["no_compatible_workers"]


# -- state helpers -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_is_serialized_stable_and_flags_mirror_conflicts(pg_dsn) -> None:
    await _reset(pg_dsn)
    coord = _coordinator(pg_dsn)
    owner = ScheduleOwner(NS, "public", "queries", "slug1")
    anchor = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)

    async def bootstrap(next_fire):
        async with await _factory(pg_dsn)() as conn:
            await conn.transaction()
            try:
                state = await coord.ensure_schedule_state(
                    conn, owner, "query", trigger_spec={"type": "interval", "minutes": 5},
                    anchor_at=anchor, next_fire_at=next_fire,
                )
                await conn.commit()
                return state
            except BaseException:
                await conn.rollback()
                raise

    results = await asyncio.gather(*[bootstrap(anchor + timedelta(minutes=i)) for i in range(6)])
    assert len({r["logical_schedule_id"] for r in results}) == 1
    # The first persisted anchor/cursor wins: nothing is recomputed from "now".
    assert len({r["next_fire_at"] for r in results}) == 1

    async with await _factory(pg_dsn)() as conn:
        states = await coord.load_schedule_states(conn, owner)
        assert len(states) == 1 and states[0]["config_revision"] == 1
        await conn.transaction()
        flagged = await coord.ensure_schedule_state(
            conn, owner, "query", trigger_spec={"type": "interval", "minutes": 7}, anchor_at=anchor
        )
        await conn.commit()
    assert flagged["trigger_spec"]["mirror_conflict"] is True
    assert flagged["logical_schedule_id"] == results[0]["logical_schedule_id"]
    with pytest.raises(ValueError):
        async with await _factory(pg_dsn)() as conn:
            await coord.ensure_schedule_state(conn, owner, "bogus", trigger_spec={})


# -- inspect -------------------------------------------------------------------------------------


async def _count_rows(dsn: str) -> tuple:
    async with await _factory(dsn)() as conn:
        out = []
        for table in ("qs_query_moves", "qs_schedule_state", "qs_schedule_runs", "qs_scheduler_workers"):
            out.append(await conn.fetchval(f"SELECT count(*) FROM management.{table}", column=0))
        return tuple(out)


async def _bootstrap_state(dsn, coord, owner, kind="query", **kw):
    async with await _factory(dsn)() as conn:
        await conn.transaction()
        state = await coord.ensure_schedule_state(
            conn, owner, kind, trigger_spec={"type": "interval", "minutes": 5}, **kw
        )
        await conn.commit()
        return state


@pytest.mark.asyncio
async def test_inspect_unscheduled_needs_no_scheduler(pg_dsn) -> None:
    await _reset(pg_dsn, install=False)  # not even DDL: unscheduled moves need no scheduler
    coord = _coordinator(pg_dsn, rollout=False, definition=_definition())
    source = QueryIdentity(_store("public", contract="legacy"), "s")
    result = await coord.inspect(source, _store("wm"))
    assert result["scheduler_required"] is False and result["blockers"] == [] and result["ready"] is True
    assert result["schedules"] == []


@pytest.mark.asyncio
async def test_inspect_scheduled_blockers_and_readonly(pg_dsn) -> None:
    await _reset(pg_dsn)
    source_store = _store("public", contract="legacy")
    source = QueryIdentity(source_store, "s")
    owner = ScheduleOwner.from_identity(source)
    coord = _coordinator(pg_dsn, definition=_definition(attributes=SCHEDULED))

    before = await _count_rows(pg_dsn)
    result = await coord.inspect(source, _store("wm"))
    assert await _count_rows(pg_dsn) == before  # read-only
    codes = {b["code"] for b in result["blockers"]}
    assert codes == {"move_scheduler_not_ready"} and result["handoff_ready"] is False

    await coord.register_worker()
    result = await coord.inspect(source, _store("wm"))
    assert any("bootstrapped" in b["message"] for b in result["blockers"])

    state = await _bootstrap_state(pg_dsn, coord, owner, next_fire_at=utcnow_plus(5), timezone_name="Europe/Madrid")
    result = await coord.inspect(source, _store("wm"))
    assert result["blockers"] == [] and result["ready"] and result["handoff_ready"]
    assert result["schedules"][0]["timezone"] == "Europe/Madrid"
    assert result["schedules"][0]["run_as"] == "preserve-existing"
    revision = result["schedule_revision"]

    # Advancing the cursor does not stale the revision; pausing does.
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET next_fire_at = next_fire_at + interval '5 min'")
    assert (await coord.inspect(source, _store("wm")))["schedule_revision"] == revision
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET paused = true")
    assert (await coord.inspect(source, _store("wm")))["schedule_revision"] != revision
    assert state["logical_schedule_id"]


def utcnow_plus(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


@pytest.mark.asyncio
async def test_inspect_unsupported_invalid_conflict_and_frozen(pg_dsn) -> None:
    await _reset(pg_dsn)
    source = QueryIdentity(_store("public", contract="legacy"), "s")
    owner = ScheduleOwner.from_identity(source)

    dwh = _coordinator(pg_dsn, definition=_definition(dwh=True, dwh_scheduler={"cron": "* * * * *"}))
    result = await dwh.inspect(source, _store("wm"))
    assert "move_scheduler_unsupported" in {b["code"] for b in result["blockers"]}

    bad = _coordinator(pg_dsn, definition=_definition(attributes=SCHEDULED), trigger_parser=lambda t, s: None)
    result = await bad.inspect(source, _store("wm"))
    assert "move_schedule_invalid" in {b["code"] for b in result["blockers"]}

    coord = _coordinator(pg_dsn, definition=_definition(attributes=SCHEDULED))
    await coord.register_worker()
    await _bootstrap_state(pg_dsn, coord, owner, next_fire_at=utcnow_plus(5))
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_schedule_state SET dispatch_gate='frozen'")
    assert "move_in_progress" in {b["code"] for b in (await coord.inspect(source, _store("wm")))["blockers"]}
    async with await _factory(pg_dsn)() as conn:
        await conn.execute(
            "UPDATE management.qs_schedule_state SET dispatch_gate='open', "
            "trigger_spec = trigger_spec || '{\"mirror_conflict\": true}'::jsonb")
    messages = [b["message"] for b in (await coord.inspect(source, _store("wm")))["blockers"]]
    assert any("disagree" in m for m in messages)


# -- prepare -------------------------------------------------------------------------------------


async def _journal(dsn: str, slug: str, source: QueryStore, target: QueryStore) -> str:
    async with await _factory(dsn)() as conn:
        op = await conn.fetchval(
            "INSERT INTO management.qs_query_moves (actor_id, idempotency_key, request_digest, query_slug,"
            " source_namespace, source_schema, source_table, source_contract,"
            " target_namespace, target_schema, target_table, target_contract, plan_snapshot)"
            " VALUES (1,$1,'d',$2,$3,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb) RETURNING operation_id",
            uuid.uuid4(), slug, source.database_namespace, source.schema, source.table, source.contract,
            target.database_namespace, target.schema, target.table, target.contract, column=0,
        )
        for st, role in ((source, "source"), (target, "target")):
            await conn.execute(
                "INSERT INTO management.qs_move_reservations VALUES ($1,$2,$3,$4,$5,$6, now())",
                st.database_namespace, st.schema, st.table, slug, op, role,
            )
        return str(op)


@pytest.mark.asyncio
async def test_prepare_unscheduled_goes_to_draining_without_gate(pg_dsn) -> None:
    await _reset(pg_dsn)
    src, dst = _store("public", contract="legacy"), _store("wm")
    op = await _journal(pg_dsn, "u", src, dst)
    result = await _coordinator(pg_dsn, rollout=False).prepare(op)
    assert result["state"] == "draining" and result["scheduler_sync"] == "not_configured"
    assert result["moved"] is None or result["moved"] is False
    # Idempotent once past preparing.
    assert (await _coordinator(pg_dsn).prepare(op))["state"] == "draining"
    with pytest.raises(TenantError):
        await _coordinator(pg_dsn).prepare(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_prepare_freezes_gate_only_when_ready_and_fails_closed_otherwise(pg_dsn) -> None:
    await _reset(pg_dsn)
    src, dst = _store("public", contract="legacy"), _store("wm")
    owner = ScheduleOwner.from_store(src, "s")
    other = ScheduleOwner.from_store(src, "other")
    coord = _coordinator(pg_dsn)
    await _bootstrap_state(pg_dsn, coord, owner, next_fire_at=utcnow_plus(5))
    await _bootstrap_state(pg_dsn, coord, other, next_fire_at=utcnow_plus(5))

    # Not ready (no worker heartbeat): failed, moved=false, gate untouched, reservations released.
    op = await _journal(pg_dsn, "s", src, dst)
    result = await coord.prepare(op)
    assert result["state"] == "failed" and result["moved"] is False
    assert result["error_code"] == "move_scheduler_not_ready"
    async with await _factory(pg_dsn)() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM management.qs_schedule_state WHERE dispatch_gate='frozen'", column=0) == 0
        assert await conn.fetchval("SELECT count(*) FROM management.qs_move_reservations", column=0) == 0

    # Ready: only the moving slug is frozen, tied to the operation.
    await coord.register_worker()
    op2 = await _journal(pg_dsn, "s", src, dst)
    result = await coord.prepare(op2)
    assert result["state"] == "draining" and result["scheduler_sync"] == "pending"
    async with await _factory(pg_dsn)() as conn:
        rows = await conn.fetch_all(
            "SELECT query_slug, dispatch_gate, gate_operation_id::text AS g FROM management.qs_schedule_state "
            "ORDER BY query_slug")
    assert [(r["query_slug"], r["dispatch_gate"]) for r in rows] == [("other", "open"), ("s", "frozen")]
    assert rows[1]["g"] == op2
