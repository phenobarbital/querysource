"""Unit tests for the SchedulerJobsView serialization helpers — FEAT-100."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from querysource.handlers.scheduler import SchedulerJobsView, _kind_from_id


class TestKindFromId:
    """Unit tests for the module-level _kind_from_id helper."""

    @pytest.mark.parametrize(
        "job_id, expected",
        [
            ("query_foo", "query"),
            ("multi_foo", "multi"),
            ("cache_foo", "cache"),
            ("weird_foo", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_prefix_mapping(self, job_id: str, expected: str) -> None:
        """Test that _kind_from_id maps prefixes to the correct kind."""
        assert _kind_from_id(job_id) == expected


class TestSerializeJob:
    """Unit tests for SchedulerJobsView._serialize_job."""

    def _fake_job(
        self,
        *,
        job_id: str = "query_foo",
        trigger=None,
        next_run_time=None,
        kwargs=None,
    ):
        """Build a MagicMock that looks like an APScheduler Job."""
        job = MagicMock()
        job.id = job_id
        job.name = f"Scheduled job: {job_id}"
        job.trigger = trigger or IntervalTrigger(seconds=30)
        job.next_run_time = next_run_time
        job.kwargs = kwargs if kwargs is not None else {
            "slug": "foo",
            "notification_manager": object(),
        }
        job.coalesce = True
        job.max_instances = 1
        job.misfire_grace_time = 1
        job.pending = False
        return job

    def _view(self) -> SchedulerJobsView:
        """Return a SchedulerJobsView bypassing __init__ (no real request needed)."""
        return SchedulerJobsView.__new__(SchedulerJobsView)

    def test_interval_trigger(self) -> None:
        """IntervalTrigger job serializes with type='interval', ISO next_run_time."""
        out = self._view()._serialize_job(
            self._fake_job(
                trigger=IntervalTrigger(seconds=30),
                next_run_time=datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc),
            )
        )
        assert out["id"] == "query_foo"
        assert out["kind"] == "query"
        assert out["slug"] == "foo"
        assert out["trigger"]["type"] == "interval"
        assert out["trigger"]["repr"]  # non-empty repr string
        assert out["next_run_time"] == "2026-05-23T14:30:00+00:00"
        # Must NOT include the raw kwargs dict or notification_manager
        assert "notification_manager" not in out
        assert "kwargs" not in out

    def test_cron_trigger(self) -> None:
        """CronTrigger job serializes with type='cron' and non-empty repr."""
        out = self._view()._serialize_job(
            self._fake_job(trigger=CronTrigger(hour="*/2"))
        )
        assert out["trigger"]["type"] == "cron"
        assert out["trigger"]["repr"]  # non-empty

    def test_next_run_time_none(self) -> None:
        """Paused job (next_run_time=None) serializes next_run_time as None."""
        out = self._view()._serialize_job(
            self._fake_job(next_run_time=None)
        )
        assert out["next_run_time"] is None

    def test_kwargs_leakage(self) -> None:
        """Only slug is extracted from kwargs; notification_manager must not appear."""
        out = self._view()._serialize_job(
            self._fake_job(
                kwargs={
                    "slug": "baz",
                    "notification_manager": MagicMock(),
                }
            )
        )
        assert out["slug"] == "baz"
        assert "kwargs" not in out
        assert "notification_manager" not in out

    def test_serialize_job_multi_kind(self) -> None:
        """Job with id 'multi_foo' gets kind='multi'."""
        out = self._view()._serialize_job(
            self._fake_job(job_id="multi_foo", kwargs={"slug": "foo"})
        )
        assert out["kind"] == "multi"

    def test_serialize_job_cache_kind(self) -> None:
        """Job with id 'cache_foo' gets kind='cache'."""
        out = self._view()._serialize_job(
            self._fake_job(job_id="cache_foo", kwargs={"slug": "foo"})
        )
        assert out["kind"] == "cache"

    def test_serialize_job_unknown_kind(self) -> None:
        """Job with id 'weird_foo' gets kind='unknown'."""
        out = self._view()._serialize_job(
            self._fake_job(job_id="weird_foo", kwargs={"slug": "foo"})
        )
        assert out["kind"] == "unknown"

    def test_all_required_fields_present(self) -> None:
        """Serialized job contains all fields required by the spec."""
        out = self._view()._serialize_job(
            self._fake_job(
                next_run_time=datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc)
            )
        )
        required_keys = {
            "id",
            "name",
            "kind",
            "slug",
            "next_run_time",
            "trigger",
            "coalesce",
            "max_instances",
            "misfire_grace_time",
            "pending",
        }
        assert required_keys.issubset(out.keys())
        assert isinstance(out["trigger"], dict)
        assert "type" in out["trigger"]
        assert "repr" in out["trigger"]
