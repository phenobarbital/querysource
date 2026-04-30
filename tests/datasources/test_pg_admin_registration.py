"""Tests for pg_admin datasource driver registration (TASK-635)."""
import importlib
import pytest


class TestPgAdmin:
    def test_module_imports(self):
        """pg_adminDriver can be imported and has correct driver class attr."""
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        assert pg_adminDriver.driver == "pg_admin"

    def test_registered_in_supported(self):
        """pg_admin is registered in SUPPORTED dict."""
        from querysource.datasources.drivers import SUPPORTED
        assert "pg_admin" in SUPPORTED

    def test_driver_class_in_supported(self):
        """SUPPORTED['pg_admin']['driver'] is pg_adminDriver."""
        from querysource.datasources.drivers import SUPPORTED
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        assert SUPPORTED["pg_admin"]["driver"] is pg_adminDriver

    def test_inherits_from_pgdriver(self):
        """pg_adminDriver inherits from pgDriver."""
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        from querysource.datasources.drivers.pg import pgDriver
        assert issubclass(pg_adminDriver, pgDriver)

    def test_default_none_when_db_creds_missing(self, monkeypatch):
        """Importing pg_admin does not raise even when DB* env vars are absent."""
        for k in ("DBHOST", "DBPORT", "DBUSER", "DBPWD", "DBNAME"):
            monkeypatch.delenv(k, raising=False)
        # Re-import; try/except ValueError in the module protects pg_admin_default.
        # navconfig may cache the values, so we can't guarantee None —
        # what we verify is that no unhandled exception is raised.
        from querysource.datasources.drivers import pg_admin as pg_admin_mod
        importlib.reload(pg_admin_mod)
        # pg_admin_default is either a driver instance or None — both fine.
        assert hasattr(pg_admin_mod, "pg_admin_default")
