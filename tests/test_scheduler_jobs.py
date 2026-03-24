"""Unit tests for QSScheduler job definitions."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
class TestScheduledQueryJob:
    @patch("querysource.queries.qs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Job calls QS(slug).query() and discards result."""
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance

        await scheduled_query_job(slug="test_slug")

        mock_qs_cls.assert_called_once_with(slug="test_slug")
        mock_instance.query.assert_awaited_once()

    @patch("querysource.queries.qs.QS")
    async def test_handles_error_and_notifies(self, mock_qs_cls):
        """Job catches exception and calls notification_manager.notify()."""
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("DB error")
        mock_qs_cls.return_value = mock_instance

        notifier = MagicMock()
        await scheduled_query_job(slug="fail_slug", notification_manager=notifier)

        notifier.notify.assert_called_once()
        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["job_id"] == "query_fail_slug"
        assert call_kwargs["slug"] == "fail_slug"
        assert isinstance(call_kwargs["error"], RuntimeError)

    @patch("querysource.queries.qs.QS")
    async def test_handles_error_without_notifier(self, mock_qs_cls):
        """Job handles errors gracefully when no notification_manager is provided."""
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("DB error")
        mock_qs_cls.return_value = mock_instance

        # Should not raise
        await scheduled_query_job(slug="fail_slug")

    @patch("querysource.queries.qs.QS")
    async def test_extra_kwargs_ignored(self, mock_qs_cls):
        """Extra keyword arguments are accepted and ignored."""
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance

        await scheduled_query_job(slug="test_slug", extra_param="ignored")
        mock_instance.query.assert_awaited_once()


@pytest.mark.asyncio
class TestCacheRefreshJob:
    @patch("querysource.queries.qs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Cache refresh calls QS(slug).query()."""
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance

        await cache_refresh_job(slug="cached_slug")

        mock_qs_cls.assert_called_once_with(slug="cached_slug")
        mock_instance.query.assert_awaited_once()

    @patch("querysource.queries.qs.QS")
    async def test_handles_error_and_notifies(self, mock_qs_cls):
        """Cache refresh catches exception and notifies."""
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = ConnectionError("Redis down")
        mock_qs_cls.return_value = mock_instance

        notifier = MagicMock()
        await cache_refresh_job(slug="broken_slug", notification_manager=notifier)

        notifier.notify.assert_called_once()
        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["job_id"] == "cache_broken_slug"
        assert call_kwargs["slug"] == "broken_slug"
        assert isinstance(call_kwargs["error"], ConnectionError)

    @patch("querysource.queries.qs.QS")
    async def test_handles_error_without_notifier(self, mock_qs_cls):
        """Cache refresh handles errors gracefully without notification_manager."""
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("fail")
        mock_qs_cls.return_value = mock_instance

        # Should not raise
        await cache_refresh_job(slug="broken_slug")


class TestJobImports:
    def test_importable(self):
        """Both jobs are importable from the scheduler package."""
        from querysource.scheduler.jobs import scheduled_query_job, cache_refresh_job
        assert callable(scheduled_query_job)
        assert callable(cache_refresh_job)
