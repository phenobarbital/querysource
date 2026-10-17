"""Contract-specific INSERT export regression: legacy keeps program_slug, tenant never does."""
import datetime

import pytest

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore


def _store(schema: str, contract: str) -> QueryStore:
    columns = {"query_slug", "description"} | ({"program_slug"} if contract == "legacy" else set())
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract=contract,
        columns=frozenset(columns),
    )


def _legacy_store() -> QueryStore:
    return _store("public", "legacy")


def _tenant_store(schema: str) -> QueryStore:
    return _store(schema, "tenant")


def _base_row(**overrides) -> dict:
    row = {
        "query_slug": "test_query", "description": "A test query", "source": None,
        "params": {}, "attributes": {}, "conditions": {}, "cond_definition": {},
        "fields": [], "filtering": {}, "ordering": [], "grouping": [],
        "columns_definition": [], "qry_options": {}, "h_filtering": False,
        "query_raw": "SELECT 1", "is_raw": False, "is_cached": True,
        "provider": "db", "parser": "SQLParser", "cache_timeout": 3600,
        "cache_refresh": 0, "cache_options": {}, "program_id": 1, "dwh": False,
        "dwh_driver": None, "dwh_info": None, "dwh_scheduler": None,
        "created_at": datetime.datetime(2026, 1, 1), "created_by": None,
        "updated_at": datetime.datetime(2026, 1, 1), "updated_by": None,
    }
    row.update(overrides)
    return row


class _MockConn:
    """Read-only asyncdb connection double."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch_one(self, sql, *args, **kwargs):
        self.calls.append(("fetch_one", sql, args))
        return self.rows[0] if self.rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _factory(conn):
    async def connection_factory():
        return conn

    return connection_factory


@pytest.mark.asyncio
async def test_legacy_export_keeps_program_slug_and_exact_table() -> None:
    """Legacy export names the exact qualified table and includes the stored program."""
    store = _legacy_store()
    conn = _MockConn([_base_row(program_slug="alpha")])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    sql = await repo.export_insert(QueryIdentity(store=store, slug="test_query"))

    assert sql.startswith('INSERT INTO "public"."queries" (')
    assert "program_slug" in sql
    assert "alpha" in sql
    assert all(call[0] == "fetch_one" for call in conn.calls)


@pytest.mark.asyncio
async def test_tenant_export_never_contains_program_slug() -> None:
    """Tenant export omits program_slug even when a stray run-as column is present."""
    store = _tenant_store("wm_assembly")
    row = _base_row(scheduler_run_as_user_id=7)
    conn = _MockConn([row])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    sql = await repo.export_insert(QueryIdentity(store=store, slug="test_query"))

    assert '"wm_assembly"."queries"' in sql
    assert "program_slug" not in sql
    assert "scheduler_run_as_user_id" not in sql
