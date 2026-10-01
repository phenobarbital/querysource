"""FEAT-161: dt transforms under pandas 3 (copy-on-write, native str dtype)."""

import pandas as pd

from querysource.types.dt.transforms import epoch_to_date, string_to_date, to_json


def test_to_json_fills_missing_with_empty_list():
    """to_json must persist the fillna result under copy-on-write."""
    df = pd.DataFrame({"payload": ["{'a': 1}", None]})

    result = to_json(df, "payload")

    assert result["payload"].tolist() == [{"a": 1}, []]


def test_string_to_date_keeps_none_for_invalid_values():
    """string_to_date preserves the None-for-NaT output consumers rely on."""
    df = pd.DataFrame({"src": ["2026-01-02", "not-a-date"]})

    result = string_to_date(df, "dst", column="src")

    assert result["dst"].iloc[0] == pd.Timestamp("2026-01-02")
    assert result["dst"].iloc[1] is None


def test_epoch_to_date_converts_milliseconds():
    """epoch_to_date converts epoch milliseconds into timestamps."""
    df = pd.DataFrame({"epoch": [1767312000000, None]})

    result = epoch_to_date(df, "epoch")

    assert result["epoch"].iloc[0] == pd.Timestamp("2026-01-02")
    assert pd.isna(result["epoch"].iloc[1])
