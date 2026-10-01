# TASK-800: ParquetGCSSource (Google Cloud Storage via gcsfs)

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-795, TASK-797
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6. `ParquetGCSSource` reads Parquet from GCS through `gcsfs.GCSFileSystem`. The token is resolved in
this fixed order, decided in the brainstorm and proposal:
1. an explicit `credentials.token`: a service-account JSON path or navconfig name, `google_default`, `anon`, or a dict;
2. navconfig **`GOOGLE_CREDENTIALS_FILE`**, if that file exists;
3. **`BIGQUERY_CREDENTIALS`**, if that file exists;
4. `google_default` (ADC).

---

## Scope

- Implement `ParquetGCSSource` in `parquet/gcs.py`.
- Write `tests/test_source_parquet_gcs.py`, patching `gcsfs.GCSFileSystem`. No real GCS access.

**NOT in scope**: any other GCS client (there is none in querysource), and registry or exports (TASK-801).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/gcs.py` | CREATE | `ParquetGCSSource` |
| `tests/test_source_parquet_gcs.py` | CREATE | Token precedence and end-to-end read over MemoryFileSystem |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource import conf                                                 # conf.GOOGLE_CREDENTIALS_FILE, conf.BIGQUERY_CREDENTIALS
from querysource.queries.multi.sources.parquet.base import ParquetSource     # TASK-797
import gcsfs                                                                   # installed by TASK-795 — import lazily
```

### Existing Signatures to Use
```python
# querysource/conf.py:305-310
GOOGLE_CREDENTIALS_FILE = Path(config.get('GOOGLE_CREDENTIALS_FILE',
                                          fallback=BASE_DIR.joinpath('env', 'google', 'key.json')))
# querysource/conf.py:194-202
BIGQUERY_CREDENTIALS  # Path (resolved), default env/google/bigquery.json
# querysource/conf.py:203
BIGQUERY_PROJECT_ID = config.get('BIGQUERY_PROJECT_ID')

# querysource/queries/multi/sources/parquet/base.py (TASK-797)
class ParquetSource(ThreadSource):
    self._storage_options: dict
    @staticmethod
    def _is_unresolved(value: object) -> bool
    @abstractmethod
    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]
# querysource/queries/multi/sources/base.py:45 resolve_credential(key, value) -> str
```
gcsfs constructor kwargs used: `project`, `token` (a path str, `"google_default"`, `"anon"` or a dict), and `skip_instance_cache`.

### Does NOT Exist
- ~~`google.cloud.storage` usage in querysource~~: none. Don't add it, because gcsfs is the client.
- ~~`conf.GCS_CREDENTIALS` / `conf.GOOGLE_APPLICATION_CREDENTIALS`~~: not defined in conf.py. Use only the two settings above.
- ~~A GCS bucket default env name~~: none exists, so `credentials.bucket` is required.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/gcs.py", "action": "CREATE"},
    {"path": "tests/test_source_parquet_gcs.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.resolve_credential"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write `gcs.py` from the block — *why*: the interface and token precedence are fixed by the spec.
2. Complete `_resolve_token` — *why*: this is the resolved decision that `GOOGLE_CREDENTIALS_FILE` wins over `BIGQUERY_CREDENTIALS`.
3. Write the tests, monkeypatching `conf.GOOGLE_CREDENTIALS_FILE` and `conf.BIGQUERY_CREDENTIALS` to `tmp_path` files — *why*: this keeps precedence deterministic regardless of the machine's `env/`.

### `querysource/queries/multi/sources/parquet/gcs.py` (CREATE)
```python
"""ParquetGCSSource — Parquet from Google Cloud Storage via gcsfs (FEAT-158)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web

from querysource import conf
from .base import ParquetSource

if TYPE_CHECKING:
    import fsspec

_TOKEN_KEYWORDS = frozenset({"google_default", "anon", "cloud", "cache"})


class ParquetGCSSource(ParquetSource):
    """Read Parquet from Google Cloud Storage via gcsfs.

    Token precedence: explicit ``credentials.token`` → ``GOOGLE_CREDENTIALS_FILE``
    → ``BIGQUERY_CREDENTIALS`` → ``google_default`` (Application Default Credentials).
    """

    _extra = "gcs"

    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._bucket = self.resolve_credential('bucket', creds.get('bucket'))
        project = creds.get('project', None)
        self._project = self.resolve_credential('project', project) if project else None
        self._token = creds.get('token', None)
        source = options.get('source', {})
        self._directory: str = source.get('directory', '')
        self._file: str = source.get('file', '')
        if not self._bucket or self._is_unresolved(self._bucket):
            raise ValueError("ParquetGCSSource: 'credentials.bucket' is required.")

    def _resolve_token(self) -> str | dict:
        """Resolve the gcsfs token by the fixed precedence.

        Returns:
            A dict, a keyword from ``_TOKEN_KEYWORDS``, or a path to an existing SA JSON file.

        Raises:
            ValueError: an explicit token resolves to a path that does not exist.
        """
        # FILL IN: (1) explicit: dict → as-is; str in _TOKEN_KEYWORDS → as-is; other str →
        #          resolve_credential('token', ...); still unresolved → log a warning and continue;
        #          resolved → must be an existing file, else ValueError (message names the path, never contents).
        #          (2) Path(conf.GOOGLE_CREDENTIALS_FILE).is_file() → str(path).
        #          (3) Path(conf.BIGQUERY_CREDENTIALS).is_file() → str(path).
        #          (4) "google_default". Bounded by: spec §8 GCS precedence (resolved).
        raise NotImplementedError

    def _build_gcs_path(self) -> str:
        """Return '<bucket>/<directory>/<file>' with masks resolved and slashes normalized."""
        # FILL IN: same rule as ParquetS3Source._build_s3_path (resolve_masks, strip "/", join non-empty).
        raise NotImplementedError

    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
        """Build ``gcsfs.GCSFileSystem(project=..., token=..., skip_instance_cache=True, **storage_options)``."""
        try:
            import gcsfs  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError("Install the gcs extra for ParquetGCSSource: pip install querysource[gcs]") from exc
        kwargs: dict = {"token": self._resolve_token()}
        if self._project:
            kwargs["project"] = self._project
        kwargs.update(self._storage_options)
        kwargs["skip_instance_cache"] = True
        return gcsfs.GCSFileSystem(**kwargs), self._build_gcs_path()
```
**Why this shape**: the interface is fixed by spec §3 M6. `creds.get('bucket')` deliberately has no default, so introspection marks it
required. `skip_instance_cache` is forced after the passthrough merge (AC).

### `tests/test_source_parquet_gcs.py` (CREATE)
```python
"""Unit tests for ParquetGCSSource (FEAT-158, TASK-800). No real GCS access."""
import asyncio
from unittest.mock import patch

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

# FILL IN: test_explicit_token_wins (explicit tmp path), test_bigquery_fallback (delete google file),
#          test_google_default_fallback (delete both), test_token_keywords (anon, google_default),
#          test_token_dict, test_explicit_missing_path_valueerror, test_bucket_required,
#          test_skip_instance_cache_forced, test_read_via_memory_fs (patch "gcsfs.GCSFileSystem",
#          return_value=MemoryFileSystem with /b/d/x.parquet; source.directory "d").
```

### FILL IN checklist
- [ ] `gcs.py::ParquetGCSSource._resolve_token`: precedence; bounded by spec §8.
- [ ] `gcs.py::ParquetGCSSource._build_gcs_path`: masks and slash normalization.
- [ ] Remaining tests listed in the test block.

---

## Acceptance Criteria

- [ ] Token precedence: explicit > `GOOGLE_CREDENTIALS_FILE` > `BIGQUERY_CREDENTIALS` > `google_default`.
- [ ] Token variants: path, `google_default`, `anon`, dict.
- [ ] `skip_instance_cache=True` is always passed, and `storage_options` is merged.
- [ ] A read over a patched `MemoryFileSystem` returns the expected DataFrame.
- [ ] Missing gcsfs → `ImportError` naming `querysource[gcs]`.
- [ ] `ruff check querysource/queries/multi/sources/parquet/gcs.py tests/test_source_parquet_gcs.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_parquet_gcs.py -q`

---

## Test Specification

See the `tests/test_source_parquet_gcs.py` block above.

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
9. **Close**: `scripts/sdd/close_task.sh TASK-800 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat sonnet (native), 1 attempt)
**Date**: 2026-09-30
**Notes**: `ParquetGCSSource` delivered per the task; combined Parquet suite 60 passed. Residual style lint left for /sdd-done
(DTZ011 in tests/test_source_parquet_local.py, B018 in tests/test_source_parquet_s3.py). No review fixes needed.

**Deviations from spec**: none
