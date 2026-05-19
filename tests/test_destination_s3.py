"""
Unit tests for ToS3 destination.

S3 API calls are mocked — no real AWS credentials required.
"""
import gzip
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.s3 import ToS3


@pytest.fixture
def sample_df():
    return pd.DataFrame({"id": [1, 2], "value": [10.5, 20.3]})


@pytest.fixture
def s3_config():
    return {
        "credentials": {
            "region_name": "us-east-1",
            "bucket": "test-bucket",
            "aws_key": "AKIATEST",
            "aws_secret": "testsecret",
        },
        "destination": {
            "file": "output.csv",
            "directory": "exports/",
        },
    }


class TestToS3:
    def test_initialization(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        assert dest.data is sample_df
        assert dest._bucket == "test-bucket"
        assert dest._region == "us-east-1"
        assert dest._aws_key == "AKIATEST"
        assert dest._aws_secret == "testsecret"
        assert dest._file == "output.csv"
        assert dest._directory == "exports"

    def test_csv_conversion(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv")
        assert b"id" in file_bytes
        assert b"value" in file_bytes

    def test_gzip_conversion(self, sample_df, s3_config):
        s3_config["destination"]["file"] = "output.csv.gz"
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv.gz")
        # gzip magic bytes
        assert file_bytes[:2] == b'\x1f\x8b'
        # Decompress and check content
        decompressed = gzip.decompress(file_bytes)
        assert b"id" in decompressed

    def test_parquet_conversion(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.parquet")
        # Parquet magic bytes: PAR1
        assert file_bytes[:4] == b'PAR1'

    def test_excel_conversion(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.xlsx")
        # XLSX is ZIP-based
        assert file_bytes[:2] == b'PK'

    def test_unknown_extension_defaults_to_csv(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.dat")
        assert b"id" in file_bytes

    def test_explicit_format_overrides_extension(self, sample_df):
        """Explicit format in destination config overrides filename extension."""
        dest = ToS3(
            data=sample_df,
            credentials={"bucket": "b"},
            destination={"file": "output.txt", "format": "csv"},
        )
        file_bytes = dest._convert_dataframe(sample_df, "output.txt")
        assert b"id" in file_bytes

    def test_s3_key_construction_with_directory(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        key = dest._build_s3_key()
        assert key == "exports/output.csv"

    def test_s3_key_construction_no_directory(self, sample_df):
        dest = ToS3(
            data=sample_df,
            credentials={"bucket": "b"},
            destination={"file": "output.csv"},
        )
        key = dest._build_s3_key()
        assert key == "output.csv"

    def test_s3_key_no_leading_slash(self, sample_df):
        dest = ToS3(
            data=sample_df,
            credentials={"bucket": "b"},
            destination={"file": "output.csv", "directory": "/exports/"},
        )
        key = dest._build_s3_key()
        assert not key.startswith("/"), f"S3 key should not start with '/': {key}"

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        with patch.object(dest, "_upload_to_s3", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df

    @pytest.mark.asyncio
    async def test_run_calls_upload_with_correct_key(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        upload_calls = []

        async def capture_upload(content, s3_key):
            upload_calls.append(s3_key)

        with patch.object(dest, "_upload_to_s3", side_effect=capture_upload):
            await dest.run()

        assert upload_calls == ["exports/output.csv"]

    @pytest.mark.asyncio
    async def test_run_dict_of_dataframes(self):
        """Dict of DataFrames: each is uploaded with a unique S3 key."""
        df_a = pd.DataFrame({"x": [1]})
        df_b = pd.DataFrame({"y": [2]})
        data = {"north": df_a, "south": df_b}
        dest = ToS3(
            data=data,
            credentials={"bucket": "test-bucket"},
            destination={"file": "report.csv", "directory": "reports/"},
        )

        upload_calls = []

        async def capture_upload(content, s3_key):
            upload_calls.append(s3_key)

        with patch.object(dest, "_upload_to_s3", side_effect=capture_upload):
            result = await dest.run()

        assert result is data
        assert len(upload_calls) == 2
        assert any("north" in k for k in upload_calls)
        assert any("south" in k for k in upload_calls)

    def test_navconfig_credential_resolution(self, sample_df):
        """Credentials given as navconfig variable names are resolved."""
        with patch("navconfig.config.get", return_value="my-bucket"):
            dest = ToS3(
                data=sample_df,
                credentials={
                    "region_name": "us-east-1",
                    "bucket": "AWS_BUCKET",
                    "aws_key": "AKIATEST",
                    "aws_secret": "testsecret",
                },
                destination={"file": "f.csv", "directory": "d/"},
            )
        assert dest._bucket == "my-bucket"

    @pytest.mark.asyncio
    async def test_missing_bucket_raises_on_upload(self, sample_df):
        """Missing bucket raises OutputError during upload."""
        from querysource.exceptions import OutputError
        dest = ToS3(
            data=sample_df,
            credentials={},
            destination={"file": "f.csv"},
        )
        with pytest.raises(OutputError, match="bucket"):
            await dest._upload_to_s3(b"data", "key")
