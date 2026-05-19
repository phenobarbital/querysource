"""Backward compatibility tests for refactored ThreadFile (TASK-645)."""
import asyncio

import pandas as pd
import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.file import ThreadFile


class TestThreadFileRefactor:
    def test_inherits_thread_source(self):
        assert issubclass(ThreadFile, ThreadSource)

    def test_has_fetch_method(self):
        assert hasattr(ThreadFile, 'fetch')

    def test_run_is_not_overridden(self):
        # run() should come from ThreadSource, not ThreadFile
        assert 'run' not in ThreadFile.__dict__

    def test_csv_file_produces_dataframe(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col1,col2\n1,a\n2,b")
        queue = asyncio.Queue()
        t = ThreadFile("test_csv", {"path": str(csv_file), "mime": "text/csv"}, None, queue)
        t.start()
        t.join()
        assert t.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        assert "test_csv" in result
        assert isinstance(result["test_csv"], pd.DataFrame)
        assert list(result["test_csv"].columns) == ["col1", "col2"]

    def test_csv_file_content_is_correct(self, tmp_path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,value\nalpha,10\nbeta,20\ngamma,30")
        queue = asyncio.Queue()
        t = ThreadFile("data", {"path": str(csv_file), "mime": "text/csv"}, None, queue)
        t.start()
        t.join()
        assert t.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        df = result["data"]
        assert len(df) == 3
        assert list(df["name"]) == ["alpha", "beta", "gamma"]

    def test_gz_csv_file_produces_dataframe(self, tmp_path):
        import gzip
        csv_content = b"col1,col2\n1,a\n2,b"
        gz_file = tmp_path / "test.csv.gz"
        with gzip.open(gz_file, 'wb') as f:
            f.write(csv_content)
        queue = asyncio.Queue()
        t = ThreadFile("gz_test", {"path": str(gz_file), "mime": "text/csv"}, None, queue)
        t.start()
        t.join()
        assert t.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        assert isinstance(result["gz_test"], pd.DataFrame)

    def test_constructor_signature_preserved(self, tmp_path):
        """Verify ThreadFile(name, file_options, request, queue) still works."""
        csv_file = tmp_path / "sig.csv"
        csv_file.write_text("a,b\n1,2")
        queue = asyncio.Queue()
        # This call matches the old signature exactly.
        t = ThreadFile(
            "sig_test",
            {"path": str(csv_file), "mime": "text/csv"},
            None,
            queue,
        )
        assert t._name == "sig_test"
        assert t._mime == "text/csv"
