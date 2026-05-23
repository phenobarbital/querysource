"""Integration tests for shorthand schedules via QSScheduler loaders.

Tests drive _load_scheduled_queries and _load_cache_refresh_jobs directly
with in-memory row fixtures (no DB, no aiohttp app).

The fixture attaches an AsyncIOScheduler via _create_scheduler() to
scheduler._scheduler WITHOUT calling .start(), so add_job() registers
jobs in the in-memory jobstore synchronously.
"""
import pytest

from querysource.scheduler.scheduler import QSScheduler


@pytest.fixture
def scheduler_with_inmemory_jobstore():
    """QSScheduler with an in-memory APScheduler attached but NOT started."""
    s = QSScheduler(loop=None)
    s._scheduler = s._create_scheduler()  # attach AsyncIOScheduler; do NOT .start()
    return s


def test_load_scheduled_queries_with_hourly_shorthand(scheduler_with_inmemory_jobstore):
    """A row with attributes.scheduler.schedule_type=='hourly' registers a query_<slug> job."""
    rows = [
        {
            "query_slug": "test_hourly",
            "attributes": {
                "scheduler": {
                    "schedule_type": "hourly",
                    "schedule": {"minute": 15},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        }
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 1
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("query_test_hourly")
        is not None
    )


def test_load_cache_refresh_with_daily_shorthand(scheduler_with_inmemory_jobstore):
    """A row with cache_options.schedule_type=='daily' and is_cached=True registers cache_<slug>."""
    rows = [
        {
            "query_slug": "cached_slug",
            "attributes": None,
            "provider": None,
            "query_raw": None,
            "is_cached": True,
            "cache_options": {
                "schedule_type": "daily",
                "schedule": {"hour": 3, "minute": 0},
            },
        }
    ]
    count = scheduler_with_inmemory_jobstore._load_cache_refresh_jobs(rows)
    assert count == 1
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("cache_cached_slug")
        is not None
    )


def test_mixed_shorthand_and_legacy_load_together(scheduler_with_inmemory_jobstore):
    """Startup with one legacy cron row and one biweekly row registers two jobs."""
    rows = [
        {
            "query_slug": "legacy_cron",
            "attributes": {
                "scheduler": {
                    "schedule_type": "cron",
                    "schedule": {"hour": "*/2", "minute": 0},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        },
        {
            "query_slug": "new_biweekly",
            "attributes": {
                "scheduler": {
                    "schedule_type": "biweekly",
                    "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        },
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 2
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("query_legacy_cron")
        is not None
    )
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("query_new_biweekly")
        is not None
    )


def test_invalid_shorthand_skips_job_but_loads_others(
    scheduler_with_inmemory_jobstore, caplog
):
    """One invalid monthly row (missing 'day') is skipped; the other two rows register."""
    rows = [
        {  # valid
            "query_slug": "ok_daily",
            "attributes": {
                "scheduler": {
                    "schedule_type": "daily",
                    "schedule": {"hour": 0, "minute": 0},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        },
        {  # invalid — monthly missing 'day'
            "query_slug": "bad_monthly",
            "attributes": {
                "scheduler": {
                    "schedule_type": "monthly",
                    "schedule": {"hour": 0, "minute": 0},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        },
        {  # valid
            "query_slug": "ok_weekly",
            "attributes": {
                "scheduler": {
                    "schedule_type": "weekly",
                    "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        },
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 2
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("query_bad_monthly")
        is None
    )
    # The invalid row must have triggered a "Failed to parse trigger" ERROR log.
    assert any(
        "Failed to parse trigger" in r.message for r in caplog.records
    )
