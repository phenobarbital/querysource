"""Regression coverage for the FEAT-153 post-merge review findings."""

from typing import Any

import pytest
import sqlglot

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser


@pytest.fixture(params=[False, True], ids=["cython", "rust"])
def backend(request: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param and not hasattr(getattr(pgsql, "_rs", None), "pgsql_unnest_plan"):
        pytest.skip("Rust unnest planner unavailable")
    monkeypatch.setattr(pgsql, "HAS_RUST", request.param)


def make_parser(query: str = "SELECT * FROM students {where_cond}") -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    return parser


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("use_alias", [False, True])
async def test_preprocessing_preserves_element_bounds(use_alias: bool) -> None:
    parser = make_parser()
    parser.fields = ["g[].points::int"]
    parser.attributes = {"jsonb_unnest": {"aliases": {"points": "g[].points::int"}}}
    key = "points" if use_alias else "g[].points::int"
    operands = {">=": 5, "<": 10}
    await parser.set_where({key: operands}, None)
    sql = await parser.build_query()
    assert "::int) >= '5' AND" in sql
    assert "::int) < '10'" in sql
    assert operands == {">=": 5, "<": 10}


@pytest.mark.usefixtures("backend")
async def test_preprocessing_does_not_hide_invalid_operator() -> None:
    parser = make_parser()
    parser.fields = ["g[].points::int"]
    await parser.set_where({"g[].points::int": {"invalid": 5, "<": 10}}, None)
    with pytest.raises(ParserError, match="invalid filter operator"):
        await parser.build_query()


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("ending", [";", "; \n\t", ""])
async def test_wrap_removes_statement_terminator(ending: str) -> None:
    parser = make_parser("SELECT *, ';' AS marker FROM students" + ending)
    parser.fields = ["g[].k"]
    sql = await parser.build_query()
    assert "FROM (SELECT *, ';' AS marker FROM students) AS _qs_src" in sql
    sqlglot.parse_one(sql, read="postgres")


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("expression", ["g[]." + "a" * 64, "sum(g[]." + "a" * 60 + ")"])
async def test_long_default_alias_requires_explicit_alias(expression: str) -> None:
    parser = make_parser()
    parser.fields = [expression]
    with pytest.raises(ParserError, match="exceeds 63 bytes; use a shorter AS alias"):
        await parser.build_query()
    parser.fields = [expression + " as short_name"]
    assert 'AS "short_name"' in await parser.build_query()


@pytest.mark.usefixtures("backend")
async def test_alias_truncation_collision_is_rejected() -> None:
    parser = make_parser()
    prefix = "a" * 63
    parser.fields = [f"g[].{prefix}x", f"g[].z as {prefix}"]
    parser.ordering = [prefix]
    with pytest.raises(ParserError, match="exceeds 63 bytes"):
        await parser.build_query()


@pytest.mark.usefixtures("backend")
async def test_63_byte_alias_is_allowed() -> None:
    parser = make_parser()
    alias = "a" * 63
    parser.fields = [f"g[].{alias}"]
    parser.ordering = [alias]
    sql = await parser.build_query()
    assert f'AS "{alias}"' in sql
    assert sql.endswith(f'ORDER BY "{alias}"')
