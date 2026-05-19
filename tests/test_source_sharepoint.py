"""Unit tests for SourceSharepoint (TASK-647)."""
import asyncio
import io

import pandas as pd
import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.sharepoint import SourceSharepoint


class TestSourceSharepoint:
    def test_inherits_thread_source(self):
        assert issubclass(SourceSharepoint, ThreadSource)

    def test_parses_credentials(self):
        options = {
            "credentials": {
                "client_id": "test_id",
                "client_secret": "test_secret",
                "tenant_id": "test_tenant",
                "tenant_name": "contoso",
                "site": "TestSite",
            },
            "source": {
                "filename": "test.xlsx",
                "directory": "Shared Documents/General",
            },
        }
        source = SourceSharepoint("sp_test", options, None, asyncio.Queue())
        assert source._client_id == "test_id"
        assert source._client_secret == "test_secret"
        assert source._tenant_id == "test_tenant"
        assert source._site == "TestSite"
        assert source._filename == "test.xlsx"
        assert source._directory == "Shared Documents/General"

    def test_parses_source_config(self):
        options = {
            "credentials": {"client_id": "id", "client_secret": "s", "tenant_id": "t"},
            "source": {"filename": "data.csv", "directory": "Reports"},
        }
        source = SourceSharepoint("sp", options, None, asyncio.Queue())
        assert source._filename == "data.csv"
        assert source._directory == "Reports"

    def test_default_credentials_resolve_navconfig_names(self):
        """When no credentials provided, defaults are navconfig var names."""
        options = {"source": {"filename": "f.xlsx", "directory": "D"}}
        source = SourceSharepoint("sp", options, None, asyncio.Queue())
        # client_id should either be resolved from navconfig or remain as string
        assert isinstance(source._client_id, str)
        assert isinstance(source._client_secret, str)
        assert isinstance(source._tenant_id, str)

    def test_parse_csv_content(self):
        options = {
            "credentials": {"client_id": "i", "client_secret": "s", "tenant_id": "t"},
            "source": {"filename": "data.csv", "directory": "D"},
        }
        source = SourceSharepoint("sp", options, None, asyncio.Queue())
        content = b"col1,col2\n1,a\n2,b"
        df = source._parse_file_content(content)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]
        assert len(df) == 2

    def test_parse_excel_content(self):
        options = {
            "credentials": {"client_id": "i", "client_secret": "s", "tenant_id": "t"},
            "source": {"filename": "data.xlsx", "directory": "D"},
        }
        source = SourceSharepoint("sp", options, None, asyncio.Queue())
        df_original = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        buf = io.BytesIO()
        df_original.to_excel(buf, index=False)
        content = buf.getvalue()
        df = source._parse_file_content(content)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]

    def test_missing_msgraph_raises_import_error(self):
        """fetch() should raise ImportError when msgraph-sdk is not installed."""
        import sys
        from unittest.mock import patch

        options = {
            "credentials": {"client_id": "i", "client_secret": "s", "tenant_id": "t",
                            "tenant_name": "contoso", "site": "S"},
            "source": {"filename": "f.xlsx", "directory": "D"},
        }
        source = SourceSharepoint("sp", options, None, asyncio.Queue())

        async def _run():
            with patch.dict(sys.modules, {'azure': None, 'azure.identity': None,
                                          'azure.identity.aio': None,
                                          'msgraph': None}):
                with pytest.raises(ImportError, match="msgraph-sdk"):
                    await source.fetch()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()
