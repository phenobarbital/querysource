"""Implement immutable store identities and catalog discovery regression contracts."""

import pytest

from querysource.tenant_errors import TenantError
from querysource.tenants import (
    DefinitionPage,
    LoadedDefinition,
    QueryIdentity,
    QueryStore,
    TenantRegistry,
    quote_identifier,
)


@pytest.mark.asyncio
async def test_allowlist_none_empty_exact_and_duplicates() -> None:
    """allowlist none empty exact and duplicates."""
    # Create a mock connection matching the asyncdb `pg` driver's
    # `query(sentence) -> (result, error)` contract used elsewhere in the
    # codebase (see querysource/datasources/introspection.py `_run`).
    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}

        async def query(self, sentence: str):
            if "information_schema.columns" in sentence:
                return (
                    [
                        ("tenant1", "queries", "query_slug", "text", "NO"),
                        ("tenant2", "queries", "query_slug", "text", "NO"),
                        ("public", "queries", "query_slug", "text", "NO"),
                    ],
                    None,
                )
            return (
                [
                    ("tenant1", "queries", "BASE TABLE"),
                    ("tenant2", "queries", "BASE TABLE"),
                    ("public", "queries", "BASE TABLE"),
                ],
                None,
            )

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
    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}

        async def query(self, sentence: str):
            if "information_schema.columns" in sentence:
                return (
                    [
                        ("tenant1", "queries", "query_slug", "text", "NO"),
                        ("tenant2", "queries", "query_slug", "text", "NO"),
                        ("public", "queries", "query_slug", "text", "NO"),
                    ],
                    None,
                )
            return (
                [
                    ("tenant1", "queries", "BASE TABLE"),
                    ("tenant2", "queries", "BASE TABLE"),
                    ("public", "queries", "BASE TABLE"),
                    # Reserved schema — must be excluded and recorded as a diagnostic.
                    ("management", "queries", "BASE TABLE"),
                ],
                None,
            )

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
    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}

        async def query(self, sentence: str):
            if "information_schema.columns" in sentence:
                return (
                    [
                        ("tenant1", "queries", "query_slug", "text", "NO"),
                        ("tenant1", "queries", "query_slug", "text", "NO"),  # Duplicate
                        ("public", "queries", "query_slug", "text", "NO"),
                    ],
                    None,
                )
            return (
                [
                    ("tenant1", "queries", "BASE TABLE"),
                    ("tenant1", "queries", "BASE TABLE"),  # Duplicate
                    ("public", "queries", "BASE TABLE"),
                ],
                None,
            )

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
    assert quoted_with_quotes == '"tenant""1"""'

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
    """scan failure has no partial snapshot.

    Spec §2 "Registry lifecycle and discovery" (verbatim): "A failed
    catalog scan fails startup, with no partial registry published."
    discover() must therefore RAISE (never silently swallow) on a real
    scan failure — a caller (QuerySource.initialize_tenants -> qs_start)
    that never sees the exception would otherwise publish an empty-but-
    valid registry as if discovery had genuinely found zero stores,
    which is a different, misleading state from "the scan itself broke."
    """
    class MockConn:
        def __init__(self):
            self.config = {"host": "localhost", "port": 5432, "database": "querysource"}

        async def query(self, sentence: str):
            # Simulate a scan failure by raising an exception
            raise ConnectionError("Connection lost")

    registry = TenantRegistry()
    conn = MockConn()

    # Discover must raise (fail startup) — never silently continue.
    with pytest.raises(TenantError) as exc_info:
        await registry.discover(conn, allowlist=None)
    assert exc_info.value.error_code == "tenant_store_unavailable"

    # No partial snapshot was ever published onto this registry instance.
    stores = registry.stores()
    assert len(stores) == 0, "Failed scan must leave registry empty"
    diagnostics = registry.diagnostics()
    assert len(diagnostics) == 0, "Failed scan must have no diagnostics"


@pytest.mark.asyncio
async def test_no_public_schema_never_picks_arbitrary_tenant_as_default() -> None:
    """No public schema discovered -> no arbitrary tenant becomes the default.

    "No owner fallback" is a repeated, explicit constraint throughout this
    feature's spec/tasks. If discovery finds only tenant-contract schemas
    (no schema literally named "public"), resolve(None) must fall through
    to the registry's own well-defined hardcoded legacy public.queries
    fallback — never silently promote whichever tenant happened to sort
    first in the catalog query's result order to "the default store" (a
    real cross-tenant exposure: any legacy/null-tenant request would then
    execute against that arbitrary tenant's data).
    """
    class MockConn:
        def __init__(self):
            self.config = {"host": "prod-db.internal", "port": 5432, "database": "querysource"}

        async def query(self, sentence: str):
            if "information_schema.columns" in sentence:
                return (
                    [
                        ("tenant_z", "queries", "query_slug", "text", "NO"),
                        ("tenant_a", "queries", "query_slug", "text", "NO"),
                    ],
                    None,
                )
            return (
                [
                    ("tenant_z", "queries", "BASE TABLE"),
                    ("tenant_a", "queries", "BASE TABLE"),
                ],
                None,
            )

    registry = TenantRegistry()
    conn = MockConn()
    await registry.discover(conn, allowlist=None)

    # Both tenant stores were discovered...
    stores = registry.stores()
    assert {s.schema for s in stores} == {"tenant_z", "tenant_a"}

    # ...but neither is silently promoted to "the default".
    default_store = registry.resolve(None)
    assert default_store.schema == "public"
    assert default_store.contract == "legacy"
    # And the fallback's namespace matches the REAL connection this
    # registry actually discovered against — not a divergent hardcoded
    # literal — so its cache-key/job-id digest is consistent with every
    # other store on this same physical database.
    assert default_store.database_namespace == "prod-db.internal:5432/querysource"
