"""Regression tests for filters.drop_duplicates (multi-column, order-stable).

Background: drop_duplicates previously did
``df.sort_values(by=columns).drop_duplicates(subset=columns, ...)``. The
``sort_values`` is unnecessary for ``keep='first'`` (drop_duplicates already
keeps the first occurrence in the frame's existing order) and is fragile on a
multi-column subset when a column holds mixed/unorderable values. The Filter
operator swallows the resulting error, so the rows are never de-duplicated and
the downstream upsert fails with a CardinalityViolation.

These tests pin the intended behaviour: de-duplicate on the *combination* of
the given columns, keep the first occurrence, and preserve the original order.
"""
import numpy as np
import pandas as pd

from querysource.types.dt.filters import drop_duplicates


def test_multicolumn_dedups_on_combination():
    df = pd.DataFrame({
        "user": ["u1", "u1", "u2", "u2", "u3"],
        "content_id": [5, 5, 7, 8, 9],  # (u1,5) is the only real duplicate
    })
    out = drop_duplicates(df, columns=["user", "content_id"], keep="first")
    # 5 rows, one duplicate pair (u1,5) collapses -> 4 unique combinations
    assert len(out) == 4
    assert out.duplicated(subset=["user", "content_id"]).sum() == 0


def test_keep_first_preserves_original_order():
    df = pd.DataFrame({
        "user": ["b", "a", "b", "a"],
        "content_id": [2, 1, 2, 1],  # rows 2 and 3 duplicate rows 0 and 1
    })
    out = drop_duplicates(df, columns=["user", "content_id"], keep="first")
    # first occurrences kept, in their original order (b,2) then (a,1)
    assert list(zip(out["user"], out["content_id"])) == [("b", 2), ("a", 1)]


def test_multicolumn_with_mixed_and_null_values_does_not_raise():
    # An object column mixing ints, strings and NaN must not blow up the dedup.
    df = pd.DataFrame({
        "user": ["u1", "u1", "u2", "u2"],
        "content_id": [10, 10, np.nan, "abc"],
    })
    out = drop_duplicates(df, columns=["user", "content_id"], keep="first")
    # only (u1,10) repeats -> 3 rows remain
    assert len(out) == 3


def test_single_column_still_works():
    df = pd.DataFrame({"user": ["u1", "u1", "u2"], "content_id": [1, 2, 3]})
    out = drop_duplicates(df, columns=["user"], keep="first")
    assert len(out) == 2  # one row per user
