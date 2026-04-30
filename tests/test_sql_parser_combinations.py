"""Combinatorial tests for the SQL Parser.

Covers many combinations of the parser inputs (``fields``, ``filter``,
``group_by``, ``order_by``, ``limit``, ``offset``) and verifies that the
generated SQL is syntactically valid by parsing it with ``sqlglot``.

The goal is to catch regressions where new options interact badly with each
other (e.g. an extra GROUP BY appended after LIMIT, an unbalanced WHERE,
trailing placeholders, etc.).
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlglot
from sqlglot.errors import ParseError

from querysource.models import QueryObject
from querysource.parsers.sql import SQLParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parser(query: str, **attrs: Any) -> SQLParser:
    """Instantiate a ``SQLParser`` with attributes set directly.

    ``set_options`` is intentionally bypassed because it requires a Redis
    connection. The parser methods we exercise (``build_query`` and the
    helpers it calls) only depend on the attributes we set here.
    """
    qo = QueryObject(query_raw=query)
    parser = SQLParser(definition=None, conditions=qo, query=query)
    # cond_definition must be a dict; the rest default to empty containers.
    parser.cond_definition = {}
    for key, value in attrs.items():
        setattr(parser, key, value)
    return parser


async def _build(query: str, **attrs: Any) -> str:
    """Build SQL using ``SQLParser.build_query`` for the given inputs."""
    parser = _make_parser(query, **attrs)
    return await parser.build_query()


def _assert_valid_sql(sql: str, dialect: str = "postgres") -> None:
    """Fail if ``sql`` doesn't parse cleanly under ``dialect``.

    Also asserts that no template placeholder leaked through (``{...}``).
    """
    assert sql, "parser produced an empty SQL string"
    assert "{" not in sql and "}" not in sql, (
        f"unresolved template placeholder in SQL: {sql!r}"
    )
    try:
        sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as exc:
        pytest.fail(f"invalid SQL produced: {sql!r}\nsqlglot error: {exc}")


# ---------------------------------------------------------------------------
# Single-option tests
# ---------------------------------------------------------------------------

class TestFields:
    """Combinations focused on ``fields``."""

    @pytest.mark.parametrize(
        "fields",
        [
            ["col1"],
            ["col1", "col2"],
            ["col1", "col2", "col3"],
            ["a", "b AS alias_b", "COUNT(*) AS total"],
        ],
    )
    async def test_fields_replace_star(self, fields: list[str]) -> None:
        sql = await _build("SELECT * FROM t", fields=fields)
        for f in fields:
            assert f in sql
        _assert_valid_sql(sql)

    async def test_fields_with_placeholder(self) -> None:
        sql = await _build(
            "SELECT {fields} FROM t",
            fields=["id", "name"],
        )
        assert "id, name" in sql
        _assert_valid_sql(sql)

    async def test_fields_empty_keeps_star(self) -> None:
        sql = await _build("SELECT * FROM t")
        assert "SELECT * FROM t" in sql
        _assert_valid_sql(sql)


class TestFilter:
    """Combinations focused on ``filter`` (the WHERE clause)."""

    async def test_filter_simple_equality(self) -> None:
        sql = await _build("SELECT * FROM t", filter={"name": "John"})
        assert "WHERE" in sql
        assert "name='John'" in sql
        _assert_valid_sql(sql)

    async def test_filter_is_null(self) -> None:
        sql = await _build("SELECT * FROM t", filter={"deleted_at": "null"})
        assert "deleted_at IS NULL" in sql
        _assert_valid_sql(sql)

    async def test_filter_is_not_null(self) -> None:
        sql = await _build("SELECT * FROM t", filter={"deleted_at": "!null"})
        assert "deleted_at IS NOT NULL" in sql
        _assert_valid_sql(sql)

    async def test_filter_negation(self) -> None:
        sql = await _build("SELECT * FROM t", filter={"status": "!archived"})
        assert "status != 'archived'" in sql
        _assert_valid_sql(sql)

    async def test_filter_in_list(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            filter={"color": ["red", "blue", "green"]},
        )
        assert "IN (" in sql
        for v in ("red", "blue", "green"):
            assert v in sql
        _assert_valid_sql(sql)

    async def test_filter_comparison_operator(self) -> None:
        sql = await _build(
            "SELECT * FROM t", filter={"amount": {">=": "100"}}
        )
        assert "amount >= 100" in sql
        _assert_valid_sql(sql)

    async def test_filter_multiple_anded(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            filter={"status": "active", "country": "US"},
        )
        assert sql.count("WHERE") == 1
        assert "AND" in sql
        _assert_valid_sql(sql)

    async def test_filter_with_where_placeholder(self) -> None:
        sql = await _build(
            "SELECT * FROM t {where_cond}", filter={"x": "1"}
        )
        assert "WHERE" in sql
        _assert_valid_sql(sql)

    async def test_filter_with_filter_placeholder(self) -> None:
        sql = await _build(
            "SELECT * FROM t {filter}", filter={"x": "1"}
        )
        assert "WHERE" in sql
        _assert_valid_sql(sql)


class TestGroupBy:
    """Combinations focused on ``group_by``."""

    async def test_group_by_single(self) -> None:
        sql = await _build("SELECT a, COUNT(*) FROM t", grouping=["a"])
        assert "GROUP BY a" in sql
        _assert_valid_sql(sql)

    async def test_group_by_multiple(self) -> None:
        sql = await _build(
            "SELECT a, b, COUNT(*) FROM t", grouping=["a", "b"]
        )
        assert "GROUP BY a, b" in sql
        _assert_valid_sql(sql)

    async def test_group_by_extends_existing(self) -> None:
        sql = await _build(
            "SELECT a, b, COUNT(*) FROM t GROUP BY a", grouping=["b"]
        )
        assert "GROUP BY a, b" in sql
        _assert_valid_sql(sql)

    async def test_group_by_skips_inner_cte_group(self) -> None:
        # Regression: GROUP BY inside a CTE must not be modified.
        query = (
            "WITH agg AS ( "
            "    SELECT a, b, SUM(c) AS s FROM t GROUP BY a, b "
            ") "
            "SELECT a, SUM(s) FROM agg"
        )
        sql = await _build(query, grouping=["a"])
        assert "FROM t GROUP BY a, b" in sql
        assert "FROM agg, a" not in sql
        _assert_valid_sql(sql)


class TestOrderBy:
    """Combinations focused on ``order_by``."""

    async def test_order_by_single(self) -> None:
        sql = await _build("SELECT * FROM t", ordering=["created_at DESC"])
        assert "ORDER BY created_at DESC" in sql
        _assert_valid_sql(sql)

    async def test_order_by_multiple(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            ordering=["a ASC", "b DESC"],
        )
        assert "ORDER BY a ASC, b DESC" in sql
        _assert_valid_sql(sql)


class TestLimitOffset:
    """Combinations focused on ``limit`` / ``offset``."""

    async def test_limit_only(self) -> None:
        sql = await _build("SELECT * FROM t", querylimit=10)
        assert "LIMIT 10" in sql
        assert "OFFSET" not in sql
        _assert_valid_sql(sql)

    async def test_offset_only(self) -> None:
        sql = await _build("SELECT * FROM t", _offset=20)
        # No limit set ⇒ build_query takes the "no limit" branch and
        # therefore does NOT emit OFFSET. The query must still be valid.
        _assert_valid_sql(sql)

    async def test_limit_and_offset(self) -> None:
        sql = await _build("SELECT * FROM t", querylimit=10, _offset=20)
        assert "LIMIT 10" in sql
        assert "OFFSET 20" in sql
        _assert_valid_sql(sql)

    async def test_limit_with_placeholder(self) -> None:
        sql = await _build(
            "SELECT * FROM t {limit}", querylimit=50
        )
        assert "LIMIT 50" in sql
        _assert_valid_sql(sql)


# ---------------------------------------------------------------------------
# Pairwise combinations
# ---------------------------------------------------------------------------

class TestPairwiseCombinations:
    """Two-option combinations to flush out interaction bugs."""

    async def test_fields_plus_filter(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            fields=["a", "b"],
            filter={"a": "1"},
        )
        assert "a, b" in sql
        assert "WHERE" in sql
        _assert_valid_sql(sql)

    async def test_filter_plus_group_by(self) -> None:
        sql = await _build(
            "SELECT a, COUNT(*) FROM t",
            filter={"flag": "true"},
            grouping=["a"],
        )
        assert "WHERE" in sql
        assert "GROUP BY a" in sql
        # WHERE must come before GROUP BY in the output
        assert sql.index("WHERE") < sql.index("GROUP BY")
        _assert_valid_sql(sql)

    async def test_group_by_plus_order_by(self) -> None:
        sql = await _build(
            "SELECT a, COUNT(*) AS n FROM t",
            grouping=["a"],
            ordering=["n DESC"],
        )
        assert sql.index("GROUP BY") < sql.index("ORDER BY")
        _assert_valid_sql(sql)

    async def test_order_by_plus_limit(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            ordering=["a ASC"],
            querylimit=5,
        )
        assert sql.index("ORDER BY") < sql.index("LIMIT")
        _assert_valid_sql(sql)

    async def test_limit_plus_offset(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            querylimit=10,
            _offset=5,
        )
        assert sql.index("LIMIT") < sql.index("OFFSET")
        _assert_valid_sql(sql)

    async def test_filter_plus_order_by(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            filter={"x": "1"},
            ordering=["x DESC"],
        )
        assert sql.index("WHERE") < sql.index("ORDER BY")
        _assert_valid_sql(sql)


# ---------------------------------------------------------------------------
# Full-stack combinations (everything at once)
# ---------------------------------------------------------------------------

class TestFullStackCombinations:
    """All six options applied to the same query."""

    async def test_all_options_together(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            fields=["a", "b", "COUNT(*) AS total"],
            filter={"flag": "active"},
            grouping=["a", "b"],
            ordering=["total DESC"],
            querylimit=25,
            _offset=50,
        )
        # Canonical SELECT-statement keyword order.
        for expected in ("WHERE", "GROUP BY", "ORDER BY", "LIMIT", "OFFSET"):
            assert expected in sql, f"missing {expected} in {sql!r}"
        order = ["WHERE", "GROUP BY", "ORDER BY", "LIMIT", "OFFSET"]
        positions = [sql.index(kw) for kw in order]
        assert positions == sorted(positions), (
            f"clauses out of order: {sql!r}"
        )
        _assert_valid_sql(sql)

    async def test_all_options_with_placeholders(self) -> None:
        # Use placeholders for {fields}, {where_cond} and {limit}; the parser
        # is responsible for filling them. ``{schema}`` / ``{table}`` are not
        # exercised here because they require ``set_options`` (which would
        # touch Redis); we keep an explicit table name instead.
        sql = await _build(
            "SELECT {fields} FROM t {where_cond} {limit}",
            fields=["a", "b", "COUNT(*) AS total"],
            filter={"a": "1"},
            grouping=["a", "b"],
            ordering=["total DESC"],
            querylimit=10,
        )
        for expected in ("WHERE", "GROUP BY", "ORDER BY", "LIMIT"):
            assert expected in sql, f"missing {expected} in {sql!r}"
        _assert_valid_sql(sql)

    async def test_extending_pre_existing_group_by(self) -> None:
        # The base query already has GROUP BY a; the parser must extend it
        # rather than appending a second GROUP BY clause.
        sql = await _build(
            "SELECT a, b, COUNT(*) FROM t WHERE flag = 1 GROUP BY a",
            grouping=["b"],
            ordering=["a"],
            querylimit=100,
        )
        assert sql.count("GROUP BY") == 1
        assert "GROUP BY a, b" in sql
        _assert_valid_sql(sql)

    async def test_full_stack_with_in_list_and_comparison(self) -> None:
        sql = await _build(
            "SELECT * FROM t",
            fields=["id", "amount"],
            filter={
                "category": ["A", "B", "C"],
                "amount": {">=": "100"},
            },
            ordering=["amount DESC"],
            querylimit=50,
            _offset=10,
        )
        assert "IN (" in sql
        assert "amount >= 100" in sql
        _assert_valid_sql(sql)


# ---------------------------------------------------------------------------
# Parametrized matrix — every subset of the six options
# ---------------------------------------------------------------------------

# Each option is "off" (None) or "on" (the value below). We then iterate all
# 2**6 combinations; this is a cheap way to fuzz interactions.
_OPTION_VALUES = {
    "fields": ["a", "b"],
    "filter": {"a": "1"},
    "grouping": ["a"],
    "ordering": ["a DESC"],
    "querylimit": 10,
    "_offset": 5,
}


def _all_subsets() -> list[dict[str, Any]]:
    keys = list(_OPTION_VALUES.keys())
    subsets: list[dict[str, Any]] = []
    for mask in range(1 << len(keys)):
        attrs: dict[str, Any] = {}
        for i, key in enumerate(keys):
            if mask & (1 << i):
                attrs[key] = _OPTION_VALUES[key]
        subsets.append(attrs)
    return subsets


@pytest.mark.parametrize(
    "attrs",
    _all_subsets(),
    ids=lambda a: "+".join(sorted(a.keys())) or "none",
)
async def test_every_combination_produces_valid_sql(
    attrs: dict[str, Any],
) -> None:
    """Every subset of the six options must yield syntactically valid SQL."""
    sql = await _build("SELECT * FROM t", **attrs)
    _assert_valid_sql(sql)
