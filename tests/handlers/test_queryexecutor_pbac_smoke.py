"""Smoke tests for QueryExecutor PBAC enforcement.

Verifies _enforce_payload is called before executor.query()/dry_run(),
and that the slug vs raw_query branching logic is correct.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web

from querysource.handlers.executor import QueryExecutor


class TestQueryExecutorPbacSmoke:
    """Verify PBAC enforcement in QueryExecutor."""

    def _make_handler(self):
        h = QueryExecutor.__new__(QueryExecutor)
        h.logger = MagicMock()
        h._json = MagicMock()
        return h

    @pytest.mark.asyncio
    async def test_query_calls_enforce_payload_before_executor_query(self):
        """_enforce_payload raising 404 prevents query.query() from being called."""
        h = self._make_handler()
        h._enforce_payload = AsyncMock(side_effect=web.HTTPNotFound())
        h.get_payload = AsyncMock(return_value={"slug": "x"})
        mock_executor = MagicMock()
        mock_executor.query = AsyncMock()
        h.get_executor = MagicMock(return_value=mock_executor)

        with pytest.raises(web.HTTPNotFound):
            await h.query(MagicMock(spec=web.Request))

        h._enforce_payload.assert_awaited_once()
        mock_executor.query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_uses_same_helper(self):
        """_enforce_payload raising 404 prevents query.dry_run() from being called."""
        h = self._make_handler()
        h._enforce_payload = AsyncMock(side_effect=web.HTTPNotFound())
        h.get_payload = AsyncMock(return_value={"query": "SELECT 1"})
        mock_executor = MagicMock()
        mock_executor.dry_run = AsyncMock()
        h.get_executor = MagicMock(return_value=mock_executor)

        with pytest.raises(web.HTTPNotFound):
            await h.dry_run(MagicMock(spec=web.Request))

        h._enforce_payload.assert_awaited_once()
        mock_executor.dry_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raw_payload_uses_raw_query_action(self):
        """_enforce_payload uses raw_query:execute when no slug in payload."""
        h = self._make_handler()
        h._enforce_pbac = AsyncMock()
        inner = MagicMock()
        inner.datasource = None
        inner.driver = None
        executor = MagicMock()
        executor._query = inner

        await h._enforce_payload(MagicMock(spec=web.Request), {"query": "SELECT 1"}, executor)

        # First call must be raw_query:execute
        first_call = h._enforce_pbac.await_args_list[0]
        action = first_call.kwargs.get('action') or first_call.args[3]
        assert action == "raw_query:execute"

    @pytest.mark.asyncio
    async def test_slug_payload_uses_slug_execute_action(self):
        """_enforce_payload uses slug:execute when slug is present in payload."""
        h = self._make_handler()
        h._enforce_pbac = AsyncMock()
        inner = MagicMock()
        inner.datasource = None
        inner.driver = None
        executor = MagicMock()
        executor._query = inner

        await h._enforce_payload(
            MagicMock(spec=web.Request), {"slug": "my-slug"}, executor
        )

        first_call = h._enforce_pbac.await_args_list[0]
        action = first_call.kwargs.get('action') or first_call.args[3]
        assert action == "slug:execute"
        resource_name = first_call.kwargs.get('resource_name') or first_call.args[2]
        assert resource_name == "my-slug"

    @pytest.mark.asyncio
    async def test_datasource_check_runs_when_datasource_set(self):
        """_enforce_payload also calls datasource:use when datasource is set."""
        h = self._make_handler()
        h._enforce_pbac = AsyncMock()
        inner = MagicMock()
        inner.datasource = "my_db"
        inner.driver = None
        executor = MagicMock()
        executor._query = inner

        await h._enforce_payload(
            MagicMock(spec=web.Request), {"query": "SELECT 1"}, executor
        )

        actions = [
            (c.kwargs.get('action') or c.args[3])
            for c in h._enforce_pbac.await_args_list
        ]
        assert "raw_query:execute" in actions
        assert "datasource:use" in actions
