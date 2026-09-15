"""Wire startup, direct usage and compatible slug lookup regression contracts."""
import asyncio
import importlib

import pytest
from aiohttp import web

from querysource.interfaces.connections import Connection
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryStore


def _fresh_query_source_class():
    """Reload querysource.services so QuerySource's Singleton cache starts
    empty for this test. Same established pattern as
    tests/test_querysource_setup_airtable.py's ``querysource_fresh`` fixture
    — QuerySource() itself never receives a live/queryable database in
    these tests, only the parts under test (qs_start's publishing/ordering,
    __init__'s reinit-compatibility check) are exercised.
    """
    from querysource import services
    importlib.reload(services)
    return services.QuerySource


def _fresh_query_connection_class():
    """Reload querysource.connections for the same Singleton-isolation
    reason as _fresh_query_source_class.
    """
    from querysource import connections
    importlib.reload(connections)
    return connections.QueryConnection


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


@pytest.mark.asyncio
async def test_startup_order_before_scheduler() -> None:
    """startup order before scheduler."""
    QuerySourceCls = _fresh_query_source_class()
    qs = QuerySourceCls(lazy=True)

    # AC-2: QuerySource.qs_start (registry/repository init) must be
    # registered after the metadata connection's own startup hook and
    # before the scheduler's — verified against the real, unmodified
    # QuerySource.setup() wiring, not a mock of the ordering itself.
    app = web.Application()
    qs.setup(app)
    hook_names = [
        getattr(h, "__qualname__", str(h)) for h in app.on_startup
    ]
    assert "QueryConnection.start" in hook_names
    assert "QuerySource.qs_start" in hook_names
    conn_idx = hook_names.index("QueryConnection.start")
    qs_idx = hook_names.index("QuerySource.qs_start")
    assert conn_idx < qs_idx, "metadata connection must start before qs_start"
    if "QSScheduler.startup" in hook_names:
        sched_idx = hook_names.index("QSScheduler.startup")
        assert qs_idx < sched_idx, "qs_start must run before scheduler startup"

    # AC-2: qs_start publishes both app services and captures the loop;
    # a failed discovery propagates (fails readiness) rather than being
    # swallowed. initialize_tenants is faked here — its own DB-touching
    # body is TASK-716's discover(), already covered by
    # tests/tenants/test_tenant_registry.py.
    sentinel_registry = object()

    async def fake_initialize_tenants():
        qs._definition_repository = "FAKE_REPO"
        return sentinel_registry

    qs.initialize_tenants = fake_initialize_tenants
    published_app: dict = {}
    await qs.qs_start(published_app)
    assert published_app["qs_tenant_registry"] is sentinel_registry
    assert published_app["qs_definition_repository"] == "FAKE_REPO"
    assert qs._loop is asyncio.get_running_loop()

    async def failing_initialize_tenants():
        raise RuntimeError("discovery failed")

    qs.initialize_tenants = failing_initialize_tenants
    with pytest.raises(RuntimeError, match="discovery failed"):
        await qs.qs_start({})


def test_singleton_allowlist_conflict() -> None:
    """singleton allowlist conflict."""
    QuerySourceCls = _fresh_query_source_class()

    # The compatibility-check logic in __init__ is exercised directly
    # (rather than through a second QuerySource(...) call) because the
    # installed datamodel.typedefs.Singleton metaclass — verified
    # behaviorally, not assumed — never invokes __init__ a second time
    # for an already-cached instance (it short-circuits inside its own
    # __call__ before reaching __new__/__init__ at all). In a real
    # process this means a frozen tenant_allowlist can never be silently
    # broadened by a second QuerySource(tenant_allowlist=...) call — it
    # is simply never processed, not silently applied — but it also means
    # no exception is raised on that second call in production. This test
    # verifies the raising logic itself is correct in isolation.
    stub = QuerySourceCls.__new__(QuerySourceCls)
    stub.__initialized__ = True
    stub._tenant_allowlist = ["tenant1", "tenant2"]

    # A differing, explicit tenant_allowlist is rejected.
    with pytest.raises(ValueError, match="Incompatible QuerySource reinitialization"):
        QuerySourceCls.__init__(stub, tenant_allowlist=["tenant3"])

    # An identical, explicit tenant_allowlist is compatible (early-return,
    # no re-initialization of instance state).
    QuerySourceCls.__init__(stub, tenant_allowlist=["tenant1", "tenant2"])

    # Omitting tenant_allowlist entirely (the _UNSET sentinel — e.g. every
    # bare QuerySource() call from Connection.get_definition_repository())
    # is always compatible, never treated as a conflicting reinit attempt.
    QuerySourceCls.__init__(stub)


@pytest.mark.asyncio
async def test_lazy_lookup_without_request() -> None:
    """lazy lookup without request."""
    store = _tenant_store()

    class FakeRegistry:
        def resolve(self, tenant):
            if tenant == "unknown":
                raise TenantError("Tenant not found", error_code="tenant_not_available")
            return store

    class FakeRepo:
        def __init__(self):
            self.registry = FakeRegistry()
            self.calls = 0

        async def get(self, ident):
            self.calls += 1
            if ident.slug == "missing":
                raise TenantError(f"Query not found: {ident.slug!r}", error_code="query_not_found")
            runtime_sentinel = object()
            return LoadedDefinition(identity=ident, runtime=runtime_sentinel, revision="rev1")

    conn = Connection()
    fake_repo = FakeRepo()

    async def fake_get_definition_repository():
        return fake_repo

    conn.get_definition_repository = fake_get_definition_repository

    # Programmatic/lazy lookup, no aiohttp request involved at all.
    loaded_runtime = await conn.get_query_slug("test_query", tenant="tenant1")
    assert loaded_runtime is not None
    assert fake_repo.calls == 1

    # Missing slug maps to SlugNotFound (established compatibility error),
    # not a raw TenantError leaking out of this compatibility layer.
    from querysource.exceptions import SlugNotFound

    with pytest.raises(SlugNotFound):
        await conn.get_query_slug("missing")

    # Unknown tenant maps to SlugNotFound too (no fallback to another
    # owner's data — see AC-4/M4 "reject" language reused here for M2).
    with pytest.raises(SlugNotFound):
        await conn.get_query_slug("test_query", tenant="unknown")

    # get_slug: program is preserved for signature compatibility but never
    # selects a schema (verified: the pre-existing implementation already
    # ignored it entirely).
    result = await conn.get_slug("test_query", program="some_program", tenant="tenant1")
    assert result is not None


@pytest.mark.asyncio
async def test_connection_retry_and_cross_loop_cleanup() -> None:
    """connection retry and cross loop cleanup."""
    store = _tenant_store()

    # -- retry preserved: get_query_slug retries on DriverError, then
    # succeeds, matching the pre-existing retry/backoff contract (AC-4).
    from asyncdb.exceptions import DriverError

    class FlakyRepo:
        def __init__(self):
            self.registry = type("R", (), {"resolve": staticmethod(lambda tenant: store)})()
            self.attempts = 0

        async def get(self, ident):
            self.attempts += 1
            if self.attempts < 2:
                raise DriverError("transient connection error")
            return LoadedDefinition(identity=ident, runtime=object(), revision="rev1")

    conn = Connection()
    flaky_repo = FlakyRepo()

    async def flaky_get_definition_repository():
        return flaky_repo

    conn.get_definition_repository = flaky_get_definition_repository
    result = await conn.get_query_slug("test_query", max_retries=3)
    assert result is not None
    assert flaky_repo.attempts == 2

    # Exhausting retries raises QueryException, not a bare DriverError.
    from querysource.exceptions import QueryException

    class AlwaysFailingRepo:
        registry = type("R", (), {"resolve": staticmethod(lambda tenant: store)})()

        async def get(self, ident):
            raise DriverError("permanently down")

    conn2 = Connection()

    async def always_failing_get_definition_repository():
        return AlwaysFailingRepo()

    conn2.get_definition_repository = always_failing_get_definition_repository
    with pytest.raises(QueryException):
        await conn2.get_query_slug("test_query", max_retries=2)

    # -- cross-loop cleanup: definition_connection() must never hand back
    # the HTTP-loop pool's acquire() when called from a different loop; it
    # must build a fresh, standalone connection instead (AC-5).
    QueryConnectionCls = _fresh_query_connection_class()
    qconn = QueryConnectionCls(lazy=True)

    class FakePool:
        def __init__(self):
            self.acquire_calls = 0

        async def acquire(self):
            self.acquire_calls += 1
            return "POOLED_CONNECTION"

    class FakeDirectDb:
        def __init__(self):
            self.connection_calls = 0

        async def connection(self):
            self.connection_calls += 1
            return "DIRECT_CONNECTION"

    fake_pool = FakePool()
    qconn._postgres = fake_pool
    qconn._loop = asyncio.get_running_loop()  # pretend this IS the HTTP loop

    # Same loop as the HTTP loop + an active pool -> reuse the pool.
    result = await qconn.definition_connection()
    assert result == "POOLED_CONNECTION"
    assert fake_pool.acquire_calls == 1

    # A different "HTTP" loop identity -> never reuse the pool; build a
    # fresh direct connection instead, bound to the current loop.
    qconn._loop = object()  # sentinel: definitely not the current running loop
    fake_direct_db = FakeDirectDb()
    qconn.get_connection = lambda driver='pg', evt=None: fake_direct_db
    result = await qconn.definition_connection()
    assert result == "DIRECT_CONNECTION"
    assert fake_pool.acquire_calls == 1, "must not have touched the HTTP-loop pool"
    assert fake_direct_db.connection_calls == 1

    # pgargs must never be mutated by definition_connection() itself.
    assert qconn.pgargs["server_settings"]["application_name"] in ("QS.Master", "QS.Lazy")
