"""CSVWriter / TSVWriter must serialise a pandas DataFrame row by row and must
not swallow writer exceptions.

Regression: slugs served by pandas-backed providers (bigquery, deltatbl,
iceberg) exported as ``:csv`` returned HTTP 200 with only the header line —
``writerows(DataFrame)`` iterated column names, raised ``AttributeError`` and
``TmpFile.__aexit__`` (returning ``self``) suppressed it.
"""
import pandas
import pytest

from querysource.outputs.writers.csv import CSVWriter, TmpFile
from querysource.outputs.writers.tsv import TSVWriter


class _Req:
    headers = {}


class _Resp:
    def __init__(self):
        self.headers = {}
        self.content_length = None


def _writer(cls, **kwargs):
    writer = cls(
        request=_Req(), resultset=[{"x": 1}], filename="slug",
        response_type="stream", download=False, compression="gzip",
        ctype=cls.ctype, **kwargs,
    )

    async def fake_response(response_type):
        return _Resp()

    async def fake_stream(response, data):
        return data

    writer.response = fake_response
    writer.stream_response = fake_stream
    return writer


DF = pandas.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": None}])


async def test_csv_dataframe_writes_every_row():
    writer = _writer(CSVWriter, delimiter=",", quoting="string")
    writer.data = DF
    out = await writer.get_response()
    lines = out.decode().splitlines()
    assert lines == ['"a","b"', '1,"x"', '2,""']


async def test_tsv_dataframe_writes_every_row():
    writer = _writer(TSVWriter)
    writer.data = DF
    out = await writer.get_response()
    lines = out.decode().splitlines()
    assert lines == ["a\tb", "1\tx", "2\t"]


async def test_csv_list_of_dicts_unchanged():
    writer = _writer(CSVWriter, delimiter=",", quoting="string")
    writer.data = DF.to_dict(orient="records")
    out = await writer.get_response()
    assert out.decode().splitlines()[1] == '1,"x"'


async def test_writer_errors_are_not_swallowed():
    writer = _writer(CSVWriter, delimiter=",", quoting="string")
    writer.data = ["not-a-mapping"]
    writer.columns = ["a"]
    with pytest.raises(AttributeError):
        await writer.get_response()


async def test_tmpfile_propagates_exceptions():
    tmp = TmpFile()
    with pytest.raises(RuntimeError):
        async with tmp.open_buffer():
            raise RuntimeError("boom")
