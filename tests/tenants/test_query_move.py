"""FEAT-176 synchronous move against a disposable PostgreSQL, through the real aiohttp handler.

The ``pg_dsn`` fixture starts a private throwaway cluster (initdb/pg_ctl) unless
``QS_TEST_PG_DSN`` names a disposable database. Only ``qsmv_*`` schemas are
created; the configured default store is redirected to ``qsmv_default.queries``.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from asyncdb import AsyncDB

from querysource.handlers.query_moves import QueryMoveView
from querysource.handlers.query_stores import QueryStoreCatalog
from querysource.models import QueryModel
from querysource.repositories.definitions import DefinitionRepository
from querysource.scheduler.scheduler import QSScheduler
from querysource.tenant_errors import OWNERSHIP_STATUS
from querysource.tenants import TenantRegistry

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_move_contract_v2.json"
SCHEMAS = ("qsmv_default", "qsmv_a", "qsmv_narrow")
ACTOR = 77
SCHEDULED = {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}

_FULL_COLUMNS = """
  query_slug text PRIMARY KEY, description text, source text,
  params jsonb DEFAULT '{{}}', attributes jsonb DEFAULT '{{}}', conditions jsonb DEFAULT '{{}}',
  cond_definition jsonb DEFAULT '{{}}', fields text[] DEFAULT '{{}}', filtering jsonb DEFAULT '{{}}',
  ordering text[] DEFAULT '{{}}', grouping text[] DEFAULT '{{}}', columns_definition text[] DEFAULT '{{}}',
  qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false, query_raw text, is_raw boolean DEFAULT false,
  is_cached boolean DEFAULT false, provider text DEFAULT 'db', parser text DEFAULT 'SQLParser',
  cache_timeout int, cache_refresh int, cache_options jsonb DEFAULT '{{}}',
  program_id int, dwh boolean, dwh_driver text, dwh_info jsonb,
  dwh_scheduler jsonb, created_at timestamptz, created_by int, updated_at timestamptz,
  updated_by int, scheduler_run_as_user_id int{extra}
"""
DDL = {
    "qsmv_default": "CREATE TABLE qsmv_default.queries (" + _FULL_COLUMNS.format(extra=", program_slug text") + ")",
    "qsmv_a": "CREATE TABLE qsmv_a.queries (" + _FULL_COLUMNS.format(extra="") + ")",
    "qsmv_narrow": "CREATE TABLE qsmv_narrow.queries (query_slug text PRIMARY KEY, description text, provider text)",
}


def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"), reverse=True):
        return candidate
    return None


@pytest.fixture(scope="module")
def pg_dsn():
    """A disposable PostgreSQL DSN (private initdb cluster unless QS_TEST_PG_DSN is set)."""
    dsn = os.environ.get("QS_TEST_PG_DSN")
    if dsn:
        yield dsn
        return
    initdb, pg_ctl = _find_bin("initdb"), _find_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("no PostgreSQL binaries and QS_TEST_PG_DSN unset")
    root = tempfile.mkdtemp(prefix="qs_move_pg_")
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


def _factory(dsn: str):
    async def factory():
        return await AsyncDB("pg", dsn=dsn).connection()

    return factory


async def _seed(dsn: str, rows: dict[str, list[dict[str, Any]]]) -> None:
    async with await _factory(dsn)() as conn:
        for schema in SCHEMAS:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await conn.execute(f"CREATE SCHEMA {schema}")
            await conn.execute(DDL[schema])
        for schema, items in rows.items():
            for item in items:
                cols = list(item)
                values = list(item.values())
                await conn.fetch_one(
                    f"INSERT INTO {schema}.queries ({', '.join(cols)}) "
                    f"VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))}) RETURNING query_slug",
                    *values,
                )


async def _fetch(dsn: str, schema: str, slug: str) -> dict | None:
    async with await _factory(dsn)() as conn:
        row = await conn.fetch_one(f"SELECT * FROM {schema}.queries WHERE query_slug = $1", slug)
    return dict(row) if row else None


DEFAULT_ROWS = {
    "qsmv_default": [
        {"query_slug": "daily", "description": "daily", "program_slug": "acme", "attributes": SCHEDULED,
         "scheduler_run_as_user_id": 5},
        {"query_slug": "dup", "description": "dup", "program_slug": "acme", "attributes": SCHEDULED},
        {"query_slug": "wide", "description": "wide", "program_slug": "acme", "query_raw": "select 1"},
    ],
    "qsmv_a": [{"query_slug": "dup", "description": "already here"}],
}


@pytest.fixture
async def env(pg_dsn, monkeypatch):
    """Seeded stores, a real repository/registry, a paused real scheduler and a test client."""
    monkeypatch.setattr(QueryModel.Meta, "schema", "qsmv_default")
    monkeypatch.setattr(QueryModel.Meta, "name", "queries")
    await _seed(pg_dsn, DEFAULT_ROWS)
    registry = TenantRegistry()
    async with await _factory(pg_dsn)() as conn:
        await registry.discover(conn, allowlist=list(SCHEMAS))
    repo = DefinitionRepository(registry=registry, connection_factory=_factory(pg_dsn))
    sched = QSScheduler()
    sched._registry, sched._repository = registry, repo
    sched._scheduler = sched._create_scheduler()
    sched._scheduler.start(paused=True)
    for slug in ("daily", "dup"):
        await sched.register_slug(slug, tenant=None)

    actor = {"id": ACTOR}

    async def _run_as_context(self):
        return actor["id"], {}

    monkeypatch.setattr(QueryMoveView, "_run_as_context", _run_as_context)
    app = web.Application()
    app["qs_tenant_registry"], app["qs_definition_repository"], app["qs_scheduler"] = registry, repo, sched
    app.router.add_get("/api/v1/management/query-stores", QueryStoreCatalog().list)
    app.router.add_route("POST", "/api/v1/management/queries/{slug}/move", QueryMoveView)
    async with TestClient(TestServer(app)) as client:
        yield {"client": client, "dsn": pg_dsn, "sched": sched, "registry": registry, "actor": actor}
    sched._scheduler.shutdown(wait=False)


async def _move(client: TestClient, slug: str, body: Any) -> tuple[int, dict]:
    response = await client.post(f"/api/v1/management/queries/{slug}/move", json=body)
    return response.status, await response.json()


def _job(env: dict, slug: str, tenant: str | None):
    sched = env["sched"]
    store = env["registry"].resolve(tenant)
    return [sched._scheduler.get_job(jid) for jid in sched._slug_job_ids(slug, store=store)
            if sched._scheduler.get_job(jid) is not None]


async def test_happy_path_moves_row_and_schedule(env) -> None:
    status, body = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200, body
    assert body["data"]["scheduler"] == {"status": "ok", "paused": 1, "registered": 1}
    assert await _fetch(env["dsn"], "qsmv_default", "daily") is None
    moved = await _fetch(env["dsn"], "qsmv_a", "daily")
    assert moved["description"] == "daily" and "program_slug" not in moved
    assert moved["scheduler_run_as_user_id"] == 5
    assert _job(env, "daily", None) == []  # origin job removed from this worker
    [dest_job] = _job(env, "daily", "qsmv_a")
    assert dest_job.kwargs["owner"]["schema"] == "qsmv_a"


async def test_move_back_to_default_restores_runtime_program_slug(env) -> None:
    await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
    status, _ = await _move(env["client"], "daily", {"tenant": "qsmv_a", "destination_tenant": None})
    assert status == 200
    assert (await _fetch(env["dsn"], "qsmv_default", "daily"))["program_slug"] == "qsmv_a"


async def test_target_exists_rolls_back_and_unpauses_jobs(env) -> None:
    [job] = _job(env, "dup", None)
    assert job.next_run_time is not None
    status, body = await _move(env["client"], "dup", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 409 and body["error_code"] == "move_target_exists"
    assert await _fetch(env["dsn"], "qsmv_default", "dup") is not None
    [job] = _job(env, "dup", None)
    assert job.next_run_time is not None  # resumed after the failed move


async def test_column_incompatibility_lists_columns(env) -> None:
    status, body = await _move(env["client"], "wide", {"tenant": None, "destination_tenant": "qsmv_narrow"})
    assert status == 400 and body["error_code"] == "move_validation_failed"
    assert "query_raw" in body["details"]["columns"]
    assert await _fetch(env["dsn"], "qsmv_default", "wide") is not None
    assert await _fetch(env["dsn"], "qsmv_narrow", "wide") is None


async def test_reregister_failure_returns_200_with_failed_status(env, monkeypatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> dict:
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(env["sched"], "register_slug", boom)
    status, body = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200
    assert body["data"]["scheduler"] == {"status": "failed", "paused": 1, "registered": 0}
    assert "internal detail" not in json.dumps(body)
    assert await _fetch(env["dsn"], "qsmv_a", "daily") is not None  # the move stays committed
    assert _job(env, "daily", None) == []


async def test_responses_match_the_v2_contract_fixture(env) -> None:
    """Every documented case is replayed against the real handler (the fixture cannot drift)."""
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["error_codes"] == {code: OWNERSHIP_STATUS[code] for code in fixture["error_codes"]}
    client = env["client"]
    for case in fixture["cases"]:
        env["actor"]["id"] = case.get("actor", ACTOR)
        if case["endpoint"] == "catalog":
            response = await client.get(fixture["endpoints"]["catalog"]["path"])
        else:
            path = fixture["endpoints"]["move"]["path"].format(slug=case["slug"])
            response = await client.post(path, json=case["body"])
        assert response.status == case["status"], case["name"]
        assert await response.json() == case["response"], case["name"]
        if case["status"] >= 400:
            assert fixture["error_codes"][case["response"]["error_code"]] == case["status"]
