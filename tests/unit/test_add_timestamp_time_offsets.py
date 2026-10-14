"""Regression coverage for timezone-bearing time values in date/time composition."""

import datetime as dt

import pandas as pd
import pytest

from querysource.types.dt.transforms import add_timestamp_to_time


@pytest.mark.parametrize(
    "time, expected",
    [
        ("10:30:00+05:30", "2025-09-15T10:30:00+05:30"),
        ("10:30:00-07:00", "2025-09-15T10:30:00-07:00"),
        ("10:30:00Z", "2025-09-15T10:30:00Z"),
        ("10:30:00.123456789+05:30", "2025-09-15T10:30:00.123456789+05:30"),
        (dt.time(10, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))), "2025-09-15T10:30:00+05:30"),
    ],
)
def test_preserves_time_offset(time: object, expected: str) -> None:
    """Both string and native time inputs retain their offset and precision."""
    frame = pd.DataFrame({"date": ["2025-09-15"], "time": [time]})
    result = add_timestamp_to_time(frame, "combined", "date", "time")
    assert result is frame
    assert result["combined"].iloc[0] == pd.Timestamp(expected)
    assert result["combined"].iloc[0].utcoffset() == pd.Timestamp(expected).utcoffset()


def test_mixed_offsets_naive_and_null_with_duplicate_index() -> None:
    """Each row keeps its timezone state without index alignment or UTC conversion."""
    frame = pd.DataFrame(
        {
            "date": ["2025-09-15"] * 5 + [None],
            "time": ["10:30+05:30", "10:30-07:00", "10:30Z", "10:30", None, "10:30Z"],
        },
        index=[7] * 6,
    )
    result = add_timestamp_to_time(frame, "combined", "date", "time")["combined"]
    expected = [
        pd.Timestamp("2025-09-15T10:30+05:30"),
        pd.Timestamp("2025-09-15T10:30-07:00"),
        pd.Timestamp("2025-09-15T10:30Z"),
        pd.Timestamp("2025-09-15T10:30"),
    ]
    assert list(result.iloc[:4]) == expected
    assert [value.utcoffset() for value in result.iloc[:4]] == [value.utcoffset() for value in expected]
    assert result.iloc[4:].isna().all()
    assert result.index.equals(frame.index)
