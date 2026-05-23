"""Smoke / integration tests for QSScheduler multi-query routing (FEAT-092).

These tests verify that all three production-code changes (TASK-644 / 645 / 646)
work together correctly:
- _load_scheduled_queries routes provider='multi' to scheduled_multiqs_job.
- _load_cache_refresh_jobs skips provider='multi' rows.
- A mixed population of rows produces exactly one of each job kind.

Tests are hermetic — no live database, no real APScheduler startup.
"""
import pytest
from unittest.mock import MagicMock

from querysource.scheduler.scheduler import QSScheduler
from querysource.scheduler.jobs import (
    scheduled_query_job,
    scheduled_multiqs_job,
)


@pytest.fixture
def mixed_rows():
    """Three rows: one single-query schedule, one multi-query schedule, one cache-refresh."""
    return [
        # single-query schedule
        {
            "query_slug": "single_a",
            "attributes": {"scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            }},
            "cache_options": {},
            "provider": "db",
            "is_cached": False,
            "query_raw": "SELECT 1",
        },
        # multi-query schedule
        {
            "query_slug": "multi_b",
            "attributes": {"scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 60},
            }},
            "cache_options": {},
            "provider": "multi",
            "is_cached": False,
            "query_raw": '{"queries": {}}',
        },
        # cache-refresh single-query (no scheduler attribute — only cache_options)
        {
            "query_slug": "cached_c",
            "attributes": {},
            "cache_options": {
                "schedule_type": "interval",
                "schedule": {"minutes": 15},
            },
            "provider": "db",
            "is_cached": True,
            "query_raw": "SELECT 2",
        },
    ]


def _make_sched_mocked():
    """Create a QSScheduler with a mocked APScheduler (not started)."""
    qs = QSScheduler.__new__(QSScheduler)
    qs.logger = MagicMock()
    qs._timezone = "UTC"
    qs._notification_manager = MagicMock()
    qs._scheduler = MagicMock()
    return qs


class TestQSSchedulerMixedPopulation:

    def test_registers_multi_job_from_db_row(self, mixed_rows):
        """Multi-query row registers exactly one job with id multi_<slug> and
        the callable IS scheduled_multiqs_job."""
        sched = _make_sched_mocked()
        sched._load_scheduled_queries(mixed_rows)

        calls = sched._scheduler.add_job.call_args_list
        ids = [c.kwargs["id"] for c in calls]
        callables = [c.args[0] for c in calls]

        assert "query_single_a" in ids
        assert "multi_multi_b" in ids
        assert scheduled_multiqs_job in callables
        assert scheduled_query_job in callables

    def test_mixed_row_population(self, mixed_rows):
        """Exactly one of each job kind is registered; NO cache_multi_b job."""
        sched = _make_sched_mocked()
        sched._load_scheduled_queries(mixed_rows)
        sched._load_cache_refresh_jobs(mixed_rows)

        calls = sched._scheduler.add_job.call_args_list
        ids = [c.kwargs["id"] for c in calls]

        # exactly one of each kind
        assert ids.count("query_single_a") == 1
        assert ids.count("multi_multi_b") == 1
        assert ids.count("cache_cached_c") == 1
        # multi-query row must NOT have a cache job
        assert "cache_multi_b" not in ids
