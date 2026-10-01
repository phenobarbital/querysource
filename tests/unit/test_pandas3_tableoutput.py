"""FEAT-161: TableOutput with pandas 3 frames (engine/driver mocked)."""
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine

from querysource.outputs.tables.TableOutput import table as out_table


@pytest.fixture
def handler_table():
    """Handler TableOutput module; skipped when Cython extensions are unbuilt."""
    return pytest.importorskip("querysource.handlers.outputs.tableOutput.table")


@pytest.fixture
def df_pandas3() -> pd.DataFrame:
    """Frame with str dtype, NA values and microsecond datetimes."""
    return pd.DataFrame({
        "name": pd.Series(["a", None, "c"], dtype="str"),
        "n": [1, None, 3],
        "ts": pd.to_datetime(["2026-01-01", None, "2026-01-03"]).as_unit("us"),
    })


class FakePgEngine:
    """Imitates the PgOutput interface used by TableOutput.table_output."""

    is_external = False

    def __init__(self, *args, **kwargs) -> None:
        self.captured: list = []
        self.keys: list = []
        self.columns: list = []
        self._sa = create_engine("sqlite://")
        self.close = MagicMock()

    def engine(self):
        return self._sa

    def db_upsert(self, table, conn, keys, data_iter):
        """Record what the driver would receive."""
        self.keys = list(keys)
        self.captured = [tuple(r) for r in data_iter]


def _is_na(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) \
        or value is pd.NaT or value is pd.NA


def _assert_rows(engine: FakePgEngine) -> None:
    assert engine.keys == ["name", "n", "ts"]
    assert len(engine.captured) == 3
    first, second, third = engine.captured
    assert first[0] == "a" and third[0] == "c"
    assert _is_na(second[0])
    assert first[1] == 1 and third[1] == 3
    assert _is_na(second[1])
    assert pd.Timestamp(first[2]) == pd.Timestamp("2026-01-01")
    assert pd.Timestamp(third[2]) == pd.Timestamp("2026-01-03")
    assert _is_na(second[2])


async def test_tableoutput_str_na_datetime(df_pandas3):
    fake = FakePgEngine()
    with patch.object(out_table, "PgOutput", return_value=fake):
        to = out_table.TableOutput(df_pandas3, flavor="postgresql")
        to.tablename = "t_p3"
        to.schema = None
        to.constraint = None  # makes table_output pass index=False
        result = await to.run()
    assert result is df_pandas3
    assert fake.columns == ["name", "n", "ts"]
    fake.close.assert_called_once()
    _assert_rows(fake)


async def test_tableoutput_dict_of_frames(df_pandas3):
    fake = FakePgEngine()
    with patch.object(out_table, "PgOutput", return_value=fake):
        to = out_table.TableOutput({"one": df_pandas3}, flavor="postgresql")
        to.tablename = "t_p3"
        to.schema = None
        to.constraint = None  # makes table_output pass index=False
        await to.run()
    _assert_rows(fake)


async def test_tableoutput_na_strings_become_null():
    """'<NA>' / 'None' literals in str columns are replaced with nulls."""
    df = pd.DataFrame({"name": pd.Series(["a", "<NA>", "None"], dtype="str")})
    fake = FakePgEngine()
    with patch.object(out_table, "PgOutput", return_value=fake):
        to = out_table.TableOutput(df, flavor="postgresql")
        to.tablename = "t_p3"
        to.schema = None
        to.constraint = None  # makes table_output pass index=False
        await to.run()
    assert fake.captured[0][0] == "a"
    assert _is_na(fake.captured[1][0])
    assert _is_na(fake.captured[2][0])


async def test_tableoutput_external_engine_receives_frame(df_pandas3):
    ext = MagicMock()
    ext.is_external = True
    ext.db_upsert = AsyncMock()
    ext.close = AsyncMock()
    with patch.object(out_table, "MongoDBOutput", return_value=ext):
        to = out_table.TableOutput(df_pandas3, flavor="mongodb")
        to.tablename = "coll"
        to.schema = "db"
        await to.run()
    ext.db_upsert.assert_awaited_once()
    kwargs = ext.db_upsert.await_args.kwargs
    assert kwargs["table"] == "coll" and kwargs["schema"] == "db"
    sent = kwargs["data"]
    assert list(sent.columns) == ["name", "n", "ts"]
    assert str(sent["ts"].dtype) == "datetime64[us]"
    assert _is_na(sent["name"].iloc[1])
    ext.close.assert_awaited_once()


async def test_tableoutput_jsonb_columns_forwarded(df_pandas3):
    fake = FakePgEngine()
    with patch.object(out_table, "PgOutput", return_value=fake) as pg:
        to = out_table.TableOutput(
            df_pandas3, flavor="postgresql", jsonb_columns=["name"]
        )
        to.tablename = "t_p3"
        to.schema = None
        to.constraint = None  # makes table_output pass index=False
        await to.run()
    assert pg.call_args.kwargs["jsonb_columns"] == {"name"}


async def test_handler_tableoutput_pandas3(df_pandas3, handler_table):
    fake = FakePgEngine()
    with patch.object(handler_table, "PgOutput", return_value=fake):
        to = handler_table.TableOutput(df_pandas3, flavor="postgresql")
        to.tablename = "t_p3"
        to.schema = None
        to.constraint = None  # makes table_output pass index=False
        result = await to.run()
    assert result is df_pandas3
    fake.close.assert_called_once()
    _assert_rows(fake)


async def test_handler_tableoutput_rejects_empty_frame(handler_table):
    from querysource.exceptions import DataNotFound

    fake = FakePgEngine()
    with patch.object(handler_table, "PgOutput", return_value=fake):
        to = handler_table.TableOutput(
            {"x": pd.DataFrame({"a": []})}, flavor="postgresql"
        )
        to.tablename = "t"
        with pytest.raises(DataNotFound):
            await to.run()


