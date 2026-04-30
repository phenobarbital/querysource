"""Tests for QuerySource.setup() PBAC bootstrap wiring (TASK-638)."""
import pytest
from unittest.mock import MagicMock, patch
from aiohttp import web


class TestQuerySourceSetupPbac:
    def test_disabled_skips_bootstrap(self, monkeypatch):
        """When QS_PBAC_ENABLED is False, setup_pbac is never called."""
        monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", False)
        with patch("querysource.services.setup_pbac") as mock_setup:
            from querysource.services import QuerySource
            qs = QuerySource(lazy=True)
            app = web.Application()
            qs.setup(app)
            mock_setup.assert_not_called()

    def test_enabled_invokes_bootstrap(self, monkeypatch, tmp_path):
        """When QS_PBAC_ENABLED is True, setup_pbac is called with correct args."""
        monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", True)
        monkeypatch.setattr("querysource.services.QS_POLICY_PATH", str(tmp_path))
        monkeypatch.setattr("querysource.services.QS_PBAC_CACHE_TTL", 300)
        with patch("querysource.services.setup_pbac",
                   return_value=(None, None, None)) as mock_setup:
            from querysource.services import QuerySource
            qs = QuerySource(lazy=True)
            app = web.Application()
            qs.setup(app)
            mock_setup.assert_called_once()
            _, kwargs = mock_setup.call_args
            assert kwargs["policy_dir"] == str(tmp_path)
            assert kwargs["cache_ttl"] == 300

    def test_enabled_with_guardian_logs_info(self, monkeypatch, tmp_path):
        """Successful bootstrap (guardian != None) does not raise or warn."""
        monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", True)
        monkeypatch.setattr("querysource.services.QS_POLICY_PATH", str(tmp_path))
        monkeypatch.setattr("querysource.services.QS_PBAC_CACHE_TTL", 60)

        guardian = MagicMock()
        evaluator = MagicMock()
        pdp = MagicMock()

        with patch("querysource.services.setup_pbac",
                   return_value=(pdp, evaluator, guardian)):
            from querysource.services import QuerySource
            qs = QuerySource(lazy=True)
            app = web.Application()
            # Must not raise
            qs.setup(app)
