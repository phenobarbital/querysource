"""Integration tests for MultiQS sources dispatch (TASK-651)."""
import asyncio
from unittest.mock import MagicMock

import pandas as pd
import pytest

from querysource.exceptions import DriverError
from querysource.queries.multi import MultiQS


class TestMultiQSSources:
    def test_sources_key_extracted(self):
        """Sources key should be parsed from query dict."""
        query = {
            "sources": [{"SourceTable": {"driver": "pg", "table": "test"}}]
        }
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._sources is not None
        assert len(mqs._sources) == 1

    def test_empty_check_includes_sources(self):
        """Should not raise DriverError when only sources is provided."""
        query = {
            "sources": [{"SourceTable": {"driver": "pg", "table": "test"}}]
        }
        # Must NOT raise DriverError
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._sources

    def test_all_empty_still_raises(self):
        """Empty queries, files, slug, and sources should still raise DriverError."""
        with pytest.raises(DriverError):
            MultiQS(query={}, request=MagicMock())

    def test_existing_queries_still_work(self):
        """Existing queries key should be parsed as before."""
        query = {"queries": {"myq": {"slug": "some-slug"}}}
        mqs = MultiQS(query=query, request=MagicMock())
        assert "myq" in mqs._queries

    def test_existing_files_still_work(self):
        """Existing files key should be parsed as before."""
        query = {"files": {"myfile": {"path": "/tmp/test.csv", "mime": "text/csv"}}}
        mqs = MultiQS(query=query, request=MagicMock())
        assert "myfile" in mqs._files

    def test_sources_defaults_to_empty_list(self):
        """When no sources key is present, _sources defaults to empty list."""
        query = {"queries": {"q": {"slug": "some-slug"}}}
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._sources == []

    def test_multiple_sources_extracted(self):
        """Multiple source entries should all be extracted."""
        query = {
            "sources": [
                {"SourceTable": {"driver": "pg", "table": "t1"}},
                {"SourceS3": {"source": {"file": "f.csv"}}},
            ]
        }
        mqs = MultiQS(query=query, request=MagicMock())
        assert len(mqs._sources) == 2

    def test_combined_queries_files_sources(self):
        """All three source types can coexist."""
        query = {
            "queries": {"q": {"slug": "slug1"}},
            "files": {"f": {"path": "/tmp/x.csv", "mime": "text/csv"}},
            "sources": [{"SourceTable": {"driver": "pg", "table": "t"}}],
        }
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._queries
        assert mqs._files
        assert mqs._sources
