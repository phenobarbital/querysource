"""ParquetFileSource — Parquet from the local filesystem (FEAT-158)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from .base import ParquetSource

if TYPE_CHECKING:
    import fsspec


class ParquetFileSource(ParquetSource):
    """Read Parquet from the local filesystem (a file, directory or glob).

    Configuration dict shape::

        {
            "source": {"path": "/data/sales/{filedate}/*.parquet"},
            "masks": {"{filedate}": ["today", {"mask": "%Y%m%d"}]},
            "columns": ["store_id", "amount"],
            "filters": [["amount", ">=", 100]],
            "partitioning": "hive"
        }
    """

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        source = options.get('source', {})
        self._path: str = source.get('path')
        if not self._path:
            raise ValueError("ParquetFileSource: 'source.path' is required.")

    def _build_filesystem(self) -> tuple[fsspec.AbstractFileSystem, str]:
        """Return a LocalFileSystem and the absolute, mask-resolved path."""
        from fsspec.implementations.local import LocalFileSystem  # noqa: PLC0415

        resolved = self.resolve_masks(self._path)
        path = os.path.abspath(Path(resolved).expanduser())
        return LocalFileSystem(), path
