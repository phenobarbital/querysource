"""Regression tests for filters.drop_columns.

Background: ``drop_columns`` used to call
``df.drop(axis=1, columns=columns, inplace=True, errors="ignore")``. Passing
``axis`` together with ``columns`` was tolerated by pandas 2 but is a hard
``TypeError`` in pandas 3, so the exact-name mode crashed. The ``endswith`` and
``startswith`` modes had a separate bug: they assigned the result to an unused
``dataframe`` local and returned the untouched ``df``, so they never dropped
anything on any pandas version.

These tests pin the intended behaviour of all three modes.
"""
import pandas as pd
import pytest

from querysource.types.dt.filters import drop_columns


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": [1, 2],
        "name_left": ["a", "b"],
        "name_right": ["c", "d"],
        "tmp_value": [0.1, 0.2],
    })


def test_columns_mode_drops_exact_names(df):
    out = drop_columns(df, columns=["name_left", "tmp_value"])
    assert list(out.columns) == ["id", "name_right"]


def test_columns_mode_ignores_missing_names(df):
    out = drop_columns(df, columns=["does_not_exist", "id"])
    assert list(out.columns) == ["name_left", "name_right", "tmp_value"]


def test_endswith_mode_drops_matching_suffixes(df):
    out = drop_columns(df, endswith=["_left", "_right"])
    assert list(out.columns) == ["id", "tmp_value"]


def test_startswith_mode_drops_matching_prefixes(df):
    out = drop_columns(df, startswith=["tmp_", "name_"])
    assert list(out.columns) == ["id"]


def test_no_criteria_returns_frame_unchanged(df):
    out = drop_columns(df)
    assert list(out.columns) == list(df.columns)
