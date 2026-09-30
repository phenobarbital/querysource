"""Unit tests for the ParquetSource shared read path (FEAT-158, TASK-797)."""
import ast
import asyncio
import builtins
import inspect
import sys

import pandas as pd
import pytest

from querysource.exceptions import DataNotFound
from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.parquet.base import ParquetSource


class _LocalTestSource(ParquetSource):
    """Test-only concrete source over the local filesystem."""

    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        self._path = options.get('path')

    def _build_filesystem(self):
        from fsspec.implementations.local import LocalFileSystem

        return LocalFileSystem(), str(self._path)


@pytest.fixture
def parquet_file(tmp_path):
    """Create a small Parquet fixture."""
    df = pd.DataFrame({"a": list(range(10)), "c": ["US", "CA"] * 5})
    path = tmp_path / "data.parquet"
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


def _make(options):
    return _LocalTestSource("pq_test", options, None, asyncio.Queue())


def test_is_thread_source():
    assert issubclass(ParquetSource, ThreadSource)


def test_module_imports_are_lazy():
    tree = ast.parse(inspect.getsource(__import__(ParquetSource.__module__, fromlist=["*"])))
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    imported = {alias.name for node in imports for alias in node.names}
    assert "fsspec" not in imported
    assert "pyarrow.dataset" not in imported


async def test_roundtrip(parquet_file):
    df = await _make({"path": parquet_file}).fetch()
    assert df.shape == (10, 2)


async def test_columns_projection(parquet_file):
    df = await _make({"path": parquet_file, "columns": ["a"]}).fetch()
    assert list(df.columns) == ["a"]


async def test_filters_pushdown(parquet_file):
    df = await _make({"path": parquet_file, "filters": [["c", "==", "US"]]}).fetch()
    assert df.shape == (5, 2)
    assert set(df["c"]) == {"US"}


async def test_max_rows_exceeded(parquet_file):
    with pytest.raises(ValueError, match="max_rows=1"):
        await _make({"path": parquet_file, "max_rows": 1}).fetch()


async def test_max_bytes_exceeded(parquet_file):
    with pytest.raises(ValueError, match="max_bytes=10"):
        await _make({"path": parquet_file, "max_bytes": 10}).fetch()


def test_base_rejects_nonpositive_limits(parquet_file):
    with pytest.raises(ValueError, match="max_rows"):
        _make({"path": parquet_file, "max_rows": 0})


async def test_unknown_column_valueerror(parquet_file):
    with pytest.raises(ValueError, match="unknown columns"):
        await _make({"path": parquet_file, "columns": ["missing"]}).fetch()


async def test_empty_filter_result_datanotfound(parquet_file):
    with pytest.raises(DataNotFound):
        await _make({"path": parquet_file, "filters": [["c", "==", "none"]]}).fetch()


async def test_no_match_datanotfound(tmp_path):
    with pytest.raises(DataNotFound):
        await _make({"path": tmp_path / "missing-*.parquet"}).fetch()


async def test_missing_pyarrow_importerror(monkeypatch, parquet_file):
    original_import = builtins.__import__

    def missing_pyarrow(name, *args, **kwargs):
        if name == "pyarrow.dataset":
            raise ImportError("missing pyarrow")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_pyarrow)
    with pytest.raises(ImportError, match=r"querysource\[parquet\]"):
        await _make({"path": parquet_file}).fetch()


async def test_backend_error_is_redacted(parquet_file):
    class _SecretSource(_LocalTestSource):
        def _secrets(self):
            return ["s3cr3t"]

        def _build_filesystem(self):
            raise OSError("bad s3cr3t")

    with pytest.raises(RuntimeError, match="read failed") as exc_info:
        await _SecretSource("secret", {"path": parquet_file}, None, asyncio.Queue()).fetch()
    assert "s3cr3t" not in str(exc_info.value)
