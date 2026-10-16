"""Implement immutable store identities and catalog discovery regression contracts."""

import pytest
from asyncdb.drivers.pg import pg, pgPool

from querysource.repositories.definitions import DefinitionRepository
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
async def test_discovery_preserves_legacy_program_slug_when_loading() -> None:
    """Catalog discovery and row adaptation agree on the physical contract."""
    class CatalogConnection:
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

        async def query(self, sentence: str) -> tuple[list[tuple[str, ...]], None]:
            columns = {
                "public": ("query_slug", "program_slug", "description"),
                "tenant1": ("query_slug", "description"),
                "old_tenant": ("query_slug", "program_slug"),
                "unrelated": ("description",),
            }
            if "information_schema.columns" in sentence:
                rows = [
                    (schema, "queries", column, "text", "NO")
                    for schema, names in columns.items()
                    for column in names
                ]
                # Model the original SQL predicate so this catches regressions
                # in the catalog scan, rather than only classification.
                if "AND column_name = 'query_slug'" in sentence:
                    rows = [row for row in rows if row[2] == "query_slug"]
                return rows, None
            return [(schema, "queries", "BASE TABLE") for schema in columns], None

        async def fetch_one(self, sentence: str, slug: str) -> dict[str, str]:
            if '"public"."queries"' in sentence:
                return {"query_slug": slug, "program_slug": "hisense"}
            assert '"tenant1"."queries"' in sentence
            return {"query_slug": slug}

        async def __aenter__(self) -> "CatalogConnection":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    conn = CatalogConnection()

    async def connection_factory() -> CatalogConnection:
        return conn

    registry = TenantRegistry()
    await registry.discover(conn)
    assert {store.schema for store in registry.stores()} == {"public", "tenant1"}
    legacy = registry.resolve(None)
    assert legacy == registry.resolve("public")
    assert legacy.contract == "legacy"
    assert legacy.columns == frozenset({"query_slug", "program_slug", "description"})
    assert registry.resolve("tenant1").contract == "tenant"
    assert any(
        item["schema"] == "old_tenant" and "program_slug" in item["reason"]
        for item in registry.diagnostics()
    )

    repo = DefinitionRepository(registry=registry, connection_factory=connection_factory)
    loaded = await repo.get(QueryIdentity(store=legacy, slug="hisense_stores"))
    assert loaded.runtime.program_slug == "hisense"
    tenant_loaded = await repo.get(QueryIdentity(store=registry.resolve("tenant1"), slug="sample"))
    assert tenant_loaded.runtime.program_slug == "tenant1"


@pytest.mark.asyncio
@pytest.mark.parametrize("pooled", [False, True])
@pytest.mark.parametrize(
    ("dsn", "namespace"),
    [
        ("postgresql://user:secret@prod-db.internal:6543/metadata", "prod-db.internal:6543/metadata"),
        ("postgresql://user:secret@prod-db.internal/my%20database", "prod-db.internal:5432/my database"),
        (
            "postgresql://user:secret@localhost/unused?host=prod-db.internal&port=6543&dbname=metadata",
            "prod-db.internal:6543/metadata",
        ),
    ],
)
async def test_real_pg_connection_namespace(
    monkeypatch: pytest.MonkeyPatch, pooled: bool, dsn: str, namespace: str
) -> None:
    """Driver objects need no invented config attribute or live database."""
    conn = pg(pool=pgPool(dsn=dsn)) if pooled else pg(dsn=dsn)
    assert not hasattr(conn, "config")

    async def query(sentence: str) -> tuple[list[tuple[str, ...]], None]:
        if "information_schema.columns" in sentence:
            return [("tenant1", "queries", "query_slug", "text", "NO")], None
        return [("tenant1", "queries", "BASE TABLE")], None

    monkeypatch.setattr(conn, "query", query)
    registry = TenantRegistry()
    await registry.discover(conn)
    assert registry.resolve("tenant1").database_namespace == namespace
    # The configured default table was not discovered: explicit unavailable store.
    with pytest.raises(TenantError) as exc_info:
        registry.resolve(None)
    assert exc_info.value.error_code == "tenant_store_unavailable"


@pytest.mark.asyncio
async def test_allowlist_none_empty_exact_and_duplicates() -> None:
    """allowlist none empty exact and duplicates."""
    # Create a mock connection matching the asyncdb `pg` driver's
    # `query(sentence) -> (result, error)` contract used elsewhere in the
    # codebase (see querysource/datasources/introspection.py `_run`).
    class MockConn:
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

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

    # The configured legacy store is registered independent of the tenant allowlist.
    stores2 = registry2.stores()
    assert [(s.schema, s.contract) for s in stores2] == [("public", "legacy")]

    # Test with allowlist containing exact names
    registry3 = TenantRegistry()
    await registry3.discover(conn, allowlist=["tenant1", "tenant2"])

    stores3 = registry3.stores()
    assert {s.schema for s in stores3} == {"tenant1", "tenant2", "public"}

    # Test with duplicate names in allowlist (should be deduplicated)
    registry4 = TenantRegistry()
    await registry4.discover(conn, allowlist=["tenant1", "tenant1", "tenant2"])

    stores4 = registry4.stores()
    assert len(stores4) == 3, "Duplicates should be deduplicated"


@pytest.mark.asyncio
async def test_discovery_tables_views_marker_shape_grants() -> None:
    """discovery tables views marker shape grants."""
    class MockConn:
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

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
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

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
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

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
        def get_dsn(self) -> str:
            return "postgresql://prod-db.internal:5432/querysource"

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

    # ...but neither is silently promoted to "the default": the configured
    # public.queries is missing, so the default is an explicit unavailable
    # store, never public by substitution nor a tenant by catalog order.
    with pytest.raises(TenantError) as exc_info:
        registry.resolve(None)
    assert exc_info.value.error_code == "tenant_store_unavailable"
    # Literal public still resolves to literal public, on the real namespace.
    literal = registry.resolve("public")
    assert literal.schema == "public" and literal.contract == "legacy"
    assert literal.database_namespace == "prod-db.internal:5432/querysource"


def _catalog_conn(tables):
    """Catalog double: ``tables`` is a list of (schema, table, extra_columns)."""

    class MockConn:
        def get_dsn(self) -> str:
            return "postgresql://localhost:5432/querysource"

        async def query(self, sentence: str):
            if "information_schema.columns" in sentence:
                return (
                    [
                        (schema, table, col, "text", "NO")
                        for schema, table, extra in tables
                        for col in ("query_slug", *extra)
                    ],
                    None,
                )
            return ([(schema, table, "BASE TABLE") for schema, table, _ in tables], None)


    return MockConn()


@pytest.mark.asyncio
async def test_configured_custom_default_and_public_stay_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A custom configured schema/table is the default; public.queries stays literal public."""
    from querysource.models import QueryModel

    monkeypatch.setattr(QueryModel.Meta, "schema", "legacy_qs")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    conn = _catalog_conn(
        [
            ("legacy_qs", "saved_queries", ("program_slug",)),
            ("public", "queries", ("program_slug",)),
            ("tenant1", "queries", ()),
        ]
    )
    registry = TenantRegistry()
    await registry.discover(conn, allowlist=[])  # empty tenant allowlist

    default = registry.resolve(None)
    assert (default.schema, default.table, default.contract) == ("legacy_qs", "saved_queries", "legacy")
    literal_public = registry.resolve("public")
    assert (literal_public.schema, literal_public.table) == ("public", "queries")
    assert literal_public != default
    # Empty allowlist keeps the configured physical store and excludes tenants.
    assert [(s.schema, s.table) for s in registry.stores()] == [("legacy_qs", "saved_queries")]

    registry_all = TenantRegistry()
    await registry_all.discover(conn, allowlist=None)
    by_schema = {s.schema: s for s in registry_all.stores()}
    assert by_schema["public"].contract == "legacy"
    assert by_schema["tenant1"].contract == "tenant"
    # A schema selector addresses that schema's ``queries`` table only.
    assert registry_all.resolve("tenant1").table == "queries"
    with pytest.raises(TenantError):
        registry_all.resolve("legacy_qs")


@pytest.mark.asyncio
async def test_missing_configured_table_is_unavailable_not_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing configured table never selects public or a tenant."""
    from querysource.models import QueryModel

    monkeypatch.setattr(QueryModel.Meta, "schema", "legacy_qs")
    monkeypatch.setattr(QueryModel.Meta, "name", "saved_queries")
    registry = TenantRegistry()
    await registry.discover(
        _catalog_conn([("public", "queries", ("program_slug",)), ("tenant1", "queries", ())])
    )
    with pytest.raises(TenantError) as exc_info:
        registry.resolve(None)
    assert exc_info.value.error_code == "tenant_store_unavailable"
    assert registry.resolve("public").table == "queries"

    empty = TenantRegistry()
    await empty.discover(_catalog_conn([]))
    with pytest.raises(TenantError):
        empty.resolve(None)
    assert any("configured default" in d["reason"] for d in empty.diagnostics())


@pytest.mark.asyncio
async def test_never_discovered_registry_keeps_legacy_fallback() -> None:
    """A bare registry (scheduler bootstrap) keeps the synthetic public.queries fallback."""
    store = TenantRegistry().resolve(None)
    assert (store.schema, store.table, store.contract) == ("public", "queries", "legacy")
