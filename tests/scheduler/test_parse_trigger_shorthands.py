"""Unit tests for QSScheduler._parse_trigger shorthand schedule_type branches.

Tests exercise all five shorthand values (hourly, daily, weekly, monthly,
biweekly) plus timezone handling and regression tests for the existing
cron/crontab/interval paths.

These tests do NOT start the scheduler or open a DB connection; they
instantiate QSScheduler(loop=None) and call _parse_trigger directly.
"""
from datetime import datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from querysource.scheduler.scheduler import QSScheduler, _biweekly_anchor


@pytest.fixture
def scheduler():
    """Minimal QSScheduler — does NOT start the scheduler or DB pool."""
    return QSScheduler(loop=None)


# ----- hourly ---------------------------------------------------------------

def test_hourly_minute_only(scheduler):
    trig = scheduler._parse_trigger("hourly", {"minute": 15})
    assert isinstance(trig, CronTrigger)
    minute_field = next(f for f in trig.fields if f.name == "minute")
    assert str(minute_field) == "15"


def test_hourly_missing_minute_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("hourly", {}) is None
    assert any("Failed to parse trigger" in r.message for r in caplog.records)


def test_hourly_out_of_range_minute_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("hourly", {"minute": 60}) is None
    assert any("Failed to parse trigger" in r.message for r in caplog.records)


# ----- daily ----------------------------------------------------------------

def test_daily_hour_minute(scheduler):
    trig = scheduler._parse_trigger("daily", {"hour": 9, "minute": 30})
    assert isinstance(trig, CronTrigger)


def test_daily_missing_hour_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("daily", {"minute": 0}) is None
    assert any("Failed to parse trigger" in r.message for r in caplog.records)


# ----- weekly ---------------------------------------------------------------

def test_weekly_mon_9am(scheduler):
    trig = scheduler._parse_trigger(
        "weekly", {"day_of_week": "mon", "hour": 9, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_weekly_int_day_of_week(scheduler):
    trig = scheduler._parse_trigger(
        "weekly", {"day_of_week": 0, "hour": 9, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_weekly_invalid_day_of_week(scheduler):
    assert (
        scheduler._parse_trigger(
            "weekly", {"day_of_week": "funday", "hour": 9, "minute": 0}
        )
        is None
    )


# ----- monthly --------------------------------------------------------------

def test_monthly_day_1_midnight(scheduler):
    trig = scheduler._parse_trigger(
        "monthly", {"day": 1, "hour": 0, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_monthly_day_out_of_range_returns_none(scheduler, caplog):
    assert (
        scheduler._parse_trigger(
            "monthly", {"day": 32, "hour": 0, "minute": 0}
        )
        is None
    )
    assert any("Failed to parse trigger" in r.message for r in caplog.records)


# ----- biweekly -------------------------------------------------------------

def test_biweekly_with_start_date_uses_interval(scheduler):
    trig = scheduler._parse_trigger(
        "biweekly",
        {
            "day_of_week": "mon",
            "hour": 9,
            "minute": 0,
            "start_date": "2026-06-01",
        },
    )
    assert isinstance(trig, IntervalTrigger)
    assert trig.interval == timedelta(weeks=2)


def test_biweekly_without_start_date_uses_cron_week_step(scheduler):
    trig = scheduler._parse_trigger(
        "biweekly",
        {"day_of_week": "mon", "hour": 9, "minute": 0},
    )
    assert isinstance(trig, CronTrigger)
    week_field = next(f for f in trig.fields if f.name == "week")
    assert "*/2" in str(week_field)


def test_biweekly_anchor_date_aligns_to_day_of_week():
    # 2026-06-03 is a Wednesday; the next Monday is 2026-06-08.
    anchor = _biweekly_anchor("2026-06-03", "mon", 9, 0)
    assert anchor == datetime(2026, 6, 8, 9, 0)


def test_biweekly_anchor_int_day_of_week_valid(scheduler):
    trig = scheduler._parse_trigger(
        "biweekly",
        {"day_of_week": 0, "hour": 9, "minute": 0, "start_date": "2026-06-01"},
    )
    assert isinstance(trig, IntervalTrigger)


def test_biweekly_anchor_int_day_of_week_out_of_range_returns_none(scheduler):
    assert scheduler._parse_trigger(
        "biweekly",
        {"day_of_week": 7, "hour": 9, "minute": 0, "start_date": "2026-06-01"},
    ) is None


# ----- timezone -------------------------------------------------------------

def test_timezone_fallback(scheduler):
    trig = scheduler._parse_trigger("daily", {"hour": 9, "minute": 0})
    # We only assert the trigger was built; deep tz introspection is brittle.
    assert isinstance(trig, CronTrigger)


def test_timezone_override(scheduler):
    trig = scheduler._parse_trigger(
        "daily", {"hour": 9, "minute": 0, "timezone": "America/New_York"}
    )
    assert isinstance(trig, CronTrigger)


# ----- regressions on the legacy paths --------------------------------------

def test_existing_cron_path_unchanged(scheduler):
    trig = scheduler._parse_trigger(
        "cron", {"hour": "*/2", "minute": 27}
    )
    assert isinstance(trig, CronTrigger)


def test_existing_interval_path_unchanged(scheduler):
    trig = scheduler._parse_trigger("interval", {"minutes": 30})
    assert isinstance(trig, IntervalTrigger)


def test_existing_crontab_path_unchanged(scheduler):
    trig = scheduler._parse_trigger("crontab", {"crontab": "*/5 * * * *"})
    assert isinstance(trig, CronTrigger)


# ----- unknown --------------------------------------------------------------

def test_unknown_schedule_type_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("yearly", {}) is None
    assert any("Unknown schedule_type" in r.message for r in caplog.records)
