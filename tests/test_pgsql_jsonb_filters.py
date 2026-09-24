"""JSONB filter conditions for the PostgreSQL parser.

A dict-typed filter value on a PostgreSQL query renders a JSONB condition
(``@>``, ``<@``, ``->``, ``->>``). Both code paths are exercised with the same
cases: the Rust fast-path (``_qs_parsers.pgsql_filter_conditions``) and the
Cython fallback (``pgSQLParser._filter_conditions_cy``). JSON is serialized
with orjson (compact form), and literals containing braces are emitted as
``E'...'`` strings with ``\\x7b``/``\\x7d`` so later ``format_map`` passes of
``build_query`` do not treat them as placeholders.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
import sqlglot

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"

PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built"),
    ),
    "cython",
]


def _make_parser(query: str, filter_: dict) -> pgSQLParser:
    """Build a ``pgSQLParser`` bypassing ``set_options`` (needs Redis)."""
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict, query: str = SQL) -> str:
    """Render ``query`` with ``filter_`` through the requested code path."""
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(query, filter_, {})
    return await _make_parser(query, filter_)._filter_conditions_cy(query)


def _where(sql: str) -> Optional[str]:
    """Return the WHERE clause body, or None when no condition was rendered."""
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", [
    # implicit containment: plain keys are the JSON document to match
    (
        {"attrs": {"status": "active"}},
        """attrs @> E'\\x7b"status":"active"\\x7d'::jsonb""",
    ),
    (
        {"attrs": {"status": "active", "level": 2}},
        """attrs @> E'\\x7b"status":"active","level":2\\x7d'::jsonb""",
    ),
    # explicit @> with a dict / list / JSON text operand
    (
        {"attrs": {"@>": {"tags": ["a", "b"]}}},
        """attrs @> E'\\x7b"tags":["a","b"]\\x7d'::jsonb""",
    ),
    ({"tags": {"@>": ["x"]}}, """tags @> '["x"]'::jsonb"""),
    (
        {"attrs": {"@>": '{"status": "active"}'}},
        """attrs @> E'\\x7b"status":"active"\\x7d'::jsonb""",
    ),
    # <@ (contained by)
    (
        {"attrs": {"<@": {"a": 1, "b": 2}}},
        """attrs <@ E'\\x7b"a":1,"b":2\\x7d'::jsonb""",
    ),
    # ->> compares the key text; non-strings by their JSON text
    ({"attrs": {"->>": {"status": "active"}}}, "attrs ->> 'status' = 'active'"),
    ({"attrs": {"->>": {"count": 3}}}, "attrs ->> 'count' = '3'"),
    ({"attrs": {"->>": {"enabled": True}}}, "attrs ->> 'enabled' = 'true'"),
    ({"attrs": {"->>": {"status": None}}}, "attrs ->> 'status' IS NULL"),
    (
        {"attrs": {"->>": {"a": 1, "b": "x"}}},
        "(attrs ->> 'a' = '1' AND attrs ->> 'b' = 'x')",
    ),
    # -> compares the JSONB value
    ({"attrs": {"->": {"status": "active"}}}, """attrs -> 'status' = '"active"'::jsonb"""),
    (
        {"attrs": {"->": {"meta": {"k": 1}}}},
        """attrs -> 'meta' = E'\\x7b"k":1\\x7d'::jsonb""",
    ),
])
async def test_jsonb_condition(path: str, filter_: dict, expected: str) -> None:
    assert _where(await _render(path, filter_)) == expected


@pytest.mark.parametrize("path", PATHS)
async def test_jsonb_values_are_escaped(path: str) -> None:
    """Quotes, backslashes and braces inside values stay inside the literal."""
    filter_ = {"attrs": {"name": "x'; DROP TABLE t; --", "path": "a\\b{c}"}}
    where = _where(await _render(path, filter_))
    assert where == (
        """attrs @> E'\\x7b"name":"x''; DROP TABLE t; --","""
        """"path":"a\\\\\\\\b\\x7bc\\x7d"\\x7d'::jsonb"""
    )


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_", [
    {"attrs": {"@>": "not json"}},  # invalid JSON text
    {"attrs": {"@>": {"a": 1}, "status": "x"}},  # operators mixed with keys
    {"attrs": {"->>": "status"}},  # ->> needs a {path: value} mapping
    {"attrs": {"->": {}}},  # empty mapping
    {"attrs;drop": {"status": "x"}},  # unsafe column identifier
])
async def test_invalid_jsonb_filters_are_dropped(path: str, filter_: dict) -> None:
    assert _where(await _render(path, filter_)) is None


@pytest.mark.parametrize("path", PATHS)
async def test_comparison_token_dict_still_supported(path: str) -> None:
    where = _where(await _render(path, {"age": {">=": 18}}))
    assert where is not None and where.startswith("age >= ")


@pytest.mark.parametrize("path", PATHS)
async def test_jsonb_filter_combined_with_scalar(path: str) -> None:
    where = _where(await _render(path, {"attrs": {"status": "active"}, "store_id": 5}))
    assert where is not None
    conditions = where.split(" AND ")
    assert len(conditions) == 2
    assert """attrs @> E'\\x7b"status":"active"\\x7d'::jsonb""" in conditions


@pytest.mark.parametrize("path", PATHS)
async def test_filter_dict_is_not_mutated(path: str) -> None:
    filter_: dict[str, Any] = {"attrs": {"status": "active"}}
    await _render(path, filter_)
    assert filter_ == {"attrs": {"status": "active"}}


@pytest.mark.parametrize("use_rust", [
    pytest.param(
        True, marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built")
    ),
    False,
])
async def test_build_query_survives_format_passes(use_rust: bool, monkeypatch) -> None:
    """The full ``build_query`` pipeline (limit, cleanup) keeps the JSONB literal."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make_parser(SQL, {"attrs": {"@>": {"status": "active"}}})
    sql = await parser.build_query(querylimit=10)
    assert """attrs @> E'\\x7b"status":"active"\\x7d'::jsonb""" in sql
    assert "LIMIT 10" in sql
    assert "{" not in sql and "}" not in sql
    sqlglot.parse_one(sql, read="postgres")
