"""FEAT-171: pandas 3 mixed-offset datetime parsing helpers and callers."""
import datetime

import pandas as pd

from querysource.types.dt.transforms import (
    _has_mixed_timezones,
    _parse_offset_dates,
    _to_datetime_series,
    _to_wall_clock,
    change_timezone,
    convert_to_datetime,
    to_excel_date,
    to_timestamp,
)

MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
NAIVE_AWARE = [
    pd.Timestamp("2025-09-15T23:30:00-07:00"),
    pd.Timestamp("2025-09-16T01:00:00"),
    None,
]


def test_reconvert_infer_tz_output_keeps_all_values():
    """Converting infer_tz output again does not introduce a NaT."""
    initial = convert_to_datetime(pd.DataFrame({"d": MIXED}), "d", infer_tz=True)
    default = convert_to_datetime(initial.copy(), "d")
    preserved = convert_to_datetime(initial.copy(), "d", infer_tz=True)

    assert str(default["d"].dtype) == "datetime64[ns, UTC]"
    assert default["d"].tolist() == [
        pd.Timestamp("2025-09-16T06:30:00Z"),
        pd.Timestamp("2025-12-01T16:00:00Z"),
        pd.NaT,
    ]
    assert preserved["d"].notna().sum() == 2
    assert preserved["d"].iloc[0].utcoffset() == pd.Timedelta(hours=-7)
    assert preserved["d"].iloc[1].utcoffset() == pd.Timedelta(hours=-8)


def test_aware_python_datetimes_mixed_offsets():
    """Aware Python datetimes with different offsets retain both values."""
    values = [
        datetime.datetime(2025, 9, 15, 23, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=-7))),
        datetime.datetime(2025, 12, 1, 8, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-8))),
        None,
    ]

    default = _to_datetime_series(pd.Series(values))
    preserved = _to_datetime_series(pd.Series(values), infer_tz=True)

    assert default.notna().sum() == 2
    assert str(default.dtype) == "datetime64[ns, UTC]"
    assert preserved.notna().sum() == 2
    assert preserved.iloc[0].utcoffset() == pd.Timedelta(hours=-7)
    assert preserved.iloc[1].utcoffset() == pd.Timedelta(hours=-8)


def test_has_mixed_timezones():
    """Timezone-state classification distinguishes homogeneous and mixed values."""
    assert _has_mixed_timezones(
        pd.Series([pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-12-01T08:00:00-08:00")])
    )
    assert _has_mixed_timezones(pd.Series(NAIVE_AWARE))
    assert _has_mixed_timezones(pd.Series(list(reversed(NAIVE_AWARE))))
    assert _has_mixed_timezones(
        pd.Series([pd.Timestamp("2025-09-15T23:30:00Z"), pd.Timestamp("2025-09-15T23:30:00+01:00")])
    )
    assert not _has_mixed_timezones(pd.Series([pd.Timestamp("2025-09-15"), pd.NaT]))
    assert not _has_mixed_timezones(
        pd.Series([pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-09-16T01:00:00-07:00")])
    )
    assert not _has_mixed_timezones(pd.Series([pd.NaT, None]))


def test_naive_aware_mixtures_keep_all_values(caplog):
    """Naive and aware values normalize or retain their states as requested."""
    cases = [
        NAIVE_AWARE,
        list(reversed(NAIVE_AWARE)),
        [value.to_pydatetime() if isinstance(value, pd.Timestamp) else value for value in NAIVE_AWARE],
        ["2025-09-15T23:30:00-07:00", "2025-09-16T01:00:00", None],
    ]
    for values in cases:
        series = pd.Series(values, index=[4, 6, 8], name="mixed_dates")
        default = _to_datetime_series(series, field="mixed_dates")
        preserved = _to_datetime_series(series, infer_tz=True, field="mixed_dates")

        assert default.notna().sum() == 2
        assert str(default.dtype) == "datetime64[ns, UTC]"
        assert default.index.equals(series.index)
        assert default.name == series.name
        assert default.isna().equals(series.isna())
        assert preserved.notna().sum() == 2
        assert preserved.dtype == object
        assert preserved.index.equals(series.index)
        assert preserved.name == series.name
        assert preserved.isna().equals(series.isna())
    assert "mixed_dates" in caplog.text


def test_to_wall_clock_modes():
    """Wall-clock conversion drops offsets without changing local times."""
    naive = _to_wall_clock(pd.Series(["2025-09-15T23:30:00", None]))
    single_offset = _to_wall_clock(pd.Series(["2025-09-15T23:30:00-07:00", None]))
    mixed = _to_wall_clock(pd.Series(MIXED))
    naive_aware = _to_wall_clock(pd.Series(NAIVE_AWARE))

    for values in [naive, single_offset, mixed, naive_aware]:
        assert str(values.dtype) == "datetime64[ns]"
    assert naive.iloc[0] == pd.Timestamp("2025-09-15T23:30:00")
    assert single_offset.iloc[0] == pd.Timestamp("2025-09-15T23:30:00")
    assert mixed.iloc[0] == pd.Timestamp("2025-09-15T23:30:00")
    assert mixed.iloc[1] == pd.Timestamp("2025-12-01T08:00:00")
    assert naive_aware.iloc[0] == pd.Timestamp("2025-09-15T23:30:00")
    assert naive_aware.iloc[1] == pd.Timestamp("2025-09-16T01:00:00")


def test_offset_date_parsed_as_midnight():
    """Date-only values carrying offsets are rewritten to local midnight."""
    raw = pd.Series(["2025-09-15-07:00", "2025-09-15Z", "2025-09-15"])
    parsed = _parse_offset_dates(raw)

    assert parsed.tolist() == ["2025-09-15T00:00:00-07:00", "2025-09-15T00:00:00Z", "2025-09-15"]
    for converter in [convert_to_datetime, to_timestamp]:
        result = converter(pd.DataFrame({"d": raw}), "d", infer_tz=True)
        assert result["d"].iloc[0] == pd.Timestamp("2025-09-15T00:00:00-07:00")
        assert result["d"].iloc[1] == pd.Timestamp("2025-09-15T00:00:00Z")
        assert result["d"].iloc[2] == pd.Timestamp("2025-09-15T00:00:00")


def test_to_excel_date_mixed_offsets():
    """Mixed strings and infer_tz object input become UTC-naive without NaT."""
    expected = [
        pd.Timestamp("2025-09-16T06:30:00"),
        pd.Timestamp("2025-12-01T16:00:00"),
        pd.NaT,
    ]
    inputs = [
        pd.DataFrame({"d": MIXED}),
        convert_to_datetime(pd.DataFrame({"d": MIXED}), "d", infer_tz=True),
    ]

    for frame in inputs:
        result = to_excel_date(frame.copy(), "d")
        assert str(result["d"].dtype) == "datetime64[ns]"
        assert result["d"].tolist() == expected
        assert result["d"].notna().sum() == 2


def test_change_timezone_mixed_and_aware(caplog):
    """Mixed and aware input converts directly; naive input retains localization."""
    expected_mixed = [
        pd.Timestamp("2025-09-16T02:30:00-04:00"),
        pd.Timestamp("2025-12-01T11:00:00-05:00"),
        pd.NaT,
    ]
    mixed = change_timezone(pd.DataFrame({"d": MIXED}), "d", to_tz="America/New_York")
    assert mixed["d"].tolist() == expected_mixed
    assert mixed["d"].notna().sum() == 2
    assert "ignoring from_tz" not in caplog.text

    caplog.clear()
    aware = convert_to_datetime(pd.DataFrame({"d": MIXED}), "d", infer_tz=True)
    aware = change_timezone(aware, "d", from_tz="UTC", to_tz="America/New_York")
    assert aware["d"].tolist() == expected_mixed
    assert aware["d"].notna().sum() == 2
    assert "change_timezone: 'd' is already timezone-aware" in caplog.text
    assert "ignoring from_tz='UTC'" in caplog.text

    caplog.clear()
    naive = change_timezone(
        pd.DataFrame({"d": ["2025-09-15T23:30:00", None]}),
        "d",
        from_tz="UTC",
        to_tz="America/New_York",
    )
    assert naive["d"].tolist() == [pd.Timestamp("2025-09-15T19:30:00-04:00"), pd.NaT]
    assert "ignoring from_tz" not in caplog.text
