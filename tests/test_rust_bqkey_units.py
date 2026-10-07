"""FEAT-162: cargo unit tests of the BigQuery Rust key validators (test_bqkey_*)."""

from rust_cargo_runner import run_cargo_lib_tests


def test_bqkey_cargo_units() -> None:
    """At least one test_bqkey_* cargo test ran and all of them passed."""
    assert run_cargo_lib_tests("tests::test_bqkey_") >= 1
