"""Unit tests for QSScheduler job definitions."""
from unittest.mock import AsyncMock, MagicMock, patch


class TestScheduledQueryJob:
    @patch("querysource.queries.qs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Job calls QS(slug).query() and discards result."""
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance

        await scheduled_query_job(slug="test_slug")

        # TASK-730: the tenant selector (from `owner`, None when omitted)
        # is now always threaded into QS explicitly.
        mock_qs_cls.assert_called_once_with(slug="test_slug", tenant=None)
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
    async def test_notifies_with_real_qualified_job_id_not_legacy_guess(
        self, mock_qs_cls
    ):
        """Code review finding 14: a tenant-store job's notify() must use
        its REAL registered id (qsj2-qualified), never the guessed legacy
        'query_<slug>' shape — the two only coincide for the default store.
        QSScheduler now threads the real id in via the job_id= kwarg.
        """
        from querysource.scheduler.jobs import scheduled_query_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("DB error")
        mock_qs_cls.return_value = mock_instance

        notifier = MagicMock()
        qualified_id = "qsj2-query-abc123def456-fail_slug"
        await scheduled_query_job(
            slug="fail_slug",
            notification_manager=notifier,
            job_id=qualified_id,
        )

        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["job_id"] == qualified_id

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


class TestCacheRefreshJob:
    @patch("querysource.queries.qs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Cache refresh calls QS(slug).query()."""
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance

        await cache_refresh_job(slug="cached_slug")

        # TASK-730: the tenant selector (from `owner`, None when omitted)
        # is now always threaded into QS explicitly.
        mock_qs_cls.assert_called_once_with(slug="cached_slug", tenant=None)
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
    async def test_notifies_with_real_qualified_job_id_not_legacy_guess(
        self, mock_qs_cls
    ):
        """See TestScheduledQueryJob's identically-named test — same finding
        (14) applies to cache-refresh jobs registered for a tenant store.
        """
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = ConnectionError("Redis down")
        mock_qs_cls.return_value = mock_instance

        notifier = MagicMock()
        qualified_id = "qsj2-cache-abc123def456-broken_slug"
        await cache_refresh_job(
            slug="broken_slug",
            notification_manager=notifier,
            job_id=qualified_id,
        )

        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["job_id"] == qualified_id

    @patch("querysource.queries.qs.QS")
    async def test_handles_error_without_notifier(self, mock_qs_cls):
        """Cache refresh handles errors gracefully without notification_manager."""
        from querysource.scheduler.jobs import cache_refresh_job

        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("fail")
        mock_qs_cls.return_value = mock_instance

        # Should not raise
        await cache_refresh_job(slug="broken_slug")


class TestScheduledMultiQSJob:

    async def test_calls_multiqs_with_slug_only(self):
        """scheduled_multiqs_job constructs MultiQS(slug=slug) and awaits query()."""
        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(return_value=({}, {}))
            mock_cls.return_value = mock_instance

            from querysource.scheduler.jobs import scheduled_multiqs_job
            await scheduled_multiqs_job(slug="test_slug")

            # TASK-730: the tenant selector (from `owner`, None when
            # omitted) is now always threaded into MultiQS explicitly.
            mock_cls.assert_called_once_with(slug="test_slug", tenant=None)
            mock_instance.query.assert_awaited_once_with()

    async def test_notifies_on_exception(self):
        """scheduled_multiqs_job notifies the manager when MultiQS.query() raises."""
        notification_manager = MagicMock()
        boom = RuntimeError("boom")

        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(side_effect=boom)
            mock_cls.return_value = mock_instance

            from querysource.scheduler.jobs import scheduled_multiqs_job
            await scheduled_multiqs_job(
                slug="bad_slug",
                notification_manager=notification_manager,
            )

            notification_manager.notify.assert_called_once_with(
                job_id="multi_bad_slug",
                slug="bad_slug",
                error=boom,
            )

    async def test_notifies_with_real_qualified_job_id_not_legacy_guess(self):
        """See TestScheduledQueryJob's identically-named test — same finding
        (14) applies to multi-query jobs registered for a tenant store.
        """
        notification_manager = MagicMock()
        boom = RuntimeError("boom")

        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(side_effect=boom)
            mock_cls.return_value = mock_instance

            from querysource.scheduler.jobs import scheduled_multiqs_job
            qualified_id = "qsj2-multi-abc123def456-bad_slug"
            await scheduled_multiqs_job(
                slug="bad_slug",
                notification_manager=notification_manager,
                job_id=qualified_id,
            )

            call_kwargs = notification_manager.notify.call_args[1]
            assert call_kwargs["job_id"] == qualified_id

    async def test_swallows_exception(self):
        """scheduled_multiqs_job does NOT re-raise after notifying."""
        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(side_effect=ValueError("nope"))
            mock_cls.return_value = mock_instance

            from querysource.scheduler.jobs import scheduled_multiqs_job
            # Must not raise.
            await scheduled_multiqs_job(
                slug="x",
                notification_manager=MagicMock(),
            )


class TestJobImports:
    def test_importable(self):
        """All three jobs are importable from the scheduler package."""
        from querysource.scheduler.jobs import (
            cache_refresh_job,
            scheduled_multiqs_job,
            scheduled_query_job,
        )
        assert callable(scheduled_query_job)
        assert callable(cache_refresh_job)
        assert callable(scheduled_multiqs_job)
