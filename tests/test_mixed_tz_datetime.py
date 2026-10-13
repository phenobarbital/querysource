import pandas as pd

from querysource.types.dt.transforms import (
    case,
    convert_to_datetime,
    fill_column,
    fill_nulls,
    to_timestamp,
)


def _df():
    return pd.DataFrame({"d": [
        "2025-09-15T11:23:30.231-07:00",
        "2025-12-01T08:00:00.000-08:00",
        None,
    ]})


def test_convert_to_datetime_mixed_offsets_default_normalizes_to_utc():
    df = convert_to_datetime(_df(), "d")
    assert str(df["d"].dtype) == "datetime64[ns, UTC]"
    assert df["d"][0] == pd.Timestamp("2025-09-15T18:23:30.231Z")
    assert df["d"][1] == pd.Timestamp("2025-12-01T16:00:00Z")
    assert pd.isna(df["d"][2])


def test_convert_to_datetime_mixed_offsets_infer_tz_keeps_offsets():
    df = convert_to_datetime(_df(), "d", infer_tz=True)
    assert df["d"][0] == pd.Timestamp("2025-09-15T11:23:30.231-07:00")
    assert df["d"][0].utcoffset() == pd.Timedelta(hours=-7)
    assert df["d"][1].utcoffset() == pd.Timedelta(hours=-8)
    assert pd.isna(df["d"][2])


def test_to_timestamp_mixed_offsets():
    df = to_timestamp(_df(), "d")
    assert df["d"][1] == pd.Timestamp("2025-12-01T16:00:00Z")
    df = to_timestamp(_df(), "d", infer_tz=True)
    assert df["d"][1].utcoffset() == pd.Timedelta(hours=-8)


def test_convert_to_datetime_single_offset_keeps_timezone():
    df = pd.DataFrame({"d": ["2025-09-15T11:23:30-07:00", None]})
    df = convert_to_datetime(df, "d")
    assert df["d"].dt.tz is not None
    assert df["d"][0].utcoffset() == pd.Timedelta(hours=-7)
    assert pd.isna(df["d"][1])


def test_convert_to_datetime_naive_is_datetime64_ns():
    df = pd.DataFrame({"d": ["2025-09-15 11:23:30", "garbage", None]})
    df = convert_to_datetime(df, "d")
    assert str(df["d"].dtype) == "datetime64[ns]"
    assert df["d"][0] == pd.Timestamp("2025-09-15 11:23:30")
    assert pd.isna(df["d"][1])
    assert pd.isna(df["d"][2])


def test_fill_nulls_non_string_value_on_string_column():
    df = pd.DataFrame({"w": pd.array(["Juan", None, ""], dtype="string")})
    df = fill_column(df, "nulls", False)
    df = fill_nulls(df, "w", "nulls")
    assert df["w"].tolist() == ["Juan", False, False]
    df = case(df, "erp", column="w", condition=False, match=False, notmatch=True)
    assert df["erp"].tolist() == [True, False, False]
