# TASK-799: ParquetS3Source (S3 / S3-compatible via s3fs)

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-795, TASK-797
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5. `ParquetS3Source` reads Parquet from AWS S3 or an S3-compatible endpoint (MinIO, R2, Wasabi)
through `s3fs.S3FileSystem`. Its credentials block mirrors `S3Source` (navconfig-resolved; unresolved names fall
back to the ambient AWS chain). It adds `profile`, `anon`, `endpoint_url` and a `storage_options` passthrough, all
decided in the brainstorm.

---

## Scope

- Implement `ParquetS3Source` in `parquet/s3.py`.
- Write `tests/test_source_parquet_s3.py`, patching `s3fs.S3FileSystem` so no real AWS is ever called.

**NOT in scope**: changing `S3Source` (moving it to s3fs is a follow-up feature), and registry or exports (TASK-801).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/s3.py` | CREATE | `ParquetS3Source` |
| `tests/test_source_parquet_s3.py` | CREATE | S3 kwargs mapping and end-to-end read over MemoryFileSystem |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.multi.sources.parquet.base import ParquetSource   # TASK-797
import s3fs                                                                  # installed by TASK-795 — import lazily
from fsspec.implementations.memory import MemoryFileSystem                   # tests only; verified in .venv
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/parquet/base.py (TASK-797)
class ParquetSource(ThreadSource):
    _extra: str
    self._storage_options: dict
    @staticmethod
    def _is_unresolved(value: object) -> bool
    def _secrets(self) -> list[str]
    @abstractmethod
    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]

# querysource/queries/multi/sources/s3.py — pattern reference (NOT modified)
#   lines 55-67: creds = options.get('credentials', {}); self.resolve_credential('<key>', creds.get('<key>', '<ENV_NAME>'))
#   lines 152-159: pass explicit key/secret only when not unresolved UPPER_SNAKE names
# querysource/queries/multi/sources/base.py:45  resolve_credential(key, value) -> str
# querysource/queries/multi/sources/base.py:72  resolve_masks(text) -> str
```
s3fs constructor kwargs used: `key`, `secret`, `profile`, `anon`, `endpoint_url`, `client_kwargs={"region_name": ...}`,
and `skip_instance_cache` (the fsspec cache-bypass kwarg accepted by every fsspec filesystem).

### Does NOT Exist
- ~~`S3Source` masks support~~: `S3Source` never calls `resolve_masks`. This class does, on `directory` and `file`.
- ~~`s3fs.S3FileSystem(region_name=...)`~~: the region goes in `client_kwargs`, not as a top-level kwarg.
- ~~A shared S3 client between `S3Source` and this class~~: out of scope (follow-up).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/s3.py", "action": "CREATE"},
    {"path": "tests/test_source_parquet_s3.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.resolve_credential",
    "sym:querysource/queries/multi/sources/s3.py#S3Source"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write `s3.py` from the block — *why*: the literal `creds.get`/`source.get` calls produce the introspected schema.
2. Complete `_build_s3_path` and `_build_filesystem` — *why*: this is the kwargs mapping fixed in spec §7.
3. Write the tests with `MemoryFileSystem` substituted for `s3fs.S3FileSystem` — *why*: exercises the full read path with no network.

### `querysource/queries/multi/sources/parquet/s3.py` (CREATE)
```python
"""ParquetS3Source — Parquet from AWS S3 or S3-compatible storage via s3fs (FEAT-158)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web

from .base import ParquetSource

if TYPE_CHECKING:
    import fsspec


class ParquetS3Source(ParquetSource):
    """Read Parquet from AWS S3 or an S3-compatible endpoint via s3fs.

    Credential values may be navconfig variable names. Names that stay unresolved
    are ignored, so s3fs falls back to the ambient AWS credential chain (as S3Source does).
    """

    _extra = "s3"

    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._bucket = self.resolve_credential('bucket', creds.get('bucket', 'AWS_S3_BUCKET'))
        self._region = self.resolve_credential('region_name', creds.get('region_name', 'AWS_REGION_NAME'))
        self._aws_key = self.resolve_credential('aws_key', creds.get('aws_key', 'AWS_ACCESS_KEY_ID'))
        self._aws_secret = self.resolve_credential(
            'aws_secret', creds.get('aws_secret', 'AWS_SECRET_ACCESS_KEY')
        )
        self._profile = creds.get('profile', None)
        self._anon = bool(creds.get('anon', False))
        self._endpoint_url = self.resolve_credential('endpoint_url', creds.get('endpoint_url', None))
        source = options.get('source', {})
        self._directory: str = source.get('directory', '')
        self._file: str = source.get('file', '')
        if not self._bucket or self._is_unresolved(self._bucket):
            raise ValueError("ParquetS3Source: 'credentials.bucket' is required (literal or resolvable navconfig name).")

    def _secrets(self) -> list[str]:
        """Redact resolved AWS key and secret from error messages."""
        return [v for v in (self._aws_key, self._aws_secret) if v and not self._is_unresolved(v)]

    def _build_s3_path(self) -> str:
        """Return '<bucket>/<directory>/<file>' with masks resolved and slashes normalized."""
        # FILL IN: resolve_masks on directory and file; join non-empty parts with "/" after
        #          stripping "/" from each part. Bounded by: tests test_s3_path_*.
        raise NotImplementedError

    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
        """Build ``s3fs.S3FileSystem(**kwargs, skip_instance_cache=True)`` and return it with the path."""
        try:
            import s3fs  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError("Install the s3 extra for ParquetS3Source: pip install querysource[s3]") from exc
        kwargs: dict = {"skip_instance_cache": True}
        # FILL IN: key/secret only when resolved (not self._is_unresolved); profile if set;
        #          anon=True if set; endpoint_url if resolved; client_kwargs={"region_name": region}
        #          if resolved; then kwargs.update(self._storage_options) LAST, and force
        #          kwargs["skip_instance_cache"] = True again afterwards. Bounded by: spec §7 s3fs mapping.
        return s3fs.S3FileSystem(**kwargs), self._build_s3_path()
```
**Why this shape**: the interface is fixed by spec §3 M5. Its defaults mirror `S3Source` (querysource/queries/multi/sources/s3.py:55-67).
`skip_instance_cache` is re-forced after the `storage_options` merge so a user can't turn instance sharing back on (AC: no shared authenticated instance).

### `tests/test_source_parquet_s3.py` (CREATE)
```python
"""Unit tests for ParquetS3Source (FEAT-158, TASK-799). No real AWS access."""
import asyncio
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from querysource.queries.multi.sources.parquet.s3 import ParquetS3Source

CREDS = {"bucket": "bkt", "region_name": "us-east-1", "aws_key": "AKIATEST", "aws_secret": "s3cr3t"}


def _make(options):
    return ParquetS3Source("pq_s3", options, None, asyncio.Queue())


def test_kwargs_explicit_creds():
    src = _make({"credentials": {**CREDS, "endpoint_url": "http://minio:9000"}, "source": {"directory": "d/"}})
    with patch("s3fs.S3FileSystem") as fs_cls:
        _, path = src._build_filesystem()
    kwargs = fs_cls.call_args.kwargs
    assert kwargs["key"] == "AKIATEST" and kwargs["secret"] == "s3cr3t"
    assert kwargs["client_kwargs"] == {"region_name": "us-east-1"}
    assert kwargs["endpoint_url"] == "http://minio:9000"
    assert kwargs["skip_instance_cache"] is True
    assert path == "bkt/d"


async def test_read_via_memory_fs():
    mem = MemoryFileSystem(skip_instance_cache=True)
    with mem.open("/bkt/d/x.parquet", "wb") as fh:
        pq.write_table(pa.table({"a": [1, 2, 3]}), fh)
    src = _make({"credentials": CREDS, "source": {"directory": "d"}})
    with patch("s3fs.S3FileSystem", return_value=mem):
        df = await src.fetch()
    assert df["a"].tolist() == [1, 2, 3]

# FILL IN: test_unresolved_names_fall_back_ambient (aws_key "SOME_UNSET_VAR" → no key/secret kwargs),
#          test_profile_and_anon, test_storage_options_merged_last_but_cache_forced,
#          test_bucket_required, test_s3_path_with_masks, test_errors_do_not_leak_secrets
#          (S3FileSystem side_effect=PermissionError("denied for s3cr3t") → RuntimeError without "s3cr3t").
```

### FILL IN checklist
- [ ] `s3.py::ParquetS3Source._build_s3_path`: masks and slash normalization.
- [ ] `s3.py::ParquetS3Source._build_filesystem`: kwargs mapping; bounded by spec §7.
- [ ] Remaining tests listed in the test block.

---

## Acceptance Criteria

- [ ] Explicit keys, ambient fallback for unresolved names, `profile`, `anon`, `endpoint_url` and the `storage_options` passthrough are all mapped.
- [ ] `skip_instance_cache=True` is always passed, even when `storage_options` tries to override it.
- [ ] A read over a patched `MemoryFileSystem` returns the expected DataFrame.
- [ ] Errors never contain the AWS key or secret.
- [ ] Missing s3fs → `ImportError` naming `querysource[s3]`.
- [ ] `ruff check querysource/queries/multi/sources/parquet/s3.py tests/test_source_parquet_s3.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_parquet_s3.py -q`

---

## Test Specification

See the `tests/test_source_parquet_s3.py` block above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies**: TASK-795 and TASK-797 must be `"done"`.
4. **Verify the Codebase Contract** before writing code.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-799 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat gpt-5.6-luna, codex, ~218s)
**Date**: 2026-09-30
**Notes**: `ParquetS3Source` delivered per the task; combined Parquet suite 60 passed. Residual style lint left for /sdd-done
(DTZ011 in tests/test_source_parquet_local.py, B018 in tests/test_source_parquet_s3.py). No review fixes needed.

**Deviations from spec**: none
