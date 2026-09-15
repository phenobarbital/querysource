"""Dispatch tenant work through a versioned worker contract regression contracts."""
import asyncio
from typing import ClassVar
from unittest import mock

import pytest

import querysource.queries.multi.sources.executors as executors_module
from querysource.exceptions import QueryException
from querysource.queries.multi.sources.executors import RemoteExecutor
from querysource.tenants import QueryStore


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
        columns=frozenset({"query_slug"}),
    )


class FakeQClient:
    """Stand-in for qw.client.QClient: records every dispatch."""

    calls: ClassVar[list] = []
    run_result = "unset"
    run_exception: BaseException | None = None
    run_sleep: float = 0.0

    def __init__(self, worker_list=None, timeout=None, **kwargs):
        self.worker_list = worker_list
        self.timeout = timeout
        self.closed = False

    async def run(self, handler, slug, **kwargs):
        type(self).calls.append((handler, slug, kwargs))
        if type(self).run_sleep:
            await asyncio.sleep(type(self).run_sleep)
        if type(self).run_exception is not None:
            raise type(self).run_exception
        return type(self).run_result

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeQClient.calls = []
    FakeQClient.run_result = "unset"
    FakeQClient.run_exception = None
    FakeQClient.run_sleep = 0.0
    yield
    FakeQClient.calls = []
    FakeQClient.run_result = "unset"
    FakeQClient.run_exception = None
    FakeQClient.run_sleep = 0.0


@pytest.mark.asyncio
async def test_exact_callable_and_owner_envelope() -> None:
    """A tenant-owned store dispatches through tenant_query_handler_v1 with
    a validated TenantOwnerEnvelope, and the result lands under the alias
    (``name``), not the stored slug."""
    store = _tenant_store("tenant1")
    FakeQClient.run_result = "tenant-df"

    queue: asyncio.Queue = asyncio.Queue()
    executor = RemoteExecutor(host="localhost", port=8888)

    with mock.patch("qw.client.QClient", FakeQClient):
        await executor.execute(
            name="revenue_alias",
            query={"slug": "monthly_revenue"},
            queue=queue,
            request=None,
            store=store,
        )

    assert len(FakeQClient.calls) == 1
    handler, slug, kwargs = FakeQClient.calls[0]
    assert handler == "querysource.remote.tenant_query_handler_v1"
    assert slug == "monthly_revenue"
    assert kwargs["owner"] == {
        "version": 1,
        "database_namespace": "localhost:5432/querysource",
        "schema": "tenant1",
        "table": "queries",
        "contract": "tenant",
    }

    # AC-1: alias queue output preserved — keyed by `name`, not the slug.
    assert queue.qsize() == 1
    result = await queue.get()
    assert result == {"revenue_alias": "tenant-df"}


@pytest.mark.asyncio
async def test_saved_raw_conditions_strip_routing() -> None:
    """Routing keys (slug/remote/worker) never reach the worker as
    conditions, for both a saved (slug-based) and a raw child query."""
    queue: asyncio.Queue = asyncio.Queue()
    FakeQClient.run_result = "df"

    # Saved (slug-based) query through the legacy handler: routing keys
    # stripped, data keys ("store_id") preserved.
    with mock.patch("qw.client.QClient", FakeQClient):
        await RemoteExecutor(host="h", port=1).execute(
            name="a",
            query={
                "slug": "q1",
                "remote": True,
                "worker": "host:1234",
                "store_id": 42,
            },
            queue=queue,
            request=None,
            store=_legacy_store(),
        )
    handler, slug, kwargs = FakeQClient.calls[-1]
    assert handler == "querysource.remote.query_handler"
    assert slug == "q1"
    assert kwargs["conditions"] == {"store_id": 42}

    # Raw child query through the tenant handler: query/driver preserved,
    # remote/worker stripped.
    with mock.patch("qw.client.QClient", FakeQClient):
        await RemoteExecutor(host="h", port=1).execute(
            name="b",
            query={
                "query": "SELECT 1",
                "driver": "pg",
                "remote": True,
                "worker": "host:1234",
                "store_id": 7,
            },
            queue=queue,
            request=None,
            store=_tenant_store(),
        )
    handler, slug, kwargs = FakeQClient.calls[-1]
    assert handler == "querysource.remote.tenant_query_handler_v1"
    assert slug is None
    assert kwargs["conditions"] == {"query": "SELECT 1", "driver": "pg", "store_id": 7}
    assert "remote" not in kwargs["conditions"]
    assert "worker" not in kwargs["conditions"]


@pytest.mark.asyncio
async def test_old_worker_explicit_error_no_fallback() -> None:
    """An old worker that hasn't registered tenant_query_handler_v1 fails
    explicitly — the error propagates as-is, with no retry against the
    legacy handler and no local execution fallback."""
    store = _tenant_store()
    FakeQClient.run_exception = RuntimeError(
        "unknown handler: querysource.remote.tenant_query_handler_v1"
    )

    queue: asyncio.Queue = asyncio.Queue()
    executor = RemoteExecutor(host="localhost", port=8888)

    with mock.patch("qw.client.QClient", FakeQClient), pytest.raises(
        RuntimeError, match="unknown handler"
    ):
        await executor.execute(
            name="a",
            query={"slug": "s"},
            queue=queue,
            request=None,
            store=store,
        )

    # Exactly one dispatch attempt — no fallback retry against the legacy
    # handler, and nothing was ever placed on the queue.
    assert len(FakeQClient.calls) == 1
    assert FakeQClient.calls[0][0] == "querysource.remote.tenant_query_handler_v1"
    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_timeout_cleanup_and_queue_alias() -> None:
    """A timeout raises QueryException and still closes the worker client
    (no leaked connection); a healthy call keeps the alias/slug distinct."""
    store = _tenant_store()
    queue: asyncio.Queue = asyncio.Queue()
    executor = RemoteExecutor(host="localhost", port=8888)

    created_clients: list[FakeQClient] = []

    class _TrackedFakeQClient(FakeQClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_clients.append(self)

    FakeQClient.run_sleep = 0.05

    with mock.patch("qw.client.QClient", _TrackedFakeQClient), mock.patch.object(
        executors_module, "QWORKER_QUERY_TIMEOUT", 0.01
    ), pytest.raises(QueryException, match="timed out"):
        await executor.execute(
            name="alias1",
            query={"slug": "monthly_revenue"},
            queue=queue,
            request=None,
            store=store,
        )

    assert len(created_clients) == 1
    assert created_clients[0].closed is True
    assert queue.qsize() == 0

    # Healthy call: alias ("out_alias") stays distinct from the stored slug.
    FakeQClient.run_sleep = 0.0
    FakeQClient.run_exception = None
    FakeQClient.run_result = "healthy-df"
    with mock.patch("qw.client.QClient", FakeQClient):
        await executor.execute(
            name="out_alias",
            query={"slug": "monthly_revenue"},
            queue=queue,
            request=None,
            store=store,
        )
    result = await queue.get()
    assert result == {"out_alias": "healthy-df"}
    assert "monthly_revenue" not in result
