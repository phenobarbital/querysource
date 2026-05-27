"""Unit tests for ThreadQuery executor integration (TASK-695)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from querysource.queries.multi.sources.query import ThreadQuery
from querysource.queries.multi.sources.executors import (
    LocalExecutor,
    RemoteExecutor,
    RemoteConfig,
)


class TestThreadQueryExecutorSelection:
    def test_default_uses_local_executor(self):
        """ThreadQuery without remote_config uses LocalExecutor."""
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue())
        assert isinstance(tq._executor, LocalExecutor)

    def test_remote_config_uses_remote_executor(self):
        """ThreadQuery with remote_config uses RemoteExecutor."""
        rc = RemoteConfig(host="localhost", port=8888)
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue(), remote_config=rc)
        assert isinstance(tq._executor, RemoteExecutor)

    def test_slug_property_from_dict(self):
        """slug property works from the query dict."""
        tq = ThreadQuery("test", {"slug": "my-slug"}, MagicMock(), asyncio.Queue())
        assert tq.slug == "my-slug"

    def test_slug_property_fallback(self):
        """slug property falls back to name when no slug key."""
        tq = ThreadQuery("fallback", {"query": "SELECT 1"}, MagicMock(), asyncio.Queue())
        assert tq.slug == "fallback"

    def test_backward_compatible_signature(self):
        """Existing callers (no remote_config) work unchanged."""
        # This mirrors the call site in MultiQS: ThreadQuery(name, query, request, queue)
        q = asyncio.Queue()
        request = MagicMock()
        tq = ThreadQuery("orders", {"slug": "all_orders"}, request, q)
        assert tq._executor is not None
        assert isinstance(tq._executor, LocalExecutor)

    def test_remote_executor_stores_connection_params(self):
        """RemoteExecutor in ThreadQuery has the right host/port/timeout."""
        rc = RemoteConfig(host="worker.internal", port=9000, timeout=120)
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue(), remote_config=rc)
        assert isinstance(tq._executor, RemoteExecutor)
        assert tq._executor._host == "worker.internal"
        assert tq._executor._port == 9000
        assert tq._executor._timeout == 120


class TestThreadQueryFetchDelegation:
    @pytest.mark.asyncio
    async def test_fetch_delegates_to_executor(self):
        """fetch() calls executor.execute() with correct args."""
        queue = asyncio.Queue()
        request = MagicMock()
        query = {"slug": "test-slug"}
        tq = ThreadQuery("test", query, request, queue)
        tq._executor = AsyncMock()

        result = await tq.fetch()

        assert result is None
        tq._executor.execute.assert_awaited_once_with("test", query, queue, request)

    @pytest.mark.asyncio
    async def test_fetch_returns_none(self):
        """fetch() always returns None regardless of executor result."""
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue())
        tq._executor = AsyncMock()
        tq._executor.execute.return_value = None

        result = await tq.fetch()

        assert result is None
