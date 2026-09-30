"""MultiQS integration for Parquet sources (FEAT-158, TASK-801)."""
import ast
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources import SOURCE_REGISTRY


def test_multiqs_parses_parquet_source(tmp_path):
    """MultiQS should retain a configured local Parquet source."""
    query = {"sources": [{"ParquetFileSource": {"source": {"path": str(tmp_path / "x.parquet")}}}]}
    mqs = MultiQS(query=query, request=MagicMock())
    assert mqs._sources and "ParquetFileSource" in mqs._sources[0]


def test_parquet_thread_puts_frame_on_queue(tmp_path):
    """A local Parquet source should place its loaded frame under its name."""
    pd.DataFrame({"a": [1, 2]}).to_parquet(tmp_path / "x.parquet", index=False)
    queue: asyncio.Queue = asyncio.Queue()
    cls = SOURCE_REGISTRY["ParquetFileSource"]
    thread = cls("ParquetFileSource", {"source": {"path": str(tmp_path / "x.parquet")}}, MagicMock(), queue)
    thread.start()
    thread.join(timeout=30)
    assert thread.exc is None
    result = queue.get_nowait()
    assert set(result) == {"ParquetFileSource"}
    assert result["ParquetFileSource"]["a"].tolist() == [1, 2]


def test_sources_import_is_lazy():
    """Parquet modules must defer optional filesystem backend imports to fetch time."""
    source_dir = Path(__file__).parent.parent / "querysource" / "queries" / "multi" / "sources" / "parquet"
    for module_name in ("base.py", "s3.py", "gcs.py"):
        tree = ast.parse((source_dir / module_name).read_text())
        imports = {
            alias.name
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert "s3fs" not in imports
        assert "gcsfs" not in imports
