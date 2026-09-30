"""ParquetSource — shared read path for MultiQS Parquet sources (FEAT-158)."""
from __future__ import annotations

import asyncio
from abc import abstractmethod
from typing import TYPE_CHECKING

import pandas as pd
from aiohttp import web

from querysource import conf
from querysource.exceptions import DataNotFound

from ..base import ThreadSource
from .filters import build_filter_expression, filter_columns

if TYPE_CHECKING:
    import fsspec

_GLOB_CHARS = ("*", "?", "[")


class ParquetSource(ThreadSource):
    """Abstract base for Parquet sources read through an fsspec filesystem.

    Reads with pyarrow.dataset (column projection, DNF filter pushdown, hive
    partitioning) inside ``asyncio.to_thread``. A hard limit refuses the read
    before materializing: ``max_rows`` counts filtered rows, and ``max_bytes`` sums
    the COMPRESSED on-storage size of the matched Parquet files. Large cloud scans
    are also bounded by ``conf.MULTIQS_SOURCE_TIMEOUT_SECONDS``.
    """

    #: Extra named in ImportError messages (subclasses override: "s3", "gcs").
    _extra: str = "parquet"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        self._columns = options.get('columns', None)
        self._filters = options.get('filters', None)
        self._partitioning = options.get('partitioning', None)
        self._recursive = options.get('recursive', True)
        self._max_rows = options.get('max_rows', conf.MULTIQS_PARQUET_MAX_ROWS)
        self._max_bytes = options.get('max_bytes', conf.MULTIQS_PARQUET_MAX_BYTES)
        self._storage_options = options.get('storage_options', {}) or {}

        if self._columns is not None and (
            not isinstance(self._columns, list) or not all(isinstance(column, str) for column in self._columns)
        ):
            raise ValueError(f"{type(self).__name__}: columns must be a list of strings")
        if self._partitioning not in (None, "hive"):
            raise ValueError(f"{type(self).__name__}: partitioning must be None or 'hive'")
        for field, value in (("max_rows", self._max_rows), ("max_bytes", self._max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{type(self).__name__}: {field} must be a positive integer")
        if not isinstance(self._storage_options, dict):
            raise ValueError(f"{type(self).__name__}: storage_options must be a dict")
        build_filter_expression(self._filters)

    @staticmethod
    def _is_unresolved(value: object) -> bool:
        """True for a value that still looks like an unresolved navconfig name (UPPER_SNAKE)."""
        return isinstance(value, str) and value.isupper() and '_' in value

    @abstractmethod
    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
        """Return (filesystem, root path or glob) with masks already resolved."""

    def _secrets(self) -> list[str]:
        """Credential values to redact from error messages (subclasses override)."""
        return []

    def _redact(self, text: str) -> str:
        """Replace every non-empty secret in *text* with ``***``."""
        for secret in self._secrets():
            if secret:
                text = text.replace(secret, "***")
        return text

    def _read(self) -> pd.DataFrame:
        """Build the dataset, enforce limits, and materialize in a worker thread.

        Raises:
            DataNotFound: No files matched, or the filtered result is empty.
            ValueError: An unknown column was requested, or a size limit was exceeded.
        """
        import pyarrow.dataset as ds  # noqa: PLC0415

        fs, path = self._build_filesystem()
        expr = build_filter_expression(self._filters)
        partition_base_dir = None
        if any(character in path for character in _GLOB_CHARS):
            sources = sorted(item for item in fs.glob(path) if item.endswith(".parquet"))
            first_glob = min(path.index(character) for character in _GLOB_CHARS if character in path)
            static_prefix = path[:first_glob]
            partition_base_dir = static_prefix.rstrip("/") or None
        elif fs.isdir(path):
            if self._recursive:
                sources = path
            else:
                sources = sorted(item for item in fs.ls(path) if item.endswith(".parquet"))
        else:
            sources = [path] if fs.isfile(path) else []

        if not sources:
            raise DataNotFound(f"{self._name}: no Parquet files at {path}")

        dataset_kwargs = {
            "filesystem": fs,
            "format": "parquet",
            "partitioning": "hive" if self._partitioning == "hive" else None,
        }
        if partition_base_dir is not None and self._partitioning == "hive":
            dataset_kwargs["partition_base_dir"] = partition_base_dir
        dataset = ds.dataset(sources, **dataset_kwargs)

        unknown = (set(self._columns or []) | filter_columns(self._filters)) - set(dataset.schema.names)
        if unknown:
            raise ValueError(
                f"{self._name}: unknown columns {sorted(unknown)}; available columns: {dataset.schema.names}"
            )

        total_bytes = sum(fs.sizes(dataset.files))
        if total_bytes > self._max_bytes:
            raise ValueError(f"{self._name}: {total_bytes} bytes exceeds max_bytes={self._max_bytes}")

        rows = dataset.count_rows(filter=expr)
        if rows > self._max_rows:
            raise ValueError(f"{self._name}: {rows} rows exceeds max_rows={self._max_rows}")
        if rows == 0:
            raise DataNotFound(f"{self._name}: no rows matched Parquet filters")
        return dataset.to_table(columns=self._columns, filter=expr).to_pandas()

    async def fetch(self) -> pd.DataFrame:
        """Lazy-import dependencies and read off the event loop.

        Raises:
            ImportError: The parquet dependencies are missing.
            DataNotFound, ValueError: Propagated from :meth:`_read`.
            RuntimeError: A redacted backend or authentication error occurred.
        """
        try:
            import fsspec  # noqa: F401, PLC0415
            import pyarrow.dataset  # noqa: F401, PLC0415
        except ImportError as exc:
            raise ImportError(
                "Install the parquet extra for Parquet sources: pip install querysource[parquet]"
            ) from exc
        try:
            return await asyncio.to_thread(self._read)
        except (DataNotFound, ValueError, ImportError):
            raise
        except Exception as exc:  # noqa: BLE001
            message = self._redact(f"{type(exc).__name__}: {exc}")
            raise RuntimeError(f"{type(self).__name__} {self._name!r} read failed: {message}") from None
