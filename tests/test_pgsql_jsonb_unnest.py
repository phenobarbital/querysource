"""FEAT-153: pgSQLParser.build_query in JSONB-unnest plan mode (Cython path)."""
from __future__ import annotations

import pytest
import sqlglot

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser
from querysource.providers.sql import sqlProvider

WHERE_SQL = "SELECT * FROM students {where_cond}"
TABLE_SQL = "SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}"


def _make(query: str = WHERE_SQL, **attrs) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser


@pytest.fixture(autouse=True)
def _cython_only(monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", False)


async def test_spec_example_renders_and_parses():
    parser = _make(
        fields=["graduation_details[].course", "graduation_details[].category",
                "count(distinct student_uid) as graduates"],
        grouping=["graduation_details[].course", "graduation_details[].category"],
        ordering=["graduates DESC"],
        filter={"licensee": "'Asia'", "graduation_details[].course_date::date": {">=": "'2025-01-01'"}},
        having={"graduates": {">": 5}},
    )
    sql = await parser.build_query(querylimit=10)
    assert sql.index("GROUP BY") < sql.index("HAVING") < sql.index("ORDER BY") < sql.index("LIMIT 10")
    inner = sql.split(") AS _qs_src", 1)[0]
    assert "LIMIT" not in inner and "GROUP BY" not in inner and "licensee='Asia'" in inner
    assert "WHERE ((_qs_e0.elem ->> 'course_date')::date) >= '2025-01-01'" in sql
    assert 'HAVING count(DISTINCT _qs_src.student_uid) > 5' in sql
    assert sql.endswith("ORDER BY \"graduates\" DESC LIMIT 10")
    sqlglot.parse_one(sql, read="postgres")


async def test_table_template_placeholders_stay_outside():
    parser = _make(TABLE_SQL, schema="public", tablename="students",
                   fields=["graduation_details[].course"], grouping=["graduation_details[].course"])
    sql = await parser.build_query(querylimit=5, offset=10)
    inner = sql.split(") AS _qs_src", 1)[0]
    assert "LIMIT" not in inner and "OFFSET" not in inner and "GROUP BY" not in inner and "{" not in sql
    assert sql.endswith("LIMIT 5 OFFSET 10")
    sqlglot.parse_one(sql, read="postgres")


async def test_row_jsonb_filters_stay_in_inner_query():
    parser = _make(
        fields=["graduation_details[].course", "count(*)"],
        grouping=["graduation_details[].course"],
        filter={"graduation_details": {"@>": [{"course": "Pilates Studio"}]}, "licensee": "'Asia'"},
    )
    sql = await parser.build_query()
    inner = sql.split(") AS _qs_src", 1)[0]
    assert "graduation_details @>" in inner and "licensee='Asia'" in inner
    assert " WHERE " not in sql.split(") AS _qs_src", 1)[1]
    sqlglot.parse_one(sql, read="postgres")


@pytest.mark.parametrize("attrs,message", [
    (dict(fields=["a;drop table x", "g[].k"]), "jsonb_unnest: invalid reference 'a;drop table x'"),
    (dict(fields=["foo(g[].k)"]), "jsonb_unnest: invalid expression 'foo(g[].k)'"),
    (dict(fields=["a[].k", "b[].k"]), "jsonb_unnest: more than one array column ('a', 'b')"),
    (dict(fields=["a[].k::regclass"]), "jsonb_unnest: unknown cast 'regclass'"),
    (dict(fields=["a[].k"], having="x"), "jsonb_unnest: having must be a mapping"),
])
async def test_invalid_raises_parser_error(attrs, message):
    parser = _make(**attrs)
    with pytest.raises(ParserError) as excinfo:
        await parser.build_query()
    assert excinfo.value.message == message
    assert excinfo.value.code == 400


async def test_add_fields_rejected_in_plan_mode():
    parser = pgSQLParser(
        definition=None,
        conditions=QueryObject(query_raw=WHERE_SQL, fields=["g[].k"], add_fields=True),
        query=WHERE_SQL,
    )
    await parser.set_options()
    with pytest.raises(ParserError, match="add_fields"):
        await parser.build_query()


async def test_having_only_has_no_lateral():
    parser = _make(fields=["licensee", "count(*) as n"], grouping=["licensee"], having={"n": {">": 1}})
    sql = await parser.build_query()
    assert "LATERAL" not in sql
    assert "GROUP BY _qs_src.licensee HAVING count(*) > 1" in sql
    sqlglot.parse_one(sql, read="postgres")


async def test_having_changes_sql_and_is_not_a_filter():
    base = dict(fields=["licensee", "count(*) as n"], grouping=["licensee"])
    one = await _make(having={"n": {">": 1}}, **base).build_query()
    two = await _make(having={"n": {">": 2}}, **base).build_query()
    assert one != two and one.count("WHERE") == 0
    assert sqlProvider._has_parser_conditions({"having": {"n": {">": 1}}})


async def test_config_aliases_from_attributes():
    parser = _make(
        attributes={"jsonb_unnest": {
            "columns": {"graduation_details": {"empty": "exclude"}},
            "aliases": {"course": "graduation_details[].course"},
        }},
        grouping=["course"],
    )
    sql = await parser.build_query()
    assert "SELECT (_qs_e0.elem ->> 'course') AS \"course\", count(*) AS \"count\" FROM (" in sql
    assert "GROUP BY (_qs_e0.elem ->> 'course')" in sql
    sqlglot.parse_one(sql, read="postgres")


async def test_strict_config_rejects_raw_path():
    parser = _make(attributes={"jsonb_unnest": {"strict": True}}, fields=["g[].k"])
    with pytest.raises(ParserError, match="strict mode"):
        await parser.build_query()


async def test_element_only_filter_and_empty_include():
    parser = _make(
        attributes={"jsonb_unnest": {"columns": {"g": {"empty": "include"}}}},
        fields=["licensee"],
        grouping=["licensee"],
        filter={"g[].k": "'x'"},
    )
    sql = await parser.build_query()
    assert "LEFT JOIN LATERAL" in sql and "WHERE (_qs_e0.elem ->> 'k') = 'x'" in sql
    sqlglot.parse_one(sql, read="postgres")


async def test_non_plan_query_unchanged():
    parser = _make(fields=["store_id", "count(*)"], grouping=["store_id"], ordering=["store_id DESC"])
    assert await parser.build_query() == "SELECT store_id, count(*) FROM students  GROUP BY store_id ORDER BY store_id DESC"
