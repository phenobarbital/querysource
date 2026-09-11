"""The ``iter`` output format must honour its contract: a list of dictionaries.

Regression: pandas-backed providers (``bigquery``, ``deltatbl``, ``iceberg``)
returned a ``pandas.DataFrame`` unchanged from ``iterFormat.serialize``. Every
row-oriented writer declaring ``output_format = 'iter'`` (csv, tsv, txt,
report/pdf) then iterated the frame, which yields *column names* instead of
rows — ``slug:csv`` answered HTTP 200 with only the header line.
"""
import pandas
import pytest

from querysource.outputs.dt.iter import iterFormat
from querysource.utils.dataframes import df_to_records, is_dataframe

DF = pandas.DataFrame(
    [
        {"a": 1, "b": "x", "ts": pandas.Timestamp("2020-01-01")},
        {"a": 2, "b": None, "ts": pandas.NaT},
    ]
)


async def test_iter_serialises_dataframe_to_records():
    data, error = await iterFormat().serialize(DF, None)
    assert error is None
    assert isinstance(data, list)
    assert data == [
        {"a": 1, "b": "x", "ts": pandas.Timestamp("2020-01-01")},
        {"a": 2, "b": None, "ts": None},
    ]


async def test_iter_dataframe_rows_are_mappings():
    # The actual failure mode: iterating a DataFrame yields column names.
    data, _ = await iterFormat().serialize(DF, None)
    assert [sorted(row.keys()) for row in data] == [["a", "b", "ts"]] * 2


async def test_iter_list_of_dicts_unchanged():
    rows = [{"a": 1}, {"a": 2}]
    data, _ = await iterFormat().serialize(rows, None)
    assert data == rows


async def test_iter_propagates_error():
    err = RuntimeError("boom")
    data, error = await iterFormat().serialize([{"a": 1}], err)
    assert error is err
    assert data == [{"a": 1}]


def test_df_to_records_maps_missing_values_to_none():
    records = df_to_records(DF)
    assert records[1]["b"] is None
    assert records[1]["ts"] is None
    # NaN in a float column must not leak as the string "nan"
    floats = pandas.DataFrame([{"v": 1.5}, {"v": float("nan")}])
    assert df_to_records(floats) == [{"v": 1.5}, {"v": None}]


@pytest.mark.parametrize(
    "value, expected",
    [(DF, True), ([{"a": 1}], False), ({"a": 1}, False), ("text", False)],
)
def test_is_dataframe(value, expected):
    assert is_dataframe(value) is expected
