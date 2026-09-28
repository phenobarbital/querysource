"""FEAT-153 AC7/AC10: the Rust planner matches the Cython reference exactly."""
from __future__ import annotations

from typing import Any

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import jsonb_unnest as ju
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

pytestmark = pytest.mark.skipif(
    not (pgsql.HAS_RUST and hasattr(pgsql._rs, "pgsql_unnest_plan")),
    reason="qs_parsers without pgsql_unnest_plan",
)

WHERE_SQL = "SELECT * FROM students {where_cond}"
GD = "graduation_details"
ALIAS_CFG = {
    "columns": {GD: {"empty": "exclude"}},
    "aliases": {"course": f"{GD}[].course", "diploma_year": f"year({GD}[].course_date::date)"},
}

# (fields, grouping, ordering, filter, having, config)
CASES: list[tuple[list, list, list, dict, Any, Any]] = [
    ([f"{GD}[].course", "count(distinct student_uid) as graduates"],
     [f"{GD}[].course"], ["graduates DESC"],
     {"licensee": "'Asia'", f"{GD}[].course_date::date": {">=": "'2025-01-01'"}},
     {"graduates": {">": 5}}, {}),
    ([f"{GD}[].meta.level", f"{GD}[].course_date::date", "created_at::date", "student_uid"], [], [], {}, {}, {}),
    ([f"month({GD}[].course_date)", "day(created_at)", f"quarter({GD}[].d::timestamp)", f"week({GD}[].d)",
      f"year({GD}[].d::timestamptz)"], [], [], {}, {}, {}),
    (["count(*)", f"sum({GD}[].points)", f"avg({GD}[].points::int)", f"min(year({GD}[].course_date))",
      f"max({GD}[].course)", "sum(amount)", f"count({GD}[].course) as c2"], [], [], {}, {}, {}),
    ([f"{GD}[].k::date", f"{GD}[].n::int", f"{GD}[].t::text", f"{GD}[].f::float", f"{GD}[].b::boolean",
      f"month({GD}[].d)", f"sum({GD}[].v)"], [], [], {}, {}, {"safe_cast": True}),
    ([f"{GD}[].k::int", f"{GD}[].j::int"], [], [], {},
     {}, {"safe_cast": True, "columns": {GD: {"safe_cast": False}}}),
    ([f"{GD}[].k::int"], [], [], {}, {}, {"columns": {GD: {"safe_cast": True}}}),
    ([f"{GD}[].course"], [f"{GD}[].course"], [], {}, {}, {"columns": {GD: {"empty": "include"}}}),
    (["course", "diploma_year", "count(*)"], ["course", "diploma_year"], ["diploma_year DESC"], {}, {}, ALIAS_CFG),
    (["licensee"], [], ["course"], {"course!": "'A'"}, {}, ALIAS_CFG),
    (["course"], [], [], {"course": "'A'", "licensee": "'Asia'"}, {}, dict(ALIAS_CFG, strict=True)),
    (["licensee"], [], [], {f"{GD}[].course": "'Pilates Studio'"}, {}, {"columns": {GD: {"prefilter": True}}}),
    (["licensee"], [], [],
     {GD: {"@>": [{"z": 1}]}, f"{GD}[].course": ["'A'", "'B'"], f"{GD}[].meta.level": "'1'",
      f"{GD}[].a::text": "'x'", f"{GD}[].b!": "'x'", f"{GD}[].c": {">=": "'x'"}},
     {}, {"columns": {GD: {"prefilter": True}}}),
    (["licensee"], [], [],
     {f"{GD}[].a!": "'A'", f"{GD}[].b": ["'X'", "'Y'"], f"{GD}[].c!": ["'1'"], f"{GD}[].d": "null",
      f"{GD}[].e!": None, f"{GD}[].f": "NULL"}, {}, {}),
    (["licensee"], [], [],
     {f"{GD}[].n": 5, f"{GD}[].f": 1.5, f"{GD}[].b": True, f"{GD}[].g": 0.00001, f"{GD}[].h": 1e16,
      f"{GD}[].p": {">=": 5, "<": 10.25}, f"{GD}[].d": "CURRENT_DATE", f"{GD}[].x": "'x'' OR 1=1 --'",
      f"{GD}[].y": "a{b}"}, {}, {}),
    ([f"{GD}[].course", "count(*) as n", f"max({GD}[].category) as top"], [], [], {},
     {"n": {">=": 2, "<": 10.5}, "top": "Comprehensive", "count(*)": 3}, {}),
    (["licensee", "count(*) as n"], ["licensee"], [], {}, {"n": {">": 1}}, {}),
    (["licensee", "max(name) as m"], [], [], {}, {"m": "a{b}"}, {}),
    ([], [f"{GD}[].course", "licensee"], [], {}, {}, {}),
    ([f"{GD}[].course", "count(*) as n"], [f"{GD}[].course"],
     ["n desc nulls last", f"{GD}[].course_date asc", "course", "licensee NULLS FIRST"], {}, {}, {}),
    (["licensee", "count(*)"], ["licensee"], [], {"attrs": {"@>": {"status": "active"}},
                                                  GD: {"@>|": [[{"course": "A"}], [{"course": "B"}]]},
                                                  "tags::text[]": "x", "status!": "'z'"},
     {"count(*)": {">": 1}}, {}),
    (["a", "b"], ["a"], [], {"x": "'1'"}, {}, {}),  # not a plan candidate: both return None
]

ERROR_CASES: list[tuple[list, list, list, dict, Any, Any]] = [
    (["a[].k::regclass"], [], [], {}, {}, {}),
    (["a;drop table x", "g[].k"], [], [], {}, {}, {}),
    (["foo(g[].k)"], [], [], {}, {}, {}),
    (["g[].k as 1x"], [], [], {}, {}, {}),
    (["g[].k"], [], ["g[].k DESC DESC"], {}, {}, {}),
    (["a[].k", "b[].k"], [], [], {}, {}, {}),
    (["other[].k"], [], [], {}, {}, ALIAS_CFG),
    (["g[].course"], [], [], {}, {}, {"strict": True}),
    (["count(*)", "count(*)", "g[].x"], [], [], {}, {}, {}),
    (["count(g[].course)", "count(distinct g[].course)"], [], [], {}, {}, {}),
    (["g[].k"], [], [], {}, {}, []),
    (["g[].k"], [], [], {}, {}, {"bogus": 1}),
    (["g[].k"], [], [], {}, {}, {"strict": 1}),
    (["g[].k"], [], [], {}, {}, {"columns": []}),
    (["g[].k"], [], [], {}, {}, {"columns": {"1x": {}}}),
    (["g[].k"], [], [], {}, {}, {"columns": {"g": {"nope": 1}}}),
    (["g[].k"], [], [], {}, {}, {"columns": {"g": {"empty": "maybe"}}}),
    (["g[].k"], [], [], {}, {}, {"columns": {"g": {"prefilter": 1}}}),
    (["g[].k"], [], [], {}, {}, {"aliases": []}),
    (["g[].k"], [], [], {}, {}, {"aliases": {"a": "foo(x)"}}),
    (["licensee", "count(*) as n"], [], [], {}, "x", {}),
    (["licensee", "count(*) as n"], [], [], {}, {"licensee": 1}, {}),
    (["licensee", "count(*) as n"], [], [], {}, {"n": {"~": 1}}, {}),
    (["licensee", "count(*) as n"], [], [], {}, {"n": [1]}, {}),
    (["licensee", "count(*) as n"], [], [], {}, {"n": True}, {}),
    (["licensee"], [], [], {}, {"licensee": 1}, {}),
    (["g[].k"], [], [], {"g[].k": [{"a": 1}]}, {}, {}),
    (["g[].k"], [], [], {"g[].k": []}, {}, {}),
    (["g[].k"], [], [], {"g[].k": {"@>": "x"}}, {}, {}),
    (["g[].k"], [], [], {"g[].k|": "x"}, {}, {}),
    (["g[].k"], [], [], {"other[].k": "x"}, {}, {}),
]


@pytest.mark.parametrize("case", CASES)
def test_plan_parity(case):
    assert pgsql._rs.pgsql_unnest_plan(*case) == ju.unnest_plan(*case)


@pytest.mark.parametrize("case", CASES)
def test_plan_key_order_parity(case):
    rust, cython = pgsql._rs.pgsql_unnest_plan(*case), ju.unnest_plan(*case)
    if cython is None:
        assert rust is None
        return
    assert list(rust) == list(cython)
    assert list(rust["row_filter"]) == list(cython["row_filter"])


@pytest.mark.parametrize("case", CASES)
def test_wrap_parity(case):
    plan = ju.unnest_plan(*case)
    if plan is None:
        pytest.skip("not a plan candidate")
    inner = "SELECT * FROM students WHERE licensee='Asia'"
    assert pgsql._rs.pgsql_unnest_wrap(inner, plan) == ju.unnest_wrap(inner, plan)


@pytest.mark.parametrize("case", ERROR_CASES)
def test_error_message_parity(case):
    with pytest.raises(ValueError) as rust_err:
        pgsql._rs.pgsql_unnest_plan(*case)
    with pytest.raises(ValueError) as cy_err:
        ju.unnest_plan(*case)
    assert str(rust_err.value) == str(cy_err.value)


def _parser(case) -> pgSQLParser:
    fields, grouping, ordering, filter_, having, config = case
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=WHERE_SQL), query=WHERE_SQL)
    parser.cond_definition = {}
    parser.fields, parser.grouping, parser.ordering = list(fields), list(grouping), list(ordering)
    parser.filter = dict(filter_)
    parser.having = having
    parser.attributes = {"jsonb_unnest": config} if config else {}
    return parser


@pytest.mark.parametrize("case", CASES)
async def test_build_query_parity(case, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    rust_sql = await _parser(case).build_query(querylimit=10)
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    cython_sql = await _parser(case).build_query(querylimit=10)
    assert rust_sql == cython_sql


# a falsy config (``[]``) is normalised to ``{}`` by the parser before the planner sees it
PARSER_ERROR_CASES = [case for case in ERROR_CASES if case[5] != []]


@pytest.mark.parametrize("case", PARSER_ERROR_CASES)
async def test_rust_validation_error_is_parser_error_without_fallback(case, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    calls = []
    monkeypatch.setattr(pgsql, "unnest_plan", lambda *a: calls.append(a))
    with pytest.raises(ParserError) as err:
        await _parser(case).build_query()
    assert err.value.code == 400
    assert calls == []  # the Cython planner was never consulted


@pytest.mark.parametrize("case", ERROR_CASES[:8])
async def test_error_message_identical_on_both_paths(case, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    with pytest.raises(ParserError) as rust_err:
        await _parser(case).build_query()
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    with pytest.raises(ParserError) as cy_err:
        await _parser(case).build_query()
    assert rust_err.value.message == cy_err.value.message


@pytest.mark.parametrize("case", [
    (["licensee", "count(*) as n"], [], [], {}, {"n": {">": 2**70}}, {}),
    (["g[].k"], [], [], {"g[].k": 2**70}, {}, {}),
    (["g[].k"], [], [], {}, {}, {"columns": {"a\n": {}}}),
])
def test_edge_error_parity(case):
    with pytest.raises(ValueError) as rust_err:
        pgsql._rs.pgsql_unnest_plan(*case)
    with pytest.raises(ValueError) as cy_err:
        ju.unnest_plan(*case)
    assert str(rust_err.value) == str(cy_err.value)
