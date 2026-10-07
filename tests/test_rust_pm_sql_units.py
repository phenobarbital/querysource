"""FEAT-180: cargo unit tests of the sql Rust builder (test_pm_sql_*)."""

from rust_cargo_runner import run_cargo_lib_tests


def test_pm_sql_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_sql_") >= 1
