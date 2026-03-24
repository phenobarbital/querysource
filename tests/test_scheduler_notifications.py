"""Unit tests for querysource.scheduler.notifications."""
import pytest
from unittest.mock import MagicMock


class TestNotificationManager:
    def test_default_callback_registered(self):
        """NotificationManager has logging_callback on init."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        assert len(mgr._callbacks) == 1

    def test_add_callback(self):
        """add_callback appends to list."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.add_callback(lambda **kw: None)
        assert len(mgr._callbacks) == 2

    def test_notify_calls_all_callbacks(self):
        """notify() invokes every registered callback."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        mock_cb = MagicMock()
        mgr.add_callback(mock_cb)
        mgr.notify(job_id="test", slug="test_slug", error=RuntimeError("fail"))
        mock_cb.assert_called_once()

    def test_failing_callback_does_not_block_others(self):
        """One failing callback does not prevent others."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        failing_cb = MagicMock(side_effect=RuntimeError("boom"))
        passing_cb = MagicMock()
        mgr.add_callback(failing_cb)
        mgr.add_callback(passing_cb)
        mgr.notify(job_id="test", slug="s", error=RuntimeError("x"))
        passing_cb.assert_called_once()


class TestLoggingCallback:
    def test_logs_at_warning(self, caplog):
        """logging_callback logs at WARNING level."""
        import logging as stdlib_logging
        from querysource.scheduler.notifications import logging_callback
        with caplog.at_level(stdlib_logging.WARNING):
            logging_callback(job_id="j1", slug="s1", error=RuntimeError("err"))
        assert "j1" in caplog.text
        assert "s1" in caplog.text
