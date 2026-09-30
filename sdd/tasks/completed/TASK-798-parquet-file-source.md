# TASK-798: ParquetFileSource (local filesystem)

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-797
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4. `ParquetFileSource` reads a local Parquet file, a directory, a hive-partitioned
dataset or a glob. `source.path` supports `masks` (for example `{filedate}`), which are resolved
like `FileSource` does. All reading logic lives in `ParquetSource` (TASK-797), so this class only builds the filesystem.

---

## Scope

- Implement `ParquetFileSource` in `parquet/local.py`.
- Write `tests/test_source_parquet_local.py`, covering the glob, masks and hive-partitioned directory end to end.

**NOT in scope**: registry or exports (TASK-801), and changing `FileSource`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/local.py` | CREATE | `ParquetFileSource` |
| `tests/test_source_parquet_local.py` | CREATE | Local-backend tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.multi.sources.parquet.base import ParquetSource   # TASK-797
from fsspec.implementations.local import LocalFileSystem                     # verified in .venv (fsspec 2026.7.0); import lazily
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/parquet/base.py (TASK-797)
class ParquetSource(ThreadSource):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None
    @abstractmethod
    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]
    async def fetch(self) -> pd.DataFrame

# querysource/queries/multi/sources/base.py:72
def resolve_masks(self, text: str) -> str      # masks come from options['masks'], popped in ThreadSource.__init__ (40-42)

# querysource/queries/multi/sources/file.py:45-51 — pattern: masks resolved AFTER super().__init__
```

### Does NOT Exist
- ~~`FileSource` Parquet support~~: `FileSource` stays CSV/Excel only. Don't modify it.
- ~~A `mime` option for Parquet sources~~: the backend is implied by the class and the format is always parquet.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/local.py", "action": "CREATE"},
    {"path": "tests/test_source_parquet_local.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.resolve_masks"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write `local.py` from the block — *why*: the `source` container plus a default-less `source.get('path')` makes `path` a required nested field in the generated schema (introspection, TASK-801).
2. Write the tests — *why*: these are the spec §4 local-backend cases.

### `querysource/queries/multi/sources/parquet/local.py` (CREATE)
```python
"""ParquetFileSource — Parquet from the local filesystem (FEAT-158)."""
from __future__ import annotations

import asyncio
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

    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        source = options.get('source', {})
        self._path: str = source.get('path')
        if not self._path:
            raise ValueError("ParquetFileSource: 'source.path' is required.")

    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
        """Return a LocalFileSystem and the absolute, mask-resolved path."""
        from fsspec.implementations.local import LocalFileSystem  # noqa: PLC0415

        # FILL IN: resolved = self.resolve_masks(self._path); expand "~"; make absolute with
        #          Path(...).expanduser() and os.path.abspath (do NOT Path.resolve() a glob —
        #          it may touch the filesystem). Return (LocalFileSystem(), that string).
        #          Bounded by: spec §3 M4 skeleton.
        raise NotImplementedError
```
**Why this shape**: the interface is fixed by spec §3 M4. The literal `options.get('source', {})` / `source.get('path')`
calls must stay literal, because `extract_source_schema` (querysource/queries/multi/_introspect.py:990) parses them.

### `tests/test_source_parquet_local.py` (CREATE)
```python
"""Unit tests for ParquetFileSource (FEAT-158, TASK-798)."""
import asyncio
from datetime import date

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

# FILL IN: test_directory_of_files (two files in a dir → 12 rows),
#          test_hive_partitioned_dir (frame.to_parquet(dir, partition_cols=["country"]);
#            partitioning="hive"; filter country=="US" → 2 rows and a "country" column present),
#          test_glob_and_masks (file "sales_<today %Y%m%d>.parquet"; path "…/sales_{d}.parquet"
#            with masks {"{d}": ["today", {"mask": "%Y%m%d"}]}),
#          test_no_match_raises_datanotfound (glob "*.parquet" in an empty dir),
#          test_recursive_false_ignores_subdirs.
```

### FILL IN checklist
- [ ] `local.py::ParquetFileSource._build_filesystem`: mask resolution and an absolute path without resolving globs.
- [ ] Remaining tests listed in the test block.

---

## Acceptance Criteria

- [ ] Local file, directory, glob, hive-partitioned directory and masks work end to end through `fetch()`.
- [ ] Missing `source.path` → `ValueError`.
- [ ] `ruff check querysource/queries/multi/sources/parquet/local.py tests/test_source_parquet_local.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_parquet_local.py -q`

---

## Test Specification

See the `tests/test_source_parquet_local.py` block above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies**: TASK-797 must be `"done"`.
4. **Verify the Codebase Contract** before writing code.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-798 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat gpt-5.6-terra, codex, ~139s)
**Date**: 2026-09-30
**Notes**: `ParquetS3Source` delivered per the task; combined Parquet suite 60 passed. Residual style lint left for /sdd-done
(DTZ011 in tests/test_source_parquet_local.py, B018 in tests/test_source_parquet_s3.py). No review fixes needed.

**Deviations from spec**: none
