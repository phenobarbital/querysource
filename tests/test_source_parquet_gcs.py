"""Unit tests for ParquetGCSSource (FEAT-158, TASK-800). No real GCS access."""
import asyncio
import builtins
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from querysource import conf
from querysource.queries.multi.sources.parquet.gcs import ParquetGCSSource


def _make(options):
    return ParquetGCSSource("pq_gcs", options, None, asyncio.Queue())


@pytest.fixture
def sa_files(tmp_path, monkeypatch):
    google = tmp_path / "key.json"
    bq = tmp_path / "bigquery.json"
    google.write_text("{}")
    bq.write_text("{}")
    monkeypatch.setattr(conf, "GOOGLE_CREDENTIALS_FILE", google)
    monkeypatch.setattr(conf, "BIGQUERY_CREDENTIALS", bq)
    return google, bq


def test_google_credentials_file_wins(sa_files):
    google, _ = sa_files
    assert _make({"credentials": {"bucket": "b"}})._resolve_token() == str(google)


def test_explicit_token_wins(sa_files, tmp_path):
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}")
    source = _make({"credentials": {"bucket": "b", "token": str(explicit)}})
    assert source._resolve_token() == str(explicit)


def test_bigquery_fallback(sa_files):
    google, bq = sa_files
    google.unlink()
    assert _make({"credentials": {"bucket": "b"}})._resolve_token() == str(bq)


def test_google_default_fallback(sa_files):
    google, bq = sa_files
    google.unlink()
    bq.unlink()
    assert _make({"credentials": {"bucket": "b"}})._resolve_token() == "google_default"


@pytest.mark.parametrize("keyword", ["anon", "google_default"])
def test_token_keywords(sa_files, keyword):
    assert _make({"credentials": {"bucket": "b", "token": keyword}})._resolve_token() == keyword


def test_token_dict(sa_files):
    token = {"type": "service_account"}
    assert _make({"credentials": {"bucket": "b", "token": token}})._resolve_token() == token


def test_explicit_missing_path_valueerror(sa_files, tmp_path):
    missing = str(tmp_path / "nope.json")
    with pytest.raises(ValueError, match="nope.json"):
        _make({"credentials": {"bucket": "b", "token": missing}})._resolve_token()


def test_bucket_required():
    with pytest.raises(ValueError, match="bucket"):
        _make({"credentials": {}})


def test_path_normalization():
    source = _make({"credentials": {"bucket": "b"}, "source": {"directory": "/d/", "file": "/x.parquet"}})
    assert source._build_gcs_path() == "b/d/x.parquet"


def test_skip_instance_cache_forced(sa_files):
    source = _make({
        "credentials": {"bucket": "b", "project": "proj"},
        "storage_options": {"skip_instance_cache": False, "block_size": 5},
    })
    with patch("gcsfs.GCSFileSystem") as mock_fs:
        source._build_filesystem()
    kwargs = mock_fs.call_args.kwargs
    assert kwargs["skip_instance_cache"] is True
    assert kwargs["block_size"] == 5
    assert kwargs["project"] == "proj"


def test_missing_gcsfs_import_error(sa_files, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gcsfs":
            raise ImportError("no gcsfs")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"querysource\[gcs\]"):
        _make({"credentials": {"bucket": "b"}})._build_filesystem()


async def test_read_via_memory_fs(sa_files):
    memfs = MemoryFileSystem()
    memfs.store.clear()
    memfs.pseudo_dirs.append("")
    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    with memfs.open("/b/d/x.parquet", "wb") as handle:
        pq.write_table(pa.Table.from_pandas(frame), handle)
    source = _make({
        "credentials": {"bucket": "b"},
        "source": {"directory": "d", "file": "x.parquet"},
    })
    with patch("gcsfs.GCSFileSystem", return_value=memfs):
        result = await source.fetch()
    pd.testing.assert_frame_equal(result.reset_index(drop=True), frame)
