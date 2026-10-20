"""FEAT-176: legacy-contract repairs, has_program_slug derivation, catalog and fire-time guard."""
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from querysource.handlers.query_stores import QueryStoreCatalog
from querysource.models import QueryModel
from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

NS = "localhost:5432/querysource"


def _store(schema: str, has_program_slug: bool, table: str = "queries") -> QueryStore:
    columns = {"query_slug", "description", "attributes"} | ({"program_slug"} if has_program_slug else set())
    return QueryStore(NS, schema, table, has_program_slug, frozenset(columns))


class _CatalogConn:
    """information_schema double: ``tables`` is ``[(schema, table, extra_columns)]``."""

    def __init__(self, tables: list[tuple[str, str, tuple[str, ...]]]) -> None:
        self.tables = tables

    def get_dsn(self) -> str:
        return f"postgresql://{NS}"

    async def query(self, sentence: str) -> tuple[list[tuple], None]:
        if "information_schema.columns" in sentence:
            return [
                (schema, table, column, "text", "YES")
                for schema, table, extra in self.tables
                for column in ("query_slug", "description", *extra)
            ], None
        return [(schema, table, "BASE TABLE") for schema, table, _ in self.tables], None


async def _registry(tables: list[tuple[str, str, tuple[str, ...]]]) -> TenantRegistry:
    registry = TenantRegistry()
    await registry.discover(_CatalogConn(tables))
    return registry


class _Conn:
    """Records SQL; ``rows`` maps a SQL substring to the row fetch_one returns."""

    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self.rows = rows or {}
        self.sql: list[tuple[str, tuple]] = []

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def transaction(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def fetch_one(self, sentence: str, *args: Any) -> Any:
        self.sql.append((sentence, args))
        for pattern, row in self.rows.items():
            if pattern in sentence:
                return row
        return None


def _repo(registry: TenantRegistry, conn: _Conn) -> DefinitionRepository:
    async def factory() -> _Conn:
        return conn

    return DefinitionRepository(registry=registry, connection_factory=factory)


# -- has_program_slug derivation ---------------------------------------------------


async def test_tenant_with_program_slug_is_accepted_and_flagged() -> None:
    registry = await _registry([
        ("public", "queries", ("program_slug",)),
        ("plain", "queries", ()),
        ("with_ps", "queries", ("program_slug",)),
    ])
    assert {s.schema for s in registry.stores()} == {"public", "plain", "with_ps"}
    assert registry.resolve("with_ps").has_program_slug is True
    assert registry.resolve("plain").has_program_slug is False
    assert registry.resolve(None).has_program_slug is True
    assert not registry.diagnostics()


async def test_default_is_the_configured_schema_and_table(monkeypatch) -> None:
    monkeypatch.setattr(QueryModel.Meta, "schema", "legacy_qs")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    registry = await _registry([
        ("legacy_qs", "saved_queries", ("program_slug",)),
        ("public", "queries", ("program_slug",)),
        ("other", "saved_queries", ()),  # not 'queries' and not configured: ignored
    ])
    default = registry.resolve(None)
    assert (default.schema, default.table) == ("legacy_qs", "saved_queries")
    assert registry.resolve("legacy_qs") == default
    assert registry.resolve("public").schema == "public"  # an ordinary schema selector
    assert {s.schema for s in registry.stores()} == {"legacy_qs", "public"}


async def test_fallback_default_when_not_discovered_has_program_slug() -> None:
    registry = await _registry([("tenant1", "queries", ())])
    default = registry.resolve(None)
    assert (default.schema, default.table, default.has_program_slug) == ("public", "queries", True)
    with pytest.raises(TenantError) as err:
        registry.resolve("unknown")
    assert err.value.error_code == "tenant_not_available"


# -- legacy-contract repairs -------------------------------------------------------


async def test_export_insert_strips_run_as_and_keeps_program_slug() -> None:
    registry = TenantRegistry()
    store = _store("public", True)
    conn = _Conn({"SELECT *": {"query_slug": "s1", "program_slug": "hisense", RUN_AS_COLUMN: 7}})
    sql = await _repo(registry, conn).export_insert(QueryIdentity(store=store, slug="s1"))
    assert RUN_AS_COLUMN not in sql
    assert "program_slug" in sql and "hisense" in sql


async def test_patch_rejects_fields_outside_the_allowlist_before_sql() -> None:
    conn = _Conn()
    repo = _repo(TenantRegistry(), conn)
    with pytest.raises(TenantError) as err:
        await repo.patch(QueryIdentity(store=_store("tenant1", False), slug="s1"), {"not_a_column": 1})
    assert err.value.error_code == "invalid_tenant"
    assert conn.sql == []


async def test_patch_accepts_program_slug_on_a_store_that_has_it() -> None:
    conn = _Conn({"UPDATE": {"query_slug": "s1", "program_slug": "p2", "attributes": None}})
    repo = _repo(TenantRegistry(), conn)
    result = await repo.patch(QueryIdentity(store=_store("public", True), slug="s1"), {"program_slug": "p2"})
    assert result["program_slug"] == "p2"


async def test_upsert_keeps_program_slug_when_omitted() -> None:
    conn = _Conn({"INSERT": {"query_slug": "s1", "program_slug": "kept", "is_inserted": False}})
    repo = _repo(TenantRegistry(), conn)
    result, created = await repo.upsert(
        QueryIdentity(store=_store("public", True), slug="s1"), {"query_slug": "s1", "description": "d"}
    )
    insert_sql, values = next((s, a) for s, a in conn.sql if "INSERT" in s)
    assert '"program_slug" = EXCLUDED' not in insert_sql  # stored value preserved on conflict
    assert "default" in values  # established default only applies on insert
    assert result["program_slug"] == "kept" and created is False


async def test_tenant_store_rejects_program_slug_on_create() -> None:
    conn = _Conn()
    with pytest.raises(TenantError) as err:
        await _repo(TenantRegistry(), conn).create(_store("t1", False), {"query_slug": "s", "program_slug": "x"})
    assert err.value.error_code == "invalid_tenant"
    assert conn.sql == []


async def test_revision_includes_program_slug_only_when_persisted() -> None:
    store = _store("public", True)
    revisions = []
    for program in ("a", "b"):
        conn = _Conn({"SELECT *": {"query_slug": "s1", "program_slug": program}})
        loaded = await _repo(TenantRegistry(), conn).get(QueryIdentity(store=store, slug="s1"))
        assert loaded.runtime.program_slug == program
        revisions.append(loaded.revision)
    assert revisions[0] != revisions[1]


# -- catalog -----------------------------------------------------------------------


async def _catalog(registry: TenantRegistry | None) -> tuple[int, dict]:
    app = web.Application()
    if registry is not None:
        app["qs_tenant_registry"] = registry
    app.router.add_get("/api/v1/management/query-stores", QueryStoreCatalog().list)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/v1/management/query-stores")
        return response.status, await response.json()


async def test_catalog_shape_default_first_and_no_secrets() -> None:
    registry = await _registry([("zeta", "queries", ()), ("public", "queries", ("program_slug",))])
    status, body = await _catalog(registry)
    assert status == 200
    assert body == {
        "version": 1,
        "data": [
            {"tenant": None, "schema": "public", "table": "queries", "has_program_slug": True, "is_default": True},
            {"tenant": "zeta", "schema": "zeta", "table": "queries", "has_program_slug": False, "is_default": False},
        ],
    }
    assert "localhost" not in json.dumps(body)


async def test_catalog_without_registry_is_503() -> None:
    status, body = await _catalog(None)
    assert status == 503 and body["error_code"] == "tenant_store_unavailable"


async def test_move_route_is_not_shadowed_by_the_meta_route() -> None:
    import importlib

    from querysource import services

    importlib.reload(services)
    app = web.Application()
    services.QuerySource(lazy=True).setup(app)
    match = await app.router.resolve(make_mocked_request("POST", "/api/v1/management/queries/s1/move"))
    assert match.route.resource.canonical == "/api/v1/management/queries/{slug}/move"
    match = await app.router.resolve(make_mocked_request("GET", "/api/v1/management/query-stores"))
    assert match.route.resource.canonical == "/api/v1/management/query-stores"


# -- fire-time guard ---------------------------------------------------------------


async def test_fire_time_guard_skips_and_removes_a_job_whose_slug_is_gone() -> None:
    from apscheduler.triggers.interval import IntervalTrigger

    from querysource.scheduler.jobs import scheduled_query_job
    from querysource.scheduler.scheduler import QSScheduler

    class _Repo:
        def __init__(self) -> None:
            self.present = True

        async def exists(self, store: QueryStore, slug: str) -> bool:
            return self.present

    store = _store("public", True)
    sched = QSScheduler()
    sched._repository = _Repo()
    sched._scheduler = sched._create_scheduler()
    row = {"query_slug": "gone", "attributes": {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}}
    job_id = sched._register_query_row(row, store=store)
    job = sched._scheduler.get_job(job_id)
    assert isinstance(job.trigger, IntervalTrigger)

    guard = job.kwargs["guard"]
    assert await guard("gone", job_id) is True
    sched._repository.present = False
    ran = []

    class _QS:
        def __init__(self, **kwargs: Any) -> None:
            ran.append(kwargs)

    import unittest.mock as mock

    with mock.patch("querysource.queries.qs.QS", _QS):
        await scheduled_query_job(**job.kwargs)
    assert ran == []  # skipped before QS was built
    assert sched._scheduler.get_job(job_id) is None  # removed from this worker
