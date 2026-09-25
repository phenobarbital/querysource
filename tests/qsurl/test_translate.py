"""translate.split: every pushdown rule and table row (spec §3 M6, AC8/AC10/AC11/AC15)."""
from __future__ import annotations

import pytest

from querysource.qsurl import QSUrlError
from querysource.qsurl.translate import like_escape, split

BASE = frozenset({"select", "filter", "in_list", "null_check"})
SQL = BASE | {"alias", "sort", "limit", "offset"}
PG = SQL | {"text_match"}


def _ir(**over) -> dict:
    ir = {
        "slug": "s",
        "fields": [],
        "filter": None,
        "sort": [],
        "limit": None,
        "offset": None,
        "distinct": False,
        "requires": [],
    }
    ir.update(over)
    return ir


def _leaf(col, expr, value=None, **extra) -> dict:
    d = {"column": col, "expression": expr, **extra}
    if value is not None:
        d["value"] = value
    return d


@pytest.mark.parametrize(
    "leaf,key,value",
    [
        (_leaf("a", "==", "x"), "a", "x"),
        (_leaf("a", "!=", "x"), "a!", "x"),
        (_leaf("a", ">=", 5), "a", {">=": 5}),
        (_leaf("a", "==", ["x", "y"]), "a", ["x", "y"]),
        (_leaf("a", "!=", ["x"]), "a!", ["x"]),
        (_leaf("a", "is_null"), "a", "null"),
        (_leaf("a", "not_null"), "a", "!null"),
        (_leaf("a", "contains", "5%_off"), "a", {"ILIKE": "%5\\%\\_off%"}),
        (_leaf("a", "not_contains", "x"), "a", {"NOT ILIKE": "%x%"}),
    ],
)
def test_leaf_table_on_pg(leaf, key, value):
    conditions, plan = split(_ir(filter={"and": [leaf]}), PG)
    assert conditions["filter"] == {key: value} and plan.filter is None


def test_text_ops_residual_without_text_match():
    leaf = _leaf("a", "contains", "x")
    conditions, plan = split(_ir(filter={"and": [leaf]}), BASE)
    assert "filter" not in conditions
    assert plan.filter == {"and": [leaf]}


def test_regex_always_residual():
    leaf = _leaf("a", "regex", "^x")
    conditions, plan = split(_ir(filter={"and": [leaf]}), PG)
    assert "filter" not in conditions
    assert plan.filter == {"and": [leaf]}


def test_root_or_is_full_residual():
    node = {"or": [_leaf("a", "==", "x"), _leaf("b", "==", "y")]}
    conditions, plan = split(_ir(filter=node), PG)
    assert "filter" not in conditions
    assert plan.filter == node


def test_duplicate_key_goes_residual():
    first = _leaf("price", ">", 10)
    second = _leaf("price", "<", 20)
    conditions, plan = split(_ir(filter={"and": [first, second]}), PG)
    assert conditions["filter"] == {"price": {">": 10}}
    assert plan.filter == {"and": [second]}


def test_dtype_dropped_on_pushdown_kept_on_residual():
    pushed_leaf = _leaf("opened", ">=", "2024-01-01", dtype="date")
    conditions, plan = split(_ir(filter={"and": [pushed_leaf]}), PG)
    assert conditions["filter"] == {"opened": {">=": "2024-01-01"}}
    assert "dtype" not in conditions["filter"]

    residual_leaf = _leaf("opened", "regex", "^2024", dtype="date")
    conditions2, plan2 = split(_ir(filter={"and": [residual_leaf]}), PG)
    assert "filter" not in conditions2
    assert plan2.filter == {"and": [residual_leaf]}
    assert plan2.filter["and"][0]["dtype"] == "date"


def test_window_rules():
    # Filter fully pushed, no distinct, no sort: limit/offset pushed.
    leaf = _leaf("a", "==", "x")
    conditions, plan = split(_ir(filter={"and": [leaf]}, limit=10, offset=5), PG)
    assert conditions["_limit"] == 10 and conditions["_offset"] == 5
    assert plan.limit is None and plan.offset is None

    # A residual filter blocks the window from being pushed at all.
    residual_leaf = _leaf("a", "regex", "x")
    conditions, plan = split(_ir(filter={"and": [residual_leaf]}, limit=10, offset=5), PG)
    assert "_limit" not in conditions and "_offset" not in conditions
    assert plan.limit == 10 and plan.offset == 5

    # distinct blocks the window.
    conditions, plan = split(_ir(distinct=True, limit=10), PG)
    assert "_limit" not in conditions
    assert plan.limit == 10

    # A residual sort blocks the window.
    conditions, plan = split(_ir(sort=[{"column": "z", "order": "asc"}], limit=10), BASE)
    assert "_limit" not in conditions
    assert plan.limit == 10


def test_alias_pushdown_only_when_plan_empty():
    fields = [{"column": "name", "alias": "n"}]
    conditions, plan = split(_ir(fields=fields), PG)
    assert conditions["fields"] == ["name AS n"]
    assert plan.rename == ()

    # Plan not otherwise empty (residual filter present, referencing an already
    # requested column so no projection column is added) -> alias goes to rename.
    residual_leaf = _leaf("name", "regex", "y")
    conditions, plan = split(_ir(fields=fields, filter={"and": [residual_leaf]}), PG)
    assert conditions["fields"] == ["name"]
    assert plan.rename == (("name", "n"),)


def test_projection_adds_missing_columns():
    fields = ["name"]
    residual_leaf = _leaf("city", "regex", "x")
    conditions, plan = split(_ir(fields=fields, filter={"and": [residual_leaf]}), PG)
    assert conditions["fields"] == ["name", "city"]
    assert plan.project == ("name",)


def test_unsupported_functions_navigation():
    with pytest.raises(QSUrlError) as exc:
        split(_ir(requires=["select", "functions"]), PG)
    assert exc.value.kind == "unsupported"
    assert "functions" in exc.value.message

    with pytest.raises(QSUrlError) as exc:
        split(_ir(requires=["navigation"]), PG)
    assert exc.value.kind == "unsupported"
    assert "navigation" in exc.value.message


def test_bad_identifier_is_lower_error():
    with pytest.raises(QSUrlError) as exc:
        split(_ir(fields=["bad name"]), PG)
    assert exc.value.kind == "lower"


def test_cost_residual_only_without_scan():
    leaf = _leaf("a", "regex", "x")
    with pytest.raises(QSUrlError) as exc:
        split(_ir(filter={"and": [leaf]}), BASE, residual_scan=False)
    assert exc.value.kind == "cost"


def test_conditions_are_fresh_dicts():
    leaf = _leaf("a", "==", "x")
    ir = _ir(filter={"and": [leaf]})
    conditions, _plan = split(ir, PG)
    conditions["filter"]["a"] = "mutated"
    assert ir["filter"]["and"][0]["value"] == "x"


def test_like_escape():
    assert like_escape("a\\b%c_d") == "a\\\\b\\%c\\_d"
