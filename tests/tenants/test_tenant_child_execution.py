"""Resolve nested owners and propagate them across local threads regression contracts."""
import asyncio
import pytest
import pandas as pd
from unittest import mock
from aiohttp import web

from querysource.exceptions import DriverError
from querysource.models import QueryModel
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore
from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources.query import ThreadQuery
from querysource.queries.multi.sources.executors import RemoteConfig, RemoteExecutor, LocalExecutor


def _tenant_store(schema: str = "tenant1", contract: str = "tenant") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract=contract,
        columns=frozenset({"query_slug", "description"}),
    )


@pytest.mark.asyncio
async def test_parent_explicit_null_and_named_child_owner() -> None:
    """parent explicit null and named child owner."""
    # Setup fake repository and registry
    store_tenant1 = _tenant_store("tenant1")
    store_tenant2 = _tenant_store("tenant2")
    store_legacy = _tenant_store("public", "legacy")

    # We have a parent MultiQS with tenant=None (explicit null)
    # It has two child queries:
    # 1. child1: slug="q1", tenant="tenant1" (explicit tenant)
    # 2. child2: slug="q2" (inherits parent, which is None -> legacy/public)
    
    ident_q1 = QueryIdentity(store=store_tenant1, slug="q1")
    ident_q2 = QueryIdentity(store=store_legacy, slug="q2")

    loaded_q1 = LoadedDefinition(
        identity=ident_q1,
        runtime=QueryModel(query_slug="q1", program_slug="tenant1", provider="db"),
        revision="rev1"
    )
    loaded_q2 = LoadedDefinition(
        identity=ident_q2,
        runtime=QueryModel(query_slug="q2", program_slug="public", provider="db"),
        revision="rev2"
    )

    class FakeRegistry:
        def resolve(self, tenant):
            if tenant == "tenant1":
                return store_tenant1
            if tenant == "tenant2":
                return store_tenant2
            return store_legacy

    class FakeRepo:
        registry = FakeRegistry()
        async def get(self, ident):
            if ident.slug == "q1" and ident.store == store_tenant1:
                return loaded_q1
            if ident.slug == "q2" and ident.store == store_legacy:
                return loaded_q2
            raise Exception(f"Not found: {ident}")

    multi_qs = MultiQS(
        queries={
            "alias1": {"slug": "q1", "tenant": "tenant1"},
            "alias2": {"slug": "q2"}
        },
        tenant=None
    )

    async def fake_get_definition_repository():
        return FakeRepo()

    multi_qs.get_definition_repository = fake_get_definition_repository

    # Stub out the actual thread execution to just verify the ThreadQuery objects created
    created_threads = []
    
    class FakeThreadQuery(ThreadQuery):
        def __init__(self, name, query, request, queue, remote_config=None, store=None):
            super().__init__(name, query, request, queue, remote_config=remote_config, store=store)
            created_threads.append(self)

    with mock.patch("querysource.queries.multi.ThreadQuery", FakeThreadQuery):
        # We expect query() to fail or succeed depending on execution, but we want to check preflight
        # Let's mock the execution loop or just let it run with mocked ThreadQuery.start/join
        with mock.patch.object(ThreadQuery, "start", lambda self: None), \
             mock.patch.object(ThreadQuery, "join", lambda self, timeout=None: None), \
             mock.patch.object(ThreadQuery, "is_alive", lambda self: False), \
             mock.patch.object(ThreadQuery, "exc", create=True, new_callable=mock.PropertyMock(return_value=None)):
            # Mock the queue to return a dummy dataframe so query() doesn't raise DataNotFound
            async def fake_get(*args, **kwargs):
                return {"alias1": pd.DataFrame([{"col": 1}])}
            
            with mock.patch.object(asyncio.Queue, "empty", side_effect=[False, True]), \
                 mock.patch.object(asyncio.Queue, "get", fake_get):
                await multi_qs.query()

    assert len(created_threads) == 2
    # Verify child1 got store_tenant1
    t1 = next(t for t in created_threads if t._name == "alias1")
    assert t1._store == store_tenant1
    # Verify child2 got store_legacy (inherited parent None)
    t2 = next(t for t in created_threads if t._name == "alias2")
    assert t2._store == store_legacy


@pytest.mark.asyncio
async def test_input_config_is_not_mutated() -> None:
    """input config is not mutated."""
    store_legacy = _tenant_store("public", "legacy")
    ident_q1 = QueryIdentity(store=store_legacy, slug="q1")
    loaded_q1 = LoadedDefinition(
        identity=ident_q1,
        runtime=QueryModel(query_slug="q1", program_slug="public", provider="db"),
        revision="rev1"
    )

    class FakeRegistry:
        def resolve(self, tenant):
            return store_legacy

    class FakeRepo:
        registry = FakeRegistry()
        async def get(self, ident):
            return loaded_q1

    input_queries = {
        "alias1": {"slug": "q1", "tenant": "tenant1"}
    }
    
    multi_qs = MultiQS(
        queries=input_queries,
        tenant=None
    )
    
    async def fake_get_definition_repository():
        return FakeRepo()
    multi_qs.get_definition_repository = fake_get_definition_repository

    with mock.patch.object(ThreadQuery, "start", lambda self: None), \
         mock.patch.object(ThreadQuery, "join", lambda self, timeout=None: None), \
         mock.patch.object(ThreadQuery, "is_alive", lambda self: False), \
         mock.patch.object(ThreadQuery, "exc", create=True, new_callable=mock.PropertyMock(return_value=None)):
        async def fake_get(*args, **kwargs):
            return {"alias1": pd.DataFrame([{"col": 1}])}
        
        with mock.patch.object(asyncio.Queue, "empty", side_effect=[False, True]), \
             mock.patch.object(asyncio.Queue, "get", fake_get):
            await multi_qs.query()

    # Verify input_queries dict was not mutated (e.g. "tenant" key is still there)
    assert "tenant" in input_queries["alias1"]
    assert input_queries["alias1"]["tenant"] == "tenant1"


@pytest.mark.asyncio
async def test_alias_vs_slug_preflight_before_side_effect() -> None:
    """alias vs slug preflight before side effect."""
    # Preflight policy checks on resolved references before a known batch starts.
    # If a child query is missing a slug, or if a child query's preflight check fails,
    # it must raise before starting any threads.
    store_legacy = _tenant_store("public", "legacy")

    class FakeRegistry:
        def resolve(self, tenant):
            return store_legacy

    class FakeRepo:
        registry = FakeRegistry()
        async def get(self, ident):
            raise Exception("Definition not found in store")

    # Case 1: Missing slug
    multi_qs_missing_slug = MultiQS(
        queries={
            "alias1": {"tenant": "tenant1"}  # missing slug
        },
        tenant=None
    )
    
    async def fake_get_definition_repository():
        return FakeRepo()
    multi_qs_missing_slug.get_definition_repository = fake_get_definition_repository

    with pytest.raises(DriverError, match="missing a 'slug' key"):
        await multi_qs_missing_slug.query()

    # Case 2: Preflight check fails (definition not found)
    multi_qs_failed_preflight = MultiQS(
        queries={
            "alias1": {"slug": "nonexistent"}
        },
        tenant=None
    )
    multi_qs_failed_preflight.get_definition_repository = fake_get_definition_repository

    with pytest.raises(Exception, match="Preflight policy check failed"):
        await multi_qs_failed_preflight.query()


@pytest.mark.asyncio
async def test_thread_loop_owner_and_single_queue_put() -> None:
    """thread loop owner and single queue put."""
    # Verify LocalExecutor forwards resolved store into loop-local QueryObject
    # and RemoteExecutor accepts the keyword now and refuses nonlegacy store.
    store_tenant = _tenant_store("tenant1", "tenant")
    store_legacy = _tenant_store("public", "legacy")

    # LocalExecutor test
    local_exec = LocalExecutor()
    
    class FakeQueryObject:
        def __init__(self, name, query, queue, request, loop, tenant=None):
            self.name = name
            self.query_dict = query
            self.queue = queue
            self.request = request
            self.loop = loop
            self.tenant = tenant

        async def build_provider(self):
            pass

        async def query(self):
            # Put exactly one result in queue
            await self.queue.put({self.name: "result_data"})

    queue = asyncio.Queue()
    
    with mock.patch("querysource.queries.multi.sources.executors.QueryObject", FakeQueryObject):
        await local_exec.execute(
            name="alias1",
            query={"slug": "q1"},
            queue=queue,
            request=None,
            store=store_tenant
        )

    assert queue.qsize() == 1
    res = await queue.get()
    assert res == {"alias1": "result_data"}

    # RemoteExecutor test: accepts store keyword, dispatches by contract.
    # (TASK-728 replaced the interim "reject non-legacy store" guard with
    # real versioned dispatch for tenant-contract stores — see
    # tests/tenants/test_tenant_remote_protocol.py for the full contract.)
    remote_exec = RemoteExecutor(host="localhost", port=9000)

    # We mock QClient to avoid actual network calls
    class FakeQClient:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, *args, **kwargs):
            return "remote_data"
        def close(self):
            pass

    with mock.patch("qw.client.QClient", FakeQClient):
        await remote_exec.execute(
            name="alias1",
            query={"slug": "q1"},
            queue=queue,
            request=None,
            store=store_legacy
        )
    assert queue.qsize() == 1
    res = await queue.get()
    assert res == {"alias1": "remote_data"}
