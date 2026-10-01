---
type: feature
base_branch: dev
projects: [multiquery]
tags: [parquet, fsspec, pyarrow, s3, gcs, multiquery-sources]
---

# Feature Specification: Parquet Sources for MultiQS (local, S3, GCS over fsspec)

**Feature ID**: FEAT-158
**Date**: 2026-09-30
**Author**: Jesus Lara (with Claude Code)
**Status**: approved
**Target version**: 5.2.0

> Exploration trail: proposal `sdd/proposals/parquet-multiqs-source.proposal.md`
> (research state `sdd/state/FEAT-177/`, where FEAT-177 is the proposal-run number, not a ledger ID)
> and brainstorm `sdd/proposals/parquet-multiqs-source.brainstorm.md` (Option A).
> The ledger-reserved feature ID for this work is **FEAT-158**.

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQS can't read Parquet from any source. `S3Source` and `FileSource` handle only CSV
and Excel, and pyarrow is used only on the write side (the `ToS3` destination and the Redis
cache). Users building MultiQS pipelines need to pull Parquet files, directories and
hive-partitioned datasets from the local filesystem, AWS S3 and Google Cloud Storage into a
DataFrame. They also need column and row pushdown, so they don't pull whole datasets into memory.

The I/O layer is **fsspec**, for three reasons: new backends later (Azure `adlfs`, HTTP,
SFTP) through the same interface, asyncio-based I/O underneath (s3fs/gcsfs), and credential
flexibility (AWS profiles, anonymous access, S3-compatible endpoints, GCS token variants).

### Goals
- Three new MultiQS sources: `ParquetFileSource`, `ParquetS3Source` and `ParquetGCSSource`,
  sharing one Parquet base. They are registered in `SOURCE_REGISTRY` and dispatched from the YAML `sources:` list.
- Read through `pyarrow.dataset` over the fsspec filesystem, with **column projection**,
  **row-filter pushdown**, **directories / hive-partitioned datasets / globs**, and **masks in paths**.
- Auth: S3-compatible `endpoint_url`, a raw `storage_options` passthrough, AWS `profile` and `anon`,
  GCS `token` variants (SA JSON path or navconfig var, `google_default`, `anon`, dict). The GCS
  default is navconfig **`GOOGLE_CREDENTIALS_FILE`, which wins over `BIGQUERY_CREDENTIALS`**, then ADC.
- A **hard size limit** (`max_rows` / `max_bytes`) that refuses oversized reads instead of exhausting memory.
- Optional dependencies, one group per backend plus a bundle (`parquet`, `s3`, `gcs`, `parquet-all`).
  The AWS stack is upgraded so that s3fs resolves.

### Non-Goals (explicitly out of scope)
- Migrating `S3Source` onto s3fs as the single S3 client. That's a **follow-up feature**, decided in the brainstorm.
- Parquet support in `S3Source` / `FileSource`, which stay CSV/Excel only.
- Parquet writing (`ToS3` already covers it), streaming or chunked delivery, and Delta/Iceberg tables (the `deltatbl` provider covers those).
- Azure / HTTP / SFTP backends. The design allows them, but v1 doesn't ship them.
- A string filter grammar or a mapping shorthand. Filters are DNF lists only.
- Alternatives rejected in the brainstorm: pyarrow.fs native (Option B), polars `scan_parquet` (Option C;
  kept as a contingency if the AWS upgrade proves disruptive), and fully async byte fetch (Option D).

---

## 2. Architectural Design

### Overview

A new subpackage `querysource/queries/multi/sources/parquet/` holds:

- **`filters.py`**: validates the YAML DNF filter lists against an operator allowlist and
  converts them into a `pyarrow.compute.Expression`. Nothing is ever `eval`-ed or parsed from a string.
- **`base.py`**: `ParquetSource(ThreadSource)`, an abstract base that owns the whole read path.
  It parses common read options, resolves masks in the path, and asks the subclass for an fsspec
  filesystem plus a root path. It then builds
  `pyarrow.dataset.dataset(path, filesystem=fs, format="parquet", partitioning=...)`, enforces the hard
  size limit from dataset metadata **before** materializing, and calls `to_table(columns, filter)` →
  `to_pandas()`. All the blocking work runs in **`asyncio.to_thread`**, and fsspec's own IO loop
  provides concurrency underneath. An empty result raises `DataNotFound`.
- **`local.py`**: `ParquetFileSource`, over `fsspec.implementations.local.LocalFileSystem`.
- **`s3.py`**: `ParquetS3Source`, over `s3fs.S3FileSystem`. It uses an `S3Source`-style credentials
  block and falls back to ambient AWS auth when names are unresolved.
- **`gcs.py`**: `ParquetGCSSource`, over `gcsfs.GCSFileSystem`. The token is resolved in this order:
  explicit → `GOOGLE_CREDENTIALS_FILE` → `BIGQUERY_CREDENTIALS` → `google_default` (ADC).

### User-Facing Behavior (YAML)

```yaml
sources:
  - ParquetFileSource:
      source:
        path: /data/sales/{filedate}/*.parquet
      masks:
        "{filedate}": ["today", {"mask": "%Y%m%d"}]
      columns: [store_id, amount, country]
      filters:
        - [country, "==", "US"]
        - [amount, ">=", 100]
  - ParquetS3Source:
      credentials:
        bucket: AWS_PLACER_BUCKET
        region_name: AWS_REGION_NAME
        aws_key: AWS_ACCESS_KEY_ID
        aws_secret: AWS_SECRET_ACCESS_KEY
        # optional: profile, anon, endpoint_url
      source:
        directory: exports/metrics/
        file: "*.parquet"          # optional; omit to read the whole directory
      partitioning: hive
      storage_options: {default_block_size: 8388608}
      max_rows: 2000000
  - ParquetGCSSource:
      credentials:
        bucket: my-gcs-bucket
        project: BIGQUERY_PROJECT_ID
        token: GOOGLE_CREDENTIALS_FILE  # or path | google_default | anon | {dict}
      source:
        directory: warehouse/events/
      filters:                     # OR of ANDs
        - [[event, "==", "click"], [year, "==", 2026]]
        - [[event, "==", "view"]]
```

Each source puts one pandas DataFrame on the MultiQS queue under its auto-assigned name
(`ParquetS3Source`, `ParquetS3Source_1`, …).

### Component Diagram
```
MultiQS (sources: dispatch) ──→ SOURCE_REGISTRY ──→ ParquetFileSource / ParquetS3Source / ParquetGCSSource
                                                          │  _build_filesystem() -> (fsspec fs, root path)
                                                          ▼
                                                   ParquetSource.fetch()
                                                   ├─ build_filter_expression(filters)   [filters.py]
                                                   └─ asyncio.to_thread(_read)
                                                        ├─ pyarrow.dataset.dataset(path, filesystem=fs, ...)
                                                        ├─ size guard (count_rows(filter), fragment bytes)
                                                        └─ to_table(columns, filter).to_pandas()
                                                   ──→ queue.put({name: df})  (ThreadSource.run)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `ThreadSource` (`sources/base.py`) | extends | `fetch()` contract, `resolve_credential`, `resolve_masks`, `DataNotFound` → HTTP 204 |
| `SOURCE_REGISTRY` / `__all__` (`sources/__init__.py`) | modifies | 3 new entries |
| `MultiQS` `sources:` dispatch (`multi/__init__.py`) | uses (unchanged) | `cls(name, config, request, queue)` |
| `ComponentRegistry` (`multi/registry.py`) | uses (unchanged) | picks up `SOURCE_REGISTRY` automatically |
| `querysource/conf.py` | modifies | `MULTIQS_PARQUET_MAX_ROWS`, `MULTIQS_PARQUET_MAX_BYTES`; reads `GOOGLE_CREDENTIALS_FILE`, `BIGQUERY_CREDENTIALS` |
| `pyproject.toml` / `uv.lock` | modifies | extras `parquet`, `s3` (+s3fs, aioboto3>=15), `gcs`, `parquet-all`; AWS stack upgrade |
| `S3Source`, `ToS3` | depends on (regression) | must pass their existing tests after the aioboto3/aiobotocore/botocore upgrade |
| `generate-multiquery-docs` → `generated/` | extends | `ParquetFileSource.json`, `ParquetS3Source.json`, `ParquetGCSSource.json` |

### Data Models

Options are plain dicts, as in every other source; no new Pydantic model is introduced.
The normalized read options held by `ParquetSource`:

| Key | Type | Default | Notes |
|---|---|---|---|
| `columns` | `list[str] \| None` | `None` (all) | projection |
| `filters` | DNF `list` \| `None` | `None` | see §2 filter grammar |
| `partitioning` | `"hive" \| None` | `None` | `"hive"` → `pyarrow.dataset` hive partitioning |
| `recursive` | `bool` | `True` | directory discovery depth |
| `max_rows` | `int > 0` | `conf.MULTIQS_PARQUET_MAX_ROWS` (5 000 000) | hard limit |
| `max_bytes` | `int > 0` | `conf.MULTIQS_PARQUET_MAX_BYTES` (1 073 741 824) | hard limit on summed Parquet object sizes |
| `storage_options` | `dict` | `{}` | merged **last** into the fsspec constructor kwargs |

**Filter grammar (DNF lists only):**
- A flat list of `[col, op, value]` triples means AND.
- A list of lists of triples means OR of ANDs.
- Allowed `op`: `=`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `is null`, `is not null`.
  `in` / `not in` require a list value. `is null` / `is not null` take no value (a 2-element entry is also accepted).

### New Public Interfaces
See the §3 Interface Skeletons.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: dependencies & settings | no | extras fixed (§7); conf keys fixed | lockfile resolution + AWS regression triage needs judgment |
| M2: filters | yes | `build_filter_expression`, `ALLOWED_OPERATORS`, `ValueError` contract fixed | — |
| M3: ParquetSource base | yes | skeleton + read/size-guard flow fixed in §2 | — |
| M4: ParquetFileSource | yes | skeleton fixed | — |
| M5: ParquetS3Source | yes | kwargs mapping fixed in §7 | — |
| M6: ParquetGCSSource | yes | token precedence fixed | — |
| M7: registry, schemas, docs | yes | registry keys = class names; `generate-multiquery-docs -c Sources` | — |

### Module 1: Dependencies & settings
- **Path**: `pyproject.toml`, `uv.lock`, `querysource/conf.py`
- **Responsibility**: Declare the extras (exact values in §7 External Dependencies), including
  `s3 = ["aioboto3>=15", "s3fs>=2026.9"]`. Run `uv lock` to upgrade the AWS stack (aioboto3 → 15.x,
  aiobotocore → 2.25.x, botocore → 1.40.x), then `uv pip install -e .[parquet,s3,gcs]`. Add the
  two size-limit settings. Re-run the existing S3 tests (`tests/test_source_s3.py`,
  `tests/test_destination_s3.py`) and record the result.
- **Depends on**: —
- **Interface Skeleton**:
  ```python
  # querysource/conf.py  (modifies, after MULTIQS_SOURCE_TIMEOUT_SECONDS block — verified: querysource/conf.py:295-298)
  MULTIQS_PARQUET_MAX_ROWS: int = config.getint("MULTIQS_PARQUET_MAX_ROWS", fallback=5_000_000)
  MULTIQS_PARQUET_MAX_BYTES: int = config.getint("MULTIQS_PARQUET_MAX_BYTES", fallback=1_073_741_824)
  ```

### Module 2: DNF filter translation
- **Path**: `querysource/queries/multi/sources/parquet/filters.py`
- **Responsibility**: Validate and convert DNF filters into a pyarrow expression. This is pure, with no I/O.
- **Depends on**: — (pyarrow is already importable; lazy-import it inside the function)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/filters.py  (new)
  ALLOWED_OPERATORS: frozenset[str]  # {"=", "==", "!=", "<", "<=", ">", ">=", "in", "not in", "is null", "is not null"}

  def build_filter_expression(filters: list | None) -> "pyarrow.compute.Expression | None":
      """Convert a DNF filter list into a pyarrow compute expression.

      Args:
          filters: None, a flat list of [col, op, value] (AND), or a list of such
              lists (OR of ANDs).

      Returns:
          The combined expression, or None when ``filters`` is None or empty.

      Raises:
          ValueError: on an unknown operator, a malformed entry, a non-list value
              for ``in``/``not in``, or a value given to ``is null``/``is not null``.
      """
  ```

### Module 3: ParquetSource base
- **Path**: `querysource/queries/multi/sources/parquet/base.py`
- **Responsibility**: Common option parsing, masks, lazy imports, dataset construction, hard size guard,
  off-loop read, `DataNotFound` on empty, error wrapping without secrets.
- **Depends on**: M2 (`build_filter_expression`), M1 (conf keys)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/base.py  (new)
  class ParquetSource(ThreadSource):  # verified: querysource/queries/multi/sources/base.py:14
      """Abstract base for Parquet sources read through an fsspec filesystem."""

      def __init__(self, name: str, options: dict, request: web.Request,
                   queue: asyncio.Queue) -> None:
          """Parse common read options (columns, filters, partitioning, recursive,
          max_rows, max_bytes, storage_options). Calls super().__init__ first
          (pops ``masks``, verified: base.py:40-42). Raises ValueError on
          non-positive limits or a non-list ``columns``."""

      @abstractmethod
      def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
          """Return (filesystem, root path or glob) with masks already resolved."""

      def _read(self) -> pd.DataFrame:
          """Blocking: build the dataset, enforce limits, materialize. Runs in a worker thread.

          Raises:
              DataNotFound: no files matched, or the filtered result is empty.
              ValueError: unknown column in columns/filters, or the size limit is exceeded
                  (message names the limit and the observed rows/bytes).
          """

      async def fetch(self) -> pd.DataFrame:  # overrides verified: base.py:117
          """Lazy-import pyarrow/fsspec (ImportError names the extra), then
          ``await asyncio.to_thread(self._read)``. Backend/auth errors are re-raised as
          RuntimeError with the source name and without credential values."""
  ```

### Module 4: ParquetFileSource
- **Path**: `querysource/queries/multi/sources/parquet/local.py`
- **Responsibility**: Local file, directory or glob. `source.path` is required and masks are resolved.
- **Depends on**: M3
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/local.py  (new)
  class ParquetFileSource(ParquetSource):
      """Read Parquet from the local filesystem (file, directory or glob)."""
      def __init__(self, name: str, options: dict, request: web.Request,
                   queue: asyncio.Queue) -> None:
          """Requires options['source']['path'] (ValueError otherwise)."""
      def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
          """LocalFileSystem + absolute, mask-resolved path."""
  ```

### Module 5: ParquetS3Source
- **Path**: `querysource/queries/multi/sources/parquet/s3.py`
- **Responsibility**: The S3 / S3-compatible filesystem, with credentials resolved via `resolve_credential`.
- **Depends on**: M3, M1 (s3fs installed)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/s3.py  (new)
  class ParquetS3Source(ParquetSource):
      """Read Parquet from AWS S3 or an S3-compatible endpoint via s3fs."""
      def __init__(self, name: str, options: dict, request: web.Request,
                   queue: asyncio.Queue) -> None:
          """credentials: bucket (required), region_name, aws_key, aws_secret, profile,
          anon, endpoint_url; source: directory, file (glob allowed, optional).
          Credential resolution mirrors S3Source (verified: s3.py:55-70, 152-159)."""
      def _build_s3_path(self) -> str:
          """'<bucket>/<directory>/<file>' with masks resolved and slashes normalized."""
      def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
          """s3fs.S3FileSystem(**kwargs, skip_instance_cache=True) + path."""
  ```

### Module 6: ParquetGCSSource
- **Path**: `querysource/queries/multi/sources/parquet/gcs.py`
- **Responsibility**: The GCS filesystem, with token precedence explicit → `GOOGLE_CREDENTIALS_FILE` →
  `BIGQUERY_CREDENTIALS` → `google_default`.
- **Depends on**: M3, M1 (gcsfs installed)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/gcs.py  (new)
  class ParquetGCSSource(ParquetSource):
      """Read Parquet from Google Cloud Storage via gcsfs."""
      def __init__(self, name: str, options: dict, request: web.Request,
                   queue: asyncio.Queue) -> None:
          """credentials: bucket (required), project, token; source: directory, file."""
      def _resolve_token(self) -> str | dict:
          """Explicit token (navconfig-resolved) → conf.GOOGLE_CREDENTIALS_FILE if the file
          exists → conf.BIGQUERY_CREDENTIALS if the file exists → 'google_default'."""
      def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
          """gcsfs.GCSFileSystem(project=..., token=..., skip_instance_cache=True, **storage_options)."""
  ```

### Module 7: Registry, generated schemas & package exports
- **Path**: `querysource/queries/multi/sources/parquet/__init__.py`,
  `querysource/queries/multi/sources/__init__.py`, `generated/Parquet*Source.json`, `tests/test_source_registry.py`
- **Responsibility**: Export the three classes and register them under their class names. Regenerate the schemas with
  `generate-multiquery-docs -c Sources` and commit only the three new JSON files. Extend the registry tests.
- **Depends on**: M4, M5, M6
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/parquet/__init__.py  (new)
  from .base import ParquetSource
  from .local import ParquetFileSource
  from .s3 import ParquetS3Source
  from .gcs import ParquetGCSSource
  __all__ = ["ParquetSource", "ParquetFileSource", "ParquetS3Source", "ParquetGCSSource"]

  # querysource/queries/multi/sources/__init__.py  (modifies — verified: __init__.py:9, :19, :29-35)
  from .parquet import ParquetFileSource, ParquetGCSSource, ParquetS3Source
  SOURCE_REGISTRY: dict = {
      ...,
      "ParquetFileSource": ParquetFileSource,
      "ParquetS3Source": ParquetS3Source,
      "ParquetGCSSource": ParquetGCSSource,
  }
  ```
  Importing the subpackage must NOT import pyarrow, fsspec, s3fs or gcsfs at module import time; all of those imports are lazy.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_filters_flat_and` | M2 | flat list → AND expression |
| `test_filters_or_of_ands` | M2 | list of lists → OR expression |
| `test_filters_in_requires_list` | M2 | `in` with scalar → ValueError |
| `test_filters_unknown_operator` | M2 | `like` / `eval` → ValueError |
| `test_filters_is_null_forms` | M2 | 2- and 3-element `is null` handling |
| `test_filters_none_or_empty` | M2 | returns None |
| `test_base_rejects_nonpositive_limits` | M3 | `max_rows: 0` → ValueError |
| `test_local_single_file_roundtrip` | M4 | `tmp_path` parquet → identical DataFrame |
| `test_local_columns_projection` | M4 | only requested columns returned |
| `test_local_filters_pushdown` | M4 | filtered rows only |
| `test_local_hive_partitioned_dir` | M4 | partition column materialized, pruning works |
| `test_local_glob_and_masks` | M4 | `{filedate}` mask resolves, glob matches |
| `test_local_no_match_raises_datanotfound` | M4 | empty dir / glob → DataNotFound |
| `test_local_empty_filter_result_datanotfound` | M4 | filter matches nothing → DataNotFound |
| `test_local_unknown_column_valueerror` | M4 | bad column in columns/filters |
| `test_local_max_rows_exceeded` | M4 | `max_rows: 1` on 10 rows → ValueError naming limit, before `to_table` |
| `test_local_max_bytes_exceeded` | M4 | tiny `max_bytes` → ValueError |
| `test_missing_pyarrow_importerror` | M3 | patched import → ImportError naming `querysource[parquet]` |
| `test_s3_kwargs_explicit_creds` | M5 | key/secret/region/endpoint_url mapped; `skip_instance_cache=True` |
| `test_s3_unresolved_names_fall_back_ambient` | M5 | UPPER_SNAKE unresolved → no key/secret kwargs |
| `test_s3_profile_and_anon` | M5 | `profile` / `anon` forwarded |
| `test_s3_storage_options_merged_last` | M5 | passthrough overrides |
| `test_s3_read_via_memory_fs` | M5 | patch `s3fs.S3FileSystem` → fsspec `MemoryFileSystem` with parquet; end-to-end read |
| `test_gcs_token_precedence` | M6 | explicit > GOOGLE_CREDENTIALS_FILE > BIGQUERY_CREDENTIALS > google_default |
| `test_gcs_token_variants` | M6 | path, `anon`, `google_default`, dict |
| `test_gcs_read_via_memory_fs` | M6 | patched `gcsfs.GCSFileSystem` → MemoryFileSystem end-to-end |
| `test_errors_do_not_leak_secrets` | M5 | auth error message contains no aws_secret |
| `test_registry_contains_parquet_sources` | M7 | 3 keys in SOURCE_REGISTRY and `__all__` |
| `test_parquet_package_import_is_lazy` | M7 | importing sources doesn't import s3fs/gcsfs |

### Integration Tests
| Test | Description |
|---|---|
| `test_multiqs_dispatches_parquet_file_source` | A MultiQS definition with a `sources: [{ParquetFileSource: …}]` entry yields the DataFrame under name `ParquetFileSource` (pattern: `tests/test_multiqs_sources_integration.py`) |
| existing `tests/test_source_s3.py`, `tests/test_destination_s3.py` | Must still pass after the AWS stack upgrade |

### Test Data / Fixtures
```python
@pytest.fixture
def parquet_dir(tmp_path):
    """Writes a small DataFrame as a single file and as a hive-partitioned dataset (country=US/CA)."""
```
Tests live flat in `tests/` (like `tests/test_source_s3.py`), split per module: `tests/test_source_parquet_filters.py` (M2),
`tests/test_source_parquet_local.py` (M3+M4), `tests/test_source_parquet_s3.py` (M5), `tests/test_source_parquet_gcs.py` (M6). No real cloud access is used;
S3/GCS go through patched constructors returning `fsspec.implementations.memory.MemoryFileSystem`.

---

## 5. Acceptance Criteria

- [ ] `ParquetFileSource`, `ParquetS3Source` and `ParquetGCSSource` exist, subclass a shared `ParquetSource(ThreadSource)`, and are registered in `SOURCE_REGISTRY` and `__all__` under their class names.
- [ ] Reads go through fsspec filesystems + `pyarrow.dataset`, and the blocking read runs inside `asyncio.to_thread`.
- [ ] `columns`, DNF `filters` (flat = AND, nested = OR of ANDs), `partitioning: hive`, directories/globs and `masks` all work on the local backend (tests above).
- [ ] Filters accept only the allowlisted operators, and no string is ever evaluated or parsed as an expression.
- [ ] The hard limit is enforced **before materialization**: exceeding `max_rows` (filtered row count) or `max_bytes` (summed object sizes) raises `ValueError` naming the limit and the observed value. Defaults come from `conf.MULTIQS_PARQUET_MAX_ROWS` / `MULTIQS_PARQUET_MAX_BYTES`, with per-source overrides.
- [ ] No match or an empty filtered result raises `DataNotFound` (MultiQS "no data").
- [ ] S3 supports explicit keys, ambient fallback for unresolved names, `profile`, `anon`, `endpoint_url` and a `storage_options` passthrough. GCS supports token path/`google_default`/`anon`/dict, and its default precedence is `GOOGLE_CREDENTIALS_FILE` > `BIGQUERY_CREDENTIALS` > ADC.
- [ ] Filesystems are created with `skip_instance_cache=True`, so no authenticated instance is shared between sources.
- [ ] Error messages never contain credential values.
- [ ] `pyproject.toml` declares `parquet = ["pyarrow>=25", "fsspec>=2026.7"]`, `s3 = ["aioboto3>=15", "s3fs>=2026.9"]`, `gcs = ["gcsfs>=2026.8"]` and `parquet-all = ["querysource[parquet,s3,gcs]"]`. `uv.lock` is updated.
- [ ] Missing optional packages raise `ImportError` naming the extra to install. Importing `querysource.queries.multi.sources` never imports s3fs/gcsfs/pyarrow eagerly.
- [ ] Existing S3 tests still pass after the AWS stack upgrade: `pytest tests/test_source_s3.py tests/test_destination_s3.py -q`.
- [ ] `pytest tests/test_source_parquet_filters.py tests/test_source_parquet_local.py tests/test_source_parquet_s3.py tests/test_source_parquet_gcs.py tests/test_source_registry.py -q` passes.
- [ ] `generated/ParquetFileSource.json`, `generated/ParquetS3Source.json` and `generated/ParquetGCSSource.json` are committed, produced by `generate-multiquery-docs -c Sources`.
- [ ] `ruff check querysource/queries/multi/sources/parquet/ querysource/queries/multi/sources/__init__.py querysource/conf.py tests/test_source_parquet_*.py` is clean.
- [ ] No changes to `S3Source`, `FileSource` or `MultiQS` dispatch logic.

---

## 6. Codebase Contract

> Verified against base commit `4c685c1` (dev, 2026-09-30).

### Verified Imports
```python
from querysource.queries.multi.sources.base import ThreadSource   # querysource/queries/multi/sources/base.py:14
from querysource.exceptions import DataNotFound                   # querysource/exceptions.py:53 (base.py:11 imports it as ....exceptions)
from querysource import conf                                      # used as `from ... import conf` in multi/__init__.py:9
from querysource.queries.multi.sources import SOURCE_REGISTRY     # querysource/queries/multi/sources/__init__.py:29
# Third-party, verified in .venv (pyarrow 25.0.1, fsspec 2026.7.0):
import pyarrow.dataset as ds
import pyarrow.compute as pc
from pyarrow.fs import PyFileSystem, FSSpecHandler
import fsspec
# NOT yet installed (M1 adds them): s3fs, gcsfs
```

### Existing Class Signatures
```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                                   # line 14
    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:                              # line 25
        self._name; self._options; self._request                             # lines 33-37
        self._masks: dict = options.pop('masks', {}) ...                     # lines 40-42
        self.logger = logging.getLogger(__name__)                            # line 43
    def resolve_credential(self, key: str, value: str) -> str:               # line 45 (UPPER_SNAKE → navconfig)
    def resolve_masks(self, text: str) -> str:                               # line 72 (fnExecutor)
    @property
    def slug(self) -> str:                                                   # line 107
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                                   # line 117
    def run(self) -> None:                                                   # line 132; DataNotFound/NoDataFound → info + self.exc (150-154)

# querysource/queries/multi/sources/s3.py  (pattern reference, NOT modified)
_SIZE_WARNING_BYTES = 100 * 1024 * 1024                                      # line 21
class S3Source(ThreadSource):                                                # line 24
    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue):                                      # line 47; creds resolved 55-67
    def _build_s3_key(self) -> str:                                          # line 72
    async def fetch(self) -> pd.DataFrame:                                   # line 127; lazy import 135-141; unresolved-name guard 152-159

# querysource/queries/multi/sources/file.py  (pattern reference, NOT modified)
class FileSource(ThreadSource):                                              # line 21; masks resolved after super() 45-51

# querysource/exceptions.py
class DataNotFound(QueryException):                                          # line 53; default_code = 404

# querysource/conf.py
MULTIQS_SOURCE_TIMEOUT_SECONDS = config.getint("MULTIQS_SOURCE_TIMEOUT_SECONDS", fallback=30)  # lines 295-298
GOOGLE_CREDENTIALS_FILE = Path(config.get('GOOGLE_CREDENTIALS_FILE', fallback=BASE_DIR/'env/google/key.json'))  # lines 305-310
BIGQUERY_CREDENTIALS  # Path, default env/google/bigquery.json                                  # lines 194-202
BIGQUERY_PROJECT_ID = config.get('BIGQUERY_PROJECT_ID')                                        # line 203
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `Parquet*Source` | `MultiQS` sources dispatch | `SOURCE_REGISTRY.get(source_type)` → `cls(name, config, self._request, self._queue)` | `querysource/queries/multi/__init__.py:545`, `:556` |
| `Parquet*Source` | component catalog | `components.update(SOURCE_REGISTRY)` | `querysource/queries/multi/registry.py:148-153` |
| `ParquetSource.fetch` result | MultiQS queue | `ThreadSource.run` puts `{name: df}` | `querysource/queries/multi/sources/base.py:147-149` |
| `ParquetGCSSource._resolve_token` | settings | `conf.GOOGLE_CREDENTIALS_FILE`, `conf.BIGQUERY_CREDENTIALS` | `querysource/conf.py:305`, `:200` |
| schemas | `generate-multiquery-docs` | `-c Sources -o generated` | `querysource/cli/generate_docs.py:103-125`, `pyproject.toml:174` |

### Does NOT Exist (Anti-Hallucination)
- ~~Any Parquet-reading MultiQS source~~: none today.
- ~~`querysource.queries.multi.sources.parquet`~~: created by this feature.
- ~~A GCS client anywhere in `querysource/`~~: no `google.cloud.storage` / `gcsfs` import exists.
- ~~`s3fs`, `gcsfs`~~: not installed, not in `uv.lock` (M1 adds them).
- ~~`pyarrow` / `fsspec` as direct dependencies~~: transitive only today (deltalake/pygwalker/streamlit, autoviz/modin).
- ~~`conf.MULTIQS_PARQUET_MAX_ROWS` / `MULTIQS_PARQUET_MAX_BYTES`~~: created by M1.
- ~~`S3Source` masks support~~: `S3Source` never calls `resolve_masks`.
- ~~A `sources/*.catalog.yaml` for S3Source~~: only `query.catalog.yaml` exists in `sources/`.
- ~~`ThreadSource.options` public attribute~~: the stored options are the private `self._options`.

### Edit Sites (Blueprint Anchors)

Verified against: `4c685c1`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `pyproject.toml` | MODIFY | `s3 = [` / `"aioboto3>=12.0",` | `pyproject.toml:157-158` | 1 / 1 |
| `pyproject.toml` | MODIFY (insert extras before) | `dev = [` | `pyproject.toml:161` | 1 |
| `uv.lock` | MODIFY (regenerate via `uv lock`) | — | — | — |
| `querysource/conf.py` | MODIFY (insert after) | `MULTIQS_SOURCE_TIMEOUT_SECONDS = config.getint(` | `conf.py:295` | 1 |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | `from .table import TableSource` | `__init__.py:9` | 1 |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | `    "TableSource",` (in `__all__`) | `__init__.py:19` | 1 |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | `    "TableSource": TableSource,` | `__init__.py:34` | 1 |
| `tests/test_source_registry.py` | MODIFY | `from querysource.queries.multi.sources import (` | `test_source_registry.py:4` | 1 |
| `querysource/queries/multi/sources/parquet/__init__.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/parquet/filters.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/parquet/base.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/parquet/local.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/parquet/s3.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/parquet/gcs.py` | CREATE | — | — | — |
| `tests/test_source_parquet_filters.py`, `tests/test_source_parquet_local.py`, `tests/test_source_parquet_s3.py`, `tests/test_source_parquet_gcs.py` | CREATE | — | — | — |
| `generated/ParquetFileSource.json`, `generated/ParquetS3Source.json`, `generated/ParquetGCSSource.json` | CREATE (generated) | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- `ThreadSource` subclass contract: `fetch()` returns a DataFrame, and `run()` handles the queue and the event loop (`base.py:132-163`).
- Credential resolution and "explicit, else ambient" auth, exactly as `S3Source` does it (`s3.py:55-67`, `152-159`): treat values
  that are still unresolved UPPER_SNAKE names as absent.
- Masks resolved **after** `super().__init__` (which pops `masks`), as `FileSource` does (`file.py:45-51`).
- Lazy optional imports inside `fetch()`/helpers, with an `ImportError` naming the extra (`s3.py:135-141`):
  `querysource[parquet]`, `querysource[s3]`, `querysource[gcs]`.
- `self.logger`, Google docstrings, strict type hints, and no `print`.
- **s3fs kwargs mapping**: `aws_key`→`key`, `aws_secret`→`secret`, `profile`→`profile`, `anon`→`anon`,
  `region_name`→`client_kwargs["region_name"]`, `endpoint_url`→`endpoint_url`, then `storage_options` merged last,
  and always `skip_instance_cache=True`.
- **gcsfs kwargs**: `project`, `token` (from `_resolve_token`), then `storage_options` merged last, and always `skip_instance_cache=True`.
- Hand pyarrow the fsspec filesystem directly (pyarrow wraps it in `PyFileSystem(FSSpecHandler(fs))`); wrap it explicitly
  only if the direct pass is rejected by the installed pyarrow.
- Size guard: `dataset.count_rows(filter=expr)` for rows, and the summed sizes of the dataset's file fragments for bytes.
  Check both before `to_table`. `max_bytes` measures on-storage (compressed) size, which the docstring must say.

### Known Risks / Gotchas
- **AWS stack upgrade** (aioboto3 13.2 → ~15.5, aiobotocore 2.15 → ~2.25, botocore 1.35 → ~1.40) can affect `S3Source`,
  `ToS3` and `async-notify` (which also depends on aiobotocore). Mitigation: M1 runs their tests. If the upgrade proves
  disruptive, escalate. The recorded contingency is brainstorm Option C (polars `scan_parquet`), which needs no new dependencies.
- **fsspec instance cache** could share credentials between sources. Mitigation: `skip_instance_cache=True` everywhere.
- **`MULTIQS_SOURCE_TIMEOUT_SECONDS` defaults to 30 s** (`conf.py:295-298`). Large cloud scans can exceed it. Pushdown and
  the size limit mitigate this, and the source docstrings must mention the timeout.
- **`max_bytes` is compressed size**: a DataFrame in memory can be several times larger. `max_rows` is the primary guard.
- **Mixed schemas across files**: pyarrow raises. Let it propagate, wrapped with the source name. No schema unification in v1.
- **`count_rows` on non-partition filters** may scan Parquet statistics or pages. That's acceptable (it's cheaper than materializing).
- **Unknown column** in `columns`/`filters` → `ValueError` listing the dataset schema's column names.
- **Secrets in errors**: wrap backend exceptions and never interpolate the credential values.
- **Hive partitioning + `columns`**: partition keys are only returned when they are requested (or when `columns` is None).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pyarrow` | `>=25` | dataset read, pushdown, partitioning (extra `parquet`) |
| `fsspec` | `>=2026.7` | filesystem abstraction, local FS, globbing (extra `parquet`) |
| `s3fs` | `>=2026.9` | S3 / S3-compatible filesystem (extra `s3`) |
| `aioboto3` | `>=15` | existing `S3Source`/`ToS3`; raised so aiobotocore resolves alongside s3fs (extra `s3`) |
| `gcsfs` | `>=2026.8` | GCS filesystem (extra `gcs`) |
| — | — | `parquet-all = ["querysource[parquet,s3,gcs]"]` bundle |

Resolution probe (`uv pip compile`, py3.11, 2026-09-30): `aioboto3>=12 + s3fs + gcsfs` → aioboto3 15.5.0,
aiobotocore 2.25.1, botocore 1.40.61, s3fs 2026.9.0, gcsfs 2026.8.1, fsspec 2026.9.0.

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-158. The `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph**:
  - M3 → M2 (base imports `build_filter_expression`); M3 → M1 (reads the `conf.MULTIQS_PARQUET_*` keys).
  - M4 → M3; M5 → M3, M1 (needs s3fs installed); M6 → M3, M1 (needs gcsfs installed).
  - M7 → M4, M5, M6 (it imports and registers them).
  - M1 and M2 have no edge between them and can run concurrently. M4, M5 and M6 can run concurrently after M3.
- **Shared files**: none among M2–M6, because tests are split per module (`tests/test_source_parquet_{filters,local,s3,gcs}.py`).
  `querysource/queries/multi/sources/__init__.py` and `tests/test_source_registry.py` are touched only by M7.
- **Exclusive resources**: M1 (`uv.lock` regeneration + venv install) is `parallel: false`. M7 runs `generate-multiquery-docs`,
  which writes to `generated/`; commit only the three new files.
- **Cross-feature dependencies**: none must merge first. The concurrently developed FEAT-178 (OneDrive source) and
  multi-source-hooks work are likely to touch `querysource/queries/multi/sources/__init__.py` and possibly `pyproject.toml`/`uv.lock`,
  so expect merge conflicts in the registry and the lockfile.

---

## 8. Open Questions

- [x] I/O layer — *Resolved in brainstorm*: fsspec (s3fs/gcsfs) with pyarrow.dataset over the fsspec filesystem
- [x] Class layout — *Resolved in brainstorm*: keep 3 classes (ParquetFileSource / ParquetS3Source / ParquetGCSSource) on a shared base
- [x] New dependencies acceptable? — *Resolved in brainstorm*: only if there's no aioboto3 clash. Verified that it resolves.
- [x] Lock resolution — *Resolved in brainstorm*: upgrade the AWS stack (aioboto3 → 15.x) and re-test S3Source/ToS3/async-notify
- [x] Async level — *Resolved in brainstorm*: running off the loop via asyncio.to_thread is enough
- [x] Auth extras — *Resolved in brainstorm*: S3-compatible endpoints, storage_options passthrough, AWS profile/anon, GCS token variants
- [x] v1 read features — *Resolved in brainstorm*: column projection, row filters, dirs/partitioned datasets, masks in path
- [x] GCS default credential precedence — *Resolved in brainstorm*: `GOOGLE_CREDENTIALS_FILE` wins over `BIGQUERY_CREDENTIALS` (then ADC)
- [x] Memory guard — *Resolved in brainstorm*: a hard limit. Reading stops with an error when it's exceeded, rather than only logging a warning.
- [x] Consolidate `S3Source` onto s3fs as the single S3 client? — *Resolved in brainstorm*: yes, as a follow-up feature, not in v1
- [x] Extras layout — *Resolved in brainstorm*: `parquet = ["pyarrow>=25", "fsspec>=2026.7"]`, `s3 = ["aioboto3>=15", "s3fs>=2026.9"]`, `gcs = ["gcsfs>=2026.8"]`, `parquet-all = ["querysource[parquet,s3,gcs]"]`
- [x] Filter syntax in YAML — *Resolved in brainstorm*: DNF lists only (flat = AND, nested = OR of ANDs), allowlisted operators, no string grammar or mapping shorthand
- [x] GCS credentials source — *Resolved in proposal*: SA JSON via navconfig, fallback to ADC
- [x] Default hard limits: `MULTIQS_PARQUET_MAX_ROWS=5_000_000` and `MULTIQS_PARQUET_MAX_BYTES=1 GiB` are spec-author defaults. Confirm or adjust before M1. — *Owner: Jesus Lara*: Yes

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (brainstorm status is `exploration`, not `accepted`; §3b preconditions not met)
> · Transcript: —

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-30 | Jesus Lara / Claude Code | Initial draft from proposal (FEAT-177 run) + brainstorm Option A |
