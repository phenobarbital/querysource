"""FEAT-153 grammar, config validation and plan detection (Cython module)."""
from __future__ import annotations

import pytest

from querysource.parsers import jsonb_unnest as ju


@pytest.mark.parametrize("text,column,keys,cast", [
    ("student_uid", "student_uid", (), None),
    ("graduation_details[].course", "graduation_details", ("course",), None),
    ("graduation_details[].meta.level", "graduation_details", ("meta", "level"), None),
    ("graduation_details[].course_date::date", "graduation_details", ("course_date",), "date"),
    ("graduation_details[].course_date :: DATE", "graduation_details", ("course_date",), "date"),
    ("created_at::timestamptz", "created_at", (), "timestamptz"),
])
def test_parse_ref(text, column, keys, cast):
    ref = ju.parse_ref(text)
    assert (ref.column, ref.keys, ref.cast) == (column, keys, cast)


@pytest.mark.parametrize("text", [
    "a;drop", "a[].", "a[]", "a[].k k", "a[].k'", "a[]..k", "1col", "a[].b[].c", "a.b", "",
])
def test_parse_ref_rejects(text):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid reference '"):
        ju.parse_ref(text)


def test_unknown_cast():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: unknown cast 'regclass'$"):
        ju.parse_ref("a[].k::regclass")


def test_parse_expr_forms():
    star = ju.parse_expr("count(*)")
    assert (star.kind, star.func, star.is_aggregate) == ("count_star", "count", True)
    cd = ju.parse_expr("COUNT(DISTINCT student_uid)")
    assert (cd.kind, cd.func, cd.distinct, cd.arg.column) == ("agg", "count", True, "student_uid")
    plain = ju.parse_expr("count( a[].k )")
    assert (plain.func, plain.distinct, plain.arg.keys) == ("count", False, ("k",))
    s = ju.parse_expr("sum(a[].n)")
    assert (s.kind, s.func, s.arg.keys) == ("agg", "sum", ("n",))
    nested = ju.parse_expr("min(year(a[].d::date))")
    assert nested.kind == "agg" and nested.arg.kind == "bucket"
    assert (nested.arg.func, nested.arg.arg.cast) == ("year", "date")
    b = ju.parse_expr("month(a[].d)")
    assert (b.kind, b.func, b.is_aggregate) == ("bucket", "month", False)
    r = ju.parse_expr("a[].k")
    assert (r.kind, r.arg.is_path) == ("ref", True)


@pytest.mark.parametrize("text", [
    "count(distinct *)", "sum(distinct x)", "max(count(*))", "foo(x)", "year(sum(x))",
    "count()", "count(distinct)",
])
def test_parse_expr_rejects(text):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid expression '"):
        ju.parse_expr(text)


def test_parse_expr_cast_error_propagates():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: unknown cast 'foo'$"):
        ju.parse_expr("sum(a[].n::foo)")


def test_parse_select_item():
    item = ju.parse_select_item("count(distinct student_uid) as graduates")
    assert item.alias == "graduates" and item.expr.kind == "agg"
    item = ju.parse_select_item("a[].course AS c")
    assert item.alias == "c" and item.expr.arg.keys == ("course",)
    item = ju.parse_select_item("a[].course")
    assert item.alias is None
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid select item 'x as 1x'$"):
        ju.parse_select_item("x as 1x")


def test_parse_order_item():
    item = ju.parse_order_item("graduates DESC")
    assert (item.name, item.direction, item.nulls) == ("graduates", "DESC", None)
    item = ju.parse_order_item("a[].k asc nulls last")
    assert (item.name, item.direction, item.nulls) == (None, "ASC", "LAST")
    assert item.expr.arg.keys == ("k",)
    item = ju.parse_order_item("count(*) desc")
    assert item.expr.kind == "count_star" and item.direction == "DESC"
    item = ju.parse_order_item("x")
    assert (item.name, item.direction) == ("x", None)
    for bad in ("x DESC DESC", "x; drop", ""):
        with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid order item '"):
            ju.parse_order_item(bad)


def test_validate_config_defaults():
    expected = {"columns": None, "aliases": {}, "strict": False, "safe_cast": False}
    assert ju.validate_config(None) == expected
    assert ju.validate_config({}) == expected


def test_validate_config_full():
    cfg = ju.validate_config({
        "columns": {"gd": {"empty": "include", "prefilter": True}, "other": {}},
        "aliases": {"course": "gd[].course", "yr": "year(gd[].d::date)"},
        "strict": True,
        "safe_cast": True,
    })
    assert cfg["columns"]["gd"] == {"empty": "include", "safe_cast": None, "prefilter": True}
    assert cfg["columns"]["other"] == {"empty": "exclude", "safe_cast": None, "prefilter": False}
    assert cfg["aliases"]["yr"] == "year(gd[].d::date)"
    assert cfg["strict"] is True and cfg["safe_cast"] is True


@pytest.mark.parametrize("config,message", [
    ([], r"^jsonb_unnest: invalid config: must be a mapping$"),
    ({"bogus": 1}, r"^jsonb_unnest: invalid config: unknown key 'bogus'$"),
    ({"columns": []}, r"^jsonb_unnest: invalid config: columns must map identifiers to mappings$"),
    ({"columns": {"1x": {}}}, r"^jsonb_unnest: invalid config: columns must map identifiers to mappings$"),
    ({"columns": {"a": "x"}}, r"^jsonb_unnest: invalid config: columns must map identifiers to mappings$"),
    ({"columns": {"a": {"nope": 1}}}, r"^jsonb_unnest: invalid config: unknown column option 'nope'$"),
    ({"columns": {"a": {"empty": "maybe"}}},
     r"^jsonb_unnest: invalid config: empty must be 'exclude' or 'include'$"),
    ({"columns": {"a": {"prefilter": 1}}}, r"^jsonb_unnest: invalid config: prefilter must be a boolean$"),
    ({"columns": {"a": {"safe_cast": "yes"}}}, r"^jsonb_unnest: invalid config: safe_cast must be a boolean$"),
    ({"strict": 1}, r"^jsonb_unnest: invalid config: strict must be a boolean$"),
    ({"safe_cast": "no"}, r"^jsonb_unnest: invalid config: safe_cast must be a boolean$"),
    ({"aliases": []}, r"^jsonb_unnest: invalid config: aliases must map identifiers to expressions$"),
    ({"aliases": {"a b": "x"}}, r"^jsonb_unnest: invalid config: aliases must map identifiers to expressions$"),
    ({"aliases": {"a": 5}}, r"^jsonb_unnest: invalid config: aliases must map identifiers to expressions$"),
    ({"aliases": {"a": "foo(x)"}}, r"^jsonb_unnest: invalid expression 'foo\(x\)'$"),
])
def test_validate_config_rejects(config, message):
    with pytest.raises(ValueError, match=message):
        ju.validate_config(config)


ALIASES = {"aliases": {"course": "gd[].course"}}


@pytest.mark.parametrize("kwargs,expected", [
    (dict(fields=["gd[].course"]), True),
    (dict(grouping=["gd[].course"]), True),
    (dict(ordering=["gd[].k DESC"]), True),
    (dict(filter={"gd[].k": "x"}), True),
    (dict(having={"n": {">": 1}}), True),
    (dict(fields=["course"], config=ALIASES), True),
    (dict(ordering=["course DESC"], config=ALIASES), True),
    (dict(filter={"course!": "x"}, config=ALIASES), True),
    (dict(fields=["tags::text[]", "id"]), False),
    (dict(filter={"attrs": {"@>": {"a": 1}}}), False),
    (dict(having={}), False),
    (dict(fields=["course"]), False),
    (dict(fields=["a", "b"], grouping=["a"], ordering=["a"], filter={"x": 1}), False),
])
def test_is_plan_candidate(kwargs, expected):
    args = dict(fields=[], grouping=[], ordering=[], filter={}, having={}, config={})
    args.update(kwargs)
    assert ju.is_plan_candidate(**args) is expected


def test_is_plan_candidate_never_raises():
    assert ju.is_plan_candidate(None, None, None, None, None, None) is False
    assert ju.is_plan_candidate([1, None], [b"x"], "str", [1], 0, {"aliases": 5}) is False
    assert ju.is_plan_candidate([object()], {}, 5, {1: 2}, None, {"aliases": {"a": "b"}}) is False
