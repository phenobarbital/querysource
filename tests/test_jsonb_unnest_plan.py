"""FEAT-153 plan rendering (Cython): golden SQL for select/group/order/having/lateral/wrap."""
from __future__ import annotations

import pytest
import sqlglot

from querysource.parsers import jsonb_unnest as ju

LATERAL = (
    "CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) "
    "WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem)"
)
LEFT_LATERAL = LATERAL.replace("CROSS JOIN", "LEFT JOIN") + " ON true"
COURSE = "(_qs_e0.elem ->> 'course')"
DATE = "(_qs_e0.elem ->> 'course_date')"


def _plan(fields=(), grouping=(), ordering=(), filter=None, having=None, config=None):
    return ju.unnest_plan(list(fields), list(grouping), list(ordering), filter or {}, having or {}, config or {})


def _parses(plan) -> None:
    sql = ju.unnest_wrap("SELECT * FROM students", plan)
    sqlglot.parse_one(sql, read="postgres")


def test_spec_example_without_element_filter():
    plan = _plan(
        fields=["graduation_details[].course", "graduation_details[].category",
                "count(distinct student_uid) as graduates"],
        grouping=["graduation_details[].course", "graduation_details[].category"],
        ordering=["graduates DESC"],
        having={"graduates": {">": 5}},
        filter={"licensee": "'Asia'"},
    )
    assert plan["select"] == [
        "(_qs_e0.elem ->> 'course') AS \"course\"",
        "(_qs_e0.elem ->> 'category') AS \"category\"",
        "count(DISTINCT _qs_src.student_uid) AS \"graduates\"",
    ]
    assert plan["group_by"] == ["(_qs_e0.elem ->> 'course')", "(_qs_e0.elem ->> 'category')"]
    assert plan["order_by"] == ['"graduates" DESC']
    assert plan["having"] == ["count(DISTINCT _qs_src.student_uid) > 5"]
    assert plan["lateral"] == LATERAL
    assert plan["element_where"] == []
    assert plan["row_filter"] == {"licensee": "'Asia'"}
    assert set(plan) == {"select", "group_by", "order_by", "having", "element_where", "lateral", "row_filter"}
    sql = ju.unnest_wrap("SELECT * FROM students WHERE licensee='Asia'", plan)
    assert sql.startswith("SELECT (_qs_e0.elem ->> 'course') AS \"course\", ")
    assert " FROM (SELECT * FROM students WHERE licensee='Asia') AS _qs_src CROSS JOIN LATERAL " in sql
    sqlglot.parse_one(sql, read="postgres")


def test_not_a_candidate_returns_none():
    assert _plan(fields=["a", "b"], grouping=["a"]) is None


def test_nested_key_and_cast():
    plan = _plan(fields=["graduation_details[].meta.level", "graduation_details[].course_date::date"])
    assert plan["select"] == [
        "(_qs_e0.elem -> 'meta' ->> 'level') AS \"level\"",
        f"({DATE}::date) AS \"course_date\"",
    ]
    _parses(plan)


def test_row_column_cast_and_plain():
    plan = _plan(fields=["graduation_details[].course", "created_at::date", "student_uid"])
    assert plan["select"][1:] == ["(_qs_src.created_at::date) AS \"created_at\"", "_qs_src.student_uid AS \"student_uid\""]


@pytest.mark.parametrize("unit", ["year", "quarter", "month", "week", "day"])
def test_buckets_implicit_date(unit):
    plan = _plan(fields=[f"{unit}(graduation_details[].course_date)"])
    assert plan["select"] == [f"(date_trunc('{unit}', ({DATE}::date))::date) AS \"{unit}_course_date\""]
    _parses(plan)


def test_bucket_keeps_timestamp_cast_and_row_ident():
    plan = _plan(fields=["month(graduation_details[].ts::timestamptz)", "day(created_at)"])
    assert plan["select"] == [
        "(date_trunc('month', ((_qs_e0.elem ->> 'ts')::timestamptz))::date) AS \"month_ts\"",
        "(date_trunc('day', _qs_src.created_at)::date) AS \"day_created_at\"",
    ]


def test_aggregates_and_default_aliases():
    plan = _plan(fields=[
        "count(*)", "count(distinct graduation_details[].course)",
        "min(graduation_details[].course_date::date)", "max(graduation_details[].course)",
        "sum(graduation_details[].points)", "avg(graduation_details[].points::int)",
        "min(year(graduation_details[].course_date))", "sum(amount)",
    ])
    assert plan["select"] == [
        'count(*) AS "count"',
        f'count(DISTINCT {COURSE}) AS "count_course"',
        f'min(({DATE}::date)) AS "min_course_date"',
        f'max({COURSE}) AS "max_course"',
        "sum(((_qs_e0.elem ->> 'points')::numeric)) AS \"sum_points\"",
        "avg(((_qs_e0.elem ->> 'points')::int)) AS \"avg_points\"",
        f"min((date_trunc('year', ({DATE}::date))::date)) AS \"min_year_course_date\"",
        'sum(_qs_src.amount) AS "sum_amount"',
    ]
    _parses(plan)


def test_explicit_alias_wins_and_duplicate_rejected():
    plan = _plan(fields=["graduation_details[].course as name", "count(*) AS n"])
    assert plan["select"] == [f'{COURSE} AS "name"', 'count(*) AS "n"']
    with pytest.raises(ValueError, match=r"^jsonb_unnest: duplicate output alias 'count'$"):
        _plan(fields=["count(*)", "count(*)", "graduation_details[].x"])
    with pytest.raises(ValueError, match=r"^jsonb_unnest: duplicate output alias 'count_course'$"):
        _plan(fields=["count(graduation_details[].course)", "count(distinct graduation_details[].course)"])


def test_empty_fields_uses_group_keys_plus_count():
    plan = _plan(grouping=["graduation_details[].course", "licensee"])
    assert plan["select"] == [f'{COURSE} AS "course"', '_qs_src.licensee AS "licensee"', 'count(*) AS "count"']
    assert plan["group_by"] == [COURSE, "_qs_src.licensee"]
    _parses(plan)


def test_group_by_select_alias_uses_expression():
    plan = _plan(fields=["graduation_details[].course as c", "count(*)"], grouping=["c"])
    assert plan["group_by"] == [COURSE]


def test_order_by_variants():
    plan = _plan(
        fields=["graduation_details[].course", "count(*) as n"],
        grouping=["graduation_details[].course"],
        ordering=["n desc nulls last", "graduation_details[].course_date asc", "course", "licensee NULLS FIRST"],
    )
    assert plan["order_by"] == [
        '"n" DESC NULLS LAST',
        f"{DATE} ASC",
        '"course"',
        "_qs_src.licensee NULLS FIRST",
    ]


CONFIG = {
    "columns": {"graduation_details": {"empty": "exclude"}},
    "aliases": {"course": "graduation_details[].course",
                "diploma_year": "year(graduation_details[].course_date::date)"},
}


def test_config_aliases_expand():
    plan = _plan(
        fields=["course", "diploma_year", "count(*)"],
        grouping=["course", "diploma_year"],
        ordering=["diploma_year DESC"],
        config=CONFIG,
    )
    year = f"(date_trunc('year', ({DATE}::date))::date)"
    assert plan["select"] == [f'{COURSE} AS "course"', f'{year} AS "diploma_year"', 'count(*) AS "count"']
    assert plan["group_by"] == [COURSE, year]
    assert plan["order_by"] == ['"diploma_year" DESC']
    assert plan["lateral"] == LATERAL
    _parses(plan)


def test_alias_used_only_in_ordering_triggers_plan():
    plan = _plan(fields=["licensee"], ordering=["course"], config=CONFIG)
    assert plan is not None
    assert plan["order_by"] == [COURSE]


def test_strict_rejects_raw_path_but_allows_alias():
    cfg = dict(CONFIG, strict=True)
    with pytest.raises(ValueError, match=r"^jsonb_unnest: raw path 'graduation_details\[\]\.course' not allowed in strict mode$"):
        _plan(fields=["graduation_details[].course"], config=cfg)
    assert _plan(fields=["course"], config=cfg) is not None


def test_columns_allowlist():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: array column 'other' is not declared in columns$"):
        _plan(fields=["other[].k"], config=CONFIG)


def test_multiple_arrays_rejected_same_column_once():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: more than one array column \('a', 'b'\)$"):
        _plan(fields=["a[].k", "b[].k"])
    plan = _plan(fields=["graduation_details[].a", "graduation_details[].b"])
    assert plan["lateral"].count("jsonb_array_elements") == 1


def test_empty_include_uses_left_join():
    cfg = {"columns": {"graduation_details": {"empty": "include"}}}
    plan = _plan(fields=["graduation_details[].course"], config=cfg)
    assert plan["lateral"] == LEFT_LATERAL
    _parses(plan)


@pytest.mark.parametrize("cast,regex", [
    ("date", "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"),
    ("timestamp", "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"),
    ("int", "^-?[0-9]+$"),
    ("bigint", "^-?[0-9]+$"),
    ("numeric", "^-?[0-9]+([.][0-9]+)?$"),
    ("float", "^-?[0-9]+([.][0-9]+)?$"),
    ("boolean", "^(true|false)$"),
])
def test_safe_cast_guard(cast, regex):
    plan = _plan(fields=[f"graduation_details[].k::{cast}"], config={"safe_cast": True})
    text = "(_qs_e0.elem ->> 'k')"
    assert plan["select"] == [f"(CASE WHEN {text} ~ '{regex}' THEN {text}::{cast} END) AS \"k\""]
    assert "{" not in plan["select"][0] and "\\" not in plan["select"][0]
    _parses(plan)


def test_safe_cast_text_never_guarded_and_column_override():
    plan = _plan(fields=["graduation_details[].k::text"], config={"safe_cast": True})
    assert plan["select"] == ["((_qs_e0.elem ->> 'k')::text) AS \"k\""]
    cfg = {"safe_cast": True, "columns": {"graduation_details": {"safe_cast": False}}}
    plan = _plan(fields=["graduation_details[].k::int"], config=cfg)
    assert plan["select"] == ["((_qs_e0.elem ->> 'k')::int) AS \"k\""]
    cfg = {"columns": {"graduation_details": {"safe_cast": True}}}
    assert "CASE WHEN" in _plan(fields=["graduation_details[].k::int"], config=cfg)["select"][0]
    assert "CASE WHEN" in _plan(fields=["sum(graduation_details[].k)"], config={"safe_cast": True})["select"][0]


def test_safe_cast_rendering_applies_to_bucket_implicit_date():
    plan = _plan(fields=["month(graduation_details[].d)"], config={"safe_cast": True})
    assert "CASE WHEN (_qs_e0.elem ->> 'd') ~ '^[0-9][0-9][0-9][0-9]-" in plan["select"][0]


def test_key_is_always_a_literal():
    plan = _plan(fields=["graduation_details[].a-b"])
    assert plan["select"] == ["(_qs_e0.elem ->> 'a-b') AS \"a-b\""]


def test_having_multi_op_and_literals():
    plan = _plan(
        fields=["graduation_details[].course", "count(*) as n", "max(graduation_details[].category) as top"],
        having={"n": {">=": 2, "<": 10.5}, "top": "Comprehensive", "count(*)": 3},
    )
    assert plan["having"] == [
        "count(*) >= 2", "count(*) < 10.5",
        f"max({'(_qs_e0.elem ->> ' + chr(39) + 'category' + chr(39) + ')'}) = 'Comprehensive'",
        "count(*) = 3",
    ]
    _parses(plan)


def test_having_only_plan_has_no_lateral():
    plan = _plan(fields=["licensee", "count(*) as n"], grouping=["licensee"], having={"n": {">": 1}})
    assert plan["lateral"] == ""
    assert plan["having"] == ["count(*) > 1"]
    _parses(plan)


def test_having_brace_literal_is_escaped():
    plan = _plan(fields=["licensee", "max(name) as m"], having={"m": "a{b}"})
    assert plan["having"] == ["max(_qs_src.name) = E'a\\x7bb\\x7d'"]


@pytest.mark.parametrize("having,message", [
    ("x", r"^jsonb_unnest: having must be a mapping$"),
    ({"licensee": 1}, r"^jsonb_unnest: unknown having key 'licensee'$"),
    ({"n": {"~": 1}}, r"^jsonb_unnest: invalid having operator '~'$"),
    ({"n": [1]}, r"^jsonb_unnest: invalid having value for 'n'$"),
    ({"n": True}, r"^jsonb_unnest: invalid having value for 'n'$"),
    ({"n": {">": None}}, r"^jsonb_unnest: invalid having value for 'n'$"),
    ({"n": float("nan")}, r"^jsonb_unnest: invalid having value for 'n'$"),
])
def test_having_errors(having, message):
    with pytest.raises(ValueError, match=message):
        _plan(fields=["licensee", "count(*) as n"], having=having)


def test_having_requires_aggregate():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: having requires an aggregate$"):
        _plan(fields=["licensee"], having={"licensee": 1})


def test_wrap_shapes():
    plan = {"select": ['a AS "a"'], "lateral": "", "element_where": [], "group_by": [], "order_by": [],
            "having": [], "row_filter": {}}
    assert ju.unnest_wrap("  SELECT 1  ", plan) == 'SELECT a AS "a" FROM (SELECT 1) AS _qs_src'
    plan["lateral"] = "CROSS JOIN LATERAL x"
    plan["element_where"] = ["p", "q"]
    assert ju.unnest_wrap("SELECT 1", plan) == (
        'SELECT a AS "a" FROM (SELECT 1) AS _qs_src CROSS JOIN LATERAL x WHERE p AND q'
    )


@pytest.mark.parametrize("field", ["a;drop table x", "foo(a[].k)", "a[].k as 1x", "a[].k::regclass"])
def test_invalid_fields_raise(field):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: "):
        _plan(fields=[field, "graduation_details[].x"])
