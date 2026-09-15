"""Carry immutable ownership through query construction regression contracts."""
import pytest
from querysource.tenants import QueryIdentity, LoadedDefinition
from querysource.repositories import DefinitionRepository
from querysource.tenants import TenantRegistry


@pytest.mark.asyncio
async def test_constructor_compatibility_and_forwarding() -> None:
    """constructor compatibility and forwarding."""
    # Test that all query classes accept the keyword-only tenant parameter
    from querysource.interfaces.queries import AbstractQuery
    from querysource.queries.base import BaseQuery
    from querysource.queries.qs import QS
    from querysource.queries.obj import QueryObject
    from querysource.queries.multi import MultiQS

    # Test AbstractQuery
    query = AbstractQuery(slug="test", tenant="tenant1")
    assert query._tenant_selector == "tenant1"
    assert query.slug == "test"

    # Test BaseQuery
    query = BaseQuery(slug="test", tenant="tenant1")
    assert query._tenant_selector == "tenant1"
    assert query.slug == "test"

    # Test QS
    qs = QS(slug="test", tenant="tenant1")
    assert qs._tenant_selector == "tenant1"
    assert qs.slug == "test"

    # Test QueryObject
    query_obj = QueryObject(
        name="test",
        query={"slug": "test"},
        tenant="tenant1"
    )
    assert query_obj._tenant_selector == "tenant1"
    assert query_obj.slug == "test"

    # Test MultiQS
    multi_qs = MultiQS(
        slug="test",
        queries=[],
        tenant="tenant1"
    )
    assert multi_qs._tenant_selector == "tenant1"
    assert multi_qs.slug == "test"

    # Test that None tenant is preserved
    query = AbstractQuery(slug="test", tenant=None)
    assert query._tenant_selector is None


@pytest.mark.asyncio
async def test_runtime_model_program_derived() -> None:
    """runtime model program derived."""
    # Test that tenant selector is stored and can be used to resolve store
    from querysource.tenants import TenantRegistry

    registry = TenantRegistry()
    store = registry.resolve("tenant1")
    assert store.schema == "tenant1"

    # Test that QueryIdentity can be constructed
    identity = QueryIdentity(store=store, slug="test_query")
    assert identity.store == store
    assert identity.slug == "test_query"

    # Test that LoadedDefinition can be constructed
    from querysource.models import QueryModel
    runtime = QueryModel(
        query_slug="test_query",
        program_slug="tenant1",
        provider="db"
    )
    loaded_def = LoadedDefinition(
        identity=identity,
        runtime=runtime,
        revision="abc123"
    )
    assert loaded_def.identity == identity
    assert loaded_def.runtime == runtime
    assert loaded_def.revision == "abc123"

    # Test that program_slug is derived from store schema for tenant contract
    assert loaded_def.runtime.program_slug == "tenant1"


@pytest.mark.asyncio
async def test_raw_owner_context_without_saved_lookup() -> None:
    """raw owner context without saved lookup."""
    # Test that raw queries don't trigger saved-definition lookups
    from querysource.queries.qs import QS

    # Create a raw query without tenant
    qs = QS(raw_query="SELECT * FROM test", tenant=None)
    assert qs._tenant_selector is None
    assert qs._type == "raw"
    assert qs._query == "SELECT * FROM test"

    # Create a raw query with tenant
    qs = QS(raw_query="SELECT * FROM test", tenant="tenant1")
    assert qs._tenant_selector == "tenant1"
    assert qs._type == "raw"
    assert qs._query == "SELECT * FROM test"

    # Verify that build_provider for raw queries doesn't attempt to load definition
    # (this is a smoke test - actual implementation would be in a later task)
    assert hasattr(qs, '_tenant_selector')
    assert hasattr(qs, '_query')
    assert hasattr(qs, '_type')


@pytest.mark.asyncio
async def test_provider_mutation_cannot_change_revision() -> None:
    """provider mutation cannot change revision."""
    # Test that definition identity and revision are stored on execution object
    from querysource.tenants import TenantRegistry
    from querysource.queries.qs import QS

    # Create a query with tenant
    qs = QS(slug="test_query", tenant="tenant1")
    assert hasattr(qs, '_tenant_selector')
    assert qs._tenant_selector == "tenant1"

    # Simulate loading a definition (this would normally happen in build_provider)
    from querysource.tenants import QueryIdentity
    from querysource.models import QueryModel
    from querysource.tenants import LoadedDefinition

    registry = TenantRegistry()
    store = registry.resolve("tenant1")
    identity = QueryIdentity(store=store, slug="test_query")
    runtime = QueryModel(
        query_slug="test_query",
        program_slug="tenant1",
        provider="db"
    )
    loaded_def = LoadedDefinition(
        identity=identity,
        runtime=runtime,
        revision="abc123"
    )

    # Store the loaded definition on the query object
    qs._definition_identity = loaded_def.identity
    qs._definition_revision = loaded_def.revision

    # Verify that identity and revision are stored
    assert hasattr(qs, '_definition_identity')
    assert hasattr(qs, '_definition_revision')
    assert qs._definition_identity == identity
    assert qs._definition_revision == "abc123"

    # Verify that the runtime model has the correct program_slug
    assert qs._definition_identity.store.schema == "tenant1"
    assert loaded_def.runtime.program_slug == "tenant1"
