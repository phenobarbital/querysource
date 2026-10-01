# TASK-797: ParquetSource base (read path, size guard, off-loop fetch)

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-795, TASK-796
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 and §2 Overview. `ParquetSource(ThreadSource)` owns the entire read path for the three
Parquet sources:
- parse the common options;
- ask the subclass for `(fsspec filesystem, path)`;
- build `pyarrow.dataset.dataset(...)` over that filesystem;
- enforce the **hard size limit before materializing**;
- read with column and filter pushdown inside `asyncio.to_thread`;
- raise `DataNotFound` on empty results;
- wrap backend errors without leaking secrets.

The subclasses (TASK-798/799/800) only build filesystems.

---

## Scope

- Implement `ParquetSource` in `parquet/base.py`, following the interface below.
- Write `tests/test_source_parquet_base.py`, using a test-only concrete subclass over `LocalFileSystem`/`MemoryFileSystem`.

**NOT in scope**: the concrete sources, and registry or `__init__` exports (TASK-801).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/base.py` | CREATE | `ParquetSource` abstract base |
| `tests/test_source_parquet_base.py` | CREATE | Unit tests for the shared read path |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource import conf                                    # verified 2026-09-30: conf imports; MULTIQS_PARQUET_* added by TASK-795
from querysource.exceptions import DataNotFound                 # querysource/exceptions.py:53
from querysource.queries.multi.sources.base import ThreadSource # querysource/queries/multi/sources/base.py:14
from querysource.queries.multi.sources.parquet.filters import build_filter_expression, filter_columns  # TASK-796
# Third-party (import lazily inside methods): pyarrow.dataset as ds, fsspec
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                                  # line 14
    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:                             # line 25
        # pops options['masks'] into self._masks (lines 40-42); sets self.logger (line 43)
    def resolve_credential(self, key: str, value: str) -> str:              # line 45
    def resolve_masks(self, text: str) -> str:                              # line 72
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                                  # line 117
    def run(self) -> None:                                                  # line 132 — DataNotFound → info log + self.exc (150-154)

# querysource/exceptions.py:53
class DataNotFound(QueryException): default_code = 404

# querysource/queries/multi/_introspect.py:990 extract_source_schema(cls)
#   regex-walks every __init__ in the MRO (up to ThreadSource) for
#   `<var>.get|pop('<field>', <default>)`; `x = options.get('<container>', {})` makes a nested object.
#   A .get WITHOUT a default is reported as REQUIRED. The options param must be the 3rd positional arg.
```
Verified pyarrow-over-fsspec behaviour (2026-09-30): `ds.dataset(path, filesystem=<fsspec fs>, format="parquet")`,
`dataset.files`, `fs.sizes(dataset.files)`, `dataset.count_rows(filter=expr)`, `dataset.schema.names`,
`dataset.to_table(columns=..., filter=expr).to_pandas()`.

### Does NOT Exist
- ~~`conf.MULTIQS_PARQUET_MAX_ROWS` before TASK-795 lands~~: this task depends on it.
- ~~`ThreadSource.options`~~: the stored options are the private `self._options`.
- ~~`ThreadSource.is_unresolved`~~: not a real helper. `ParquetSource._is_unresolved` is created here, mirroring `S3Source` (querysource/queries/multi/sources/s3.py:154).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/base.py", "action": "CREATE"},
    {"path": "tests/test_source_parquet_base.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource",
    "sym:querysource/exceptions.py#DataNotFound"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Keep the literal `options.get('<field>', <default>)` calls in `__init__`: `extract_source_schema` introspects them (TASK-801 generates schemas from them).
- `super().__init__` first (it pops `masks`). Parse the options after it.
- No pyarrow/fsspec import at module level, because the registry import must stay lazy.
- The size guard runs before `to_table`. Check bytes first (cheap), then `count_rows(filter=expr)`.
- `max_bytes` is **compressed on-storage size**, and the class docstring must say so. Also mention
  `MULTIQS_SOURCE_TIMEOUT_SECONDS` (30 s default), which bounds large cloud scans.

---

## Implementation Blueprint

### Steps (in order)
1. Write `base.py` from the block — *why*: it fixes the interface TASK-798/799/800 extend.
2. Complete the FILL INs in `_read` in this order: path expansion, dataset, schema check, bytes guard, rows guard, empty → DataNotFound, materialize — *why*: that order is the "refuse before materializing" AC.
3. Write the tests with a `_LocalTestSource(ParquetSource)` defined in the test module — *why*: the concrete sources don't exist yet.

### `querysource/queries/multi/sources/parquet/base.py` (CREATE)
```python
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

    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        self._columns = options.get('columns', None)
        self._filters = options.get('filters', None)
        self._partitioning = options.get('partitioning', None)
        self._recursive = options.get('recursive', True)
        self._max_rows = options.get('max_rows', conf.MULTIQS_PARQUET_MAX_ROWS)
        self._max_bytes = options.get('max_bytes', conf.MULTIQS_PARQUET_MAX_BYTES)
        self._storage_options = options.get('storage_options', {}) or {}
        # FILL IN: validate — columns is None or list[str]; partitioning in (None, "hive");
        #          max_rows/max_bytes positive ints (bool rejected); storage_options is a dict;
        #          build_filter_expression(self._filters) once to fail fast. ValueError naming
        #          the class and field. Bounded by: spec §2 Data Models, test_base_rejects_nonpositive_limits.

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
```
*(continued in the next block, same file)*

```python
    def _read(self) -> pd.DataFrame:
        """Blocking read. Builds the dataset, enforces limits and materializes. Runs in a worker thread.

        Raises:
            DataNotFound: no files matched, or the filtered result is empty.
            ValueError: unknown column in columns/filters, or a size limit exceeded
                (the message names the limit and the observed rows/bytes).
        """
        import pyarrow.dataset as ds  # noqa: PLC0415

        fs, path = self._build_filesystem()
        expr = build_filter_expression(self._filters)
        # FILL IN (1) sources: glob chars in path → fs.glob(path) (sorted, only *.parquet files);
        #          directory → path as-is if self._recursive, else top-level *.parquet from fs.ls;
        #          file → [path]. Nothing matched → DataNotFound(f"{self._name}: no Parquet files at {path}").
        # FILL IN (2) dataset = ds.dataset(sources, filesystem=fs, format="parquet",
        #          partitioning="hive" if self._partitioning == "hive" else None)
        #          (for a glob list with hive, pass partition_base_dir = the glob's static prefix).
        # FILL IN (3) unknown = (set(self._columns or []) | filter_columns(self._filters)) - set(dataset.schema.names)
        #          → ValueError listing the unknown names and dataset.schema.names.
        # FILL IN (4) total_bytes = sum(fs.sizes(dataset.files)); > self._max_bytes → ValueError
        #          f"{self._name}: {total_bytes} bytes exceeds max_bytes={self._max_bytes}".
        # FILL IN (5) rows = dataset.count_rows(filter=expr); > self._max_rows → ValueError (same style);
        #          rows == 0 → DataNotFound.
        # FILL IN (6) return dataset.to_table(columns=self._columns, filter=expr).to_pandas()
        raise NotImplementedError

    async def fetch(self) -> pd.DataFrame:
        """Lazy-import the dependencies, then read off the event loop.

        Raises:
            ImportError: pyarrow/fsspec (or the backend package) is missing; names the extra.
            DataNotFound, ValueError: propagated unchanged from :meth:`_read`.
            RuntimeError: any backend/auth error, wrapped with the source name and redacted.
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
```
**Why this shape**: the signatures are fixed by spec §3 M3. `_is_unresolved`/`_secrets`/`_redact` are shared helpers that
TASK-799/800 rely on. They mirror `S3Source`'s unresolved-name guard (s3.py:154) and satisfy the "no secrets in errors" AC.
`from None` drops the original exception chain on purpose, so a secret inside the backend exception isn't
re-surfaced by tracebacks. Keep the ImportError/ValueError/DataNotFound pass-through.

### `tests/test_source_parquet_base.py` (CREATE)
```python
"""Unit tests for the ParquetSource shared read path (FEAT-158, TASK-797)."""
import asyncio

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
    df = pd.DataFrame({"a": list(range(10)), "c": ["US", "CA"] * 5})
    path = tmp_path / "data.parquet"
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


def _make(options):
    return _LocalTestSource("pq_test", options, None, asyncio.Queue())


def test_is_thread_source():
    assert issubclass(ParquetSource, ThreadSource)


async def test_roundtrip(parquet_file):
    df = await _make({"path": parquet_file}).fetch()
    assert df.shape == (10, 2)

# FILL IN: test_columns_projection, test_filters_pushdown, test_max_rows_exceeded (max_rows=1 →
#          ValueError mentioning "max_rows=1"), test_max_bytes_exceeded (max_bytes=10),
#          test_base_rejects_nonpositive_limits (max_rows=0 → ValueError at __init__),
#          test_unknown_column_valueerror, test_empty_filter_result_datanotfound,
#          test_no_match_datanotfound (glob with no hits), test_missing_pyarrow_importerror
#          (monkeypatch builtins.__import__ for "pyarrow.dataset" → ImportError mentioning
#          "querysource[parquet]"), test_backend_error_is_redacted (subclass whose _secrets()
#          returns ["s3cr3t"] and _build_filesystem raises OSError("bad s3cr3t") →
#          RuntimeError without "s3cr3t").
```

### FILL IN checklist
- [ ] `base.py::ParquetSource.__init__`: option validation; bounded by spec §2 Data Models.
- [ ] `base.py::ParquetSource._read` (1)–(6): order fixed; bounded by the AC "refuse before materialization".
- [ ] Remaining tests listed in the test block.

---

## Acceptance Criteria

- [ ] `ParquetSource` subclasses `ThreadSource`, and `fetch()` runs `_read` via `asyncio.to_thread`.
- [ ] Columns, DNF filters and hive partitioning are pushed into the pyarrow dataset scan.
- [ ] Exceeding `max_rows`/`max_bytes` raises `ValueError` naming the limit **before** `to_table` is called.
- [ ] No files matched, or an empty filtered result → `DataNotFound`.
- [ ] Missing pyarrow/fsspec → `ImportError` naming `querysource[parquet]`.
- [ ] Wrapped backend errors never contain values returned by `_secrets()`.
- [ ] Module import doesn't import pyarrow/fsspec.
- [ ] `ruff check querysource/queries/multi/sources/parquet/base.py tests/test_source_parquet_base.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_parquet_base.py -q`

---

## Test Specification

See the `tests/test_source_parquet_base.py` block above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies**: TASK-795 and TASK-796 must be `"done"` in `sdd/tasks/index/parquet-multiqs-source.json`.
4. **Verify the Codebase Contract** before writing code.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-797 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat gpt-5.6-terra, codex, 1 attempt, ~230s)
**Date**: 2026-09-30
**Notes**: `ParquetSource` base delivered per the blueprint; 13 tests pass, ruff clean. No review fixes needed.

**Deviations from spec**: none
