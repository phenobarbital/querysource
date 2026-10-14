"""FEAT-174 — timezone transforms hosted in QuerySource."""
import pandas as pd

from querysource.types.dt.transforms import (
    _to_datetime_series,
    _to_wall_clock,
    parse_datetime_series,
    to_wall_clock,
)

MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
NAIVE_AWARE = [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-09-16T01:00:00"), None]
OFFSET_DATES = ["2025-09-15-07:00", "2025-12-01-08:00", "2025-09-15Z", "2025-09-15", None]
DST_AMBIGUOUS = ["2025-11-03 09:00:00", "2025-11-02 01:30:00"]     # America/Chicago, unsorted
DST_NONEXISTENT = ["2025-03-09 02:30:00", "2025-03-10 09:00:00"]  # America/Chicago


def test_public_helpers_delegate():
    """Public wrappers equal the private helpers on MIXED, NAIVE_AWARE, naive and all-null input."""
    inputs = [
        pd.Series(MIXED, name="d"),
        pd.Series(NAIVE_AWARE, name="d", dtype=object),
        pd.Series(["2025-09-15 10:00", None], name="d"),
        pd.Series([None, None], name="d"),
    ]
    for s in inputs:
        for infer_tz in (False, True):
            pd.testing.assert_series_equal(
                parse_datetime_series(s, infer_tz=infer_tz), _to_datetime_series(s, infer_tz=infer_tz)
            )
        pd.testing.assert_series_equal(to_wall_clock(s), _to_wall_clock(s))
