"""Unit tests for querysource.scheduler.scheduler (QSScheduler Core)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTriggerParsing:
    def _make_scheduler(self):
        """Create a QSScheduler instance without __init__ side effects."""
        from querysource.scheduler.scheduler import QSScheduler
        qs = QSScheduler.__new__(QSScheduler)
        qs.logger = MagicMock()
        qs._timezone = "UTC"
        return qs

    def test_parse_cron_trigger(self):
        """Parses cron schedule into CronTrigger."""
        from apscheduler.triggers.cron import CronTrigger
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger("cron", {"hour": "*/2", "minute": 27})
        assert trigger is not None
        assert isinstance(trigger, CronTrigger)

    def test_parse_crontab_trigger(self):
        """Parses crontab expression into CronTrigger."""
        from apscheduler.triggers.cron import CronTrigger
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger("crontab", {"crontab": "*/5 * * * *"})
        assert trigger is not None
        assert isinstance(trigger, CronTrigger)

    def test_parse_crontab_with_timezone(self):
        """Parses crontab with explicit timezone."""
        from apscheduler.triggers.cron import CronTrigger
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger(
            "crontab", {"crontab": "0 9 * * *", "timezone": "America/New_York"}
        )
        assert trigger is not None
        assert isinstance(trigger, CronTrigger)

    def test_parse_interval_trigger(self):
        """Parses interval schedule into IntervalTrigger."""
        from apscheduler.triggers.interval import IntervalTrigger
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger("interval", {"minutes": 30})
        assert trigger is not None
        assert isinstance(trigger, IntervalTrigger)

    def test_parse_invalid_schedule_returns_none(self):
        """Invalid schedule_type returns None."""
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger("unknown", {})
        assert trigger is None

    def test_parse_bad_cron_params_returns_none(self):
        """Bad cron parameters return None (not raise)."""
        scheduler = self._make_scheduler()
        trigger = scheduler._parse_trigger("cron", {"invalid_field": "bad"})
        assert trigger is None


class TestLoadScheduledQueries:
    def _make_scheduler_with_apscheduler(self):
        """Create a QSScheduler with a real AsyncIOScheduler (not started)."""
        from querysource.scheduler.scheduler import QSScheduler
        qs = QSScheduler.__new__(QSScheduler)
        qs.logger = MagicMock()
        qs._timezone = "UTC"
        qs._notification_manager = MagicMock()
        qs._scheduler = qs._create_scheduler()
        return qs

    def test_registers_job_for_valid_scheduler_attribute(self):
        """Row with valid attributes.scheduler registers a job."""
        qs = self._make_scheduler_with_apscheduler()
        rows = [{
            "query_slug": "test_query",
            "attributes": {
                "scheduler": {
                    "schedule_type": "interval",
                    "schedule": {"seconds": 60},
                }
            },
            "cache_options": {},
            "is_cached": False,
        }]
        count = qs._load_scheduled_queries(rows)
        assert count == 1
        job = qs._scheduler.get_job("query_test_query")
        assert job is not None

    def test_skips_row_without_scheduler_attribute(self):
        """Row without attributes.scheduler is skipped."""
        qs = self._make_scheduler_with_apscheduler()
        rows = [{
            "query_slug": "no_schedule",
            "attributes": {},
            "cache_options": {},
            "is_cached": False,
        }]
        count = qs._load_scheduled_queries(rows)
        assert count == 0


class TestCacheRefreshFiltering:
    def _make_scheduler_with_apscheduler(self):
        from querysource.scheduler.scheduler import QSScheduler
        qs = QSScheduler.__new__(QSScheduler)
        qs.logger = MagicMock()
        qs._timezone = "UTC"
        qs._notification_manager = MagicMock()
        qs._scheduler = qs._create_scheduler()
        return qs

    def test_skip_when_not_cached(self):
        """Row with cache_options but is_cached=False should not register a job."""
        qs = self._make_scheduler_with_apscheduler()
        rows = [{
            "query_slug": "uncached",
            "attributes": {},
            "cache_options": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            },
            "is_cached": False,
        }]
        count = qs._load_cache_refresh_jobs(rows)
        assert count == 0

    def test_register_when_cached(self):
        """Row with cache_options and is_cached=True registers a job."""
        qs = self._make_scheduler_with_apscheduler()
        rows = [{
            "query_slug": "cached_query",
            "attributes": {},
            "cache_options": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            },
            "is_cached": True,
        }]
        count = qs._load_cache_refresh_jobs(rows)
        assert count == 1
        job = qs._scheduler.get_job("cache_cached_query")
        assert job is not None

    def test_skip_when_no_schedule_in_cache_options(self):
        """Row with is_cached=True but empty cache_options is skipped."""
        qs = self._make_scheduler_with_apscheduler()
        rows = [{
            "query_slug": "no_cache_schedule",
            "attributes": {},
            "cache_options": {},
            "is_cached": True,
        }]
        count = qs._load_cache_refresh_jobs(rows)
        assert count == 0


class TestQSSchedulerInit:
    def test_init_creates_notification_manager(self):
        """QSScheduler.__init__ creates a NotificationManager."""
        from querysource.scheduler.scheduler import QSScheduler
        from querysource.scheduler.notifications import NotificationManager
        qs = QSScheduler()
        assert isinstance(qs._notification_manager, NotificationManager)

    def test_add_notification_callback(self):
        """add_notification_callback delegates to NotificationManager."""
        from querysource.scheduler.scheduler import QSScheduler
        qs = QSScheduler()
        cb = MagicMock()
        qs.add_notification_callback(cb)
        assert cb in qs._notification_manager._callbacks


# ---------------------------------------------------------------------------
# TASK-645: Provider routing tests
# ---------------------------------------------------------------------------

def _row(provider="db", attrs=None, query_raw=None):
    return {
        "query_slug": "test_slug",
        "attributes": attrs if attrs is not None else {
            "scheduler": {"schedule_type": "interval",
                          "schedule": {"minutes": 30}}
        },
        "cache_options": {},
        "provider": provider,
        "is_cached": False,
        "query_raw": query_raw,
    }


@pytest.fixture
def sched():
    """Fresh QSScheduler instance with a mocked APScheduler and logger."""
    from querysource.scheduler.scheduler import QSScheduler
    qs = QSScheduler.__new__(QSScheduler)
    qs.logger = MagicMock()
    qs._timezone = "UTC"
    qs._notification_manager = MagicMock()
    qs._scheduler = MagicMock()
    return qs


class TestLoadScheduledQueriesProviderRouting:

    def test_routes_multi_provider_to_new_job(self, sched):
        """provider='multi' row registers scheduled_multiqs_job with id multi_<slug>."""
        from querysource.scheduler.jobs import scheduled_multiqs_job
        sched._load_scheduled_queries([_row(provider="multi",
                                            query_raw='{"queries":{}}')])
        args, kwargs = sched._scheduler.add_job.call_args
        assert args[0] is scheduled_multiqs_job
        assert kwargs["id"] == "multi_test_slug"
        assert kwargs["name"] == "Scheduled multi-query: test_slug"

    def test_keeps_single_query_path_for_non_multi(self, sched):
        """provider='db' row registers scheduled_query_job with id query_<slug>."""
        from querysource.scheduler.jobs import scheduled_query_job
        sched._load_scheduled_queries([_row(provider="db")])
        args, kwargs = sched._scheduler.add_job.call_args
        assert args[0] is scheduled_query_job
        assert kwargs["id"] == "query_test_slug"

    def test_skips_row_without_scheduler_attribute(self, sched):
        """provider='multi' row with no attributes.scheduler is skipped."""
        sched._load_scheduled_queries([_row(provider="multi", attrs={})])
        sched._scheduler.add_job.assert_not_called()

    def test_warns_on_misconfigured_multi(self, sched):
        """provider='multi' with plain-SQL query_raw triggers a WARNING log."""
        sched._load_scheduled_queries([
            _row(provider="multi", query_raw="SELECT 1"),
        ])
        # logger is a MagicMock; check it was called with the expected substring.
        warning_calls = [str(call) for call in sched.logger.warning.call_args_list]
        assert any(
            "MultiQS will fall back to single-query mode" in call
            for call in warning_calls
        )

    def test_debug_log_for_reserved_output_subkey(self, sched):
        """attributes.scheduler.output present triggers a DEBUG log; not passed into kwargs."""
        attrs = {"scheduler": {
            "schedule_type": "interval",
            "schedule": {"minutes": 30},
            "output": {"type": "tableOutput"},
        }}
        sched._load_scheduled_queries([
            _row(provider="multi",
                 attrs=attrs,
                 query_raw='{"queries":{}}'),
        ])
        debug_calls = [str(call) for call in sched.logger.debug.call_args_list]
        assert any(
            "attributes.scheduler.output" in call
            for call in debug_calls
        )
        _, kwargs = sched._scheduler.add_job.call_args
        assert "output" not in kwargs["kwargs"]
