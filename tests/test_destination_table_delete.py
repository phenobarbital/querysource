"""Unit tests for TableDeleteDestination (FEAT-155, TASK-815). No real database."""
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.exceptions import DataNotFound, OutputError
from querysource.queries.multi.destinations import table_delete as td
from querysource.queries.multi.destinations.table_delete import TableDeleteDestination

CFG = {"schema": "wm_assembly", "table": "employee_detail_profile",
       "pk": ["associate_id", "activity_date"]}


@pytest.fixture
def keys_df():
    return pd.DataFrame({"associate_id": ["A1", "A2", None, "A1"],
                         "activity_date": pd.to_datetime(["2026-09-01"] * 4)})


@pytest.fixture
def fake_raw():
    """Mocked raw asyncpg connection; `transaction()` is an async context manager."""
    raw = MagicMock()
    raw.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    raw.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    raw.fetch = AsyncMock(return_value=[("associate_id", "character varying"),
                                        ("activity_date", "date")])
    raw.execute = AsyncMock(side_effect=["CREATE TABLE", "DELETE 2"])
    raw.copy_records_to_table = AsyncMock(return_value="COPY 2")
    return raw


@pytest.fixture
def patched_db(fake_raw):
    conn = MagicMock()
    conn.engine.return_value = fake_raw
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.connection = AsyncMock(return_value=conn)
    with patch.object(td, "AsyncDB", return_value=db) as mock_cls:
        yield mock_cls


def test_init_requires_table_and_pk(keys_df):
    with pytest.raises(OutputError):
        TableDeleteDestination(data=keys_df, schema="s", pk=["a"])
    with pytest.raises(OutputError):
        TableDeleteDestination(data=keys_df, schema="s", table="t", pk=[])


@pytest.mark.parametrize("override", [{"table": "t; drop"}, {"pk": ["a b"]}, {"schema": 'x"y'}])
def test_init_rejects_bad_identifiers(keys_df, override):
    with pytest.raises(OutputError):
        TableDeleteDestination(data=keys_df, **{**CFG, **override})


def test_key_frame_missing_column(keys_df):
    dest = TableDeleteDestination(data=keys_df, **{**CFG, "pk": ["nope"]})
    with pytest.raises(OutputError) as exc:
        dest._key_frame(keys_df)
    assert getattr(exc.value, "category", "data") == "data"


def test_key_frame_drops_nulls_and_dupes(keys_df):
    dest = TableDeleteDestination(data=keys_df, **CFG)
    out = dest._key_frame(keys_df)
    assert len(out) == 2
    assert list(out["associate_id"]) == ["A1", "A2"]


async def test_run_empty_dataframe():
    empty = pd.DataFrame({"associate_id": [], "activity_date": []})
    with pytest.raises(DataNotFound):
        await TableDeleteDestination(data=empty, **CFG).run()


async def test_run_passthrough(keys_df):
    dest = TableDeleteDestination(data=keys_df, **CFG)
    with patch.object(TableDeleteDestination, "_delete_keys", AsyncMock(return_value=2)):
        result = await dest.run()
    assert result is keys_df
    assert dest.deleted_rows == 2


async def test_run_dict_of_frames(keys_df):
    data = {"a": keys_df, "b": keys_df, "c": pd.DataFrame()}
    dest = TableDeleteDestination(data=data, **CFG)
    mock = AsyncMock(return_value=2)
    with patch.object(TableDeleteDestination, "_delete_keys", mock):
        result = await dest.run()
    assert result is data
    assert mock.await_count == 2
    assert dest.deleted_rows == 4


async def test_delete_sql_shape(keys_df, fake_raw, patched_db):
    dest = TableDeleteDestination(data=keys_df, **CFG)
    result = await dest.run()
    assert result is keys_df
    patched_db.assert_called_once_with("pg", dsn=td.default_dsn)
    create_sql = fake_raw.execute.await_args_list[0].args[0]
    delete_sql = fake_raw.execute.await_args_list[1].args[0]
    assert "ON COMMIT DROP" in create_sql
    assert '"associate_id" character varying' in create_sql
    fake_raw.copy_records_to_table.assert_awaited_once()
    assert fake_raw.copy_records_to_table.await_args.kwargs["columns"] == CFG["pk"]
    assert not fake_raw.copy_into_table.called
    assert 'FROM "wm_assembly"."employee_detail_profile" t USING' in delete_sql
    assert 't."associate_id" = d."associate_id"' in delete_sql
    fake_raw.transaction.return_value.__aenter__.assert_awaited_once()
    assert dest.deleted_rows == 2
    for sql in (create_sql, delete_sql):
        assert "A1" not in sql and "2026" not in sql


async def test_delete_error_wrapped(keys_df, fake_raw, patched_db):
    boom = RuntimeError("boom")
    fake_raw.copy_records_to_table.side_effect = boom
    dest = TableDeleteDestination(data=keys_df, **CFG)
    with pytest.raises(OutputError) as exc:
        await dest.run()
    assert exc.value.__cause__ is boom
    exit_args = fake_raw.transaction.return_value.__aexit__.await_args.args
    assert exit_args[0] is RuntimeError
