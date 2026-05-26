"""Unit tests for LocalExecutor and RemoteConfig (TASK-693)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from querysource.queries.multi.sources.executors import (
    LocalExecutor,
    QueryExecutor,
    RemoteConfig,
)


class TestRemoteConfig:
    def test_frozen_dataclass(self):
        rc = RemoteConfig(host="localhost", port=8888)
        assert rc.host == "localhost"
        assert rc.port == 8888
        assert rc.timeout == 60

    def test_custom_timeout(self):
        rc = RemoteConfig(host="worker.internal", port=9000, timeout=120)
        assert rc.timeout == 120

    def test_immutable(self):
        rc = RemoteConfig(host="localhost", port=8888)
        with pytest.raises((AttributeError, TypeError)):
            rc.host = "other"


class TestQueryExecutorInterface:
    def test_is_abstract(self):
        """QueryExecutor cannot be instantiated directly."""
        with pytest.raises(TypeError):
            QueryExecutor()  # type: ignore[abstract]

    def test_local_executor_is_subclass(self):
        assert issubclass(LocalExecutor, QueryExecutor)


class TestLocalExecutor:
    @pytest.mark.asyncio
    async def test_delegates_to_query_object(self):
        """LocalExecutor creates QueryObject and calls build_provider + query."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = LocalExecutor()

        with patch(
            "querysource.queries.multi.sources.executors.QueryObject"
        ) as MockQO:
            mock_qo = AsyncMock()
            MockQO.return_value = mock_qo
            result = await executor.execute("test", {"slug": "test-slug"}, queue, request)

        assert result is None
        MockQO.assert_called_once()
        mock_qo.build_provider.assert_awaited_once()
        mock_qo.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none(self):
        """LocalExecutor returns None (queue written by QueryObject)."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = LocalExecutor()

        with patch(
            "querysource.queries.multi.sources.executors.QueryObject"
        ) as MockQO:
            MockQO.return_value = AsyncMock()
            result = await executor.execute("test", {"slug": "s"}, queue, request)

        assert result is None

    @pytest.mark.asyncio
    async def test_passes_name_queue_request_to_query_object(self):
        """LocalExecutor passes name, queue, request, and loop to QueryObject."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = LocalExecutor()
        query = {"slug": "some-slug"}

        with patch(
            "querysource.queries.multi.sources.executors.QueryObject"
        ) as MockQO:
            MockQO.return_value = AsyncMock()
            await executor.execute("my_name", query, queue, request)

        call_kwargs = MockQO.call_args
        # First positional arg is name
        assert call_kwargs[0][0] == "my_name"
        # queue kwarg
        assert call_kwargs[1]["queue"] == queue
        # request kwarg
        assert call_kwargs[1]["request"] == request
