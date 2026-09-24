"""FEAT-151: columns_definition field, reads without the column, write policy."""
import datetime

import pytest

from querysource.models import QueryModel
from querysource.repositories.definitions import _TENANT_COLUMNS, DefinitionRepository
from querysource.tenant_models import TenantQueryDefinition
from querysource.tenants import QueryIdentity, QueryStore


def test_models_declare_columns_definition() -> None:
    assert TenantQueryDefinition(query_slug="x").columns_definition == []
    assert QueryModel(query_slug="x", program_slug="p").columns_definition == []
    assert "columns_definition" in _TENANT_COLUMNS


def test_runtime_model_accepts_declared_columns() -> None:
    m = QueryModel(query_slug="x", program_slug="p", columns_definition=["a", "b"])
    assert m.columns_definition == ["a", "b"]


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


def _base_row(**overrides) -> dict:
    row = {
        "query_slug": "test_query",
        "description": "A test query",
        "source": None,
        "params": {},
        "attributes": {},
        "conditions": {},
        "cond_definition": {},
        "fields": [],
        "filtering": {},
        "ordering": [],
        "grouping": [],
        "columns_definition": [],
        "qry_options": {},
        "h_filtering": False,
        "query_raw": "SELECT 1",
        "is_raw": False,
        "is_cached": True,
        "provider": "db",
        "parser": "SQLParser",
        "cache_timeout": 3600,
        "cache_refresh": 0,
        "cache_options": {},
        "program_id": 1,
        "dwh": False,
        "dwh_driver": None,
        "dwh_info": None,
        "dwh_scheduler": None,
        "created_at": datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc),
        "created_by": None,
        "updated_at": datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc),
        "updated_by": None,
    }
    row.update(overrides)
    return row


class _MockConn:
    """Mimics the verified asyncdb pg contract used by DefinitionRepository:
    fetch_one/fetch_all/fetchval, as an async context manager (spec §6).
    """

    def __init__(self, rows=None, scalar=None):
        self.rows = rows if rows is not None else []
        self.scalar = scalar
        self.calls = []

    async def fetch_one(self, sql, *args, **kwargs):
        self.calls.append(("fetch_one", sql, args))
        return self.rows[0] if self.rows else None

    async def fetch_all(self, sql, *args, **kwargs):
        self.calls.append(("fetch_all", sql, args))
        return list(self.rows)

    async def fetchval(self, sql, *args, column=0, **kwargs):
        self.calls.append(("fetchval", sql, args))
        return self.scalar if self.scalar is not None else len(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _factory(conn: _MockConn):
    async def connection_factory():
        return conn

    return connection_factory


@pytest.mark.asyncio
async def test_repository_write_omits_empty_columns_definition() -> None:
    store = _tenant_store()

    # create(): no columns_definition supplied -> INSERT omits the column.
    row = _base_row()
    conn = _MockConn(rows=[row])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))
    await repo.create(store, _base_row())
    insert_call = next(c for c in conn.calls if "INSERT" in c[1])
    assert "columns_definition" not in insert_call[1]

    # create(): non-empty columns_definition -> INSERT includes the column.
    conn2 = _MockConn(rows=[_base_row(columns_definition=["a"])])
    repo2 = DefinitionRepository(registry=None, connection_factory=_factory(conn2))
    await repo2.create(store, _base_row(columns_definition=["a"]))
    insert_call2 = next(c for c in conn2.calls if "INSERT" in c[1])
    assert "columns_definition" in insert_call2[1]

    # upsert(): no columns_definition supplied -> INSERT omits the column.
    conn3 = _MockConn(rows=[{**_base_row(), "is_inserted": True}])
    repo3 = DefinitionRepository(registry=None, connection_factory=_factory(conn3))
    await repo3.upsert(
        QueryIdentity(store=store, slug="test_query"), _base_row()
    )
    insert_call3 = next(c for c in conn3.calls if "INSERT" in c[1])
    assert "columns_definition" not in insert_call3[1]

    # upsert(): non-empty columns_definition -> INSERT includes the column.
    conn4 = _MockConn(rows=[{**_base_row(columns_definition=["a"]), "is_inserted": True}])
    repo4 = DefinitionRepository(registry=None, connection_factory=_factory(conn4))
    await repo4.upsert(
        QueryIdentity(store=store, slug="test_query"),
        _base_row(columns_definition=["a"]),
    )
    insert_call4 = next(c for c in conn4.calls if "INSERT" in c[1])
    assert "columns_definition" in insert_call4[1]


@pytest.mark.asyncio
async def test_patch_writes_columns_definition() -> None:
    store = _tenant_store()
    conn = _MockConn(rows=[_base_row(columns_definition=["a"])])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    await repo.patch(
        QueryIdentity(store=store, slug="test_query"),
        {"columns_definition": ["a"]},
    )

    update_call = next(c for c in conn.calls if "UPDATE" in c[1])
    assert "columns_definition" in update_call[1]
