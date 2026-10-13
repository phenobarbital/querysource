"""FEAT-171: pandas 3 mixed-offset datetime parsing helpers and callers."""
import datetime

import pandas as pd

from querysource.types.dt.transforms import (
    _has_mixed_timezones,
    _parse_offset_dates,
    _to_datetime_series,
    _to_wall_clock,
    _vectorised_datetime_series,
    add_timestamp_to_time,
    change_timezone,
    convert_to_datetime,
    extract,
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


def test_extract_mixed_offsets():
    """Components use every mixed-offset value's local wall-clock time."""
    frame = pd.DataFrame({"source": MIXED})

    extract(frame, "day", column="source", value="day")
    extract(frame, "month", column="source", value="month")
    extract(frame, "hour", column="source", value="hour")

    assert frame["day"].iloc[:2].tolist() == [15.0, 1.0]
    assert frame["day"].isna().iloc[2]
    assert frame["month"].tolist() == [9, 12, pd.NA]
    assert frame["hour"].iloc[:2].tolist() == [23.0, 8.0]
    assert frame["hour"].isna().iloc[2]
    assert frame["source"].iloc[0].utcoffset() == pd.Timedelta(hours=-7)
    assert frame["source"].iloc[1].utcoffset() == pd.Timedelta(hours=-8)


def test_extract_ldom_preserves_timezone():
    """Month-end output retains each input's timezone, local time, and nulls."""
    cases = [
        (MIXED, [pd.Timestamp("2025-09-30T23:30:00-07:00"), pd.Timestamp("2025-12-31T08:00:00-08:00"), pd.NaT]),
        (
            NAIVE_AWARE,
            [pd.Timestamp("2025-09-30T23:30:00-07:00"), pd.Timestamp("2025-09-30T01:00:00"), pd.NaT],
        ),
    ]

    for values, expected in cases:
        frame = pd.DataFrame({"source": values})
        extract(frame, "month_end", column="source", value="ldom")

        assert frame["month_end"].tolist() == expected
        assert frame["source"].iloc[0].utcoffset() == pd.Timedelta(hours=-7)
        assert frame["month_end"].iloc[0].utcoffset() == pd.Timedelta(hours=-7)
        assert frame["month_end"].isna().iloc[2]


def test_add_timestamp_to_time_mixed():
    """Mixed inputs combine local parts, keep fractional seconds, and propagate nulls."""
    frame = pd.DataFrame(
        {
            "date": ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00", None],
            "time": ["1900-01-01T01:02:03.456789-08:00", "1900-01-01T10:11:12.123456-07:00", "00:00:00"],
        }
    )

    add_timestamp_to_time(frame, "combined", "date", "time")

    assert frame["combined"].tolist() == [
        pd.Timestamp("2025-09-15T01:02:03.456789"),
        pd.Timestamp("2025-12-01T10:11:12.123456"),
        pd.NaT,
    ]


MIXED_PRECISION = [
    "2025-09-15T23:30:00-07:00",
    "2025-12-01T08:00:00.123-08:00",
    None,
]


def test_mixed_iso_precision_keeps_all_values():
    """Seconds-only and fractional ISO strings keep every value and fraction."""
    expected_utc = [
        pd.Timestamp("2025-09-16T06:30:00Z"),
        pd.Timestamp("2025-12-01T16:00:00.123Z"),
        pd.NaT,
    ]
    expected_local = [
        pd.Timestamp("2025-09-15T23:30:00-07:00"),
        pd.Timestamp("2025-12-01T08:00:00.123-08:00"),
        pd.NaT,
    ]
    expected_new_york = [
        pd.Timestamp("2025-09-16T02:30:00-04:00"),
        pd.Timestamp("2025-12-01T11:00:00.123-05:00"),
        pd.NaT,
    ]
    time_values = [
        "1900-01-01T01:02:03.456789-08:00",
        "1900-01-01T10:11:12.123456-07:00",
        None,
    ]
    expected_combined = [
        pd.Timestamp("2025-09-15T01:02:03.456789"),
        pd.Timestamp("2025-12-01T10:11:12.123456"),
        pd.NaT,
    ]

    for values in (MIXED_PRECISION, list(reversed(MIXED_PRECISION))):
        for dtype in ("object", "string"):
            date_values = pd.Series(values, dtype=dtype)
            frame = pd.DataFrame({"d": date_values})
            for converter in (convert_to_datetime, to_timestamp):
                utc = converter(frame.copy(), "d", infer_tz=False)["d"]
                preserved = converter(frame.copy(), "d", infer_tz=True)["d"]
                assert utc.notna().sum() == 2
                assert preserved.notna().sum() == 2
                assert utc.tolist() == (expected_utc if values[0] == MIXED_PRECISION[0] else expected_utc[::-1])
                # the converters expose nulls as None on object output
                expected_preserved = [
                    None if pd.isna(item) else item
                    for item in (expected_local if values[0] == MIXED_PRECISION[0] else expected_local[::-1])
                ]
                assert preserved.tolist() == expected_preserved
                assert preserved.iloc[values.index(MIXED_PRECISION[1])].microsecond == 123000

            excel = to_excel_date(frame.copy(), "d")["d"]
            expected_excel = [item if pd.isna(item) else item.tz_localize(None) for item in expected_utc]
            assert excel.tolist() == (expected_excel if values[0] == MIXED_PRECISION[0] else expected_excel[::-1])

            converted = change_timezone(frame.copy(), "d", to_tz="America/New_York")["d"]
            assert converted.tolist() == (
                expected_new_york if values[0] == MIXED_PRECISION[0] else expected_new_york[::-1]
            )

            extracted = pd.DataFrame({"source": date_values})
            extract(extracted, "day", column="source", value="day")
            expected_days = [15.0, 1.0, float("nan")]
            if values[0] != MIXED_PRECISION[0]:
                expected_days = expected_days[::-1]
            assert extracted["day"].isna().tolist() == [pd.isna(day) for day in expected_days]
            assert extracted["day"].dropna().tolist() == [day for day in expected_days if not pd.isna(day)]
            assert extracted["source"].notna().sum() == 2

            combined = pd.DataFrame(
                {
                    "date": date_values,
                    "time": pd.Series(
                        time_values if values[0] == MIXED_PRECISION[0] else list(reversed(time_values)), dtype=dtype
                    ),
                }
            )
            add_timestamp_to_time(combined, "combined", "date", "time")
            assert combined["combined"].notna().sum() == 2
            assert combined["combined"].tolist() == (
                expected_combined if values[0] == MIXED_PRECISION[0] else expected_combined[::-1]
            )


def test_naive_and_single_offset_regressions():
    """Working naive and single-offset inputs retain their literal outputs."""
    naive = ["2025-09-15T23:30:00", None]
    single_offset = ["2025-09-15T23:30:00-07:00", None]

    assert to_excel_date(pd.DataFrame({"d": naive}), "d")["d"].tolist() == [
        pd.Timestamp("2025-09-15T23:30:00"),
        pd.NaT,
    ]
    assert to_excel_date(pd.DataFrame({"d": single_offset}), "d")["d"].tolist() == [
        pd.Timestamp("2025-09-16T06:30:00"),
        pd.NaT,
    ]

    naive_tz = change_timezone(
        pd.DataFrame({"d": naive}), "d", from_tz="UTC", to_tz="America/New_York"
    )["d"]
    assert naive_tz.tolist() == [pd.Timestamp("2025-09-15T19:30:00-04:00"), pd.NaT]
    aware_tz = change_timezone(
        pd.DataFrame({"d": single_offset}), "d", from_tz="UTC", to_tz="America/New_York"
    )["d"]
    assert aware_tz.tolist() == [pd.Timestamp("2025-09-16T02:30:00-04:00"), pd.NaT]

    source = pd.DataFrame({"source": naive})
    extract(source, "month_end", column="source", value="ldom")
    assert source["month_end"].tolist() == [pd.Timestamp("2025-09-30T23:30:00"), pd.NaT]
    assert source["source"].tolist() == [pd.Timestamp("2025-09-15T23:30:00"), pd.NaT]

    aware_source = pd.DataFrame({"source": single_offset})
    extract(aware_source, "month_end", column="source", value="ldom")
    assert aware_source["month_end"].tolist() == [pd.Timestamp("2025-09-30T23:30:00-07:00"), pd.NaT]
    assert aware_source["source"].iloc[0] == pd.Timestamp("2025-09-15T23:30:00-07:00")

    combined = pd.DataFrame(
        {
            "date": naive,
            "time": ["1900-01-01T01:02:03.456789", None],
        }
    )
    add_timestamp_to_time(combined, "combined", "date", "time")
    assert combined["combined"].tolist() == [pd.Timestamp("2025-09-15T01:02:03.456789"), pd.NaT]


def test_time_of_day_and_timedelta_inputs_are_not_lost():
    """datetime.time and timedelta columns never silently become NaT."""
    import datetime as dt

    frame = pd.DataFrame(
        {
            "t": pd.Series([dt.time(1, 2, 3), dt.time(10, 11, 12)], dtype=object),
            "d": pd.to_datetime(["2025-09-15", "2025-12-01"]),
            "td": pd.to_timedelta(["01:02:03", "10:11:12"]),
        }
    )
    extract(frame, "hour", column="t", value="hour")
    assert frame["hour"].tolist() == [1, 10]
    add_timestamp_to_time(frame, "c1", "d", "t")
    add_timestamp_to_time(frame, "c2", "d", "td")
    expected = [pd.Timestamp("2025-09-15 01:02:03"), pd.Timestamp("2025-12-01 10:11:12")]
    assert frame["c1"].tolist() == expected
    assert frame["c2"].tolist() == expected


def test_out_of_ns_range_dates_do_not_raise():
    """Sentinel dates such as 9999-12-31 survive the wall-clock and UTC paths."""
    frame = pd.DataFrame(
        {
            "d": pd.Series(["9999-12-31", "2025-09-15"], dtype=object),
            "m": pd.Series(["9999-12-31T00:00:00-07:00", "2025-12-01T08:00:00-08:00"], dtype=object),
        }
    )
    wall = _to_wall_clock(frame["d"])
    assert wall.iloc[0].year == 9999
    utc = _to_datetime_series(frame["m"], infer_tz=False)
    assert utc.notna().all()
    assert utc.iloc[0].year == 9999


def _per_value_parse(series, infer_tz, monkeypatch):
    """Run ``_to_datetime_series`` with the vectorised fast path disabled."""
    from querysource.types.dt import transforms

    with monkeypatch.context() as patch:
        patch.setattr(transforms, "_vectorised_datetime_series", lambda values: None)
        return _to_datetime_series(series, infer_tz=infer_tz)


def test_vectorised_fast_path_matches_per_value_parsing(monkeypatch):
    """Uniform string columns take the fast path with identical results."""
    cases = [
        ["2025-09-15", "2025-12-01", None],
        ["2025-09-15 10:11:12", "2025-12-01 08:00:00.123456", "2025-12-01"],
        ["2025-09-15T10:00:00-07:00", "2025-09-16T10:00:00-07:00"],
        ["2025-09-15-07:00", "2025-09-16-07:00"],
        ["2025-09-15Z", "2025-09-16T10:00:00+00:00"],
        ["9999-12-31", "2025-09-15"],
        ["2025-09-15", ""],
        ["09/15/2025", "12/01/2025"],
    ]
    for values in cases:
        for dtype in (object, "string"):
            series = pd.Series(values, dtype=dtype)
            assert _vectorised_datetime_series(series) is not None, values
            for infer_tz in (False, True):
                got = _to_datetime_series(series, infer_tz=infer_tz)
                expected = _per_value_parse(series, infer_tz, monkeypatch)
                assert got.dtype == expected.dtype, values
                assert got.equals(expected), values


def test_vectorised_fast_path_declines_heterogeneous_input():
    """Mixed offsets, unparseable or non-string values use the per-value path."""
    cases = [
        MIXED,
        ["2025-09-15", "2025-09-15T10:00:00-07:00"],
        ["2025-09-15", "not a date"],
        [datetime.time(1, 2, 3), "2025-09-15"],
        [1, 2],
        [None, None],
    ]
    for values in cases:
        assert _vectorised_datetime_series(pd.Series(values, dtype=object)) is None, values


def test_vectorised_fast_path_still_warns_on_lost_values(caplog):
    """Empty strings parsed to NaT on the fast path are still reported."""
    with caplog.at_level("WARNING"):
        result = _to_datetime_series(pd.Series(["2025-09-15", ""], dtype=object), field="d")
    assert result.isna().tolist() == [False, True]
    assert "1 non-null value(s) could not be parsed" in caplog.text
