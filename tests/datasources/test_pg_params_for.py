"""Unit tests for pgDriver.params_for() and credential_prefix (TASK-636)."""
import pytest
from unittest.mock import MagicMock
from querysource.auth.credentials import ResolvedCredentials
from querysource.datasources.drivers.pg import pgDriver


@pytest.fixture
def driver():
    d = pgDriver(
        dsn="postgres://u:p@h:5432/db",
        host="h", port=5432, database="db", user="u", password="p",
    )
    return d


class TestParamsFor:
    def test_no_app_falls_back_to_params(self, driver):
        """params_for(app=None) returns same dict as params()."""
        assert driver.params_for(session={"username": "x"}, app=None) == driver.params()

    def test_no_resolver_falls_back(self, driver):
        """params_for with app dict but no credential_resolver falls back."""
        app = {}  # no 'credential_resolver'
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()

    def test_resolver_none_falls_back(self, driver):
        """When resolver.resolve() returns None, fall back to params()."""
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=None)
        app = {"credential_resolver": resolver}
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()
        resolver.resolve.assert_called_once_with(
            prefix="PG", session={"username": "x"}, credential_profile=None,
        )

    def test_resolver_returns_creds(self, driver):
        """When resolver returns ResolvedCredentials, map to params() shape."""
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=ResolvedCredentials(
            host="resolved-host", port=6543, user="ru", password="rp",
            database="rd", source="user-override",
        ))
        app = {"credential_resolver": resolver}
        result = driver.params_for(session={"username": "x"}, app=app)
        assert result == {
            "host": "resolved-host", "port": 6543,
            "username": "ru", "password": "rp", "database": "rd",
        }

    def test_resolver_exception_falls_back(self, driver):
        """resolver.resolve() raising must not propagate — fall back to params()."""
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=RuntimeError("boom"))
        app = {"credential_resolver": resolver}
        # Should fall back, not raise:
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()

    def test_credential_profile_extracted_from_userinfo(self, driver):
        """credential_profile is pulled from session userinfo and forwarded."""
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=None)
        app = {"credential_resolver": resolver}
        session = {"user": {"credential_profile": "analytics-readonly"}}
        driver.params_for(session=session, app=app)
        resolver.resolve.assert_called_once_with(
            prefix="PG", session=session, credential_profile="analytics-readonly",
        )

    def test_session_none_safe(self, driver):
        """params_for(session=None) does not raise even with a resolver present."""
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=None)
        app = {"credential_resolver": resolver}
        assert driver.params_for(session=None, app=app) == driver.params()
        resolver.resolve.assert_called_once_with(
            prefix="PG", session=None, credential_profile=None,
        )


class TestCredentialPrefix:
    def test_pg_default(self):
        """pgDriver.credential_prefix == 'PG'."""
        from querysource.datasources.drivers.pg import pgDriver as _pg
        assert _pg.credential_prefix == "PG"

    def test_postgres(self):
        """postgresDriver.credential_prefix == 'PG'."""
        from querysource.datasources.drivers.postgres import postgresDriver
        assert postgresDriver.credential_prefix == "PG"

    def test_pg_admin(self):
        """pg_adminDriver.credential_prefix == 'DB'."""
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        assert pg_adminDriver.credential_prefix == "DB"
