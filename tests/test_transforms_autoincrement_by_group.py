"""Regression tests for transforms.autoincrement_by_group under pandas 3.

Background: the transform used ``df.groupby(group_column).apply(fn)`` with a
callback that wrote ``group.at[idx, field] = i``. Two things broke on pandas 3:

* ``groupby().apply()`` no longer passes the grouping column to the callback,
  so the rebuilt frame lost ``group_column`` entirely.
* Writing an ``int`` into a ``str`` column raises ``TypeError``; the error was
  swallowed and the frame was returned untouched.

These tests pin the intended behaviour: per-group 1-based counters fill the
missing/empty slots, existing values and every other column are preserved,
and row order is unchanged.
"""
import numpy as np
import pandas as pd

from querysource.types.dt.transforms import autoincrement_by_group


def test_text_column_gets_per_group_counters():
    df = pd.DataFrame({
        "grp": ["a", "a", "b", "a", "b"],
        "code": ["x", None, "", None, None],
    })
    out = autoincrement_by_group(df, field="code", group_column="grp")
    assert list(out.columns) == ["grp", "code"]
    assert out["grp"].tolist() == ["a", "a", "b", "a", "b"]
    assert out["code"].tolist() == ["x", 1, 1, 2, 2]


def test_numeric_column_keeps_numeric_dtype():
    df = pd.DataFrame({
        "grp": ["a", "a", "b"],
        "code": [7.0, np.nan, np.nan],
    })
    out = autoincrement_by_group(df, field="code", group_column="grp")
    assert pd.api.types.is_numeric_dtype(out["code"])
    assert out["code"].tolist() == [7.0, 1.0, 1.0]
    assert "grp" in out.columns


def test_nothing_missing_returns_frame_unchanged():
    df = pd.DataFrame({"grp": ["a", "b"], "code": ["x", "y"]})
    out = autoincrement_by_group(df, field="code", group_column="grp")
    assert out["code"].tolist() == ["x", "y"]
    assert list(out.columns) == ["grp", "code"]
