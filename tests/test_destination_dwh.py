"""
Unit tests for DWHDestination.

All DWH backend calls are mocked — no real cloud credentials required.
"""
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.dwh import DWHDestination, _clean_dynamo_record
from querysource.exceptions import OutputError


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "date": ["2025-01-01", "2025-01-02"],
        "store_id": [1, 2],
        "metric": [100.0, 200.0],
    })


@pytest.fixture
def bigquery_config():
    return {
        "driver": "bigquery",
        "schema": "analytics",
        "table": "daily_metrics",
        "method": "append",
        "pk": ["date", "store_id"],
        "credentials": {
            "project_id": "test-project",
            "credentials": "/path/to/creds.json",
        },
    }


@pytest.fixture
def documentdb_config():
    return {
        "driver": "documentdb",
        "schema": "analytics",
        "table": "daily_metrics",
        "method": "append",
        "pk": ["store_id"],
        "credentials": {
            "host": "localhost",
            "port": 27017,
            "username": "user",
            "password": "pass",
            "database": "analytics",
        },
    }


@pytest.fixture
def dynamodb_config():
    return {
        "driver": "dynamodb",
        "schema": "",
        "table": "metrics",
        "method": "append",
        "pk": ["date", "store_id"],
        "credentials": {
            "region_name": "us-east-1",
            "aws_key": "AKIATEST",
            "aws_secret": "secret",
        },
    }


class TestDWHDestination:
    def test_initialization_bigquery(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.data is sample_df
        assert dest._driver == "bigquery"
        assert dest._schema == "analytics"
        assert dest._table == "daily_metrics"
        assert dest._method == "append"

    def test_invalid_driver(self, sample_df):
        with pytest.raises(OutputError, match="unsupported driver"):
            DWHDestination(data=sample_df, driver="redis", schema="s", table="t")

    def test_invalid_method(self, sample_df):
        with pytest.raises(OutputError, match="unsupported method"):
            DWHDestination(data=sample_df, driver="bigquery", schema="s", table="t", method="drop")

    def test_valid_dwh_drivers(self, sample_df):
        for driver in ("bigquery", "documentdb", "dynamodb"):
            dest = DWHDestination(data=sample_df, driver=driver, schema="s", table="t")
            assert dest._driver == driver

    def test_default_method_is_append(self, sample_df):
        dest = DWHDestination(data=sample_df, driver="bigquery", schema="s", table="t")
        assert dest._method == "append"

    def test_navconfig_credential_resolution(self, sample_df):
        """Credentials given as navconfig variable names are resolved."""
        with patch("navconfig.config.get", return_value="test-project-id"):
            dest = DWHDestination(
                data=sample_df,
                driver="bigquery",
                schema="analytics",
                table="t",
                credentials={"project_id": "BIGQUERY_PROJECT_ID"},
            )
        assert dest._credentials["project_id"] == "test-project-id"

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        with patch.object(dest, "_write_to_dwh", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df

    @pytest.mark.asyncio
    async def test_run_empty_dataframe_skips_write(self, bigquery_config):
        """Empty DataFrame: run() completes without calling _write_to_dwh."""
        empty_df = pd.DataFrame()
        dest = DWHDestination(data=empty_df, **bigquery_config)
        with patch.object(dest, "_write_to_dwh", new_callable=AsyncMock) as mock_write:
            result = await dest.run()
        mock_write.assert_not_called()
        assert result is empty_df

    @pytest.mark.asyncio
    async def test_run_dict_of_dataframes(self, bigquery_config):
        """Dict of DataFrames: each non-empty partition is written."""
        df_a = pd.DataFrame({"x": [1]})
        df_b = pd.DataFrame({"x": [2]})
        data = {"q1": df_a, "q2": df_b}
        dest = DWHDestination(data=data, **bigquery_config)

        calls = []

        async def capture(df):
            calls.append(len(df))

        with patch.object(dest, "_write_to_dwh", side_effect=capture):
            result = await dest.run()

        assert result is data
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_bigquery_dispatches_write_bigquery(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        with patch.object(dest, "_write_bigquery", new_callable=AsyncMock) as mock_bq:
            await dest._write_to_dwh(sample_df)
        mock_bq.assert_called_once()

    @pytest.mark.asyncio
    async def test_documentdb_dispatches_write_documentdb(self, sample_df, documentdb_config):
        dest = DWHDestination(data=sample_df, **documentdb_config)
        with patch.object(dest, "_write_documentdb", new_callable=AsyncMock) as mock_db:
            await dest._write_to_dwh(sample_df)
        mock_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_dynamodb_dispatches_write_dynamodb(self, sample_df, dynamodb_config):
        dest = DWHDestination(data=sample_df, **dynamodb_config)
        with patch.object(dest, "_write_dynamodb", new_callable=AsyncMock) as mock_dyn:
            await dest._write_to_dwh(sample_df)
        mock_dyn.assert_called_once()


class TestDWHDestinationParentProtocol:
    """Verify parent-protocol methods required by BigQueryOutput."""

    def test_get_schema_returns_schema(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.get_schema() == "analytics"

    def test_primary_keys_returns_pk_list(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.primary_keys() == ["date", "store_id"]

    def test_constraints_returns_none(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.constraints() is None

    def test_foreign_keys_returns_none(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.foreign_keys() is None


class TestCleanDynamoRecord:
    def test_float_converted_to_decimal(self):
        from decimal import Decimal
        record = {"id": 1, "val": 3.14}
        cleaned = _clean_dynamo_record(record)
        assert isinstance(cleaned["val"], Decimal)
        assert abs(float(cleaned["val"]) - 3.14) < 0.001

    def test_non_float_values_unchanged(self):
        record = {"id": 1, "name": "test", "active": True}
        cleaned = _clean_dynamo_record(record)
        assert cleaned["id"] == 1
        assert cleaned["name"] == "test"
        assert cleaned["active"] is True

    def test_none_becomes_empty_string(self):
        record = {"id": 1, "val": None}
        cleaned = _clean_dynamo_record(record)
        assert cleaned["val"] == ""
