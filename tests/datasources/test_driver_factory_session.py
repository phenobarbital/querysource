"""Tests for driver factory session plumbing (TASK-637).

Verifies that Connection.datasource() and Connection.default_driver()
call params_for(session, app) when a session and app are supplied, and
fall back to params() otherwise.
"""
import pytest
from unittest.mock import MagicMock, patch
from asyncdb.exceptions import ProviderError, DriverError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pg_driver_mock(params_return=None):
    """Build a mock that looks like a pgDriver instance with params_for."""
    drv = MagicMock()
    drv.driver_type = 'asyncdb'
    drv.driver = 'pg'
    drv.dsn = 'postgres://u:p@h:5432/db'
    drv.params = MagicMock(return_value={"host": "default-h", "port": 5432,
                                          "username": "u", "password": "p",
                                          "database": "db"})
    drv.params_for = MagicMock(return_value=params_return or {
        "host": "resolved-h", "port": 6543,
        "username": "ru", "password": "rp", "database": "rd",
    })
    return drv


def _make_mysql_driver_mock():
    """Build a mock that looks like a MySQL driver (no params_for)."""
    drv = MagicMock()
    drv.driver_type = 'asyncdb'
    drv.driver = 'mysql'
    drv.dsn = 'mysql://u:p@h:3306/db'
    drv.params = MagicMock(return_value={"host": "h", "port": 3306,
                                          "username": "u", "password": "p",
                                          "database": "db"})
    # No params_for attribute
    del drv.params_for
    return drv


# ---------------------------------------------------------------------------
# Tests for Connection.datasource()
# ---------------------------------------------------------------------------

class TestDatasourceSessionPlumbing:
    """Unit tests for Connection.datasource() FEAT-091 plumbing."""

    @pytest.mark.asyncio
    async def test_pg_uses_params_for_when_session_and_app_present(self):
        """When session+app are provided and driver has params_for, use it."""
        from querysource.interfaces.connections import Connection, DATASOURCES

        pg_drv = _make_pg_driver_mock()
        DATASOURCES['_test_pg'] = pg_drv
        try:
            conn = Connection.__new__(Connection)
            conn.logger = MagicMock()

            session = MagicMock()
            app = {"credential_resolver": MagicMock()}

            with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                MockAsyncDB.return_value = MagicMock()
                result = await conn.datasource('_test_pg', session=session, app=app)

            pg_drv.params_for.assert_called_once_with(session, app)
            pg_drv.params.assert_not_called()
        finally:
            DATASOURCES.pop('_test_pg', None)

    @pytest.mark.asyncio
    async def test_pg_falls_back_to_params_when_no_session(self):
        """When session is None, params() is called (legacy path)."""
        from querysource.interfaces.connections import Connection, DATASOURCES

        pg_drv = _make_pg_driver_mock()
        DATASOURCES['_test_pg2'] = pg_drv
        try:
            conn = Connection.__new__(Connection)
            conn.logger = MagicMock()

            with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                MockAsyncDB.return_value = MagicMock()
                await conn.datasource('_test_pg2', session=None, app={"credential_resolver": MagicMock()})

            pg_drv.params.assert_called_once()
            pg_drv.params_for.assert_not_called()
        finally:
            DATASOURCES.pop('_test_pg2', None)

    @pytest.mark.asyncio
    async def test_pg_falls_back_to_params_when_no_app(self):
        """When app is None, params() is called (legacy path)."""
        from querysource.interfaces.connections import Connection, DATASOURCES

        pg_drv = _make_pg_driver_mock()
        DATASOURCES['_test_pg3'] = pg_drv
        try:
            conn = Connection.__new__(Connection)
            conn.logger = MagicMock()
            session = MagicMock()

            with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                MockAsyncDB.return_value = MagicMock()
                await conn.datasource('_test_pg3', session=session, app=None)

            pg_drv.params.assert_called_once()
            pg_drv.params_for.assert_not_called()
        finally:
            DATASOURCES.pop('_test_pg3', None)

    @pytest.mark.asyncio
    async def test_non_pg_always_uses_params(self):
        """A MySQL driver (no params_for) always calls params()."""
        from querysource.interfaces.connections import Connection, DATASOURCES

        mysql_drv = _make_mysql_driver_mock()
        DATASOURCES['_test_mysql'] = mysql_drv
        try:
            conn = Connection.__new__(Connection)
            conn.logger = MagicMock()
            session = MagicMock()
            app = {"credential_resolver": MagicMock()}

            with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                MockAsyncDB.return_value = MagicMock()
                await conn.datasource('_test_mysql', session=session, app=app)

            mysql_drv.params.assert_called_once()
            assert not hasattr(mysql_drv, 'params_for')
        finally:
            DATASOURCES.pop('_test_mysql', None)


# ---------------------------------------------------------------------------
# Tests for Connection.default_driver()
# ---------------------------------------------------------------------------

class TestDefaultDriverSessionPlumbing:
    """Unit tests for Connection.default_driver() FEAT-091 plumbing."""

    @pytest.mark.asyncio
    async def test_pg_uses_params_for_when_session_and_app_present(self):
        """When session+app are present, default_driver calls params_for."""
        from querysource.interfaces.connections import Connection, DRIVERS_CACHE

        pg_drv = _make_pg_driver_mock()
        DRIVERS_CACHE['querysource.datasources.drivers._test_drv'] = pg_drv
        try:
            conn = Connection.__new__(Connection)
            conn.logger = MagicMock()

            def _supported(drv):
                return True
            conn.supported_drivers = _supported

            session = MagicMock()
            app = {"credential_resolver": MagicMock()}

            import_path = 'querysource.interfaces.connections.import_module'
            drv_module = MagicMock()
            drv_module._test_drv_default = pg_drv

            with patch(import_path, return_value=drv_module):
                with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                    MockAsyncDB.return_value = MagicMock()
                    DRIVERS_CACHE.pop('querysource.datasources.drivers._test_drv', None)
                    await conn.default_driver('_test_drv', session=session, app=app)

            pg_drv.params_for.assert_called_once_with(session, app)
            pg_drv.params.assert_not_called()
        finally:
            DRIVERS_CACHE.pop('querysource.datasources.drivers._test_drv', None)

    @pytest.mark.asyncio
    async def test_no_session_falls_back_to_params(self):
        """default_driver with session=None falls back to params()."""
        from querysource.interfaces.connections import Connection, DRIVERS_CACHE

        pg_drv = _make_pg_driver_mock()

        conn = Connection.__new__(Connection)
        conn.logger = MagicMock()

        def _supported(drv):
            return True
        conn.supported_drivers = _supported

        import_path = 'querysource.interfaces.connections.import_module'
        drv_module = MagicMock()
        drv_module._test_drv2_default = pg_drv

        with patch(import_path, return_value=drv_module):
            with patch('querysource.interfaces.connections.AsyncDB') as MockAsyncDB:
                MockAsyncDB.return_value = MagicMock()
                DRIVERS_CACHE.pop('querysource.datasources.drivers._test_drv2', None)
                await conn.default_driver('_test_drv2', session=None, app={"credential_resolver": MagicMock()})

        pg_drv.params.assert_called_once()
        pg_drv.params_for.assert_not_called()
        DRIVERS_CACHE.pop('querysource.datasources.drivers._test_drv2', None)
