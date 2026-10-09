"""FEAT-165: full-pipeline rendering, Rust and Cython paths agree."""
from __future__ import annotations

import logging as stdlib_logging
import re
import sys
import types
from typing import Any

import pytest

# ``navconfig.logging`` probes Logstash while importing parser modules.  Tests
# run in a socket-restricted sandbox, so use the same local fallback as the
# dialect-specific suites.
try:
    import navconfig.logging  # noqa: F401
except Exception:
    stdlib_logging.Logger.notice = stdlib_logging.Logger.info
    fake_navconfig_logging = types.ModuleType("navconfig.logging")
    fake_navconfig_logging.logging = stdlib_logging
    sys.modules["navconfig.logging"] = fake_navconfig_logging

from querysource.exceptions import ParserError
from querysource.parsers import QS_VARIABLES, bigquery, pgsql, sql, sqlserver
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM public.t {where_cond}"


class _Connection:
    """Minimal asynchronous connection context manager for parser setup."""

    async def __aenter__(self) -> None:
        """Return the no-op connection."""
        return None

    async def __aexit__(self, *args: object) -> None:
        """Close the no-op connection."""
        return None


class _Redis:
    """Minimal Redis facade used by the full preprocessing pipeline."""

    async def connection(self) -> _Connection:
        """Return a no-op asynchronous connection context manager."""
        return _Connection()


def _rust_current() -> bool:
    """Return whether the staged extension includes FEAT-165 multi-operator support."""
    if not pgsql.HAS_RUST:
        return False
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"x": {">": "'1'", "<": "'9'"}}, {})
    return "(x > '1' AND x < '9')" in out


PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(
            not _rust_current(), reason="stale _qs_parsers: run `make build-rust && make stage-rust`"
        ),
    ),
    "cython",
]


def _where_body(rendered: str) -> str | None:
    """Return the exact predicate after the query's WHERE clause."""
    if " WHERE " not in rendered:
        return None
    return rendered.split(" WHERE ", 1)[1].strip()


def _numeric_literals(predicate: str | None) -> str | None:
    """Unquote plain numeric literals: ``'1'`` and ``1`` are equivalent SQL operands."""
    if predicate is None:
        return None
    return re.sub(r"(?<![\w'])(['\"])(-?\d+(?:\.\d+)?)\1", r"\2", predicate)


async def render(
    path: str,
    conditions: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    parser_cls: type = pgSQLParser,
    module: object = pgsql,
    query: str = SQL,
) -> str:
    """Run ``set_options()`` and ``build_query()`` on a selected parser path."""
    if path == "cython":
        monkeypatch.setattr(module, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions=dict(conditions), query=query)
    parser._redis = _Redis()
    await parser.set_options()
    return await parser.build_query()


PG_CORPUS = [
    ({"filter": {"amount": "BETWEEN 100 AND 500"}}, "(amount BETWEEN 100 AND 500)"),
    (
        {"filter": {"created_at": "BETWEEN '2025-01-01' AND 2025-12-31"}},
        "(created_at BETWEEN '2025-01-01' AND '2025-12-31')",
    ),
    ({"filter": {"amount!": "BETWEEN 1 AND 5"}}, "(amount NOT BETWEEN 1 AND 5)"),
    ({"filter": {"note": "IN BETWEEN"}}, "note='IN BETWEEN'"),
    (
        {"filter": {"attributes": {"status": "active", "tier": "gold"}}},
        "attributes @> E'\\x7b\"status\":\"active\",\"tier\":\"gold\"\\x7d'::jsonb",
    ),
    ({"filter": {"attributes": {"@>": {"status": "active"}}}}, "attributes @> E'\\x7b\"status\":\"active\"\\x7d'::jsonb"),
    (
        {"filter": {"attributes": {"@>|": [{"status": "active"}, {"tier": "gold"}]}}},
        "(attributes @> E'\\x7b\"status\":\"active\"\\x7d'::jsonb OR attributes @> E'\\x7b\"tier\":\"gold\"\\x7d'::jsonb)",
    ),
    (
        {"filter": {"attributes": {"@!": [{"status": "active"}, {"tier": "gold"}]}}},
        "NOT (attributes @> E'\\x7b\"status\":\"active\"\\x7d'::jsonb OR attributes @> E'\\x7b\"tier\":\"gold\"\\x7d'::jsonb)",
    ),
    (
        {"filter": {"attributes": {"@$": [{"status": "active"}, {"tier": "gold"}]}}},
        "((NOT attributes @> E'\\x7b\"status\":\"active\"\\x7d'::jsonb) OR (NOT attributes @> E'\\x7b\"tier\":\"gold\"\\x7d'::jsonb))",
    ),
    ({"filter": {"attributes": {"->>": {"status": "active"}}}}, "attributes ->> 'status' = 'active'"),
    ({"filter": {"attributes": {"->": {"status": "active"}}}}, "attributes -> 'status' = '\"active\"'::jsonb"),
    ({"filter": {"x": {">": 1, "<": 9}}}, "(x > '1' AND x < '9')"),
    ({"filter": {"qty": {">": 0}}}, "qty > '0'"),
    ({"filter": {"name": {"startswith": "andre"}}}, "name LIKE 'andre%'"),
]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("conditions,expected", PG_CORPUS)
async def test_pg_full_pipeline(
    path: str, conditions: dict[str, Any], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostgreSQL integration cases render the documented predicate on both paths."""
    assert _numeric_literals(_where_body(await render(path, conditions, monkeypatch))) == _numeric_literals(expected)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    ("parser_cls", "module", "expected"),
    [
        (SQLParser, sql, "(amount BETWEEN 100 AND 500)"),
        (msSQLParser, sqlserver, "(amount BETWEEN 100 AND 500)"),
        (BigQueryParser, bigquery, "(amount BETWEEN 100 AND 500)"),
    ],
)
async def test_other_dialects_render_between(
    path: str, parser_cls: type, module: object, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic SQL, SQL Server, and BigQuery preserve canonical BETWEEN clauses."""
    rendered = await render(path, {"filter": {"amount": "BETWEEN 100 AND 500"}}, monkeypatch, parser_cls, module)
    assert _where_body(rendered) == expected


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    ("parser_cls", "module", "expected"),
    [
        (SQLParser, sql, "(x > '1' AND x < '9')"),
        (BigQueryParser, bigquery, '(x > "1" AND x < "9")'),
    ],
)
async def test_other_dialects_render_all_comparison_operators(
    path: str, parser_cls: type, module: object, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic SQL and BigQuery retain each comparison-dict member in order."""
    rendered = await render(path, {"filter": {"x": {">": 1, "<": 9}}}, monkeypatch, parser_cls, module)
    assert _numeric_literals(_where_body(rendered)) == _numeric_literals(expected)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        ({"filter": {"tags": "vip"}, "cond_definition": {"tags": "array"}}, "'vip'::character varying = ANY(tags)"),
        ({"filter": {"tags": ["a", "b"]}, "cond_definition": {"tags": "array"}}, "ARRAY['a','b']::character varying[]  <@ tags::character varying[]"),
        ({"filter": {"tags|": ["a", "b"]}, "cond_definition": {"tags": "array"}}, "ARRAY['a','b']::character varying[]  && tags::character varying[]"),
        ({"filter": {"score": "5"}, "cond_definition": {"score": "numrange"}}, "5::numeric <@ score"),
        ({"filter": {"rank": "5"}, "cond_definition": {"rank": "int4range"}}, "5::integer <@ rank::int4range"),
        ({"filter": {"seen": "2025-01-01"}, "cond_definition": {"seen": "tstzrange"}}, "'2025-01-01'::timestamptz <@ seen::tstzrange"),
        ({"filter": {"day": "2025-01-01"}, "cond_definition": {"day": "daterange"}}, "'2025-01-01'::date <@ day::daterange"),
        ({"filter": {"d": ["2025-01-01", "2025-02-01"]}, "cond_definition": {"d": "date"}}, "d BETWEEN '2025-01-01' AND '2025-02-01'"),
        ({"filter": {"d!": ["2025-01-01", "2025-02-01"]}, "cond_definition": {"d": "date"}}, "d NOT BETWEEN '2025-01-01' AND '2025-02-01'"),
    ],
)
async def test_pg_typed_filters_full_pipeline(
    path: str, conditions: dict[str, Any], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed filters retain their PostgreSQL builder-specific rendering."""
    assert _where_body(await render(path, conditions, monkeypatch)) == expected


async def test_typed_placeholder_and_flat_key_keep_placeholder_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template placeholders and flat typed keys preserve their legacy handling."""
    placeholder = await render(
        "cython",
        {"filter": {"since": "2025-01-01"}, "cond_definition": {"since": "date"}},
        monkeypatch,
        query="SELECT * FROM public.t WHERE created_at >= {since} {and_cond}",
    )
    flat = await render(
        "cython",
        {"tags": "vip", "cond_definition": {"tags": "array"}},
        monkeypatch,
    )
    assert _where_body(placeholder) == "created_at >= '2025-01-01'"
    assert flat == "SELECT * FROM public.t "


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "conditions",
    [
        {"filter": {"amount": "BETWEEN 1 OR 5"}},
        {"filter": {"amount": "BETWEEN 1 AND 2", "amount!": "BETWEEN 3 AND 4"}},
        {"filter": {"d": "@nope"}},
    ],
)
async def test_full_pipeline_errors_are_parser_errors(
    path: str, conditions: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed ranges, key collisions, and unknown variables are client errors."""
    with pytest.raises(ParserError):
        await render(path, conditions, monkeypatch)


async def test_registered_variable_and_caller_filter_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registered variables resolve and caller-owned filters remain untouched."""
    filter_value = {"x": {">": 1, "<": 9}}
    monkeypatch.setitem(QS_VARIABLES, "qs_test_var", lambda key, value: "2025-01-01")
    rendered = await render(
        "cython",
        {"filter": {"d": "@qs_test_var"}, "cond_definition": {"d": "date"}},
        monkeypatch,
    )
    await render("cython", {"filter": filter_value}, monkeypatch)
    assert _where_body(rendered) == "d='2025-01-01'"
    assert filter_value == {"x": {">": 1, "<": 9}}
