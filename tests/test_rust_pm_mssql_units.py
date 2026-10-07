"""FEAT-180: cargo unit tests of the mssql Rust builder (test_pm_mssql_*)."""
from rust_cargo_runner import run_cargo_lib_tests


def test_pm_mssql_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_mssql_") >= 1
