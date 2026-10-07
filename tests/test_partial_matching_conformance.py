"""FEAT-180 conformance: four dialects, both builders, and all table operators."""
from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest
import sqlglot

# navconfig probes Logstash during import; the test environment denies sockets.
logging.Logger.notice = logging.Logger.info
try:
    import navconfig.logging  # noqa: F401
except Exception:
    fake_navconfig_logging = types.ModuleType("navconfig.logging")
    fake_navconfig_logging.logging = logging
    sys.modules["navconfig.logging"] = fake_navconfig_logging

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod, pgsql, sql as sqlmod, sqlserver as mssqlmod
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser


SQL = "SELECT n FROM t {where_cond}"
OPERATORS = tuple(PARTIAL_MATCH_OPERATORS)
OPERAND_CASES = (
    ("plain", "andre"),
    ("wildcard_chars", "an_re%"),
    ("quote", "o'brien"),
    ("backslash", r"a\b"),
    ("bang", "a!b"),
    ("bracket", "a[b"),
    ("non_str", 123),
    ("too_short", "ab"),
    ("multi_key", None),
)

DIALECTS = {
    "pg": (pgSQLParser, pgsql, "pgsql_filter_conditions", "postgres", True),
    "sql": (SQLParser, sqlmod, "filter_conditions", "mysql", False),
    "mssql": (msSQLParser, mssqlmod, "mssql_filter_conditions", "tsql", False),
    "bq": (BigQueryParser, bqmod, "bq_filter_conditions", "bigquery", False),
}


def _make_parser(dialect: str, filter_: dict[str, Any]) -> Any:
    """Build a parser with the supplied filter state."""
    parser_cls = DIALECTS[dialect][0]
    parser = parser_cls(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


def _where_body(sql: str) -> str:
    """Return the expression after the WHERE keyword."""
    return sql.split(" WHERE ", 1)[1].strip()


def _quote(dialect: str, value: str) -> str:
    """Render a literal using the independently specified dialect rules."""
    if dialect == "bq":
        value = value.replace("\\", "\\\\")
        return f'"{value.replace(chr(34), chr(92) + chr(34))}"'
    value = value.replace("'", "''")
    if dialect == "sql":
        value = value.replace("\\", "\\\\")
    if dialect == "pg" and ("\\" in value or "{" in value or "}" in value):
        value = value.replace("\\", "\\\\").replace("{", "\\x7b").replace("}", "\\x7d")
        return f"E'{value}'"
    return f"'{value}'"


def _escape(dialect: str, value: str) -> str:
    """Escape a wildcard operand according to the rendered dialect contract."""
    if dialect == "pg" or dialect == "bq":
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")


def _expected(dialect: str, operator: str, operand: str) -> str:
    """Build the expected SQL form from the spec's operator semantics."""
    entry = PARTIAL_MATCH_OPERATORS[operator]
    if entry.kind == "regex":
        if dialect != "pg":
            raise ParserError("regex operators are not supported by this query parser")
        token = "!~*" if entry.negated and entry.insensitive else "!~" if entry.negated else "~*" if entry.insensitive else "~"
        return f"n {token} {_quote(dialect, operand)}"
    pattern = operand if not entry.escape else _escape(dialect, operand)
    pattern = f"{entry.prefix}{pattern}{entry.suffix}"
    literal = _quote(dialect, pattern)
    token = "NOT LIKE" if entry.negated else "LIKE"
    if entry.insensitive and dialect == "pg":
        token = "NOT ILIKE" if entry.negated else "ILIKE"
        return f"n {token} {literal}"
    if entry.insensitive:
        rendered = f"LOWER(n) {token} LOWER({literal})"
    else:
        rendered = f"n {token} {literal}"
    if entry.escape and dialect in {"sql", "mssql"}:
        rendered += " ESCAPE '!'"
    return rendered


def _filter_for_case(operator: str, kind: str, operand: object) -> dict[str, Any]:
    """Return one matrix filter, including the deliberate invalid shapes."""
    if kind == "multi_key":
        return {"n": {operator: "andre", ">=": "5"}}
    return {"n": {operator: operand}}


async def _render(dialect: str, path: str, filter_: dict[str, Any]) -> str:
    """Render through the selected Rust or Cython path."""
    _, module, rust_name, _, _ = DIALECTS[dialect]
    if path == "rust":
        return getattr(module._rs, rust_name)(SQL, filter_, {})
    parser = _make_parser(dialect, filter_)
    if dialect == "sql":
        return await parser.filter_conditions(SQL)
    return await parser._filter_conditions_cy(SQL)


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_extension_is_fresh(dialect: str) -> None:
    """The staged Rust extension exposes the FEAT-180 operators for every dialect."""
    _, module, rust_name, _, _ = DIALECTS[dialect]
    assert module.HAS_RUST
    assert "n LIKE" in getattr(module._rs, rust_name)(SQL, {"n": {"startswith": "andre"}}, {})


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
@pytest.mark.parametrize("path", ["rust", "cython"])
@pytest.mark.parametrize("operator", OPERATORS)
@pytest.mark.parametrize("kind,operand", OPERAND_CASES)
async def test_conformance_matrix(
    dialect: str, path: str, operator: str, kind: str, operand: object
) -> None:
    """All operators agree across paths for valid operands and reject invalid shapes."""
    actual_operand = "andre" if kind == "multi_key" else operand
    filter_ = _filter_for_case(operator, kind, actual_operand)
    invalid = kind in {"non_str", "multi_key"} or (
        kind == "too_short" and PARTIAL_MATCH_OPERATORS[operator].min_length
    ) or (PARTIAL_MATCH_OPERATORS[operator].kind == "regex" and dialect != "pg")
    if invalid:
        with pytest.raises(ParserError):
            if path == "rust":
                await _make_parser(dialect, filter_).filter_conditions(SQL)
            else:
                await _render(dialect, path, filter_)
        return
    expected = _expected(dialect, operator, str(actual_operand))
    assert _where_body(await _render(dialect, path, filter_)) == expected


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
@pytest.mark.parametrize("use_rust", [True, False])
async def test_set_where_raises_before_builder(dialect: str, use_rust: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Contains prevalidation raises consistently regardless of the selected path."""
    _, module, _, _, _ = DIALECTS[dialect]
    monkeypatch.setattr(module, "HAS_RUST", use_rust)
    parser = _make_parser(dialect, {})
    with pytest.raises(ParserError, match="at least 3 characters"):
        await parser.set_where({"n": {"contains": "ab"}}, None)


async def test_set_where_regex_requires_postgres() -> None:
    """Regex is accepted only by PostgreSQL's pre-dispatch validator."""
    await _make_parser("pg", {}).set_where({"n": {"regex": "^a"}}, None)
    for dialect in ("sql", "mssql", "bq"):
        with pytest.raises(ParserError, match="regex operators are not supported"):
            await _make_parser(dialect, {}).set_where({"n": {"regex": "^a"}}, None)


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
async def test_filter_options_path_still_validates(dialect: str) -> None:
    """Filters merged by filtering_options still pass through partial validation."""
    parser = _make_parser(dialect, {})
    parser.filter_options = {"n": {"contains": "ab"}}
    parser.filtering_options(SQL)
    with pytest.raises(ParserError, match="at least 3 characters"):
        await parser.filter_conditions(SQL)


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
@pytest.mark.parametrize("operator", ["startswith", "icontains", "not_istartswith"])
async def test_build_query_sqlglot_valid(dialect: str, operator: str) -> None:
    """Complete query building produces SQL accepted by the corresponding dialect parser."""
    parser = _make_parser(dialect, {"n": {operator: "andre"}})
    rendered = await parser.build_query()
    sqlglot.parse_one(rendered, read=DIALECTS[dialect][3])


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
@pytest.mark.parametrize("operator", OPERATORS)
async def test_rust_and_cython_agree(
    dialect: str, operator: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rust and Cython render identical WHERE bodies for the complete valid corpus."""
    if PARTIAL_MATCH_OPERATORS[operator].kind == "regex" and dialect != "pg":
        filter_ = {"n": {operator: "andre"}}
        module = DIALECTS[dialect][1]
        for use_rust in (True, False):
            monkeypatch.setattr(module, "HAS_RUST", use_rust)
            with pytest.raises(ParserError, match="regex operators are not supported"):
                await _make_parser(dialect, filter_).filter_conditions(SQL)
        return
    filter_ = {"n": {operator: "andre"}}
    rust = _where_body(await _render(dialect, "rust", filter_))
    cython = _where_body(await _render(dialect, "cython", filter_))
    assert rust == cython
