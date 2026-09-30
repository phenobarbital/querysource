"""Unit tests for ParquetFileSource (FEAT-158, TASK-798)."""
import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

from querysource.exceptions import DataNotFound
from querysource.queries.multi.sources.parquet.local import ParquetFileSource


@pytest.fixture
def frame():
    return pd.DataFrame({"a": list(range(6)), "country": ["US", "CA", "MX"] * 2})


def _make(options):
    return ParquetFileSource("pq_local", options, None, asyncio.Queue())


def test_path_required():
    with pytest.raises(ValueError, match="source.path"):
        _make({"source": {}})


async def test_single_file_roundtrip(tmp_path, frame):
    path = tmp_path / "x.parquet"
    frame.to_parquet(path, index=False)
    df = await _make({"source": {"path": str(path)}}).fetch()
    pd.testing.assert_frame_equal(df, frame)


async def test_directory_of_files(tmp_path, frame):
    frame.to_parquet(tmp_path / "first.parquet", index=False)
    frame.to_parquet(tmp_path / "second.parquet", index=False)

    df = await _make({"source": {"path": str(tmp_path)}}).fetch()

    assert len(df) == 12


async def test_hive_partitioned_dir(tmp_path, frame):
    path = tmp_path / "hive"
    frame.to_parquet(path, index=False, partition_cols=["country"])

    df = await _make(
        {
            "source": {"path": str(path)},
            "partitioning": "hive",
            "filters": [["country", "==", "US"]],
        }
    ).fetch()

    assert len(df) == 2
    assert "country" in df.columns
    assert set(df["country"]) == {"US"}


async def test_glob_and_masks(tmp_path, frame):
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    frame.to_parquet(tmp_path / f"sales_{today}.parquet", index=False)

    df = await _make(
        {
            "source": {"path": str(tmp_path / "sales_{d}.parquet")},
            "masks": {"{d}": ["today", {"mask": "%Y%m%d"}]},
        }
    ).fetch()

    pd.testing.assert_frame_equal(df, frame)


async def test_no_match_raises_datanotfound(tmp_path):
    with pytest.raises(DataNotFound):
        await _make({"source": {"path": str(tmp_path / "*.parquet")}}).fetch()


async def test_recursive_false_ignores_subdirs(tmp_path, frame):
    frame.to_parquet(tmp_path / "top.parquet", index=False)
    nested = tmp_path / "nested"
    nested.mkdir()
    frame.to_parquet(nested / "nested.parquet", index=False)

    df = await _make({"source": {"path": str(tmp_path)}, "recursive": False}).fetch()

    pd.testing.assert_frame_equal(df, frame)


async def test_hive_glob_keeps_partition_column(tmp_path, frame):
    for part in ("dt=20260101", "dt=20260102"):
        (tmp_path / part).mkdir()
        frame.to_parquet(tmp_path / part / "p.parquet", index=False)

    df = await _make(
        {"source": {"path": str(tmp_path / "dt=2026*" / "*.parquet")}, "partitioning": "hive"}
    ).fetch()

    assert "dt" in df.columns
