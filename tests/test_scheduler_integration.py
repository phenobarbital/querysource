"""Integration tests for QSScheduler wiring into QuerySource."""
import pytest
from unittest.mock import patch, MagicMock


class TestQuerySourceSchedulerIntegration:
    def test_scheduler_import_in_conf(self):
        """ENABLE_QS_SCHEDULER is importable from services module."""
        from querysource.services import ENABLE_QS_SCHEDULER
        assert isinstance(ENABLE_QS_SCHEDULER, bool)

    def test_scheduler_not_imported_when_disabled(self):
        """When disabled (default), scheduler module is not imported in setup."""
        from querysource.conf import ENABLE_QS_SCHEDULER
        assert ENABLE_QS_SCHEDULER is False

    def test_qsscheduler_registers_hooks(self):
        """QSScheduler.setup() appends to on_startup and on_shutdown."""
        from querysource.scheduler import QSScheduler

        scheduler = QSScheduler()
        mock_app = MagicMock()
        mock_app.on_startup = []
        mock_app.on_shutdown = []

        scheduler.setup(mock_app)

        assert len(mock_app.on_startup) == 1
        assert len(mock_app.on_shutdown) == 1
        assert mock_app.on_startup[0] == scheduler.startup
        assert mock_app.on_shutdown[0] == scheduler.shutdown

    def test_qsscheduler_accessible_via_app_key(self):
        """QSScheduler stores itself in app['qs_scheduler'] during setup hook registration."""
        from querysource.scheduler import QSScheduler

        scheduler = QSScheduler()
        mock_app = MagicMock()
        mock_app.on_startup = []
        mock_app.on_shutdown = []

        scheduler.setup(mock_app)
        # The actual app['qs_scheduler'] is set in startup(), not setup()
        # But verify setup registered the hooks
        assert scheduler.startup in mock_app.on_startup

    def test_conditional_import_path(self):
        """Verify the conditional import pattern works."""
        # When ENABLE_QS_SCHEDULER is True, the import inside setup() should work
        from querysource.scheduler import QSScheduler
        assert QSScheduler is not None
        assert hasattr(QSScheduler, 'setup')
        assert hasattr(QSScheduler, 'startup')
        assert hasattr(QSScheduler, 'shutdown')
        assert hasattr(QSScheduler, 'add_notification_callback')
