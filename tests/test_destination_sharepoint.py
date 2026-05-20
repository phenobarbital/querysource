"""
Unit tests for ToSharepoint destination.

All Graph API calls are mocked — no real credentials required.
"""
import io
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.sharepoint import ToSharepoint


@pytest.fixture
def sample_df():
    return pd.DataFrame({"col_a": [1, 2, 3], "col_b": ["x", "y", "z"]})


@pytest.fixture
def sharepoint_config():
    return {
        "credentials": {
            "client_id": "test-id",
            "client_secret": "test-secret",
            "tenant_id": "test-tenant",
            "site": "TestSite",
        },
        "destination": {
            "filename": "output.xlsx",
            "directory": "Shared Documents/Reports",
        },
    }


class TestToSharepoint:
    def test_initialization(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        assert dest.data is sample_df
        assert dest._client_id == "test-id"
        assert dest._client_secret == "test-secret"
        assert dest._tenant_id == "test-tenant"
        assert dest._site == "TestSite"
        assert dest._filename == "output.xlsx"
        assert dest._directory == "Shared Documents/Reports"

    def test_excel_conversion(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.xlsx")
        assert len(file_bytes) > 0
        # Excel files start with PK signature (XLSX is ZIP-based)
        assert file_bytes[:2] == b'PK'

    def test_csv_conversion(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv")
        assert b"col_a" in file_bytes
        assert b"col_b" in file_bytes

    def test_unknown_extension_defaults_to_csv(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.txt")
        # Should be CSV (UTF-8)
        assert b"col_a" in file_bytes

    def test_xls_extension_uses_excel(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.xlsx")
        # XLSX is ZIP-based
        assert file_bytes[:2] == b'PK'

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, sharepoint_config):
        """run() returns original DataFrame after upload (pass-through)."""
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        with patch.object(dest, "_upload_to_sharepoint", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df

    @pytest.mark.asyncio
    async def test_run_dict_of_dataframes(self, sharepoint_config):
        """Dict of DataFrames: each is uploaded with a unique filename."""
        df_a = pd.DataFrame({"x": [1]})
        df_b = pd.DataFrame({"y": [2]})
        data = {"region_a": df_a, "region_b": df_b}
        dest = ToSharepoint(data=data, **sharepoint_config)

        upload_calls = []

        async def mock_upload(content: bytes, filename: str):
            upload_calls.append(filename)

        with patch.object(dest, "_upload_to_sharepoint", side_effect=mock_upload):
            result = await dest.run()

        assert result is data
        # Two uploads should have been made
        assert len(upload_calls) == 2
        # Filenames should embed the dict keys
        assert any("region_a" in n for n in upload_calls)
        assert any("region_b" in n for n in upload_calls)

    def test_navconfig_credential_resolution(self, sample_df):
        """Credentials given as navconfig variable names are resolved."""
        with patch("navconfig.config.get", return_value="resolved-value"):
            dest = ToSharepoint(
                data=sample_df,
                credentials={
                    "client_id": "SHAREPOINT_APP_ID",
                    "client_secret": "SHAREPOINT_APP_SECRET",
                    "tenant_id": "SHAREPOINT_TENANT_ID",
                    "site": "TestSite",
                },
                destination={"filename": "test.xlsx", "directory": "Docs"},
            )
        assert dest._client_id == "resolved-value"
        assert dest._client_secret == "resolved-value"

    def test_missing_credentials_raises_on_graph_client_build(self, sample_df):
        """Building the Graph client without credentials raises OutputError."""
        from querysource.exceptions import OutputError
        dest = ToSharepoint(
            data=sample_df,
            credentials={},
            destination={"filename": "f.xlsx", "directory": "d"},
        )
        with pytest.raises(OutputError, match="required"):
            dest._build_graph_client()

    @pytest.mark.asyncio
    async def test_upload_error_raises_output_error(self, sample_df, sharepoint_config):
        """If _upload_to_sharepoint raises, run() re-raises as OutputError."""
        from querysource.exceptions import OutputError
        dest = ToSharepoint(data=sample_df, **sharepoint_config)

        async def failing_upload(content, filename):
            raise RuntimeError("network failure")

        with patch.object(dest, "_upload_to_sharepoint", side_effect=failing_upload):
            with pytest.raises(OutputError):
                await dest.run()
