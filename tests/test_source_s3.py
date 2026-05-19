"""Unit tests for SourceS3 (TASK-649)."""
import asyncio
import gzip
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.s3 import SourceS3


class TestSourceS3:
    def test_inherits_thread_source(self):
        assert issubclass(SourceS3, ThreadSource)

    def test_parses_credentials(self):
        options = {
            "credentials": {
                "region_name": "us-east-1",
                "bucket": "my-bucket",
                "aws_key": "AKIATEST",
                "aws_secret": "secret123",
            },
            "source": {
                "file": "data.csv",
                "directory": "exports/",
            },
        }
        source = SourceS3("s3_test", options, None, asyncio.Queue())
        assert source._bucket == "my-bucket"
        assert source._file == "data.csv"
        assert source._directory == "exports/"
        assert source._region == "us-east-1"

    def test_s3_key_construction_with_directory(self):
        options = {
            "credentials": {"region_name": "us-east-1", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv", "directory": "path/to/"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        key = source._build_s3_key()
        assert key == "path/to/data.csv"

    def test_s3_key_construction_no_directory(self):
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        assert source._build_s3_key() == "data.csv"

    def test_s3_key_strips_trailing_slash(self):
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "f.csv", "directory": "dir/subdir/"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        assert source._build_s3_key() == "dir/subdir/f.csv"

    def test_parse_csv_content(self):
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        content = b"col1,col2\n1,a\n2,b"
        df = source._parse_content(content, "data.csv")
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]
        assert len(df) == 2

    def test_parse_gz_csv_content(self):
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv.gz"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        raw = b"col1,col2\n10,x\n20,y"
        compressed = gzip.compress(raw)
        df = source._parse_content(compressed, "data.csv.gz")
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]
        assert len(df) == 2

    def test_parse_excel_content(self):
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.xlsx"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        df_original = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        buf = io.BytesIO()
        df_original.to_excel(buf, index=False)
        content = buf.getvalue()
        df = source._parse_content(content, "data.xlsx")
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["A", "B"]

    def test_missing_aioboto3_raises_import_error(self):
        """fetch() should raise ImportError when aioboto3 is not installed."""
        import sys

        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv"},
        }
        source = SourceS3("test", options, None, asyncio.Queue())

        async def _run():
            with patch.dict(sys.modules, {'aioboto3': None}):
                with pytest.raises(ImportError, match="aioboto3"):
                    await source.fetch()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()

    def test_missing_file_raises_value_error(self):
        """fetch() should raise ValueError if source.file is not set."""
        options = {
            "credentials": {"region_name": "r", "bucket": "b",
                            "aws_key": "k", "aws_secret": "s"},
            "source": {},
        }
        source = SourceS3("test", options, None, asyncio.Queue())

        async def _run():
            with pytest.raises(ValueError, match="source.file"):
                await source.fetch()

        # aioboto3 must be importable for this test to reach the file check
        try:
            import aioboto3  # noqa: F401
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_run())
            loop.close()
        except ImportError:
            pytest.skip("aioboto3 not installed")
