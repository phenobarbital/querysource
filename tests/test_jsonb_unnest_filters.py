"""FEAT-153 element-level filters and opt-in containment pre-filter (Cython)."""
from __future__ import annotations

import pytest

from querysource.parsers import jsonb_unnest as ju

CFG_PREFILTER = {"columns": {"graduation_details": {"prefilter": True}}}
COURSE = "(_qs_e0.elem ->> 'course')"


def _plan(filter, config=None, fields=("graduation_details[].course",)):
    return ju.unnest_plan(list(fields), [], [], filter, {}, config or {})


def test_scalar_element_filter_prequoted():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'", "licensee": "'Asia'"})
    assert plan["element_where"] == [f"{COURSE} = 'Pilates Studio'"]
    assert plan["row_filter"] == {"licensee": "'Asia'"}


def test_cast_comparison():
    plan = _plan({"graduation_details[].course_date::date": {">=": "'2025-01-01'"}})
    assert plan["element_where"] == ["((_qs_e0.elem ->> 'course_date')::date) >= '2025-01-01'"]


def test_prefilter_opt_in():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'"}, CFG_PREFILTER)
    assert plan["row_filter"] == {"graduation_details|": {"@>": [{"course": "Pilates Studio"}]}}


def test_no_prefilter_by_default():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'"})
    assert plan["row_filter"] == {}


def test_negation_scalar_list_null():
    plan = _plan({
        "graduation_details[].course!": "'A'",
        "graduation_details[].category": ["'X'", "'Y'"],
        "graduation_details[].level!": ["'1'", "'2'"],
        "graduation_details[].note": "null",
        "graduation_details[].other!": None,
    })
    assert plan["element_where"] == [
        f"{COURSE} <> 'A'",
        "(_qs_e0.elem ->> 'category') IN ('X', 'Y')",
        "(_qs_e0.elem ->> 'level') NOT IN ('1', '2')",
        "(_qs_e0.elem ->> 'note') IS NULL",
        "(_qs_e0.elem ->> 'other') IS NOT NULL",
    ]


def test_comparison_dict_multi_op_and_negation_ignored():
    plan = _plan({"graduation_details[].points!": {">=": 5, "<": 10}})
    assert plan["element_where"] == [
        "(_qs_e0.elem ->> 'points') >= '5'", "(_qs_e0.elem ->> 'points') < '10'",
    ]


def test_numbers_and_bools_are_text_literals():
    plan = _plan({"graduation_details[].n": 5, "graduation_details[].f": 1.5, "graduation_details[].b": True})
    assert plan["element_where"] == [
        "(_qs_e0.elem ->> 'n') = '5'", "(_qs_e0.elem ->> 'f') = '1.5'", "(_qs_e0.elem ->> 'b') = 'true'",
    ]


def test_sql_function_value_stays_literal_and_quotes_escaped():
    plan = _plan({"graduation_details[].d": "CURRENT_DATE", "graduation_details[].x": "'x'' OR 1=1 --'"})
    assert plan["element_where"] == [
        "(_qs_e0.elem ->> 'd') = 'CURRENT_DATE'",
        "(_qs_e0.elem ->> 'x') = 'x'' OR 1=1 --'",
    ]


def test_brace_value_is_escaped():
    plan = _plan({"graduation_details[].x": "a{b}"})
    assert plan["element_where"] == ["(_qs_e0.elem ->> 'x') = E'a\\x7bb\\x7d'"]


@pytest.mark.parametrize("value", [[{"a": 1}], [], [["x"]], {}, object()])
def test_invalid_values(value):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid filter value for 'graduation_details\[\]\.k'$"):
        _plan({"graduation_details[].k": value})


def test_invalid_operator():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid filter operator '@>' for 'graduation_details\[\]\.k'$"):
        _plan({"graduation_details[].k": {"@>": "x"}})


@pytest.mark.parametrize("key", ["graduation_details[].k|", "graduation_details[].k#", "graduation_details[].k@"])
def test_path_key_suffixes_are_reference_errors(key):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid reference '"):
        _plan({key: "x"})


def test_filter_only_path_yields_lateral():
    plan = ju.unnest_plan(["licensee"], [], [], {"graduation_details[].course": "'A'"}, {}, {})
    assert "jsonb_array_elements" in plan["lateral"]
    assert plan["element_where"] == [f"{COURSE} = 'A'"]


def test_second_array_column_in_filter_rejected():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: more than one array column \('graduation_details', 'other'\)$"):
        _plan({"other[].k": "x"})


def test_strict_and_alias_filter_key():
    cfg = {"strict": True, "aliases": {"course": "graduation_details[].course"}}
    with pytest.raises(ValueError, match=r"^jsonb_unnest: raw path 'graduation_details\[\]\.course' not allowed in strict mode$"):
        ju.unnest_plan(["licensee"], [], [], {"graduation_details[].course": "x"}, {}, cfg)
    plan = ju.unnest_plan(["licensee"], [], [], {"course!": "'A'", "licensee": "'Asia'"}, {}, cfg)
    assert plan["element_where"] == [f"{COURSE} <> 'A'"]
    assert plan["row_filter"] == {"licensee": "'Asia'"}


def test_row_filters_untouched_and_ordered():
    row = {
        "attrs": {"@>": {"status": "active"}},
        "graduation_details": {"@>|": [[{"course": "A"}], [{"course": "B"}]]},
        "tags::text[]": "x",
        "status!": "'z'",
    }
    plan = _plan(dict(row))
    assert plan["row_filter"] == row
    assert list(plan["row_filter"]) == list(row)


def test_prefilter_in_and_nested_and_suffix_collision():
    filt = {
        "graduation_details": {"@>": [{"z": 1}]},
        "graduation_details[].course": ["'A'", "'B'"],
        "graduation_details[].meta.level": "'1'",
    }
    plan = _plan(filt, CFG_PREFILTER)
    assert plan["row_filter"] == {
        "graduation_details": {"@>": [{"z": 1}]},
        "graduation_details|": {"@>|": [[{"course": "A"}], [{"course": "B"}]]},
        "graduation_details||": {"@>": [{"meta": {"level": "1"}}]},
    }
    assert len(plan["element_where"]) == 2


def test_prefilter_skipped_for_cast_negation_comparison_null():
    plan = _plan({
        "graduation_details[].a::text": "'x'",
        "graduation_details[].b!": "'x'",
        "graduation_details[].c": {">=": "'x'"},
        "graduation_details[].d": "null",
        "graduation_details[].e": [1, 2],
    }, CFG_PREFILTER)
    assert plan["row_filter"] == {}
    assert len(plan["element_where"]) >= 5


def test_prefilter_requires_declared_column_flag():
    plan = _plan({"graduation_details[].course": "'A'"}, {"columns": {"graduation_details": {"prefilter": False}}})
    assert plan["row_filter"] == {}
