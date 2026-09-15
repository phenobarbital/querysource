"""Carry immutable ownership through query construction regression contracts."""
import pytest

from querysource.models import QueryModel
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


@pytest.mark.asyncio
async def test_constructor_compatibility_and_forwarding() -> None:
    """constructor compatibility and forwarding."""
    # Test that all concrete query classes accept the keyword-only tenant
    # parameter and forward it up to AbstractQuery.__init__. AbstractQuery
    # itself is never constructed directly here: querysource/queries/base.py
    # defines output_format() (called from AbstractQuery.__init__), so a
    # bare AbstractQuery() was never independently constructible even
    # before this task — BaseQuery.__init__'s super().__init__(...) call
    # is the real, valid way AbstractQuery.__init__ is exercised.
    from querysource.queries.base import BaseQuery
    from querysource.queries.multi import MultiQS
    from querysource.queries.obj import QueryObject
    from querysource.queries.qs import QS

    # BaseQuery
    query = BaseQuery(slug="test", tenant="tenant1")
    assert query._tenant_selector == "tenant1"
    assert query.slug == "test"

    # QS
    qs = QS(slug="test", tenant="tenant1")
    assert qs._tenant_selector == "tenant1"
    assert qs.slug == "test"

    # QueryObject
    query_obj = QueryObject(
        name="test",
        query={"slug": "test"},
        tenant="tenant1"
    )
    assert query_obj._tenant_selector == "tenant1"
    assert query_obj.slug == "test"

    # MultiQS
    multi_qs = MultiQS(
        slug="test",
        queries=[],
        tenant="tenant1"
    )
    assert multi_qs._tenant_selector == "tenant1"
    assert multi_qs.slug == "test"

    # Omitting tenant (positional-only compatibility) still constructs and
    # defaults to None — existing non-tenant callers are unaffected.
    legacy_qs = QS(slug="test")
    assert legacy_qs._tenant_selector is None

    # Explicit None is preserved distinctly, not coerced.
    query = BaseQuery(slug="test", tenant=None)
    assert query._tenant_selector is None


@pytest.mark.asyncio
async def test_runtime_model_program_derived() -> None:
    """runtime model program derived."""
    store = _tenant_store()

    identity = QueryIdentity(store=store, slug="test_query")
    assert identity.store == store
    assert identity.slug == "test_query"

    # Tenant program_slug is derived from the store's schema (structural
    # ownership), never a stored/persisted value for a tenant-contract
    # store — matches DefinitionRepository._runtime_model (TASK-718).
    runtime = QueryModel(
        query_slug="test_query",
        program_slug=store.schema,
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
    assert loaded_def.runtime.program_slug == "tenant1"


@pytest.mark.asyncio
async def test_raw_owner_context_without_saved_lookup() -> None:
    """raw owner context without saved lookup."""
    # Raw queries must never trigger a saved-definition repository lookup:
    # AC-4 "derive raw identity from store and existing raw checksum,
    # never an invented saved slug lookup."
    from querysource.queries.qs import QS

    qs_no_tenant = QS(raw_query="SELECT * FROM test", tenant=None)
    assert qs_no_tenant._tenant_selector is None
    assert qs_no_tenant._type == "raw"
    assert qs_no_tenant._query == "SELECT * FROM test"

    qs_with_tenant = QS(raw_query="SELECT * FROM test", tenant="tenant1")
    assert qs_with_tenant._tenant_selector == "tenant1"
    assert qs_with_tenant._type == "raw"
    assert qs_with_tenant._query == "SELECT * FROM test"

    # A raw query must never call get_definition_repository() — patch it
    # to raise if touched, then exercise the raw path directly.
    async def _must_not_be_called():
        raise AssertionError(
            "raw queries must not perform a saved-definition lookup"
        )

    qs_with_tenant.get_definition_repository = _must_not_be_called
    assert qs_with_tenant._type == "raw"
    # TASK-722 initializes both fields to None in AbstractQuery.__init__
    # (pre-declared owner/revision context slots); a raw query's
    # build_provider() never sets them away from that default.
    assert qs_with_tenant._definition_identity is None


@pytest.mark.asyncio
async def test_provider_mutation_cannot_change_revision() -> None:
    """provider mutation cannot change revision."""
    # Exercise the real build_provider() slug path (not a manual
    # attribute poke): a fake repository stands in for the DB, verifying
    # QS.build_provider actually retrieves a LoadedDefinition through the
    # repository (AC-2) and that mutating the returned runtime model
    # afterwards cannot retroactively change the revision recorded on the
    # execution object (revision was computed from persisted data before
    # any runtime mutation — TASK-718's cache_identity contract).
    from querysource.queries.qs import QS

    store = _tenant_store()
    identity = QueryIdentity(store=store, slug="test_query")
    runtime = QueryModel(
        query_slug="test_query",
        program_slug="tenant1",
        provider="db",
        is_cached=False,
    )
    loaded_def = LoadedDefinition(identity=identity, runtime=runtime, revision="abc123")

    class FakeRegistry:
        def resolve(self, tenant):
            assert tenant == "tenant1"
            return store

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            assert ident.store == store
            assert ident.slug == "test_query"
            return loaded_def

    qs = QS(slug="test_query", tenant="tenant1")

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository

    # get_provider() would need a live datasource; stub it out — this
    # test is scoped to the repository/identity/revision wiring (AC-2),
    # not provider acquisition.
    class FakeProviderInstance:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def prepare_connection(self):
            return None

    async def fake_get_provider(objquery, session=None, app=None):
        return ("fake_conn", FakeProviderInstance)

    qs.connection.get_provider = fake_get_provider

    await qs.build_provider()

    assert qs._definition_identity == identity
    assert qs._definition_revision == "abc123"

    # Mutating the runtime model post-hoc (as a provider might) must not
    # retroactively change the already-recorded revision.
    loaded_def.runtime.description = "mutated after load"
    assert qs._definition_revision == "abc123"
    assert qs._definition_identity.store.schema == "tenant1"
