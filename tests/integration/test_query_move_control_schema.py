"""Apply the FEAT-176 control DDL to a disposable PostgreSQL and verify its invariants.

Opt-in/safe: uses QS_TEST_PG_DSN (a scratch database) when set, otherwise boots a
throw-away local cluster from the installed PostgreSQL binaries; skips when neither
is available. Never touches production metadata or business tables.
"""
import asyncio
import glob
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

import asyncpg
import pytest

DDL = Path(__file__).resolve().parents[2] / "docs" / "sql" / "query_move_control.sql"


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
    root = tempfile.mkdtemp(prefix="qs_ctl_pg_")
    data = os.path.join(root, "data")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "qs"], check=True, capture_output=True)
    subprocess.run(
        [pg_ctl, "-D", data, "-o", f"-p {port} -k {root} -c listen_addresses=127.0.0.1",
         "-l", os.path.join(root, "log"), "-w", "start"],
        check=True, capture_output=True,
    )
    try:
        yield f"postgresql://qs@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


async def _connect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await conn.execute("DROP SCHEMA IF EXISTS management CASCADE")
    await conn.execute(DDL.read_text())
    return conn


def _move_args(**over) -> dict:
    args = dict(
        actor_id=7, idempotency_key=uuid.uuid4(), request_digest="d", query_slug="s",
        sns="h:5432/db", ssc="public", stb="queries", scn="legacy",
        tns="h:5432/db", tsc="wm", ttb="queries", tcn="tenant",
    )
    args.update(over)
    return args


_INSERT_MOVE = (
    "INSERT INTO management.qs_query_moves (actor_id, idempotency_key, request_digest, query_slug,"
    " source_namespace, source_schema, source_table, source_contract,"
    " target_namespace, target_schema, target_table, target_contract, plan_snapshot)"
    " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'{}'::jsonb) RETURNING operation_id"
)


async def _insert_move(conn, **over):
    a = _move_args(**over)
    return await conn.fetchval(
        _INSERT_MOVE, a["actor_id"], a["idempotency_key"], a["request_digest"], a["query_slug"],
        a["sns"], a["ssc"], a["stb"], a["scn"], a["tns"], a["tsc"], a["ttb"], a["tcn"],
    )


@pytest.mark.asyncio
async def test_control_ddl_is_repeatable_and_creates_only_control_tables(pg_dsn) -> None:
    conn = await _connect(pg_dsn)
    try:
        await conn.execute(DDL.read_text())  # second run changes nothing
        tables = {r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'management'")}
        assert tables == {
            "qs_query_moves", "qs_move_reservations", "qs_schedule_state",
            "qs_schedule_runs", "qs_scheduler_workers",
        }
        # Never creates a queries table anywhere.
        assert await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'queries'") == 0
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='management' AND table_name='qs_query_moves'")}
        assert {"lease_owner", "lease_expires_at", "plan_snapshot", "moved", "scheduler_sync",
                "request_digest", "cutover_at"} <= cols
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_idempotency_key_unique_per_deployment_and_actor(pg_dsn) -> None:
    conn = await _connect(pg_dsn)
    try:
        key = uuid.uuid4()
        await _insert_move(conn, idempotency_key=key, query_slug="a")
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_move(conn, idempotency_key=key, query_slug="b")
        # A different actor may reuse the same key value.
        await _insert_move(conn, idempotency_key=key, actor_id=8, query_slug="c")
        # Same store on both ends is refused.
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_move(conn, tsc="public", ttb="queries", query_slug="d")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_identity_reservations_block_overlap_and_opposite_direction(pg_dsn) -> None:
    conn = await _connect(pg_dsn)
    try:
        op1 = await _insert_move(conn, query_slug="s")
        ident = ("h:5432/db", "public", "queries", "s")
        target = ("h:5432/db", "wm", "queries", "s")
        ins = ("INSERT INTO management.qs_move_reservations "
               "(namespace_id, schema_name, table_name, query_slug, operation_id, role) "
               "VALUES ($1,$2,$3,$4,$5,$6)")
        await conn.execute(ins, *ident, op1, "source")
        await conn.execute(ins, *target, op1, "target")
        # Opposite direction (wm -> public, same slug) collides on either identity.
        op2 = await _insert_move(conn, query_slug="s", ssc="wm", scn="tenant", tsc="public", tcn="legacy")
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(ins, *target, op2, "source")
        # Another slug is unaffected; releasing the operation frees the identities.
        await conn.execute("DELETE FROM management.qs_query_moves WHERE operation_id = $1", op1)
        await conn.execute(ins, *target, op2, "source")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_schedule_state_runs_and_workers(pg_dsn) -> None:
    conn = await _connect(pg_dsn)
    try:
        ins_state = (
            "INSERT INTO management.qs_schedule_state (owner_namespace, owner_schema, owner_table,"
            " query_slug, kind, trigger_spec, timezone, next_fire_at) "
            "VALUES ('h:5432/db', 'public', 'queries', 's', $1, '{\"type\":\"interval\"}', 'Europe/Madrid',"
            " now()) RETURNING logical_schedule_id"
        )
        sid = await conn.fetchval(ins_state, "query")
        # Stable id + one schedule per owner/slug/kind.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.fetchval(ins_state, "query")
        await conn.fetchval(ins_state, "cache")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.fetchval(ins_state, "bogus")
        row = await conn.fetchrow(
            "SELECT generation, config_revision, paused, dispatch_gate, max_instances "
            "FROM management.qs_schedule_state WHERE logical_schedule_id = $1", sid)
        assert tuple(row) == (1, 1, False, "open", 1)

        # Owner change keeps the logical id (live transfer updates the row in place).
        await conn.execute(
            "UPDATE management.qs_schedule_state SET owner_schema='wm', generation=generation+1 "
            "WHERE logical_schedule_id=$1", sid)

        ins_run = (
            "INSERT INTO management.qs_schedule_runs (logical_schedule_id, generation, scheduled_fire_at,"
            " owner_namespace, owner_schema, owner_table, query_slug, worker_id) "
            "VALUES ($1, $2, '2026-10-16T00:00:00Z', 'h:5432/db', 'wm', 'queries', 's', $3)"
        )
        await conn.execute(ins_run, sid, 2, "w1")
        # The same occurrence is admitted once, whichever process wakes up.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(ins_run, sid, 2, "w2")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(ins_run, uuid.uuid4(), 2, "w3")

        await conn.execute(
            "INSERT INTO management.qs_scheduler_workers (worker_id, protocol_version, process_id) "
            "VALUES ('w1', 1, 123)")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO management.qs_scheduler_workers (worker_id, protocol_version) VALUES ('w0', 0)")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_terminal_state_constraints_and_least_privilege(pg_dsn) -> None:
    conn = await _connect(pg_dsn)
    try:
        op = await _insert_move(conn, query_slug="t")
        with pytest.raises(asyncpg.CheckViolationError):  # completed requires moved=true
            await conn.execute(
                "UPDATE management.qs_query_moves SET state='completed', moved=false WHERE operation_id=$1", op)
        with pytest.raises(asyncpg.CheckViolationError):  # failed requires moved=false
            await conn.execute(
                "UPDATE management.qs_query_moves SET state='failed', moved=NULL WHERE operation_id=$1", op)
        await conn.execute(
            "UPDATE management.qs_query_moves SET state='cutover', moved=NULL WHERE operation_id=$1", op)

        # Grants: runtime role can read/write control rows but never DDL.
        await conn.execute("DROP ROLE IF EXISTS qs_runtime_test")
        await conn.execute("CREATE ROLE qs_runtime_test")
        await conn.execute("SET qs.runtime_role = 'qs_runtime_test'")
        await conn.execute(DDL.read_text())
        assert await conn.fetchval(
            "SELECT has_table_privilege('qs_runtime_test','management.qs_query_moves','UPDATE')")
        assert not await conn.fetchval(
            "SELECT has_table_privilege('qs_runtime_test','management.qs_query_moves','DELETE')")
        assert not await conn.fetchval(
            "SELECT has_table_privilege('qs_runtime_test','management.qs_query_moves','TRUNCATE')")
        assert not await conn.fetchval("SELECT has_schema_privilege('qs_runtime_test','management','CREATE')")
    finally:
        await conn.execute("RESET qs.runtime_role")
        await conn.execute("DROP SCHEMA IF EXISTS management CASCADE")
        await conn.execute("DROP ROLE IF EXISTS qs_runtime_test")
        await conn.close()
