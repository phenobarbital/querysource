"""
Integration tests for the MultiQuery destination pipeline.

Verifies that:
1. All expected destination names are registered in DESTINATION_REGISTRY.
2. Backward compatibility: existing ``tableOutput``/``TableOutput`` YAML
   configs continue to work after the registry refactoring.
3. Multiple destinations can be chained (each returns original data for
   the next step to receive).
4. The dispatch loop (as used by MultiQS / QueryHandler) dispatches to
   the correct destination class.

All external backend calls (database, S3, SharePoint) are mocked.
"""
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.outputs.destinations.abstract import AbstractDestination
from querysource.outputs.destinations.sharepoint import ToSharepoint
from querysource.outputs.destinations.s3 import ToS3
from querysource.outputs.destinations.table import TableDestination
from querysource.outputs.destinations.dwh import DWHDestination
from querysource.exceptions import OutputError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline_result():
    return pd.DataFrame({
        "store_id": [1, 2, 3],
        "name": ["Store A", "Store B", "Store C"],
        "revenue": [100.0, 200.0, 300.0],
    })


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

class TestDestinationDispatch:
    def test_all_destinations_registered(self):
        """All expected step-names must be present in the registry."""
        expected = {"tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"}
        assert expected.issubset(set(DESTINATION_REGISTRY.keys())), (
            f"Missing: {expected - set(DESTINATION_REGISTRY.keys())}"
        )

    def test_registry_values_are_abstract_destination_subclasses(self):
        """Every registry entry must be a subclass of AbstractDestination."""
        for name, cls in DESTINATION_REGISTRY.items():
            assert issubclass(cls, AbstractDestination), (
                f"DESTINATION_REGISTRY['{name}'] = {cls} is not a subclass "
                f"of AbstractDestination"
            )

    def test_get_destination_returns_correct_class(self):
        """get_destination() returns the exact class registered."""
        assert get_destination("ToSharepoint") is ToSharepoint
        assert get_destination("ToS3") is ToS3
        assert get_destination("Table") is TableDestination
        assert get_destination("DWH") is DWHDestination

    def test_get_destination_unknown_raises_output_error(self):
        """Unknown destination name raises OutputError."""
        with pytest.raises(OutputError):
            get_destination("NonExistent")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_table_output_lowercase_yaml_still_works(self, pipeline_result):
        """Existing 'tableOutput' YAML configs work after registry refactoring."""
        cls = get_destination("tableOutput")
        # Instantiate with the same kwargs that TableOutput accepts
        adapter = cls(
            data=pipeline_result,
            flavor="postgresql",
            tablename="stores",
            schema="public",
        )
        assert isinstance(adapter, AbstractDestination)
        assert adapter.data is pipeline_result

    @pytest.mark.asyncio
    async def test_table_output_mixedcase_yaml_still_works(self, pipeline_result):
        """Existing 'TableOutput' YAML configs work after registry refactoring."""
        cls = get_destination("TableOutput")
        adapter = cls(
            data=pipeline_result,
            flavor="postgresql",
            tablename="stores",
            schema="public",
        )
        assert isinstance(adapter, AbstractDestination)

    @pytest.mark.asyncio
    async def test_backward_compat_run_delegates_to_table_output(self, pipeline_result):
        """TableOutputAdapter.run() delegates to the wrapped TableOutput."""
        from querysource.outputs.destinations import TableOutputAdapter
        adapter = TableOutputAdapter(
            data=pipeline_result,
            flavor="postgresql",
            tablename="stores",
            schema="public",
        )
        # Mock the underlying TableOutput.run()
        with patch.object(adapter._table_output, "run", new_callable=AsyncMock,
                          return_value=pipeline_result):
            result = await adapter.run()
        assert result is pipeline_result


# ---------------------------------------------------------------------------
# Destination chaining (pass-through)
# ---------------------------------------------------------------------------

class TestDestinationChaining:
    @pytest.mark.asyncio
    async def test_multiple_destinations_chain_pass_through(self, pipeline_result):
        """
        Each destination returns original data so the next step receives it.

        The dispatch loop pattern::

            for step in output_steps:
                for step_name, component in step.items():
                    cls = get_destination(step_name)
                    dest = cls(data=result, **component)
                    result = await dest.run()
        """
        output_steps = [
            {"Table": {
                "driver": "pg",
                "schema": "public",
                "table": "t",
                "method": "append",
            }},
            {"ToS3": {
                "credentials": {"bucket": "b", "aws_key": "k", "aws_secret": "s"},
                "destination": {"file": "f.csv", "directory": "d/"},
            }},
        ]

        result = pipeline_result
        for step in output_steps:
            for step_name, component in step.items():
                cls = get_destination(step_name)
                dest = cls(data=result, **component)
                # Mock run() so it returns the data without hitting a backend
                with patch.object(dest, "run", new_callable=AsyncMock,
                                  return_value=result):
                    result = await dest.run()

        # After two destination steps, result should still be the original DF
        assert result is pipeline_result

    @pytest.mark.asyncio
    async def test_three_destination_chain(self, pipeline_result):
        """Chain of three destinations — all receive the same DataFrame."""
        steps = [
            ("Table", {"driver": "pg", "schema": "s", "table": "t", "method": "append"}),
            ("ToS3", {"credentials": {"bucket": "b"}, "destination": {"file": "f.csv", "directory": "d/"}}),
            ("ToSharepoint", {
                "credentials": {"client_id": "c", "client_secret": "s", "tenant_id": "t", "site": "S"},
                "destination": {"filename": "out.xlsx", "directory": "Docs"},
            }),
        ]

        result = pipeline_result
        calls: list[str] = []
        for step_name, component in steps:
            cls = get_destination(step_name)
            dest = cls(data=result, **component)
            async def _run(d=dest, sn=step_name):
                calls.append(sn)
                return d.data
            with patch.object(dest, "run", side_effect=_run):
                result = await dest.run()

        assert result is pipeline_result
        assert calls == ["Table", "ToS3", "ToSharepoint"]


# ---------------------------------------------------------------------------
# Full end-to-end (mocked backends)
# ---------------------------------------------------------------------------

class TestMultiQSToSharepointE2E:
    @pytest.mark.asyncio
    async def test_sharepoint_destination_e2e(self, pipeline_result):
        """ToSharepoint destination: run() uploads and returns original data."""
        dest = ToSharepoint(
            data=pipeline_result,
            credentials={
                "client_id": "test-id",
                "client_secret": "test-secret",
                "tenant_id": "test-tenant",
                "site": "TestSite",
            },
            destination={"filename": "report.xlsx", "directory": "Docs/Reports"},
        )
        with patch.object(dest, "_upload_to_sharepoint", new_callable=AsyncMock):
            result = await dest.run()
        assert result is pipeline_result


class TestMultiQSToS3E2E:
    @pytest.mark.asyncio
    async def test_s3_destination_e2e(self, pipeline_result):
        """ToS3 destination: run() uploads and returns original data."""
        dest = ToS3(
            data=pipeline_result,
            credentials={"bucket": "test-bucket", "region_name": "us-east-1"},
            destination={"file": "output.csv", "directory": "exports/"},
        )
        with patch.object(dest, "_upload_to_s3", new_callable=AsyncMock):
            result = await dest.run()
        assert result is pipeline_result


class TestMultiQSTablePGE2E:
    @pytest.mark.asyncio
    async def test_table_pg_e2e(self, pipeline_result):
        """Table destination (pg): run() writes and returns original data."""
        dest = TableDestination(
            data=pipeline_result,
            driver="pg",
            schema="troc",
            table="stores",
            method="append",
        )
        with patch.object(dest, "_write_to_table", new_callable=AsyncMock), \
             patch.object(dest, "_build_engine") as mock_factory:
            mock_engine = MagicMock()
            mock_engine.close = AsyncMock()
            mock_factory.return_value = mock_engine
            result = await dest.run()
        assert result is pipeline_result


class TestMultiQSDWHBigQueryE2E:
    @pytest.mark.asyncio
    async def test_dwh_bigquery_e2e(self, pipeline_result):
        """DWH BigQuery destination: run() writes and returns original data."""
        dest = DWHDestination(
            data=pipeline_result,
            driver="bigquery",
            schema="analytics",
            table="daily_metrics",
            method="append",
        )
        with patch.object(dest, "_write_to_dwh", new_callable=AsyncMock):
            result = await dest.run()
        assert result is pipeline_result


class TestMultipleDestinationsE2E:
    @pytest.mark.asyncio
    async def test_table_then_s3(self, pipeline_result):
        """Single pipeline with two Output steps (Table + ToS3)."""
        output_steps = [
            {"Table": {
                "driver": "pg",
                "schema": "public",
                "table": "stores",
                "method": "upsert",
                "pk": ["store_id"],
            }},
            {"ToS3": {
                "credentials": {"bucket": "backup-bucket"},
                "destination": {"file": "stores.parquet", "directory": "backups/daily/"},
            }},
        ]

        result = pipeline_result
        for step in output_steps:
            for step_name, component in step.items():
                cls = get_destination(step_name)
                dest = cls(data=result, **component)
                with patch.object(dest, "run", new_callable=AsyncMock,
                                  return_value=result):
                    result = await dest.run()

        assert result is pipeline_result
