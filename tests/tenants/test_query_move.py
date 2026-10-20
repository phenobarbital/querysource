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
# A store that can hold a scheduled definition must also have its run-as audit
# table: transferring a delegated identity without a trail is refused.
_AUDIT_DDL = """
CREATE TABLE {schema}.queries_run_as_audit (
  audit_id bigserial PRIMARY KEY, query_slug varchar NOT NULL, old_user_id int, new_user_id int,
  operation varchar NOT NULL, changed_by int, changed_at timestamptz NOT NULL DEFAULT now(),
  request_info jsonb
)
"""
DDL = {
    "qsmv_default": "CREATE TABLE qsmv_default.queries (" + _FULL_COLUMNS.format(extra=", program_slug text") + ");"
    + _AUDIT_DDL.format(schema="qsmv_default"),
    "qsmv_a": "CREATE TABLE qsmv_a.queries (" + _FULL_COLUMNS.format(extra="") + ");"
    + _AUDIT_DDL.format(schema="qsmv_a"),
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


# -- ported guards (FEAT-176 review): references, audit, attribution, in-flight ----------


async def _audit(dsn: str, schema: str, slug: str) -> list[dict]:
    async with await _factory(dsn)() as conn:
        rows = await conn.fetch_all(
            f"SELECT * FROM {schema}.queries_run_as_audit WHERE query_slug = $1 ORDER BY audit_id", slug
        )
    return [dict(r) for r in rows or []]


async def test_a_pipeline_whose_children_inherit_the_owner_is_refused(env) -> None:
    """A saved child that omits 'tenant' resolves against its parent's owner.

    Moving the parent would re-point it with nothing failing: the pipeline breaks on
    its next run, or quietly runs a different definition that shares the slug in the
    destination. The move must refuse until the child is pinned.
    """
    await _seed(env["dsn"], {"qsmv_default": [{
        "query_slug": "parent", "description": "p", "program_slug": "acme", "provider": "multi",
        "query_raw": json.dumps({"queries": {"detail": {"slug": "child_detail"}}}),
    }]})

    status, body = await _move(env["client"], "parent", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 409, body
    assert body["error_code"] == "move_inherited_references"
    assert body["details"]["blockers"][0]["path"] == "queries.detail.tenant"
    # Nothing moved.
    assert await _fetch(env["dsn"], "qsmv_default", "parent") is not None
    assert await _fetch(env["dsn"], "qsmv_a", "parent") is None


async def test_a_pinned_child_does_not_block_the_move(env) -> None:
    """An explicit 'tenant' (including an explicit null) is pinned and never blocks."""
    await _seed(env["dsn"], {"qsmv_default": [{
        "query_slug": "pinned", "description": "p", "program_slug": "acme", "provider": "multi",
        "query_raw": json.dumps({"queries": {
            "a": {"slug": "child_a", "tenant": "qsmv_a"},
            "b": {"slug": "child_b", "tenant": None},
            "c": {"raw": "SELECT 1"},
        }}),
    }]})

    status, body = await _move(env["client"], "pinned", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 200, body
    assert await _fetch(env["dsn"], "qsmv_a", "pinned") is not None


async def test_a_dynamic_reference_cannot_be_proven_safe(env) -> None:
    """A templated slug/tenant is reported, never guessed at."""
    await _seed(env["dsn"], {"qsmv_default": [{
        "query_slug": "dynamic", "description": "d", "program_slug": "acme", "provider": "multi",
        "query_raw": json.dumps({"queries": {"x": {"slug": "{{ var }}"}}}),
    }]})

    status, body = await _move(env["client"], "dynamic", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 409, body
    assert body["error_code"] == "move_dependency_analysis_unsupported"


async def test_a_transferred_run_as_leaves_an_attributed_audit_trail(env) -> None:
    """'daily' carries run_as=5: the origin records a clear, the destination a set."""
    status, body = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200, body

    [cleared] = await _audit(env["dsn"], "qsmv_default", "daily")
    assert (cleared["operation"], cleared["old_user_id"], cleared["new_user_id"]) == ("clear", 5, None)
    [was_set] = await _audit(env["dsn"], "qsmv_a", "daily")
    assert (was_set["operation"], was_set["old_user_id"], was_set["new_user_id"]) == ("set", None, 5)
    # Attributed to the mover, not to the delegated identity.
    assert cleared["changed_by"] == ACTOR and was_set["changed_by"] == ACTOR


async def test_a_definition_without_run_as_writes_no_audit_events(env) -> None:
    """Null-to-null is not an identity change and must not emit events."""
    status, body = await _move(env["client"], "wide", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200, body
    assert await _audit(env["dsn"], "qsmv_default", "wide") == []
    assert await _audit(env["dsn"], "qsmv_a", "wide") == []


async def test_a_missing_audit_table_refuses_the_transfer_before_anything_moves(env) -> None:
    """Without the destination audit table a run-as transfer has no trail, so it is refused."""
    async with await _factory(env["dsn"])() as conn:
        await conn.execute("DROP TABLE qsmv_a.queries_run_as_audit")

    status, body = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 400, body
    assert body["error_code"] == "move_validation_failed"
    assert body["details"]["run_as_audit"] == "qsmv_a.queries_run_as_audit"
    assert await _fetch(env["dsn"], "qsmv_default", "daily") is not None
    assert await _fetch(env["dsn"], "qsmv_a", "daily") is None


async def test_the_move_is_stamped_with_the_mover(env) -> None:
    """A moved row must not be indistinguishable from one that was never touched."""
    before = await _fetch(env["dsn"], "qsmv_default", "wide")
    status, _ = await _move(env["client"], "wide", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200

    moved = await _fetch(env["dsn"], "qsmv_a", "wide")
    assert moved["updated_by"] == ACTOR
    assert moved["updated_at"] is not None and moved["updated_at"] != before["updated_at"]
    # Creation provenance is preserved verbatim.
    assert moved["created_by"] == before["created_by"]


async def test_a_run_executing_right_now_refuses_the_move(env) -> None:
    """Pausing stops future dispatches, not one already running: refuse instead of racing it."""
    from querysource.scheduler import jobs

    origin = env["registry"].resolve(None)
    owner = {"schema": origin.schema, "table": origin.table}
    with jobs._running(owner, "daily"):
        status, body = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
        assert status == 409, body
        assert body["error_code"] == "move_run_in_flight"
        assert await _fetch(env["dsn"], "qsmv_default", "daily") is not None
        # The origin schedule was not left paused by the refusal.
        assert all(job.next_run_time is not None for job in _job(env, "daily", None))

    status, _ = await _move(env["client"], "daily", {"tenant": None, "destination_tenant": "qsmv_a"})
    assert status == 200, "the move succeeds once the run finishes"


async def test_a_destination_that_persists_something_else_aborts_the_move(env) -> None:
    """The INSERT only fails on a hard type mismatch.

    A destination whose stored value differs from what was sent — a coercing column
    type, a rewriting trigger — would otherwise commit a move that quietly changed
    the definition. The readback catches it while the origin row is still there.
    """
    async with await _factory(env["dsn"])() as conn:
        await conn.execute("""
            CREATE FUNCTION qsmv_a.mangle() RETURNS trigger AS $$
            BEGIN NEW.description := 'mangled'; RETURN NEW; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER mangle BEFORE INSERT ON qsmv_a.queries
            FOR EACH ROW EXECUTE FUNCTION qsmv_a.mangle();
        """)

    status, body = await _move(env["client"], "wide", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 400, body
    assert body["error_code"] == "move_validation_failed"
    assert body["details"]["columns"] == ["description"]
    # Rolled back: the origin keeps the definition and the destination holds nothing.
    assert await _fetch(env["dsn"], "qsmv_default", "wide") is not None
    assert await _fetch(env["dsn"], "qsmv_a", "wide") is None


async def test_a_jsonb_round_trip_is_not_reported_as_a_mismatch(env) -> None:
    """The same JSON value read back as text or as a mapping is the same value."""
    await _seed(env["dsn"], {"qsmv_default": [{
        "query_slug": "jsonrow", "description": "j", "program_slug": "acme",
        "attributes": {"a": [1, 2], "b": {"c": True}}, "params": {"x": None},
    }]})

    status, body = await _move(env["client"], "jsonrow", {"tenant": None, "destination_tenant": "qsmv_a"})

    assert status == 200, body
    moved = await _fetch(env["dsn"], "qsmv_a", "jsonrow")
    assert json.loads(moved["attributes"]) if isinstance(moved["attributes"], str) else moved["attributes"]
