"""FEAT-165: Cython builders — type lookup, anchored BETWEEN, comparison dicts."""
from __future__ import annotations

import logging as stdlib_logging
import sys
import types
from typing import Any

import pytest

# ``navconfig.logging`` probes Logstash during import. Sandboxed runners deny
# sockets, so retain a minimal fallback while using the real module elsewhere.
try:
    import navconfig.logging  # noqa: F401
except Exception:
    stdlib_logging.Logger.notice = stdlib_logging.Logger.info
    fake_navconfig_logging = types.ModuleType("navconfig.logging")
    fake_navconfig_logging.logging = stdlib_logging
    sys.modules["navconfig.logging"] = fake_navconfig_logging

from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers import pgsql as pgmod
from querysource.parsers import sql as sqlmod
from querysource.parsers import sqlserver as mssqlmod
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond}"


def _parser(parser_type: type, filter_: dict[str, Any], cond_definition: dict[str, str] | None = None) -> Any:
    """Build a parser with filtering state assigned directly."""
    parser = parser_type(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = cond_definition or {}
    parser.filter = filter_
    return parser


def _where_body(sql: str) -> str:
    """Return the rendered WHERE expression."""
    return sql.split(" WHERE ", 1)[1].strip()


@pytest.mark.parametrize(
    ("module", "parser_type", "method"),
    [
        (pgmod, pgSQLParser, "_filter_conditions_cy"),
        (sqlmod, SQLParser, "filter_conditions"),
        (bqmod, BigQueryParser, "filter_conditions"),
    ],
)
async def test_comparison_dicts_render_every_operator(
    monkeypatch: pytest.MonkeyPatch, module: Any, parser_type: type, method: str
) -> None:
    """Comparison dictionaries preserve every operator in insertion order."""
    monkeypatch.setattr(module, "HAS_RUST", False)
    parser = _parser(parser_type, {"x": {">": "'1'", "<": "'9'"}})
    sql = await getattr(parser, method)(SQL)
    quote = '"' if parser_type is BigQueryParser else "'"
    assert _where_body(sql) == f"(x > {quote}1{quote} AND x < {quote}9{quote})"


@pytest.mark.parametrize(
    ("module", "parser_type", "method", "expected"),
    [
        (pgmod, pgSQLParser, "_filter_conditions_cy", "x > '1'"),
        (sqlmod, SQLParser, "filter_conditions", "x > '1'"),
        (bqmod, BigQueryParser, "filter_conditions", 'x > "1"'),
    ],
)
async def test_single_comparison_operator_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, module: Any, parser_type: type, method: str, expected: str
) -> None:
    """A one-operator comparison dictionary retains its historic rendering."""
    monkeypatch.setattr(module, "HAS_RUST", False)
    parser = _parser(parser_type, {"x": {">": "'1'"}})
    assert _where_body(await getattr(parser, method)(SQL)) == expected


@pytest.mark.parametrize(
    ("module", "parser_type", "method", "expected"),
    [
        (pgmod, pgSQLParser, "_filter_conditions_cy", "note='IN BETWEEN'"),
        (sqlmod, SQLParser, "filter_conditions", "note='IN BETWEEN'"),
        (mssqlmod, msSQLParser, "_filter_conditions_cy", "note = 'IN BETWEEN'"),
        (bqmod, BigQueryParser, "filter_conditions", 'note="IN BETWEEN"'),
    ],
)
async def test_between_text_is_plain_equality(
    monkeypatch: pytest.MonkeyPatch, module: Any, parser_type: type, method: str, expected: str
) -> None:
    """A value merely containing BETWEEN is not interpreted as a range clause."""
    monkeypatch.setattr(module, "HAS_RUST", False)
    parser = _parser(parser_type, {"note": "IN BETWEEN"})
    assert _where_body(await getattr(parser, method)(SQL)) == expected


@pytest.mark.parametrize(
    ("module", "parser_type", "method"),
    [
        (pgmod, pgSQLParser, "_filter_conditions_cy"),
        (sqlmod, SQLParser, "filter_conditions"),
        (mssqlmod, msSQLParser, "_filter_conditions_cy"),
        (bqmod, BigQueryParser, "filter_conditions"),
    ],
)
async def test_not_between_is_preserved(
    monkeypatch: pytest.MonkeyPatch, module: Any, parser_type: type, method: str
) -> None:
    """Canonical NOT BETWEEN strings retain the guarded range rendering."""
    monkeypatch.setattr(module, "HAS_RUST", False)
    parser = _parser(parser_type, {"amount": "NOT BETWEEN 1 AND 5"})
    assert _where_body(await getattr(parser, method)(SQL)) == "(amount NOT BETWEEN 1 AND 5)"


async def test_pg_type_lookup_uses_base_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suffix-bearing array key retrieves its definition from its base key."""
    monkeypatch.setattr(pgmod, "HAS_RUST", False)
    parser = _parser(pgSQLParser, {"tags|": ["a", "b"]}, {"tags": "array"})
    sql = await parser._filter_conditions_cy(SQL)
    assert "&&" in _where_body(sql)


async def test_mssql_type_lookup_uses_base_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suffix-bearing SQL Server key retrieves its type from its base key."""
    monkeypatch.setattr(mssqlmod, "HAS_RUST", False)
    parser = _parser(msSQLParser, {"created|": ["2024-01-01", "2024-01-31"]}, {"created": "date"})
    assert _where_body(await parser._filter_conditions_cy(SQL)) == "created| BETWEEN '2024-01-01' AND '2024-01-31'"
