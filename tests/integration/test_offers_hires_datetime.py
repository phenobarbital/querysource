"""FEAT-171 M5: real-data validation of datetime transforms on a Workday export.

The CSV holds candidate PII: it is never committed, and assertion messages
report counts and column names only.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from querysource.types.dt.transforms import convert_to_datetime, drop_timezone, to_timestamp

COLUMNS = [
    "Candidate_Start_Date",
    "Job_Requisition_Date_Job_Posted",
    "Offer_Event_Offer_Sent",
    "Offer_Event_Offer_Completed",
    "Job_Application_Ready_for_Hire_Completed_Date",
]
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "artifacts" / "get_offers_and_hires_1.6.csv"


@pytest.fixture(scope="module")
def offers_hires() -> pd.DataFrame:
    """Load the CSV per the load contract; skip when the file is absent."""
    path = Path(os.environ.get("QS_OFFERS_HIRES_CSV", DEFAULT_CSV))
    if not path.is_file():
        pytest.skip(f"real-data CSV not found: set QS_OFFERS_HIRES_CSV (looked at {path})")
    return pd.read_csv(path, sep="|", dtype=str, usecols=COLUMNS)


def _reference(value: str) -> datetime:
    """Parse one source string independently (date-only form expanded to midnight).

    Args:
        value: Source string, ``YYYY-MM-DD±HH:MM`` or full ISO timestamp with offset.

    Returns:
        Aware datetime at the source offset.
    """
    value = value.strip()
    if len(value) == 16:
        value = f"{value[:10]}T00:00:00{value[10:]}"
    return datetime.fromisoformat(value)


def _drop_timezone(series: pd.Series) -> pd.Series:
    """Apply the real QuerySource drop_timezone to a one-column frame."""
    frame = series.to_frame(name="value")
    return drop_timezone(frame, "value")["value"]


def _source(offers_hires: pd.DataFrame, column: str) -> pd.Series:
    """Return the non-null source strings of a column."""
    return offers_hires[column].dropna()


def _run(offers_hires: pd.DataFrame, func, column: str, infer_tz: bool) -> pd.Series:
    """Apply ``func`` to a copy of the one-column frame and return the column."""
    frame = offers_hires[[column]].copy()
    return func(frame, column, infer_tz=infer_tz)[column]


@pytest.mark.parametrize("func", [convert_to_datetime, to_timestamp])
@pytest.mark.parametrize("infer_tz", [False, True])
@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_datetime_columns(offers_hires, func, infer_tz, column) -> None:
    """No value lost, misread or left as str; same instant as the source."""
    src = _source(offers_hires, column)
    result = _run(offers_hires, func, column, infer_tz)
    out = result.loc[src.index]
    assert int(result.notna().sum()) == len(src), f"{column}: non-null count changed"
    assert out.notna().all(), f"{column}: values lost"
    assert not any(isinstance(v, str) for v in out), f"{column}: str values left"

    refs = [_reference(v) for v in src]
    got = list(out)
    bad_instant = sum(1 for g, r in zip(got, refs, strict=True) if g.to_pydatetime() != r)
    assert bad_instant == 0, f"{column}: {bad_instant} values with a different instant"

    if not infer_tz:
        assert str(result.dtype) == "datetime64[ns, UTC]", f"{column}: dtype {result.dtype}"
    else:
        bad_off = sum(1 for g, r in zip(got, refs, strict=True) if g.utcoffset() != r.utcoffset())
        assert bad_off == 0, f"{column}: {bad_off} values with a different offset"
        if column == "Candidate_Start_Date":
            bad_mid = sum(
                1 for g in got if (g.hour, g.minute, g.second, g.microsecond) != (0, 0, 0, 0)
            )
            assert bad_mid == 0, f"{column}: {bad_mid} values not at local midnight"

    # idempotence: applying the function again to its own output changes nothing
    again = func(pd.DataFrame({column: result}), column, infer_tz=infer_tz)[column]
    assert again.notna().sum() == result.notna().sum(), f"{column}: second pass lost values"
    same = sum(
        1 for a, b in zip(again.loc[src.index], out, strict=True) if a != b or a.utcoffset() != b.utcoffset()
    ) if infer_tz else int((again.loc[src.index] != out).sum())
    assert same == 0, f"{column}: second pass changed {same} values"


@pytest.mark.parametrize("func", [convert_to_datetime, to_timestamp])
@pytest.mark.parametrize("infer_tz", [False, True])
@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_drop_timezone_wall_clock(offers_hires, func, infer_tz, column) -> None:
    """infer_tz=True -> local wall-clock (pandas<3 result); False -> UTC wall-clock."""
    src = _source(offers_hires, column)
    result = _drop_timezone(_run(offers_hires, func, column, infer_tz))
    assert str(result.dtype) == "datetime64[ns]", f"{column}: dtype {result.dtype}"
    assert int(result.notna().sum()) == len(src), f"{column}: non-null count changed"
    assert not any(isinstance(v, str) for v in result), f"{column}: str values left"

    refs = [_reference(v) for v in src]
    if infer_tz:
        expected = [r.replace(tzinfo=None) for r in refs]
    else:
        expected = [r.astimezone(timezone.utc).replace(tzinfo=None) for r in refs]
    got = [v.to_pydatetime() for v in result.loc[src.index]]
    bad = sum(1 for g, e in zip(got, expected, strict=True) if g != e)
    assert bad == 0, f"{column}: {bad} wall-clock mismatches"


@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_drop_timezone_raw_strings(offers_hires, column) -> None:
    """Raw source strings -> drop_timezone -> local wall-clock for every non-null value."""
    src = _source(offers_hires, column)
    result = _drop_timezone(offers_hires[column])
    assert int(result.notna().sum()) == len(src), f"{column}: non-null count changed"
    out = result.loc[src.index]
    assert not any(isinstance(v, str) for v in out), f"{column}: str values left"
    refs = [_reference(v).replace(tzinfo=None) for v in src]
    bad = sum(1 for g, r in zip(out, refs, strict=True) if g.to_pydatetime() != r)
    assert bad == 0, f"{column}: {bad} values with a wrong wall-clock"
