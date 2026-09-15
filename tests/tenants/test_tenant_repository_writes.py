"""Implement atomic definition mutations regression contracts."""
import datetime

import pytest
from asyncdb.drivers.pg import UndefinedTableError

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


def _legacy_store() -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="public",
        table="queries",
        contract="legacy",
        columns=frozenset({"query_slug", "description", "program_slug"}),
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
        "created_at": datetime.datetime(2026, 1, 1, 0, 0, 0),
        "created_by": None,
        "updated_at": datetime.datetime(2026, 1, 1, 0, 0, 0),
        "updated_by": None,
    }
    row.update(overrides)
    return row


class _MockConn:
    """Mimics the verified asyncdb pg contract used by DefinitionRepository:
    fetch_one/fetch_all/fetchval, as an async context manager (spec §6).
    """

    def __init__(self, rows=None, scalar=None, error_on_write=None):
        self.rows = rows if rows is not None else []
        self.scalar = scalar
        self.calls = []
        self.error_on_write = error_on_write

    def _should_error(self, sql: str) -> bool:
        """Check if the SQL is a write operation and should error."""
        if self.error_on_write is None:
            return False
        write_keywords = ("INSERT", "UPDATE", "DELETE")
        return any(keyword in sql.upper() for keyword in write_keywords)

    async def fetch_one(self, sql, *args, **kwargs):
        self.calls.append(("fetch_one", sql, args))
        if self._should_error(sql):
            if isinstance(self.error_on_write, Exception):
                raise self.error_on_write
            # If it's a string, create an exception from it
            raise RuntimeError(self.error_on_write)
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
async def test_create_and_upsert_created_flag() -> None:
    """create and upsert created flag."""
    store = _tenant_store()
    row = _base_row()

    # Test create returns persisted row
    conn = _MockConn(rows=[row])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    result = await repo.create(store, _base_row())

    assert result["query_slug"] == "test_query"
    assert "description" in result
    # Verify program_slug is not in the persisted result
    assert "program_slug" not in result

    # Verify the INSERT was executed
    insert_call = next(c for c in conn.calls if "INSERT" in c[1])
    assert insert_call is not None

    # Test upsert with created=True (new row) - return row with correct slug
    new_row = _base_row(query_slug="new_query")
    conn_created = _MockConn(rows=[{**new_row, "is_inserted": True}])
    repo_created = DefinitionRepository(registry=None, connection_factory=_factory(conn_created))

    result, is_created = await repo_created.upsert(
        QueryIdentity(store=store, slug="new_query"),
        _base_row(query_slug="new_query")
    )

    assert is_created is True
    assert result["query_slug"] == "new_query"

    # Test upsert with created=False (existing row updated) - return row with correct slug
    existing_row = _base_row(query_slug="existing_query")
    conn_updated = _MockConn(rows=[{**existing_row, "is_inserted": False}])
    repo_updated = DefinitionRepository(registry=None, connection_factory=_factory(conn_updated))

    result, is_created = await repo_updated.upsert(
        QueryIdentity(store=store, slug="existing_query"),
        _base_row(query_slug="existing_query")
    )

    assert is_created is False
    assert result["query_slug"] == "existing_query"


@pytest.mark.asyncio
async def test_concurrent_upserts_same_identity() -> None:
    """concurrent upserts same identity."""
    store = _tenant_store()

    # Simulate two concurrent upserts - both should succeed atomically
    # One will get is_inserted=True, the other is_inserted=False
    concurrent_row = _base_row(query_slug="concurrent_query")
    conn1 = _MockConn(rows=[{**concurrent_row, "is_inserted": True}])
    conn2 = _MockConn(rows=[{**concurrent_row, "is_inserted": False}])

    repo1 = DefinitionRepository(registry=None, connection_factory=_factory(conn1))
    repo2 = DefinitionRepository(registry=None, connection_factory=_factory(conn2))

    identity = QueryIdentity(store=store, slug="concurrent_query")
    data = _base_row(query_slug="concurrent_query")

    # Both upserts should complete without race condition errors
    result1, is_created1 = await repo1.upsert(identity, data)
    result2, is_created2 = await repo2.upsert(identity, data)

    # Exactly one should report created=True
    assert is_created1 != is_created2
    # Both should return valid persisted results
    assert result1["query_slug"] == "concurrent_query"
    assert result2["query_slug"] == "concurrent_query"

    # Verify both used atomic ON CONFLICT SQL
    upsert_call1 = next(c for c in conn1.calls if "ON CONFLICT" in c[1])
    upsert_call2 = next(c for c in conn2.calls if "ON CONFLICT" in c[1])
    assert upsert_call1 is not None
    assert upsert_call2 is not None


@pytest.mark.asyncio
async def test_patch_null_and_immutable_keys() -> None:
    """patch null and immutable keys."""
    store = _tenant_store()
    identity = QueryIdentity(store=store, slug="test_query")

    # Test PATCH with null value (should set to null, not omit)
    existing_row = _base_row(description="original")
    conn = _MockConn(rows=[{**existing_row, "description": None}])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    result = await repo.patch(identity, {"description": None})

    # Verify the UPDATE was executed with explicit NULL
    update_call = next(c for c in conn.calls if "UPDATE" in c[1])
    assert update_call is not None
    # The SQL should contain SET for description
    assert "description" in update_call[1]

    # Test PATCH rejects query_slug change
    with pytest.raises(TenantError) as exc_info:
        await repo.patch(identity, {"query_slug": "new_slug"})
    assert exc_info.value.error_code == "invalid_tenant"

    # Test PATCH rejects program_slug (forbidden for tenants)
    with pytest.raises(TenantError) as exc_info:
        await repo.patch(identity, {"program_slug": "some_program"})
    assert exc_info.value.error_code == "invalid_tenant"

    # Test PATCH with only immutable fields returns current row
    conn_noop = _MockConn(rows=[existing_row])
    repo_noop = DefinitionRepository(registry=None, connection_factory=_factory(conn_noop))
    result = await repo_noop.patch(identity, {"query_slug": identity.slug})  # same slug
    assert result["query_slug"] == "test_query"

    # Test PATCH on missing row raises query_not_found
    conn_missing = _MockConn(rows=[])
    repo_missing = DefinitionRepository(registry=None, connection_factory=_factory(conn_missing))
    with pytest.raises(TenantError) as exc_info:
        await repo_missing.patch(identity, {"description": "new"})
    assert exc_info.value.error_code == "query_not_found"


@pytest.mark.asyncio
async def test_delete_missing_permission_and_store_loss() -> None:
    """delete missing permission and store loss."""
    store = _tenant_store()
    identity = QueryIdentity(store=store, slug="test_query")

    # Test successful delete
    conn = _MockConn(rows=[{"query_slug": "test_query"}])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    result = await repo.delete(identity)
    assert result is True

    # Verify DELETE was executed
    delete_call = next(c for c in conn.calls if "DELETE" in c[1])
    assert delete_call is not None

    # Test delete on missing row raises query_not_found
    conn_missing = _MockConn(rows=[])
    repo_missing = DefinitionRepository(registry=None, connection_factory=_factory(conn_missing))
    with pytest.raises(TenantError) as exc_info:
        await repo_missing.delete(identity)
    assert exc_info.value.error_code == "query_not_found"

    # Test delete with permission denied raises tenant_write_forbidden
    conn_perm = _MockConn(rows=None, error_on_write=Exception("permission denied for table queries"))
    repo_perm = DefinitionRepository(registry=None, connection_factory=_factory(conn_perm))
    with pytest.raises(TenantError) as exc_info:
        await repo_perm.delete(identity)
    assert exc_info.value.error_code == "tenant_write_forbidden"

    # Test delete with missing table raises tenant_store_unavailable
    conn_table = _MockConn(rows=None, error_on_write=Exception('relation "tenant1.queries" does not exist'))
    repo_table = DefinitionRepository(registry=None, connection_factory=_factory(conn_table))
    with pytest.raises(TenantError) as exc_info:
        await repo_table.delete(identity)
    assert exc_info.value.error_code == "tenant_store_unavailable"

    # Test create with permission denied raises tenant_write_forbidden
    conn_create_perm = _MockConn(rows=None, error_on_write=Exception("permission denied for table queries"))
    repo_create_perm = DefinitionRepository(registry=None, connection_factory=_factory(conn_create_perm))
    with pytest.raises(TenantError) as exc_info:
        await repo_create_perm.create(store, _base_row())
    assert exc_info.value.error_code == "tenant_write_forbidden"

    # Test create with missing table raises tenant_store_unavailable
    conn_create_table = _MockConn(rows=None, error_on_write=Exception('relation "tenant1.queries" does not exist'))
    repo_create_table = DefinitionRepository(registry=None, connection_factory=_factory(conn_create_table))
    with pytest.raises(TenantError) as exc_info:
        await repo_create_table.create(store, _base_row())
    assert exc_info.value.error_code == "tenant_store_unavailable"

    # Test upsert with permission denied raises tenant_write_forbidden
    conn_upsert_perm = _MockConn(rows=None, error_on_write=Exception("permission denied for table queries"))
    repo_upsert_perm = DefinitionRepository(registry=None, connection_factory=_factory(conn_upsert_perm))
    with pytest.raises(TenantError) as exc_info:
        await repo_upsert_perm.upsert(identity, _base_row())
    assert exc_info.value.error_code == "tenant_write_forbidden"

    # Test patch with permission denied raises tenant_write_forbidden
    conn_patch_perm = _MockConn(rows=None, error_on_write=Exception("permission denied for table queries"))
    repo_patch_perm = DefinitionRepository(registry=None, connection_factory=_factory(conn_patch_perm))
    with pytest.raises(TenantError) as exc_info:
        await repo_patch_perm.patch(identity, {"description": "updated"})
    assert exc_info.value.error_code == "tenant_write_forbidden"

    # Test delete with the driver's typed UndefinedTableError (not just a
    # message-text match) also raises tenant_store_unavailable.
    conn_typed_table = _MockConn(
        rows=None,
        error_on_write=UndefinedTableError('relation "tenant1.queries" does not exist'),
    )
    repo_typed_table = DefinitionRepository(registry=None, connection_factory=_factory(conn_typed_table))
    with pytest.raises(TenantError) as exc_info:
        await repo_typed_table.delete(identity)
    assert exc_info.value.error_code == "tenant_store_unavailable"

    # An unrelated failure (neither permission nor missing-table) must
    # propagate unchanged, never becoming a fallback query.
    conn_unrelated = _MockConn(rows=None, error_on_write=RuntimeError("connection reset by peer"))
    repo_unrelated = DefinitionRepository(registry=None, connection_factory=_factory(conn_unrelated))
    with pytest.raises(RuntimeError, match="connection reset by peer"):
        await repo_unrelated.delete(identity)