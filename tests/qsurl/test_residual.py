"""Residual evaluator semantics (spec §3 M7, AC11/AC13/AC14)."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

import querysource.qsurl.residual as residual
from querysource.qsurl import QSUrlError, ResidualPlan


def test_module_never_uses_eval():
    src = Path(residual.__file__).read_text(encoding="utf-8")
    for banned in ("eval(", "exec(", ".query(", ".eval("):
        assert banned not in src


def test_empty_plan_returns_rows_untouched(stores_df):
    assert residual.apply(stores_df, ResidualPlan()) is stores_df


@pytest.mark.parametrize(
    "leaf,expected_ids",
    [
        ({"column": "city", "expression": "istartswith", "value": "SAN"}, [1, 2]),
        ({"column": "city", "expression": "startswith", "value": "SAN"}, []),
        ({"column": "city", "expression": "startswith", "value": "San"}, [1]),
        ({"column": "city", "expression": "contains", "value": "san"}, [2]),
        ({"column": "city", "expression": "icontains", "value": "SAN"}, [1, 2]),
        ({"column": "city", "expression": "iregex", "value": "^SAN "}, [1, 2]),
        ({"column": "city", "expression": "is_null"}, [3, 4]),
        ({"column": "city", "expression": "contains", "value": "a.b"}, []),
        (
            {
                "column": "opened",
                "expression": ">=",
                "value": "2024-01-01T00:00:00+02:00",
                "dtype": "datetime",
            },
            [1, 8],
        ),
    ],
)
def test_leaf_semantics(stores_df, leaf, expected_ids):
    mask = residual.evaluate(stores_df, {"and": [leaf]})
    matched = sorted(stores_df.loc[mask, "store_id"].tolist())
    assert matched == expected_ids


def test_apply_order(stores_df):
    plan = ResidualPlan(
        filter={"and": [{"column": "state_code", "expression": "==", "value": ["CA", "NY"]}]},
        sort=(("price", True),),
        project=("store_id", "price"),
        distinct=True,
        offset=1,
        limit=2,
        rename=(("price", "amount"),),
    )
    result = residual.apply(stores_df, plan)
    assert result.to_dict("records") == [
        {"store_id": 1, "amount": 19.99},
        {"store_id": 2, "amount": 5.5},
    ]


def test_list_roundtrip(stores_df):
    rows = stores_df.to_dict("records")
    plan = ResidualPlan(project=("store_id", "name"))
    result = residual.apply(rows, plan)
    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)
    assert result[0] == {"store_id": 1, "name": "Acme Store"}


def test_unknown_column_is_lower_error(stores_df):
    plan = ResidualPlan(project=("does_not_exist",))
    with pytest.raises(QSUrlError) as exc:
        residual.apply(stores_df, plan)
    assert exc.value.kind == "lower"
    assert "does_not_exist" in exc.value.message


def test_bad_regex_is_lower_error(stores_df):
    leaf = {"column": "city", "expression": "regex", "value": "("}
    with pytest.raises(QSUrlError) as exc:
        residual.evaluate(stores_df, {"and": [leaf]})
    assert exc.value.kind == "lower"


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+",
        "(a*)+",
        "(a+)*",
        "(.*)*",
        "(x+)+$",
    ],
)
def test_nested_quantifier_regex_is_rejected(stores_df, pattern):
    """Ledger issue:2241b8e60919: reject the classic catastrophic-backtracking
    shape before it ever reaches ``str.contains``."""
    leaf = {"column": "city", "expression": "regex", "value": pattern}
    with pytest.raises(QSUrlError) as exc:
        residual.evaluate(stores_df, {"and": [leaf]})
    assert exc.value.kind == "lower"
    assert "nested quantifier" in exc.value.message


def test_overlong_regex_is_rejected(stores_df):
    """Ledger issue:2241b8e60919: cap pattern length regardless of shape."""
    leaf = {"column": "city", "expression": "regex", "value": "a" * 201}
    with pytest.raises(QSUrlError) as exc:
        residual.evaluate(stores_df, {"and": [leaf]})
    assert exc.value.kind == "lower"
    assert "too long" in exc.value.message


def test_safe_regex_still_matches(stores_df):
    """A normal, bounded pattern is unaffected by the new guard."""
    mask = residual.evaluate(stores_df, {"and": [{"column": "city", "expression": "regex", "value": "^San.*"}]})
    matched = sorted(stores_df.loc[mask, "store_id"].tolist())
    assert matched


BYPASS_PATTERNS = ["(a|a)+$", "(a|aa)*b$", "((a+))+$", "(a+){2,}$", "(.*a){25}$"]


@pytest.mark.parametrize("pattern", BYPASS_PATTERNS)
def test_screen_bypass_patterns_run_in_linear_time(pattern):
    """Ledger issue:2241b8e60919: patterns the static screen misses stay linear on RE2."""
    df = pd.DataFrame({"city": ["a" * 5000 + "!"] * 50})
    started = time.perf_counter()
    mask = residual.evaluate(df, {"column": "city", "expression": "regex", "value": pattern})
    assert time.perf_counter() - started < 1.0
    assert not mask.any()


@pytest.mark.parametrize("pattern", ["(?=a)(a|a)+$", "(?!x)a", "(?<=a)b", r"(a)\1"])
def test_lookaround_and_backreference_are_rejected(pattern):
    """Lookaround/backrefs used to force pandas onto Python `re`; RE2 rejects them."""
    df = pd.DataFrame({"city": ["a" * 26 + "!"]})
    started = time.perf_counter()
    with pytest.raises(QSUrlError) as exc:
        residual.evaluate(df, {"column": "city", "expression": "regex", "value": pattern})
    assert time.perf_counter() - started < 1.0
    assert exc.value.kind == "lower"
    assert exc.value.message.startswith("invalid regex")


def test_regex_never_uses_python_re_engine(stores_df, monkeypatch):
    """The regex leaves never reach `str.contains`, whose engine the pattern controls."""
    def _forbidden(*args, **kwargs):
        raise AssertionError("str.contains must not evaluate a user regex")

    monkeypatch.setattr(pd.core.strings.accessor.StringMethods, "contains", _forbidden)
    for expr, pattern in (("regex", "^San"), ("iregex", "^SAN ")):
        mask = residual.evaluate(stores_df, {"column": "city", "expression": expr, "value": pattern})
        assert mask.any()


def test_regex_fails_closed_without_pyarrow(stores_df, monkeypatch):
    """Without pyarrow the leaf refuses to run instead of falling back to Python `re`."""
    monkeypatch.setattr(residual, "pc", None)
    with pytest.raises(QSUrlError) as exc:
        residual.evaluate(stores_df, {"column": "city", "expression": "regex", "value": "^San"})
    assert exc.value.kind == "lower"
    assert "pyarrow" in exc.value.message


def test_regex_mask_semantics():
    """Search semantics, case handling, nulls and index alignment match the old behaviour."""
    df = pd.DataFrame({"city": ["San Jose", "SAN DIEGO", None, "x"]}, index=[10, 11, 12, 13])
    mask = residual.evaluate(df, {"column": "city", "expression": "regex", "value": "^SAN "})
    assert mask.tolist() == [False, True, False, False]
    assert mask.index.tolist() == [10, 11, 12, 13]
    mask = residual.evaluate(df, {"column": "city", "expression": "iregex", "value": "^SAN "})
    assert mask.tolist() == [True, True, False, False]
    numbers = pd.DataFrame({"n": [1, 22, None]})
    mask = residual.evaluate(numbers, {"column": "n", "expression": "regex", "value": "2"})
    assert mask.tolist() == [False, True, False]
