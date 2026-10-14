from querysource.datasources.drivers import SUPPORTED
from querysource.semantic.dialects import DRIVER_DIALECTS, NON_SQL_DRIVERS, dialect_for_driver
from querysource.semantic.exceptions import SemanticResourceError


def test_dialect_for_driver_table():
    for driver in SUPPORTED:
        if driver in NON_SQL_DRIVERS:
            try:
                dialect_for_driver(driver)
            except SemanticResourceError as exc:
                assert exc.code == 400
            else:
                raise AssertionError(f"Expected non-SQL driver {driver!r} to be rejected")
        elif driver in DRIVER_DIALECTS:
            assert dialect_for_driver(driver) == (DRIVER_DIALECTS[driver], None)
        else:
            dialect, warning = dialect_for_driver(driver)
            assert dialect == "ansi"
            assert warning and driver in warning
