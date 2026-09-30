---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [multiquery]
tags: [parquet, fsspec, pyarrow, s3, gcs, multiquery-sources]
---

# Brainstorm: Parquet Sources for MultiQS over fsspec (local, S3, GCS)

**Date**: 2026-09-30
**Author**: Jesus Lara (with Claude Code)
**Status**: exploration
**Recommended Option**: A
**Supersedes (I/O layer only)**: FEAT-177 proposal, `sdd/proposals/parquet-multiqs-source.proposal.md` §5 U1 (pyarrow.fs). All other proposal decisions stand.

---

## Problem Statement

MultiQS can't read Parquet from any source. `S3Source` and `FileSource` handle only
CSV and Excel, and pyarrow is used only for writing (the `ToS3` destination and the
Redis cache). Users building MultiQS pipelines need to pull Parquet files, directories
and hive-partitioned datasets from the local filesystem, AWS S3 and Google Cloud Storage
into a DataFrame. They also want column and row pushdown, so they don't pull whole
datasets into memory.

The FEAT-177 proposal picked `pyarrow.fs` as the I/O layer. This brainstorm reconsiders
that choice in favour of **fsspec**, for three reasons the user stated:
1. **More backends later.** Azure (`adlfs`), HTTP, SFTP and others would use the same URL/filesystem interface.
2. **Async-native I/O underneath.** `s3fs` and `gcsfs` are built on asyncio (aiobotocore / aiohttp).
3. **Credential flexibility.** fsspec supports AWS named profiles, anonymous access, S3-compatible endpoints and GCS token variants.

## Constraints & Requirements

- **Keep 3 classes**: `ParquetFileSource`, `ParquetS3Source` and `ParquetGCSSource`,
  sharing one Parquet base. Each is registered in `SOURCE_REGISTRY` and dispatched from the YAML `sources:` list.
- **Engine**: `pyarrow.dataset` reading *through* the fsspec filesystem. This keeps column
  projection, row-filter pushdown and hive partitioning.
- **Async level**: running the read off the event loop with `asyncio.to_thread` is enough.
  fsspec's own IO loop provides concurrency underneath, and full async byte fetching isn't required.
- **v1 features**: column projection, row filters, directories and partitioned datasets, masks in paths.
- **Auth features in scope**: S3-compatible `endpoint_url` (MinIO, R2, Wasabi), a raw `storage_options`
  passthrough, AWS profile and anonymous access, and GCS token variants (SA JSON path, `google_default`,
  `anon`, credentials dict). The GCS default comes from navconfig, with ADC as the fallback.
- **Dependencies**: add `s3fs`/`gcsfs` only if the lockfile resolves without clashing with aioboto3.
  Verified: it resolves. The user chose to **upgrade the AWS stack** (aioboto3 13.2 → ~15.x,
  aiobotocore 2.15 → ~2.25, botocore 1.35 → ~1.40). `S3Source`, `ToS3` and `async-notify` must be re-tested.
- Repo conventions: async-first, navconfig for secrets, `self.logger`, Google docstrings,
  lazy optional imports that raise an actionable `ImportError`, and no `eval` of user-provided filter strings.

---

## Options Explored

### Option A: fsspec filesystems + pyarrow.dataset (pushdown)

Each subclass builds an fsspec filesystem from its config: `LocalFileSystem`,
`s3fs.S3FileSystem` or `gcsfs.GCSFileSystem`. The shared base resolves masks, expands
globs or directories with the fsspec API, and hands the filesystem to
`pyarrow.dataset.dataset(..., filesystem=<fsspec fs>, format="parquet", partitioning=...)`.
pyarrow wraps a non-native filesystem in `PyFileSystem(FSSpecHandler(fs))`. The base then
calls `to_table(columns=..., filter=...)` inside `asyncio.to_thread` and converts the
result to pandas. Credentials map one-to-one onto s3fs/gcsfs constructor kwargs, and a
`storage_options` dict is merged last.

✅ **Pros:**
- Delivers all three stated drivers. A new backend (Azure `adlfs`, HTTP, SFTP) is one more thin subclass.
- Keeps pyarrow's pushdown (columns and predicates, and row-group skipping via Parquet statistics) and hive partitioning.
- s3fs/gcsfs cover the full auth set: `profile`, `anon`, `endpoint_url` / `client_kwargs`, and GCS `token` variants.
- `storage_options` passthrough is native, because it's just filesystem kwargs.
- It's the same filesystem layer pandas uses for `storage_options`, which makes it familiar to flowtask/pandas users.

❌ **Cons:**
- Adds `s3fs` and `gcsfs`, and the lockfile has to upgrade the AWS stack (aioboto3/aiobotocore/botocore). That's a regression risk for `S3Source`, `ToS3` and `async-notify`.
- The fsspec → pyarrow bridge is somewhat slower than pyarrow's native C++ filesystems for very large scans, because reads go through Python file handles.
- fsspec caches filesystem instances. Credentials must be passed per instance (or with `skip_instance_cache=True`) so different sources never share an authenticated instance.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `fsspec` | Filesystem abstraction, glob/find, local FS | 2026.x, already transitive (autoviz/modin); declare it |
| `s3fs` | S3 / S3-compatible filesystem (aiobotocore) | 2026.9.0 resolves with aioboto3 15.5.0 / aiobotocore 2.25.1 |
| `gcsfs` | GCS filesystem (aiohttp + google-auth) | 2026.8.1 resolved |
| `pyarrow` | `pyarrow.dataset` / `pyarrow.parquet` read, pushdown, partitioning | 25.0.1 installed (transitive via deltalake); declare it |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/sources/base.py`: `ThreadSource` (fetch contract, `resolve_credential`, `resolve_masks`, `DataNotFound` handling)
- `querysource/queries/multi/sources/s3.py`: the credentials block shape and the "explicit, else ambient" auth logic
- `querysource/queries/multi/sources/file.py`: masks resolved after `super().__init__`
- `querysource/conf.py`: `GOOGLE_CREDENTIALS_FILE`, `BIGQUERY_CREDENTIALS` as GCS SA defaults

---

### Option B: pyarrow.fs native filesystems (original proposal choice)

Use pyarrow's C++ filesystems (`LocalFileSystem`, `S3FileSystem`, `GcsFileSystem`) with
`pyarrow.dataset` directly.

✅ **Pros:**
- No new dependencies. Only `pyarrow` needs declaring, and the AWS stack is left alone.
- Fastest scans (native C++ I/O, parallel range reads).
- `GcsFileSystem` and `S3FileSystem` were verified present in the local pyarrow 25.0.1.

❌ **Cons:**
- Misses the user's drivers. There's no AWS named-profile support in `S3FileSystem`, fewer GCS token forms, and adding a backend depends on what pyarrow ships.
- A `storage_options` passthrough would need translating to pyarrow's own kwarg names.
- The I/O isn't asyncio-based at all.

📊 **Effort:** Low–Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `pyarrow` | fs + dataset + parquet | 25.0.1 |

🔗 **Existing Code to Reuse:**
- Same as Option A.

---

### Option C: polars `scan_parquet` with cloud `storage_options` (unconventional)

Use `polars.scan_parquet(url, storage_options=..., hive_partitioning=True)`, which relies on
Rust `object_store` for s3://, gs:// and az:// URLs. The lazy query applies
`.select(columns).filter(expr)`, then `.collect().to_pandas()`.

✅ **Pros:**
- Polars is **already a direct dependency** (`polars==1.27.1`), so there are no new packages and the AWS stack is untouched.
- Lazy engine with projection and predicate pushdown, very fast, and supports S3, GCS and Azure natively.

❌ **Cons:**
- `object_store` credential keys differ from fsspec/boto (`aws_access_key_id`, `google_service_account`, …), which is a second auth vocabulary in the repo.
- Filters would be Polars expressions. Building them safely from YAML means writing a translator.
- Tied to the pinned `polars==1.27.1` cloud feature set. `.to_pandas()` still needs pyarrow.
- Doesn't meet the "async-native" or "fsspec ecosystem" drivers.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `polars` | Lazy parquet scan over object_store | 1.27.1, direct dependency |
| `pyarrow` | `to_pandas()` conversion | 25.0.1 |

🔗 **Existing Code to Reuse:**
- Same `ThreadSource` base; no other polars-based MultiQS source exists.

---

### Option D: Fully async fsspec byte fetch + in-memory parse

Use s3fs/gcsfs's async API (`_find`, `_cat_file`) to download whole objects concurrently,
then parse each one with `pd.read_parquet(BytesIO(...))` and concatenate. This is the
same whole-object model `S3Source` uses today.

✅ **Pros:**
- Truly async I/O with no sync calls, and it mirrors `S3Source` closely.

❌ **Cons:**
- No pushdown: whole files are downloaded and held in memory, and partition pruning has to be hand-written.
- The user explicitly said running the read off the event loop is enough.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `s3fs` / `gcsfs` | async byte fetch | as Option A |
| `pandas` | parse | installed |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/sources/s3.py`: `S3Source.fetch` whole-object download pattern

---

## Recommendation

**Option A** is recommended. It's the only option that meets all three stated drivers:
new backends through the fsspec ecosystem, asyncio-based I/O underneath, and the full
credential matrix (profile, anon, endpoint_url, GCS token variants, `storage_options`).
It still keeps the pyarrow.dataset pushdown and hive partitioning that the v1 feature
list needs.

What we trade away:
- Some raw scan throughput compared with Option B's native C++ filesystems. This is
  acceptable because every source ends in a single in-memory pandas DataFrame, and
  pushdown matters far more than transfer speed at that size.
- A **lockfile upgrade of the AWS stack**. The user accepted it, and it's contained by
  making the dependency change its own task, with `S3Source`, `ToS3` and `async-notify` re-tested.

Option C is a good zero-new-dependency fallback if the AWS upgrade proves disruptive. Record it in the spec as the contingency.

---

## Feature Description

### User-Facing Behavior

A MultiQS definition lists one or more Parquet sources under `sources:`:

- **`ParquetFileSource`**: `source.path` is a local file, a directory, or a glob
  (for example `/data/sales/{filedate}/*.parquet`), with `masks` resolved as in `FileSource`.
- **`ParquetS3Source`**: an `S3Source`-style `credentials` block (`bucket`, `region_name`,
  `aws_key`, `aws_secret`, each a navconfig variable or a literal), plus optional `profile`,
  `anon`, `endpoint_url` and `storage_options`. `source` takes `directory` and `file`, where the file can be a glob.
- **`ParquetGCSSource`**: `credentials.bucket`, `credentials.project`, and a `credentials.token`
  that can be an SA JSON path or navconfig variable, `google_default`, `anon` or a dict. It defaults to
  navconfig `GOOGLE_CREDENTIALS_FILE`, which wins over `BIGQUERY_CREDENTIALS`, then ADC. It also accepts
  `storage_options`, and `source.directory`/`file` as for S3.
- **Common read options**: `columns: [...]`, `filters: [[col, op, value], ...]` (DNF, with
  operators from an allowlist), `partitioning: hive | null`, optional `recursive`, and
  `max_rows` / `max_bytes` to override the hard size limit.

Each source puts one pandas DataFrame on the MultiQS queue under its auto-assigned name
(`ParquetS3Source`, `ParquetS3Source_1`, …), ready for transformations and outputs.

### Internal Behavior

1. `__init__` (per subclass): parses credentials with `resolve_credential`, resolves masks in
   the path after `super().__init__`, and stores the read options.
2. The base `fetch()` imports pyarrow and the backend package lazily. If one is missing it
   raises `ImportError` naming the extra.
3. The subclass builds its fsspec filesystem with per-instance credentials merged with
   `storage_options`, bypassing the fsspec instance cache when credentials are explicit.
4. The base, inside `asyncio.to_thread`, expands the path or glob, builds
   `pyarrow.dataset.dataset(...)` over the fsspec filesystem, converts the validated `filters`
   into a `pyarrow.compute` expression, and calls `to_table(columns, filter)` then `to_pandas()`.
5. An empty result raises `DataNotFound`, which `ThreadSource.run` turns into MultiQS "no data" (HTTP 204).
   A **hard limit** (configurable `max_rows` / `max_bytes`, with a conservative default) is enforced. Before the read,
   dataset metadata (`count_rows()` with the filter applied, and file sizes) is checked. If the limit is exceeded, the read is refused
   with a `ValueError` that names the limit and the observed size, so the source fails instead of exhausting memory.

### Edge Cases & Error Handling

- **No files match the path or glob**: `DataNotFound` with the resolved path in the message.
- **Unknown column in `columns`/`filters`**: `ValueError` naming the column and the available schema.
- **Filter operator not in the allowlist** (`=`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`,
  `is null`, `is not null`): `ValueError`. Filters are never `eval`-ed.
- **Mixed or incompatible schemas across files**: pyarrow raises. Let it propagate with the
  source name (optionally add a `schema_unify` flag later).
- **Auth failure or missing bucket**: re-raise as `RuntimeError`, without echoing secrets in the message.
- **Unresolved navconfig variable names** (UPPER_SNAKE left as-is): treat as absent and fall back to ambient auth, as `S3Source` does.
- **fsspec instance cache**: never reuse an authenticated instance across sources with different credentials.
- **Size limit exceeded**: `ValueError` before the table is materialized. The limit is checked against filtered row counts and object sizes.

---

## Capabilities

### New Capabilities
- `multiquery-parquet-sources`: `ParquetFileSource`, `ParquetS3Source` and `ParquetGCSSource` over fsspec + pyarrow.dataset.
- `parquet-read-options`: validated `columns`/`filters`/`partitioning`, and conversion of filters to pyarrow expressions.

### Modified Capabilities
- `multiquery-new-sources` (`sdd/specs/multiquery-new-sources.spec.md`): the `SOURCE_REGISTRY` gains three entries.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/queries/multi/sources/` (new `parquet.py`) | extends | New base + 3 subclasses |
| `querysource/queries/multi/sources/__init__.py` | modifies | `SOURCE_REGISTRY` + `__all__` |
| `pyproject.toml` / `uv.lock` | modifies | New extras (e.g. `parquet` = pyarrow+fsspec, `s3` += s3fs, `gcs` = gcsfs). Upgrades aioboto3/aiobotocore/botocore |
| `S3Source`, `ToS3`, `async-notify` | depends on | Must be re-tested after the AWS stack upgrade. Migrating `S3Source` to s3fs is a follow-up feature, not in scope here |
| `generated/*.json` | extends | `ParquetFileSource.json`, `ParquetS3Source.json`, `ParquetGCSSource.json` via `generate-multiquery-docs` |
| `tests/` | extends | `test_source_parquet.py`; `test_source_registry.py` updated |

No breaking changes to existing YAML definitions.

---

## Code Context

### User-Provided Code
None. The request was inline text only.

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/queries/multi/sources/base.py:14
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:  # line 25; pops options['masks'] (40-42)
    def resolve_credential(self, key: str, value: str) -> str:  # line 45
    def resolve_masks(self, text: str) -> str:  # line 72
    @property
    def slug(self) -> str:  # line 107
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:  # line 117
    def run(self) -> None:  # line 132; DataNotFound/NoDataFound -> info + self.exc (150-154)

# From querysource/queries/multi/sources/s3.py:24
class S3Source(ThreadSource):
    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue):  # line 47
    def _build_s3_key(self) -> str:  # line 72
    def _parse_content(self, content: bytes, filename: str) -> pd.DataFrame:  # line 78
    async def fetch(self) -> pd.DataFrame:  # line 127; lazy `import aioboto3` (135-141)
    # explicit creds only if not unresolved UPPER_SNAKE names (lines 152-159)

# From querysource/queries/multi/sources/file.py:21
class FileSource(ThreadSource):
    def __init__(self, name: str, file_options: dict, request: web.Request,
                 queue: asyncio.Queue):  # line 34; masks resolved after super() (45-51)
    async def fetch(self) -> pd.DataFrame:  # line 69; csv/excel only, ValueError otherwise (101-102)

# From querysource/queries/multi/__init__.py:541-557 (MultiQS dispatch)
#   for entry in self._sources: for source_type, config in entry.items():
#       cls = SOURCE_REGISTRY.get(source_type)  -> DriverError if None
#       name = source_type | f"{source_type}_{idx}"
#       t = cls(name, config, self._request, self._queue)
```

#### Verified Imports
```python
from querysource.queries.multi.sources import SOURCE_REGISTRY, ThreadSource, S3Source, FileSource  # sources/__init__.py:1-35
from querysource.queries.multi.sources.file import excel_based  # file.py:12
from querysource.exceptions import DataNotFound  # imported in base.py:11 as ....exceptions
from pyarrow.fs import PyFileSystem, FSSpecHandler  # verified in .venv (pyarrow 25.0.1)
import pyarrow.dataset as ds                          # verified in .venv
import fsspec                                         # verified in .venv (2026.7.0, transitive)
```

#### Key Attributes & Constants
- `SOURCE_REGISTRY` → `dict` of `{type_name: class}` (querysource/queries/multi/sources/__init__.py:29-35)
- `_SIZE_WARNING_BYTES = 100 * 1024 * 1024` (querysource/queries/multi/sources/s3.py:21)
- `conf.GOOGLE_CREDENTIALS_FILE` → `Path`, default `env/google/key.json` (querysource/conf.py:305-310)
- `conf.BIGQUERY_CREDENTIALS` → `Path`, default `env/google/bigquery.json` (querysource/conf.py:194-202)
- `conf.BIGQUERY_PROJECT_ID` (querysource/conf.py:203)
- `[project.optional-dependencies].s3 = ["aioboto3>=12.0"]` (pyproject.toml:157-159)
- `generate-multiquery-docs = "querysource.cli.generate_docs:main"` (pyproject.toml:174)
- Lock today: aioboto3 13.2.0, aiobotocore 2.15.2, botocore 1.35.36, fsspec 2026.7.0, pyarrow 25.0.1, polars 1.27.1 (uv.lock)
- Resolution probe (`uv pip compile`, py3.11): `aioboto3>=12 + s3fs + gcsfs` → aioboto3 15.5.0, aiobotocore 2.25.1, botocore 1.40.61, s3fs 2026.9.0, gcsfs 2026.8.1, fsspec 2026.9.0. Holding aioboto3==13.2.0 → s3fs/fsspec 2026.1.0.

### Does NOT Exist (Anti-Hallucination)
- ~~Any Parquet-reading MultiQS source~~: none. `S3Source`/`FileSource` are CSV/Excel only.
- ~~A GCS client in `querysource/`~~: no `google.cloud.storage` or `gcsfs` import anywhere.
- ~~`s3fs`, `gcsfs`~~: not installed and not in `uv.lock`.
- ~~`pyarrow`, `fsspec` as direct dependencies~~: transitive only (deltalake/pygwalker/streamlit, autoviz/modin).
- ~~A `sources/*.catalog.yaml` for S3Source~~: only `query.catalog.yaml` exists in `sources/`.
- ~~`S3Source` masks support~~: `S3Source` does not call `resolve_masks`.
- ~~A `profile` kwarg on `pyarrow.fs.S3FileSystem`~~: not supported (relevant only to Option B).

---

## Parallelism Assessment

- **Internal parallelism**: low. The dependency/lock upgrade task must land first,
  because it gates imports and re-tests the AWS stack. After it, the three subclasses could
  proceed in parallel, but they share the new base module and `parquet.py`.
- **Cross-feature independence**: `pyproject.toml`/`uv.lock` are hot files. Any in-flight
  feature touching dependencies will conflict. `sources/__init__.py` is shared with any
  other new-source work. Nothing else in `sources/` is in flight (recent commits: 76b8851, a4536ff).
- **Recommended isolation**: `per-spec`
- **Rationale**: this is a small feature with a strict order (deps → base → subclasses →
  registry/schemas/tests) and one shared module, so one worktree is simpler than merging parallel branches.

---

## Open Questions

- [x] I/O layer — *Owner: Jesus Lara*: fsspec (s3fs/gcsfs) with pyarrow.dataset over the fsspec filesystem
- [x] Class layout — *Owner: Jesus Lara*: keep 3 classes (ParquetFileSource / ParquetS3Source / ParquetGCSSource) on a shared base
- [x] New dependencies acceptable? — *Owner: Jesus Lara*: only if no aioboto3 clash. Verified that it resolves.
- [x] Lock resolution — *Owner: Jesus Lara*: upgrade the AWS stack (aioboto3 → 15.x) and re-test S3Source/ToS3/async-notify
- [x] Async level — *Owner: Jesus Lara*: running off the loop via asyncio.to_thread is enough
- [x] Auth extras — *Owner: Jesus Lara*: S3-compatible endpoints, storage_options passthrough, AWS profile/anon, GCS token variants
- [x] v1 read features — *Owner: Jesus Lara*: column projection, row filters, dirs/partitioned datasets, masks in path
- [ ] Extras layout: one `parquet` extra (pyarrow+fsspec) plus `s3` += s3fs and a new `gcs` = gcsfs, or a single `parquet-cloud` extra? — *Owner: tbd*
- [x] GCS default credential precedence — *Owner: Jesus Lara*: `GOOGLE_CREDENTIALS_FILE` wins over `BIGQUERY_CREDENTIALS` (then ADC)
- [ ] Filter syntax in YAML: DNF lists only, or also a restricted string grammar? — *Owner: tbd*
- [x] Memory guard — *Owner: Jesus Lara*: a hard limit. Reading stops with an error when it's exceeded, rather than only logging a warning.
- [x] Consolidate `S3Source` onto s3fs as the single S3 client? — *Owner: Jesus Lara*: yes, as a **follow-up feature**, not in FEAT-177 v1
