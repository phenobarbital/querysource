"""Unit tests for RemoteExecutor (TASK-694)."""
import asyncio
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch

from querysource.queries.multi.sources.executors import RemoteExecutor
from querysource.exceptions import QueryException


class TestRemoteExecutor:
    @pytest.mark.asyncio
    async def test_calls_qclient_run(self):
        """RemoteExecutor dispatches to QClient.run() with slug and conditions."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = RemoteExecutor(host="localhost", port=8888, timeout=30)
        expected_df = pd.DataFrame({"id": [1], "val": [10]})

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = expected_df
            MockClient.return_value = mock_instance

            await executor.execute(
                "revenue", {"slug": "monthly-revenue", "store_id": 42}, queue, request
            )

        result = await queue.get()
        assert "revenue" in result
        assert result["revenue"].equals(expected_df)

    @pytest.mark.asyncio
    async def test_wraps_connection_error(self):
        """ConnectionError from QClient is wrapped in QueryException."""
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="bad-host", port=9999)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.side_effect = ConnectionError("refused")
            MockClient.return_value = mock_instance

            with pytest.raises(QueryException, match="bad-host:9999"):
                await executor.execute("q", {"slug": "s"}, queue, MagicMock())

    @pytest.mark.asyncio
    async def test_wraps_timeout_error(self):
        """TimeoutError from QClient is wrapped in QueryException."""
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="host", port=8888)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.side_effect = TimeoutError("timed out")
            MockClient.return_value = mock_instance

            with pytest.raises(QueryException, match="host:8888"):
                await executor.execute("q", {"slug": "s"}, queue, MagicMock())

    @pytest.mark.asyncio
    async def test_propagates_slug_not_found(self):
        """SlugNotFound from qworker side propagates as-is."""
        from querysource.exceptions import SlugNotFound
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="localhost", port=8888)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.side_effect = SlugNotFound("no-such-slug")
            MockClient.return_value = mock_instance

            with pytest.raises(SlugNotFound):
                await executor.execute("q", {"slug": "no-such-slug"}, queue, MagicMock())

    @pytest.mark.asyncio
    async def test_strips_non_condition_keys(self):
        """slug, query, driver, datasource keys are excluded from conditions."""
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="localhost", port=8888)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = pd.DataFrame({"x": [1]})
            MockClient.return_value = mock_instance

            await executor.execute(
                "test",
                {
                    "slug": "my-slug",
                    "query": "SELECT 1",
                    "driver": "pg",
                    "datasource": "prod",
                    "store_id": 42,
                },
                queue,
                MagicMock(),
            )

        # Verify run was called with slug and only store_id as a condition
        call_kwargs = mock_instance.run.call_args
        conditions = call_kwargs[1]["conditions"]
        assert "slug" not in conditions
        assert "query" not in conditions
        assert "driver" not in conditions
        assert "datasource" not in conditions
        assert conditions["store_id"] == 42
