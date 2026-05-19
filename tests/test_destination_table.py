"""
Unit tests for TableDestination.

Database engine calls are mocked — no real database connections required.
"""
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.table import TableDestination, DRIVER_MAP
from querysource.exceptions import OutputError


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "store_id": [1, 2, 3],
        "name": ["A", "B", "C"],
        "revenue": [100.0, 200.0, 300.0],
    })


@pytest.fixture
def pg_table_config():
    return {
        "driver": "pg",
        "schema": "troc",
        "table": "stores",
        "method": "append",
        "pk": ["store_id"],
    }


class TestTableDestination:
    def test_initialization(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.data is sample_df
        assert dest._table == "stores"
        assert dest._schema == "troc"
        assert dest._method == "append"
        assert dest._pk == ["store_id"]

    def test_driver_normalization_pg(self, sample_df):
        for alias in ("pg", "postgresql", "postgres"):
            dest = TableDestination(data=sample_df, driver=alias, schema="s", table="t")
            assert dest._normalized_driver == "postgresql", \
                f"alias '{alias}' should normalize to 'postgresql'"

    def test_driver_normalization_mysql(self, sample_df):
        for alias in ("mysql", "mariadb"):
            dest = TableDestination(data=sample_df, driver=alias, schema="s", table="t")
            assert dest._normalized_driver == "mysql"

    def test_driver_normalization_bigquery(self, sample_df):
        for alias in ("bigquery", "bq"):
            dest = TableDestination(data=sample_df, driver=alias, schema="s", table="t")
            assert dest._normalized_driver == "bigquery"

    def test_invalid_driver_raises(self, sample_df):
        with pytest.raises(OutputError, match="unsupported driver"):
            TableDestination(data=sample_df, driver="unknown_db", schema="s", table="t")

    def test_invalid_method_raises(self, sample_df):
        with pytest.raises(OutputError, match="unsupported method"):
            TableDestination(data=sample_df, driver="pg", schema="s", table="t", method="delete")

    def test_default_method_is_append(self, sample_df):
        dest = TableDestination(data=sample_df, driver="pg", schema="s", table="t")
        assert dest._method == "append"

    def test_if_exists_append(self, sample_df):
        dest = TableDestination(data=sample_df, driver="pg", schema="s", table="t", method="append")
        assert dest.if_exists == "append"

    def test_if_exists_truncate_maps_to_append(self, sample_df):
        """Truncate method still uses 'append' for if_exists (truncate runs separately)."""
        dest = TableDestination(data=sample_df, driver="pg", schema="s", table="t", method="truncate")
        assert dest.if_exists == "append"

    def test_if_exists_upsert(self, sample_df):
        dest = TableDestination(data=sample_df, driver="pg", schema="s", table="t", method="upsert")
        assert dest.if_exists == "upsert"

    def test_parent_protocol_tablename(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.tablename == "stores"

    def test_parent_protocol_pk(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.pk == ["store_id"]

    def test_parent_protocol_foreign_key_is_none(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.foreign_key is None

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        with patch.object(dest, "_write_to_table", new_callable=AsyncMock):
            with patch.object(dest, "_build_engine") as mock_engine_factory:
                mock_engine = MagicMock()
                mock_engine.close = AsyncMock()
                mock_engine_factory.return_value = mock_engine
                result = await dest.run()
        assert result is sample_df

    @pytest.mark.asyncio
    async def test_run_calls_truncate_when_method_is_truncate(self, sample_df):
        dest = TableDestination(
            data=sample_df,
            driver="pg",
            schema="troc",
            table="stores",
            method="truncate",
            pk=["store_id"],
        )
        with patch.object(dest, "_truncate_table", new_callable=AsyncMock) as mock_trunc, \
             patch.object(dest, "_write_to_table", new_callable=AsyncMock), \
             patch.object(dest, "_build_engine") as mock_engine_factory:
            mock_engine = MagicMock()
            mock_engine.close = AsyncMock()
            mock_engine_factory.return_value = mock_engine
            await dest.run()
        mock_trunc.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_does_not_truncate_on_append(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        with patch.object(dest, "_truncate_table", new_callable=AsyncMock) as mock_trunc, \
             patch.object(dest, "_write_to_table", new_callable=AsyncMock), \
             patch.object(dest, "_build_engine") as mock_engine_factory:
            mock_engine = MagicMock()
            mock_engine.close = AsyncMock()
            mock_engine_factory.return_value = mock_engine
            await dest.run()
        mock_trunc.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_dict_of_dataframes(self):
        """Dict of DataFrames: each is written to the same table."""
        df_a = pd.DataFrame({"id": [1], "val": ["a"]})
        df_b = pd.DataFrame({"id": [2], "val": ["b"]})
        data = {"part_a": df_a, "part_b": df_b}

        dest = TableDestination(
            data=data,
            driver="pg",
            schema="public",
            table="t",
            method="append",
        )

        write_calls = []

        async def capture_write(df, engine):
            write_calls.append(len(df))

        with patch.object(dest, "_write_to_table", side_effect=capture_write), \
             patch.object(dest, "_build_engine") as mock_engine_factory:
            mock_engine = MagicMock()
            mock_engine.close = AsyncMock()
            mock_engine_factory.return_value = mock_engine
            result = await dest.run()

        assert result is data
        assert len(write_calls) == 2

    def test_all_driver_map_entries_covered(self):
        """Smoke test: all DRIVER_MAP keys produce valid normalized drivers."""
        df = pd.DataFrame({"x": [1]})
        for alias in DRIVER_MAP:
            dest = TableDestination(data=df, driver=alias, schema="s", table="t")
            assert dest._normalized_driver in ("postgresql", "mysql", "bigquery")


class TestTableDestinationParentProtocol:
    """Verify parent-protocol methods required by AbstractOutput engines."""

    def test_get_schema_returns_schema(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.get_schema() == "troc"

    def test_primary_keys_returns_pk_list(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.primary_keys() == ["store_id"]

    def test_constraints_returns_none(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.constraints() is None

    def test_foreign_keys_returns_none(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.foreign_keys() is None

    @pytest.mark.asyncio
    async def test_write_to_table_calls_to_sql_for_sqlalchemy_engine(self, sample_df, pg_table_config):
        """_write_to_table calls df.to_sql with engine.db_upsert as method for non-external engines."""
        dest = TableDestination(data=sample_df, **pg_table_config)

        mock_engine = MagicMock()
        mock_engine.is_external = False
        mock_engine.engine.return_value = MagicMock()  # SQLAlchemy engine
        mock_engine.db_upsert = MagicMock()
        mock_engine.columns = []

        to_sql_calls = []

        def fake_to_sql(name, con, **kwargs):
            to_sql_calls.append({
                "name": name,
                "method": kwargs.get("method"),
                "if_exists": kwargs.get("if_exists"),
                "schema": kwargs.get("schema"),
            })

        with patch.object(sample_df, "to_sql", side_effect=fake_to_sql):
            import asyncio as _asyncio
            await dest._write_to_table(sample_df, mock_engine)

        assert len(to_sql_calls) == 1
        assert to_sql_calls[0]["name"] == "stores"
        assert to_sql_calls[0]["method"] is mock_engine.db_upsert
        assert to_sql_calls[0]["schema"] == "troc"
