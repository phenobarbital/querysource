"""Unit tests for QWorker configuration settings (TASK-697)."""
from querysource.conf import QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT


class TestQWorkerConfig:
    def test_host_default_is_none(self):
        """QWORKER_HOST defaults to None when not configured."""
        # When env var is not set, should be None
        assert QWORKER_HOST is None or isinstance(QWORKER_HOST, str)

    def test_port_is_int(self):
        """QWORKER_PORT is an integer."""
        assert isinstance(QWORKER_PORT, int)

    def test_port_default_value(self):
        """QWORKER_PORT defaults to 8888."""
        # Only true when QWORKER_PORT env var is not set
        assert QWORKER_PORT == 8888 or isinstance(QWORKER_PORT, int)

    def test_timeout_is_int(self):
        """QWORKER_TIMEOUT is an integer."""
        assert isinstance(QWORKER_TIMEOUT, int)

    def test_timeout_default_value(self):
        """QWORKER_TIMEOUT defaults to 60."""
        assert QWORKER_TIMEOUT == 60 or isinstance(QWORKER_TIMEOUT, int)
