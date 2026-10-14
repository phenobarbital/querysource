"""FEAT-174 — timezone transforms hosted in QuerySource."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from querysource.types.dt.transforms import (
    _to_datetime_series,
    _to_wall_clock,
    change_timezone,
    convert_timezone,
    drop_timezone,
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


def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:
    """Mirror of Flowtask TransformRows drop_timezone (functions.py:408) — the behaviour being replaced."""
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_localize(None)
    if series.dtype == "object":
        return series.apply(
            lambda x: x.replace(tzinfo=None) if isinstance(x, (pd.Timestamp, datetime)) and x.tzinfo else x
        ).astype("datetime64[ns]")
    return series


def test_drop_timezone_mixed_strings():
    """MIXED strings -> naive [2025-09-15 23:30, 2025-12-01 08:00, NaT]; no str left."""
    df = pd.DataFrame({"d": MIXED})
    out = drop_timezone(df, "d")["d"]
    assert pd.api.types.is_datetime64_any_dtype(out)
    assert out.dt.tz is None
    assert out.iloc[0] == pd.Timestamp("2025-09-15 23:30:00")
    assert out.iloc[1] == pd.Timestamp("2025-12-01 08:00:00")
    assert pd.isna(out.iloc[2])
    assert not any(isinstance(v, str) for v in out)


def test_drop_timezone_offset_date():
    """'2025-09-15-07:00' -> 2025-09-15 00:00; '2025-09-15Z' -> midnight; plain date stays midnight."""
    out = drop_timezone(pd.DataFrame({"d": OFFSET_DATES}), "d")["d"]
    assert out.iloc[0] == pd.Timestamp("2025-09-15")
    assert out.iloc[1] == pd.Timestamp("2025-12-01")
    assert out.iloc[2] == pd.Timestamp("2025-09-15")
    assert out.iloc[3] == pd.Timestamp("2025-09-15")
    assert pd.isna(out.iloc[4])


def test_drop_timezone_dtypes():
    """tz dtype (incl. UTC) -> tz_localize(None); naive unchanged; aware object mixed -> local wall-clock;
    numeric column copied unchanged; column != field leaves the source untouched."""
    base = pd.to_datetime(["2025-09-15 10:00", "2025-12-01 08:00"])
    # tz dtype
    df = pd.DataFrame({"d": base.tz_localize("America/Chicago")})
    out = drop_timezone(df, "d")["d"]
    assert out.dt.tz is None
    assert list(out) == list(base)
    # UTC dtype keeps UTC wall-clock
    df = pd.DataFrame({"d": base.tz_localize("UTC")})
    assert list(drop_timezone(df, "d")["d"]) == list(base)
    # naive unchanged
    df = pd.DataFrame({"d": base})
    pd.testing.assert_series_equal(drop_timezone(df, "d")["d"], pd.Series(base, name="d"))
    # aware object with mixed offsets
    aware = pd.Series(
        [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-12-01T08:00:00-08:00")],
        dtype=object,
    )
    out = drop_timezone(pd.DataFrame({"d": aware}), "d")["d"]
    assert list(out) == [pd.Timestamp("2025-09-15 23:30:00"), pd.Timestamp("2025-12-01 08:00:00")]
    # numeric unchanged
    df = pd.DataFrame({"n": [1, 2, 3]})
    pd.testing.assert_series_equal(drop_timezone(df, "n")["n"], pd.Series([1, 2, 3], name="n"))
    # column != field
    df = pd.DataFrame({"src": MIXED})
    res = drop_timezone(df, "dst", column="src")
    assert list(res["src"].iloc[:2]) == MIXED[:2]
    assert res["dst"].iloc[0] == pd.Timestamp("2025-09-15 23:30:00")


def test_drop_timezone_flowtask_parity():
    """On Flowtask's working inputs (tz dtype, aware object, naive) output equals the mirror."""
    base = pd.to_datetime(["2025-09-15 10:00", "2025-12-01 08:00"])
    aware_obj = pd.Series(
        [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-09-16T01:00:00-07:00")],
        dtype=object,
    )
    for series in (
        pd.Series(base.tz_localize("America/Chicago"), name="d"),
        pd.Series(base.tz_localize("UTC"), name="d"),
        aware_obj.rename("d"),
        pd.Series(base, name="d"),
    ):
        expected = _drop_timezone_like_flowtask(series)
        got = drop_timezone(series.to_frame(), "d")["d"]
        pd.testing.assert_series_equal(got, expected, check_dtype=False)


def test_change_timezone_existing_callers():
    """Positional change_timezone(df, field, from_tz, to_tz) calls that work today give identical output."""
    df = pd.DataFrame({"d": pd.to_datetime(["2025-09-15 10:00", "2025-12-01 08:00"])})
    out = change_timezone(df, "d", "America/Chicago", "UTC")["d"]
    assert str(out.dt.tz) == "UTC"
    assert list(out) == [pd.Timestamp("2025-09-15 15:00", tz="UTC"), pd.Timestamp("2025-12-01 14:00", tz="UTC")]
    df = pd.DataFrame({"d": ["2025-09-15T10:00:00-07:00", "2025-09-15T12:00:00-07:00"]})
    out = change_timezone(df, "d", None, "America/New_York")["d"]
    assert list(out.dt.strftime("%Y-%m-%d %H:%M")) == ["2025-09-15 13:00", "2025-09-15 15:00"]


def test_change_timezone_naive_without_from_tz():
    """Naive input, no from_tz -> localized as UTC and converted (today: column unchanged)."""
    df = pd.DataFrame({"d": pd.to_datetime(["2025-09-15 10:00", "2025-12-01 08:00"])})
    out = change_timezone(df, "d", to_tz="America/Chicago")["d"]
    assert list(out.dt.strftime("%Y-%m-%d %H:%M")) == ["2025-09-15 05:00", "2025-12-01 02:00"]


@pytest.mark.parametrize("func", [change_timezone, convert_timezone])
def test_timezone_mixed_strings(func):
    """MIXED -> America/New_York, same instants, no NaT, for both names."""
    df = pd.DataFrame({"d": MIXED})
    out = func(df, "d", to_tz="America/New_York")["d"]
    assert str(out.dt.tz) == "America/New_York"
    assert out.iloc[0] == pd.Timestamp("2025-09-16T06:30:00", tz="UTC")
    assert out.iloc[1] == pd.Timestamp("2025-12-01T16:00:00", tz="UTC")
    assert out.notna().sum() == 2
    assert pd.isna(out.iloc[2])


@pytest.mark.parametrize("func", [change_timezone, convert_timezone])
def test_timezone_aware_object_no_nat(func):
    """object of aware Timestamps -07:00/-08:00 -> all converted (Flowtask's silent NaT regression)."""
    aware = pd.Series(
        [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-12-01T08:00:00-08:00")],
        dtype=object,
    )
    out = func(pd.DataFrame({"d": aware}), "d", to_tz="UTC")["d"]
    assert out.notna().all()
    assert list(out) == [pd.Timestamp("2025-09-16T06:30:00", tz="UTC"), pd.Timestamp("2025-12-01T16:00:00", tz="UTC")]


def test_timezone_from_tz_warning(caplog):
    """Aware input + explicit from_tz -> warning logged; no warning when omitted."""
    df = pd.DataFrame({"d": ["2025-09-15T10:00:00-07:00"]})
    change_timezone(df.copy(), "d", to_tz="UTC")
    assert "ignoring from_tz" not in caplog.text
    caplog.clear()
    change_timezone(df.copy(), "d", from_tz="UTC", to_tz="UTC")
    assert "ignoring from_tz='UTC'" in caplog.text


def test_timezone_dst_policy():
    """America/Chicago: DST_AMBIGUOUS -> 01:30-06:00 (standard), whole column converted;
    DST_NONEXISTENT -> 2025-03-09 03:00-05:00, whole column converted."""
    out = change_timezone(
        pd.DataFrame({"d": DST_AMBIGUOUS}), "d", from_tz="America/Chicago", to_tz="America/Chicago"
    )["d"]
    assert out.notna().all()
    assert out.iloc[1] == pd.Timestamp("2025-11-02T01:30:00-06:00")
    assert out.iloc[0] == pd.Timestamp("2025-11-03T09:00:00-06:00")
    out = change_timezone(
        pd.DataFrame({"d": DST_NONEXISTENT}), "d", from_tz="America/Chicago", to_tz="America/Chicago"
    )["d"]
    assert out.notna().all()
    assert out.iloc[0] == pd.Timestamp("2025-03-09T03:00:00-05:00")
    assert out.iloc[1] == pd.Timestamp("2025-03-10T09:00:00-05:00")


def test_convert_timezone_tz_column():
    """Per-row zones incl. a null and an invalid zone -> default_timezone fallback;
    networkninja config (from_tz="UTC", tz_column="store_timezone") matches Flowtask on working input."""
    df = pd.DataFrame(
        {
            "d": pd.to_datetime(["2025-09-15 12:00", "2025-09-15 12:00", "2025-09-15 12:00", "2025-09-15 12:00", None]),
            "store_timezone": ["America/Chicago", "Asia/Tokyo", None, "Not/AZone", "America/Chicago"],
        }
    )
    out = convert_timezone(df, "d", from_tz="UTC", tz_column="store_timezone")["d"]
    assert out.iloc[0] == pd.Timestamp("2025-09-15 07:00", tz="America/Chicago")
    assert out.iloc[0].utcoffset() == pd.Timedelta(hours=-5)
    assert out.iloc[1].utcoffset() == pd.Timedelta(hours=9)
    assert out.iloc[1].hour == 21
    assert out.iloc[2].utcoffset() == pd.Timedelta(0)
    assert out.iloc[3].utcoffset() == pd.Timedelta(0)
    assert pd.isna(out.iloc[4])
    # Flowtask algorithm on the working rows
    expected = pd.Timestamp("2025-09-15 12:00", tz="UTC").tz_convert(ZoneInfo("America/Chicago"))
    assert out.iloc[0] == expected


def test_convert_timezone_is_alias():
    """convert_timezone(...) == change_timezone(..., to_tz=to_tz or default_timezone, ...)."""
    for to_tz, default in (("America/New_York", "UTC"), (None, "America/Chicago"), (None, "UTC")):
        a = convert_timezone(pd.DataFrame({"d": MIXED}), "d", to_tz=to_tz, default_timezone=default)
        b = change_timezone(
            pd.DataFrame({"d": MIXED}), "d", from_tz=None, to_tz=to_tz or default, default_timezone=default
        )
        pd.testing.assert_frame_equal(a, b)


def test_timezone_column_output():
    """column is the source, field the output; source column unchanged."""
    df = pd.DataFrame({"src": MIXED})
    for func in (change_timezone, convert_timezone):
        res = func(df.copy(), "dst", column="src", to_tz="America/New_York")
        assert list(res["src"].iloc[:2]) == MIXED[:2]
        assert res["dst"].notna().sum() == 2


def test_transforms_never_raise(caplog):
    """A missing column is logged with logging.error and the frame is returned (all three transforms)."""
    for func in (drop_timezone, change_timezone, convert_timezone):
        caplog.clear()
        df = pd.DataFrame({"a": [1]})
        res = func(df, "missing")
        assert res is df
        assert "missing" in caplog.text
