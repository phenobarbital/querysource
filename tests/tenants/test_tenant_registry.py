"""Implement immutable store identities and catalog discovery regression contracts."""

import pytest
from querysource.tenants import (
    DefinitionPage,
    LoadedDefinition,
    QueryIdentity,
    QueryStore,
    TenantOwnerEnvelope,
    TenantRegistry,
    quote_identifier,
)


@pytest.mark.asyncio
async def test_allowlist_none_empty_exact_and_duplicates() -> None:
    """allowlist none empty exact and duplicates."""
    # Create a mock connection
    class MockCursor:
        async def execute(self, query: str) -> None:
            pass

        async def fetchall(self) -> list:
            return [
                ("tenant1", "queries", "BASE TABLE"),
                ("tenant2", "queries", "BASE TABLE"),
                ("public", "queries", "BASE TABLE"),
            ]

    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}
            self._cursor = MockCursor()

        async def cursor(self):
            return self._cursor

    registry = TenantRegistry()

    # Test with allowlist=None (discover all)
    conn = MockConn()
    await registry.discover(conn, allowlist=None)

    # Should have discovered stores
    stores = registry.stores()
    assert len(stores) > 0, "Should have discovered stores with allowlist=None"

    # Test with allowlist=[] (discover none)
    registry2 = TenantRegistry()
    await registry2.discover(conn, allowlist=[])

    stores2 = registry2.stores()
    assert len(stores2) == 0, "Should have no stores with allowlist=[]"

    # Test with allowlist containing exact names
    registry3 = TenantRegistry()
    await registry3.discover(conn, allowlist=["tenant1", "tenant2"])

    stores3 = registry3.stores()
    assert len(stores3) == 2, "Should have exactly 2 stores in allowlist"
    assert all(store.schema in ("tenant1", "tenant2") for store in stores3)

    # Test with duplicate names in allowlist (should be deduplicated)
    registry4 = TenantRegistry()
    await registry4.discover(conn, allowlist=["tenant1", "tenant1", "tenant2"])

    stores4 = registry4.stores()
    assert len(stores4) == 2, "Duplicates should be deduplicated"


@pytest.mark.asyncio
async def test_discovery_tables_views_marker_shape_grants() -> None:
    """discovery tables views marker shape grants."""
    class MockCursor:
        async def execute(self, query: str) -> None:
            pass

        async def fetchall(self) -> list:
            return [
                ("tenant1", "queries", "BASE TABLE"),
                ("tenant2", "queries", "BASE TABLE"),
                ("public", "queries", "BASE TABLE"),
            ]

    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}
            self._cursor = MockCursor()

        async def cursor(self):
            return self._cursor

    registry = TenantRegistry()
    conn = MockConn()
    await registry.discover(conn, allowlist=None)

    # Should have discovered stores
    stores = registry.stores()
    assert len(stores) > 0, "Should have discovered stores"

    # Test resolve with valid tenant
    store = registry.resolve("tenant1")
    assert isinstance(store, QueryStore)
    assert store.schema == "tenant1"
    assert store.table == "queries"
    assert store.contract in ("legacy", "tenant")

    # Test resolve with public
    public_store = registry.resolve("public")
    assert isinstance(public_store, QueryStore)
    assert public_store.schema == "public"
    assert public_store.table == "queries"

    # Test resolve with None (should return default)
    default_store = registry.resolve(None)
    assert isinstance(default_store, QueryStore)
    assert default_store.table == "queries"

    # Test resolve with unknown tenant
    with pytest.raises(Exception) as exc_info:
        registry.resolve("unknown")
    assert "Tenant not found" in str(exc_info.value)

    # Test diagnostics
    diagnostics = registry.diagnostics()
    assert len(diagnostics) > 0, "Should have diagnostics"


@pytest.mark.asyncio
async def test_quoted_names_and_default_alias_dedup() -> None:
    """quoted names and default alias dedup."""
    class MockCursor:
        async def execute(self, query: str) -> None:
            pass

        async def fetchall(self) -> list:
            return [
                ("tenant1", "queries", "BASE TABLE"),
                ("tenant1", "queries", "BASE TABLE"),  # Duplicate
                ("public", "queries", "BASE TABLE"),
            ]

    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}
            self._cursor = MockCursor()

        async def cursor(self):
            return self._cursor

    registry = TenantRegistry()
    conn = MockConn()
    await registry.discover(conn, allowlist=None)

    # Test deduplication
    stores = registry.stores()
    assert len(stores) == 2, "Duplicates should be deduplicated"

    # Test quote_identifier
    quoted = quote_identifier("tenant1")
    assert quoted == '"tenant1"'

    quoted_with_quotes = quote_identifier('tenant"1"')
    assert quoted_with_quotes == '"tenant""1""'

    # Test invalid identifier
    with pytest.raises(ValueError):
        quote_identifier("")

    with pytest.raises(ValueError):
        quote_identifier("\0")

    # Test identity creation
    store = stores[0]
    identity = QueryIdentity(store=store, slug="test_query")
    assert identity.store == store
    assert identity.slug == "test_query"

    # Test loaded definition
    from querysource.models import QueryModel

    runtime = QueryModel(
        query_slug="test_query",
        description="Test query",
    )
    loaded = LoadedDefinition(
        identity=identity,
        runtime=runtime,
        revision="1",
    )
    assert loaded.identity == identity
    assert loaded.runtime == runtime
    assert loaded.revision == "1"

    # Test definition page
    page = DefinitionPage(
        rows=({"query_slug": "test_query"},),
        total=1,
    )
    assert len(page.rows) == 1
    assert page.total == 1


@pytest.mark.asyncio
async def test_scan_failure_has_no_partial_snapshot() -> None:
    """scan failure has no partial snapshot."""
    class MockCursor:
        async def execute(self, query: str) -> None:
            pass

        async def fetchall(self) -> list:
            # Simulate a scan failure by raising an exception
            raise Exception("Connection lost")

    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}
            self._cursor = MockCursor()

        async def cursor(self):
            return self._cursor

    registry = TenantRegistry()
    conn = MockConn()

    # Discover should not raise, but should leave registry empty
    await registry.discover(conn, allowlist=None)

    # Registry should be empty after failed scan
    stores = registry.stores()
    assert len(stores) == 0, "Failed scan should leave registry empty"

    # Should not have any diagnostics
    diagnostics = registry.diagnostics()
    assert len(diagnostics) == 0, "Failed scan should have no diagnostics"

    # Resolve should return default (legacy) store
    default_store = registry.resolve(None)
    assert isinstance(default_store, QueryStore)
    assert default_store.table == "queries"
