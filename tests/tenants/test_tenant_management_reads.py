"""Add selector parsing and tenant-aware management reads regression contracts."""

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from querysource.handlers.tenant import resolve_request_store
from querysource.tenants import QueryStore, TenantRegistry


def _mock_registry() -> TenantRegistry:
    """Create a mock registry with a tenant store plus a store literally
    named "null" (schema="null") — a legal, quoted schema name, distinct
    from JSON/Python None, used to verify the literal-string-"null" case.
    """
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )
    null_store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="null",
        table="queries",
        contract="legacy",
        columns=frozenset({"query_slug", "description", "program_slug"}),
    )
    registry._stores = (store, null_store)
    registry._default_store = store
    return registry


def _mock_request(
    method: str = "GET",
    path: str = "/api/v1/management/queries",
    query: dict | None = None,
    match_info: dict | None = None,
    json_data: dict | None = None,
) -> web.Request:
    """Create a mock aiohttp request for testing."""
    request = MagicMock(spec=web.Request)
    request.method = method
    # resolve_request_store reads request.query (the real aiohttp
    # Request property backing the query string), not request.rel_url.
    request.query = query or {}
    request.rel_url = MagicMock()
    request.rel_url.query = query or {}
    request.match_info = match_info or {}
    request.json = MagicMock(return_value=json_data) if json_data else MagicMock()
    return request


@pytest.mark.asyncio
async def test_selector_missing_null_duplicates_conflicts() -> None:
    """Test that missing, null, and duplicate selectors are handled correctly."""
    registry = _mock_registry()

    # Test 1: No selector → configured default store
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "tenant1"
    assert store.contract == "tenant"

    # Test 2: Explicit tenant selector → exact store
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "tenant1"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "tenant1"
    assert store.contract == "tenant"

    # Test 3: Literal URL 'null' → treated as schema name
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "null"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    # 'null' is treated as a schema name, not JSON null
    assert store.schema == "null"
    assert store.contract == "legacy"

    # Test 4: Empty string selector → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": ""},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid query selector" in str(exc_info.value.reason)

    # Test 5: Non-string selector → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": 123},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid query selector" in str(exc_info.value.reason)

    # Test 6: Duplicate selectors → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "tenant1", "other": "value"},
        match_info={"tenant": "tenant2"},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Multiple conflicting tenant selectors" in str(exc_info.value.reason)

    # Test 7: Unknown tenant → 400 error
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "unknown"},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry)
    assert "Invalid tenant selector" in str(exc_info.value.reason)


class _FakeCursor:
    """Mimics the pg connection contract used by QueryManager._paginate_list:
    fetchval/fetch_all, as an async context manager acquired from a pool.
    """

    def __init__(self, total: int, rows: list):
        self._total = total
        self._rows = rows

    async def fetchval(self, sql):
        return self._total

    async def fetch_all(self, sql):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeDb:
    def __init__(self, total: int = 0, rows: list | None = None):
        self._total = total
        self._rows = rows or []

    async def acquire(self):
        return _FakeCursor(self._total, self._rows)


class _FakeRepo:
    """Stands in for the app-published DefinitionRepository."""

    def __init__(self, store: QueryStore):
        self._store = store

    def schema(self, store: QueryStore) -> dict:
        # Tenant-contract stores never expose program_slug (AC-2/TASK-718:
        # "excluding tenant runtime-only program_slug").
        properties = {"query_slug": {"type": "string"}, "description": {"type": "string"}}
        if store.contract == "legacy":
            properties["program_slug"] = {"type": "string"}
        return {"properties": properties}


@pytest.mark.asyncio
async def test_metadata_export_tenant_shape() -> None:
    """Test that metadata export preserves tenant shape."""
    from querysource.handlers.manager import QueryManager

    registry = _mock_registry()
    store = registry.resolve(None)
    app = {}
    app['qs_tenant_registry'] = registry
    app['qs_connection'] = _FakeDb()
    app['qs_definition_repository'] = _FakeRepo(store)

    # Create a mock request for :meta endpoint
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries/:meta",
        query={},
        match_info={"meta": ":meta"},
    )
    request.app = app

    manager = QueryManager(request)
    response = await manager.get()
    assert response.status == 200
    # self.json_response() returns a plain aiohttp/navigator Response with
    # a pre-serialized body — .json() is a ClientResponse method and does
    # not exist here; decode the raw text instead.
    data = json.loads(response.text)
    properties = data["properties"]
    assert "query_slug" in properties
    assert "description" in properties
    # AC-2: the resolved store here is tenant-contract — program_slug is
    # a runtime-only field, never part of the persisted/exported schema.
    assert "program_slug" not in properties


@pytest.mark.asyncio
async def test_tenant_pagination_headers_and_empty() -> None:
    """Test that tenant pagination headers and empty results are preserved."""
    from querysource.handlers.manager import QueryManager

    registry = _mock_registry()
    store = registry.resolve(None)
    app = {}
    app['qs_tenant_registry'] = registry
    app['qs_connection'] = _FakeDb(total=0, rows=[])
    app['qs_definition_repository'] = _FakeRepo(store)

    # Create a mock request for paginated list with no rows — 204 path.
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"page": "1", "page_size": "10"},
        match_info={},
    )
    request.app = app

    manager = QueryManager(request)
    response = await manager.get()
    assert response.status == 204
    assert response.headers["X-Total-Count"] == "0"
    assert response.headers["X-Page"] == "1"
    assert response.headers["X-Page-Size"] == "10"
    assert response.headers["X-Total-Pages"] == "0"


@pytest.mark.asyncio
async def test_legacy_overrides_explicit_public() -> None:
    """Test that explicit public remains literal and allowlisted even with legacy overrides."""
    registry = _mock_registry()

    # Test 1: Explicit public selector → literal public.queries
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    store = resolve_request_store(request, registry)
    assert store.schema == "public"
    assert store.contract == "legacy"

    # Test 2: Public in allowlist → resolved store
    registry_with_public = _mock_registry()
    public_store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="public",
        table="queries",
        contract="legacy",
        columns=frozenset({"query_slug", "description", "program_slug"}),
    )
    registry_with_public._stores = (public_store,)
    registry_with_public._default_store = public_store

    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    store = resolve_request_store(request, registry_with_public)
    assert store.schema == "public"
    assert store.contract == "legacy"

    # Test 3: "public" with no public store registered — spec §2 "explicit
    # public is literal and allowlisted even without explicit
    # configuration": TenantRegistry.resolve("public") never raises, it
    # always falls back to the legacy public.queries store (verified
    # against TenantRegistry.resolve, TASK-716). Unlike any other unknown
    # tenant name, "public" is never a 400.
    registry_unknown = _mock_registry()
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "public"},
        match_info={},
    )
    store = resolve_request_store(request, registry_unknown)
    assert store.schema == "public"
    assert store.contract == "legacy"

    # A genuinely unknown, non-"public" tenant name IS rejected.
    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "no_such_tenant"},
        match_info={},
    )
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        resolve_request_store(request, registry_unknown)
    assert "Invalid tenant selector" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_tenant_store_search_never_sql_references_program_slug() -> None:
    """A tenant-store ?search= request must never SQL-reference program_slug.

    Code review finding 11: tenant-contract stores never persist
    program_slug (see docs/PER_TENANT_QUERIES.md "Persistence versus
    runtime shape"), but build_where_clause's search OR-group used to
    unconditionally include program_slug for every caller — a tenant-store
    ?search= request generated SQL referencing a column that table does
    not have, a real runtime SQL error (not a 400 validation rejection;
    _paginate_list's existing program_slug guard only checked
    sort_field/fields/equality-filter keys, never params.search).
    """
    from querysource.handlers.manager import QueryManager

    class _CapturingCursor(_FakeCursor):
        def __init__(self, total: int, rows: list):
            super().__init__(total, rows)
            self.sql_statements: list[str] = []

        async def fetchval(self, sql):
            self.sql_statements.append(sql)
            return await super().fetchval(sql)

        async def fetch_all(self, sql):
            self.sql_statements.append(sql)
            return await super().fetch_all(sql)

    captured = _CapturingCursor(total=0, rows=[])

    class _CapturingDb:
        async def acquire(self):
            return captured

    registry = _mock_registry()
    store = registry.resolve("tenant1")
    assert store.contract == "tenant"
    app = {}
    app['qs_tenant_registry'] = registry
    app['qs_connection'] = _CapturingDb()
    app['qs_definition_repository'] = _FakeRepo(store)

    request = _mock_request(
        method="GET",
        path="/api/v1/management/queries",
        query={"tenant": "tenant1", "search": "report"},
        match_info={},
    )
    request.app = app

    manager = QueryManager(request)
    response = await manager.get()
    # No SQL error (500) — request completes normally (204: no matching rows
    # in the fake cursor).
    assert response.status == 204
    assert captured.sql_statements, "expected at least one SQL statement to run"
    for sql in captured.sql_statements:
        assert "program_slug" not in sql
