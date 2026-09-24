"""Implement repository reads, listing, metadata and revision identity regression contracts."""
import datetime

import pytest

from querysource.cache_identity import definition_revision, result_cache_key
from querysource.repositories.definitions import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


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
async def test_get_returns_detached_runtime_and_revision() -> None:
    """get returns detached runtime and revision."""
    store = _tenant_store()
    row = _base_row()
    conn = _MockConn(rows=[row])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))
    identity = QueryIdentity(store=store, slug="test_query")

    loaded = await repo.get(identity)

    assert isinstance(loaded, LoadedDefinition)
    assert loaded.identity == identity
    # Tenant program_slug is derived from the store's schema, never stored.
    assert loaded.runtime.program_slug == store.schema
    assert loaded.runtime.query_slug == "test_query"
    assert not hasattr(loaded, "program_slug")
    assert loaded.revision == definition_revision(dict(row.items()))

    # Missing row raises TenantError(query_not_found), not a silent None.
    empty_conn = _MockConn(rows=[])
    empty_repo = DefinitionRepository(registry=None, connection_factory=_factory(empty_conn))
    with pytest.raises(TenantError) as exc_info:
        await empty_repo.get(QueryIdentity(store=store, slug="missing"))
    assert exc_info.value.error_code == "query_not_found"

    # AC-2: a legacy-contract store keeps its stored program_slug verbatim
    # instead of deriving it from the schema.
    legacy_store = _legacy_store()
    legacy_row = _base_row(program_slug="legacy_program")
    legacy_conn = _MockConn(rows=[legacy_row])
    legacy_repo = DefinitionRepository(registry=None, connection_factory=_factory(legacy_conn))
    legacy_loaded = await legacy_repo.get(QueryIdentity(store=legacy_store, slug="test_query"))
    assert legacy_loaded.runtime.program_slug == "legacy_program"
    assert legacy_loaded.runtime.program_slug != legacy_store.schema


@pytest.mark.asyncio
async def test_list_count_filters_and_tenant_columns() -> None:
    """list count filters and tenant columns."""
    store = _tenant_store()
    rows = [_base_row(query_slug="a"), _base_row(query_slug="b")]
    conn = _MockConn(rows=rows, scalar=2)
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    page = await repo.list(store, {"page": 1, "page_size": 10})
    assert page.total == 2
    assert len(page.rows) == 2
    # Persisted rows never carry program_slug for a tenant store.
    assert all("program_slug" not in r for r in page.rows)

    # AC-4: program_slug is rejected as a filter field.
    with pytest.raises(TenantError):
        await repo.list(store, {"filters": {"program_slug": "tenant1"}})

    # AC-4: program_slug is rejected as a sort field.
    with pytest.raises(TenantError):
        await repo.list(store, {"sort_field": "program_slug"})

    # AC-4: program_slug is rejected as a projection field.
    with pytest.raises(TenantError):
        await repo.list(store, {"fields": ["query_slug", "program_slug"]})

    # Unknown filter/sort fields are rejected outright.
    with pytest.raises(TenantError):
        await repo.list(store, {"filters": {"not_a_real_column": "x"}})


@pytest.mark.asyncio
async def test_metadata_export_only_persisted_fields() -> None:
    """metadata export only persisted fields."""
    store = _tenant_store()
    row = _base_row()
    conn = _MockConn(rows=[row])
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    schema = repo.schema(store)
    assert "program_slug" not in schema.get("properties", {})
    assert "query_slug" in schema.get("properties", {})

    identity = QueryIdentity(store=store, slug="test_query")
    sql = await repo.export_insert(identity)
    assert sql.startswith("INSERT INTO ")
    assert '"tenant1"."queries"' in sql
    assert "program_slug" not in sql
    assert "query_slug" in sql

    # export_insert never writes — it only reads (fetch_one) via the
    # connection factory, no execute/mutation call is ever made.
    assert all(call[0] == "fetch_one" for call in conn.calls)

    # Missing row raises rather than rendering a bogus INSERT.
    empty_conn = _MockConn(rows=[])
    empty_repo = DefinitionRepository(registry=None, connection_factory=_factory(empty_conn))
    with pytest.raises(TenantError):
        await empty_repo.export_insert(QueryIdentity(store=store, slug="missing"))


@pytest.mark.asyncio
async def test_revision_determinism_and_physical_identity() -> None:
    """revision determinism and physical identity."""
    row_a = _base_row()
    row_b = _base_row()  # identical content, different dict object
    row_c = _base_row(description="different")

    # Same canonical content -> same revision; different content -> different.
    assert definition_revision(row_a) == definition_revision(row_b)
    assert definition_revision(row_a) != definition_revision(row_c)

    # Key ordering must not affect the hash (ordered-keys canonicalization).
    reordered = dict(reversed(list(row_a.items())))
    assert definition_revision(row_a) == definition_revision(reordered)

    store = _tenant_store()
    identity_a = QueryIdentity(store=store, slug="test_query")
    revision = definition_revision(row_a)

    key_1 = result_cache_key(identity_a, revision, "checksum-1")
    key_2 = result_cache_key(identity_a, revision, "checksum-1")
    assert key_1 == key_2
    assert key_1.startswith("qs:r2:")

    # Same physical-store alias (identical database_namespace/schema/table)
    # shares identity even as a distinct QueryStore instance.
    alias_store = _tenant_store()
    identity_alias = QueryIdentity(store=alias_store, slug="test_query")
    key_alias = result_cache_key(identity_alias, revision, "checksum-1")
    assert key_alias == key_1

    # Different owners (schema) with identical slug/revision/checksum differ.
    other_store = _tenant_store(schema="tenant2")
    identity_other = QueryIdentity(store=other_store, slug="test_query")
    key_other = result_cache_key(identity_other, revision, "checksum-1")
    assert key_other != key_1

    # A different provider checksum also changes the key.
    key_checksum_2 = result_cache_key(identity_a, revision, "checksum-2")
    assert key_checksum_2 != key_1


@pytest.mark.asyncio
async def test_schedulable_preserves_existing_eligibility_predicate() -> None:
    """schedulable candidates use the existing attributes/cache_options predicate."""
    store = _tenant_store()
    rows = [
        {
            "query_slug": "scheduled_one",
            "attributes": {"scheduler": {"schedule_type": "daily"}},
            "cache_options": {},
            "provider": "db",
            "is_cached": True,
            "query_raw": None,
        }
    ]
    conn = _MockConn(rows=rows)
    repo = DefinitionRepository(registry=None, connection_factory=_factory(conn))

    candidates = await repo.schedulable(store)

    assert len(candidates) == 1
    assert candidates[0]["query_slug"] == "scheduled_one"
    fetch_all_call = next(call for call in conn.calls if call[0] == "fetch_all")
    sql = fetch_all_call[1]
    assert "attributes IS NOT NULL" in sql
    assert "cache_options IS NOT NULL" in sql
    assert '"tenant1"."queries"' in sql
