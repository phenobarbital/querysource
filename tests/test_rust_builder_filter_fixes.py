"""FEAT-165: Rust builders (staged ``_qs_parsers``) — direct calls."""

import pytest

from querysource.parsers import bigquery, pgsql, sql, sqlserver

SQL = "SELECT * FROM t {where_cond}"
pytestmark = pytest.mark.skipif(
    not all((pgsql.HAS_RUST, sql.HAS_RUST, bigquery.HAS_RUST, sqlserver.HAS_RUST)),
    reason="Rust extension not available",
)


def test_pg_multi_operator() -> None:
    """PostgreSQL keeps every comparison operator and joins them with AND."""
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"x": {">": "'1'", "<": "'9'"}}, {})
    assert "(x > '1' AND x < '9')" in out


def test_pg_typed_string_and_suffix() -> None:
    """Typed array values are quoted and suffixes resolve their format hint."""
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"tags|": "vip"}, {"tags": "array"})
    assert "'vip'::character varying = ANY(tags)" in out


@pytest.mark.parametrize("value", ["BETWEEN 100 AND 500", "NOT BETWEEN 500 AND 100"])
def test_canonical_between_is_rendered_verbatim(value: str) -> None:
    """Canonical numeric BETWEEN clauses are not quoted as one string."""
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"amount": value}, {})
    assert f"(amount {value})" in out


def test_between_word_inside_value_is_equality() -> None:
    """A quoted value containing BETWEEN does not enter the BETWEEN branch."""
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"note": "contains BETWEEN safely"}, {})
    assert "note='contains BETWEEN safely'" in out


def test_sql_comparison_dict_is_anded() -> None:
    """Generic SQL keeps every comparison operator."""
    out = sql._rs.filter_conditions(SQL, {"x": {">": "1", "<": "9"}}, {})
    assert "(x > 1 AND x < 9)" in out


def test_bigquery_comparison_dict_is_anded() -> None:
    """BigQuery quotes each comparison operand with its dialect literal."""
    out = bigquery._rs.bq_filter_conditions(SQL, {"x": {">": "1", "<": "9"}}, {})
    assert '(x > "1" AND x < "9")' in out


def test_mssql_canonical_between() -> None:
    """The shared SQL Server path preserves canonical BETWEEN text."""
    out = sqlserver._rs.mssql_filter_conditions(SQL, {"amount": "BETWEEN 100 AND 500"}, {})
    assert "(amount BETWEEN 100 AND 500)" in out
