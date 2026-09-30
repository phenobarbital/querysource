---
id: FEAT-177
title: Parquet sources for MultiQS (local filesystem, S3, GCS) modelled on S3Source
slug: parquet-multiqs-source
type: feature
mode: enrichment
status: review
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-09-30
  summary_oneline: Parquet-based MultiQS source reading from local filesystem, S3 or GCS, modelled on the existing S3 source
overall_confidence: medium
base_branch: dev
projects: [multiquery]
tags: [parquet, pyarrow, s3, gcs, multiquery-sources]
research_state: sdd/state/FEAT-177/
created: 2026-09-30
updated: 2026-09-30
---

# FEAT-177 — Parquet sources for MultiQS (local filesystem, S3, GCS)

> **Mode**: enrichment
> **Confidence**: medium
> **Source**: `inline`
> **Audit**: [`sdd/state/FEAT-177/`](../state/FEAT-177/)

---

## 0. Origin

> parquet-multiqs-source -- Using the S3 source as example for creating a Parquet
> based source for MultiQS, extracting the Parquet file from local filesystem or
> S3 or GCS

**Initial signals** (extracted, not interpreted):
- Verbs: "creating", "extracting" → new capability (enrichment)
- Named entities: S3 source, Parquet, MultiQS, local filesystem, S3, GCS
- Components / labels: none (inline source)
- Acceptance criteria provided: no

---

## 1. Synthesis Summary

MultiQS cannot read Parquet today. `S3Source` handles only CSV, CSV.GZ and Excel,
`FileSource` handles only CSV and Excel, and pyarrow is used only on the write side
(the `ToS3` destination and the Redis cache). The request adds Parquet ingestion from
three backends. They follow the established pattern: a `ThreadSource` subclass that
implements `async fetch() -> DataFrame` and is registered in `SOURCE_REGISTRY` for
YAML `sources:` dispatch by `MultiQS`. The agreed design uses three separate classes,
`ParquetFileSource`, `ParquetS3Source` and `ParquetGCSSource`. They share a Parquet
base that reads through `pyarrow.fs` filesystems plus `pyarrow.parquet`/`pyarrow.dataset`
inside `asyncio.to_thread`. Version 1 supports column projection, row filters,
directories/partitioned datasets, and masks in paths. There is no GCS client code in
querysource yet, and pyarrow and google-cloud-storage are only transitive dependencies,
so both must be declared.

---

## 2. Codebase Findings

> All entries are grounded in `sdd/state/FEAT-177/findings/`.

### 2.1 Localization

| # | Path | Symbol | Lines | Role | Evidence |
|---|------|--------|-------|------|----------|
| 1 | `querysource/queries/multi/sources/s3.py` | `S3Source` | 24-165 | Reference implementation: credential resolution, lazy optional import, S3 key building | F002 |
| 2 | `querysource/queries/multi/sources/base.py` | `ThreadSource` | 14-163 | Base contract: abstract `fetch()`, `resolve_credential`, `resolve_masks`, per-thread event loop, `DataNotFound` handling | F003 |
| 3 | `querysource/queries/multi/sources/file.py` | `FileSource` | 21-105 | Local-file precedent: masks resolution in path, no Parquet | F004 |
| 4 | `querysource/queries/multi/sources/__init__.py` | `SOURCE_REGISTRY` | 1-35 | Registration point and `__all__` | F005 |
| 5 | `querysource/queries/multi/__init__.py` | `MultiQS` (thread dispatch) | 535-557 | `sources:` list → `SOURCE_REGISTRY` lookup → `cls(name, config, request, queue)` | F005 |
| 6 | `querysource/queries/multi/registry.py` | `ComponentRegistry` | 148-153 | Merges `SOURCE_REGISTRY` into the component catalog | F005 |
| 7 | `querysource/queries/multi/destinations/s3.py` | `ToS3` | 141-143 | Existing pyarrow Parquet usage (write side) | F006 |
| 8 | `querysource/conf.py` | `BIGQUERY_CREDENTIALS`, `GOOGLE_CREDENTIALS_FILE` | 193-203, 300-305 | Existing Google service-account settings | F007 |
| 9 | `pyproject.toml` | `[project.optional-dependencies].s3` | 157-160 | Optional-extra precedent (`aioboto3`) | F008 |
| 10 | `generated/S3Source.json` | — | — | Generated component schema (via `generate-multiquery-docs`) | F009 |
| 11 | `tests/test_source_s3.py` | `TestS3Source` | 1-130 | Unit-test pattern for sources | F010 |

### 2.2 Constraints Discovered

- **Dispatch contract.** `MultiQS` instantiates a source as `cls(name, config, request, queue)`.
  If the type name is not in `SOURCE_REGISTRY` it raises `DriverError`, and repeated
  types are auto-named `Type_1`, `Type_2`, and so on.
  *Implication*: all three classes must keep that constructor signature and be registered under their class names. *Evidence*: F005
- **Base-class semantics.** `ThreadSource.__init__` pops `masks` from options.
  `run()` puts a non-`None` result in the queue as `{name: df}`, and `DataNotFound`/`NoDataFound`
  are treated as "no data" (HTTP 204), not as failures.
  *Implication*: an empty dataset or a filter that matches nothing should raise `DataNotFound`. *Evidence*: F003
- **Credential resolution.** `resolve_credential` looks up UPPER_SNAKE values in
  navconfig. `S3Source` passes explicit keys only when they resolved, and otherwise falls
  back to the default AWS chain.
  *Implication*: `ParquetS3Source` and `ParquetGCSSource` should keep the same "explicit, else ambient" behaviour. *Evidence*: F002, F003
- **Optional dependencies.** `pyarrow` (25.0.1) is present only through deltalake,
  pygwalker and streamlit, and `google-cloud-storage` only through asyncdb.
  `S3Source` imports `aioboto3` lazily and raises an `ImportError` that names the extra.
  *Implication*: declare a `parquet` extra (pyarrow) and import it lazily with the same error style. `pyarrow.fs.GcsFileSystem` does not need google-cloud-storage. *Evidence*: F002, F008
- **Async convention.** `fetch()` already runs in a dedicated thread with its own loop,
  but repo rules forbid blocking I/O inside `async def`.
  *Implication*: run the pyarrow reads in `asyncio.to_thread`. *Evidence*: F003
- **Docs/schema.** Every component has a `generated/<Component>.json` produced by
  `generate-multiquery-docs`, and schema fixes ship with the source change.
  *Evidence*: F009

### 2.3 Recent History (Relevant)

| Commit | When | Author | Message |
|--------|------|--------|---------|
| `76b8851` | 2026-09-30 | Jesus Lara | perf(multi): load a slug definition once per request and hand it to the thread |
| `a4536ff` | 2026-09-30 | Jesus Lara | another fix on filter parser and empty results |
| `6d8681b` | 2026-06-29 | Juan2coder | refactor(sources): use flowtask-style `masks` dict instead of hardcoded tokens |
| `30cccae` | 2026-06-29 | Juan2coder | feat(sources): support {today}/{yesterday} date masks in file paths |

Nothing is in flight on `S3Source` or on Parquet reading. *Evidence*: F011

---

## 3. Probable Scope

### What's New

- **`querysource/queries/multi/sources/parquet.py`** (proposed):
  - a shared Parquet base (abstract `ThreadSource` subclass) that owns the read path.
    It resolves masks in the path, builds `pyarrow.dataset.dataset(path, filesystem=…,
    format="parquet", partitioning="hive" | None)` for files, directories and
    partitioned datasets, applies `columns` and `filters` (DNF list or expression),
    runs `to_table()` in `asyncio.to_thread`, converts to pandas, and raises
    `DataNotFound` when the result is empty;
  - **`ParquetFileSource`**: `pyarrow.fs.LocalFileSystem`, with `path` resolved like `FileSource`;
  - **`ParquetS3Source`**: `pyarrow.fs.S3FileSystem`, with a credentials block shaped like `S3Source`
    (`region_name`, `bucket`, `aws_key`, `aws_secret`) plus `source.directory`/`source.file`,
    falling back to the ambient AWS chain;
  - **`ParquetGCSSource`**: `pyarrow.fs.GcsFileSystem`. The credentials come from a
    service-account JSON resolved through navconfig (default `GOOGLE_CREDENTIALS_FILE` /
    `BIGQUERY_CREDENTIALS`), with a fallback to Application Default Credentials.
- **`parquet` optional extra** in `pyproject.toml` (pyarrow), with a lazy import and an actionable `ImportError`.
- **`generated/ParquetFileSource.json`, `generated/ParquetS3Source.json`, `generated/ParquetGCSSource.json`**, created by `generate-multiquery-docs`.
- **Tests** `tests/test_source_parquet.py`: a local round-trip using `tmp_path`, columns, filters, a hive-partitioned directory, masks, empty → `DataNotFound`, missing pyarrow → `ImportError`, and S3/GCS filesystem construction with mocked `pyarrow.fs`.

### What Changes

- **`querysource/queries/multi/sources/__init__.py`::`SOURCE_REGISTRY` / `__all__`**: register the three classes. *Evidence*: F005
- **`tests/test_source_registry.py`**: assert the new registry entries. *Evidence*: F010

### What's Untouched (Non-Goals)

- `S3Source` and `FileSource` keep CSV/Excel only. Parquet support is not bolted onto them.
- No Parquet writing: `ToS3` already covers the write side.
- No streaming or chunked delivery: the result is a single DataFrame, like every other source.
- No Azure or HTTP backends and no Delta/Iceberg tables. Those are covered by the existing `deltatbl` provider.
- `MultiQS` dispatch logic stays unchanged.

### Patterns to Follow

- Credential resolution and "explicit, else ambient" auth, as in `S3Source.__init__`/`fetch`. *Evidence*: F002
- Masks resolved after `super().__init__`, as in `FileSource.__init__`. *Evidence*: F004
- Lazy optional import with the extra named in the error message. *Evidence*: F002
- Tests built with `request=None` and `asyncio.Queue()`, mocking I/O. *Evidence*: F010

### Integration Risks

- **Memory**: a whole dataset turns into one pandas DataFrame. Mitigation: log a
  warning above a row/byte threshold (like `S3Source`'s 100 MB warning) and rely on
  `columns`/`filters` pushdown. *Evidence*: F002
- **Filter syntax from YAML**: `filters` must be passed as pyarrow DNF tuples, not as
  arbitrary expressions evaluated from strings, so nothing in the config is `eval`-ed.
  Validate the operators against an allowlist.
- **pyarrow GCS/S3 build support**: the PyPI pyarrow wheels include S3 and GCS
  filesystems, but that should be verified on the deployed platform (see §5).

---

## 4. Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | New sources register in `SOURCE_REGISTRY` and are dispatched from YAML `sources:` by type name | F005 | high | Direct read of dispatch code and registry |
| C2 | `S3Source` reads the full object into memory via aioboto3 and parses only CSV/GZ/Excel | F002 | high | Direct read |
| C3 | No MultiQS source reads Parquet today; pyarrow is only used for writing | F004, F006 | high | Repo-wide grep |
| C4 | There is no GCS client pattern in querysource to reuse | F007 | high | Repo-wide grep, empty |
| C5 | pyarrow and google-cloud-storage are only transitive dependencies and must be declared | F008 | high | uv.lock dependents |
| C6 | Wrapping sync pyarrow reads in `asyncio.to_thread` satisfies the async convention inside the thread's loop | F003 | medium | Inferred from `ThreadSource.run` design and repo rules |
| C7 | Generated schemas and tests mirroring `test_source_s3.py` are expected deliverables | F009, F010 | medium | Inferred from existing per-component artifacts |

Distribution: **5** high, **2** medium, **0** low.

---

## 5. Open Questions

### Resolved (during proposal phase)

- [x] **Which I/O layer reads remote Parquet?** — *Resolved*: pyarrow.fs (S3FileSystem / GcsFileSystem / LocalFileSystem) + pyarrow parquet/dataset read in `asyncio.to_thread`. *Resolves*: C6
- [x] **One class or several?** — *Resolved*: Separate classes (`ParquetFileSource`, `ParquetS3Source`, `ParquetGCSSource`) sharing a common Parquet base. *Resolves*: C1
- [x] **How are GCS credentials supplied?** — *Resolved*: Service-account JSON via navconfig (default `GOOGLE_CREDENTIALS_FILE` / `BIGQUERY_CREDENTIALS`), fallback to ADC. *Resolves*: C4
- [x] **Which read features are in scope for v1?** — *Resolved*: all four: column projection, row filters, directories/partitioned datasets, masks in path.

### Unresolved (defer to spec / implementation)

- [ ] **Should the `parquet` extra be folded into `s3`, or added to a new `gcs` extra?** — *Owner*: tbd
  *Plausible answers*: a) a single `parquet` extra with only pyarrow (GcsFileSystem/S3FileSystem are built in) · b) separate extras per backend
- [ ] **Do the deployed pyarrow wheels expose `GcsFileSystem`/`S3FileSystem`?** — *Owner*: tbd. They are present in the local venv (pyarrow 25.0.1: `pyarrow._gcsfs.GcsFileSystem`, `pyarrow._s3fs.S3FileSystem`). The production image still needs checking.

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-177`**: *Rationale*: the localization is high-confidence, the
`ThreadSource` + `SOURCE_REGISTRY` pattern is well established, and the Q&A settled
every architectural fork (I/O layer, class layout, credentials, feature scope).

### Alternatives

- **`/sdd-brainstorm FEAT-177`**: only if you want to revisit fsspec or native clients instead of pyarrow.fs.
- **`/sdd-task`**: not recommended. The work spans a new module, three classes, deps, schemas and tests.

---

## 7. Research Audit

| Artifact | Path |
|----------|------|
| State checkpoints | `sdd/state/FEAT-177/state.json` |
| Source (raw) | `sdd/state/FEAT-177/source.md` |
| Research plan | `sdd/state/FEAT-177/research_plan.json` |
| Findings (digests) | `sdd/state/FEAT-177/findings/F001-*.md` … `F011-*.md` |
| Synthesis (JSON) | `sdd/state/FEAT-177/synthesis.json` |

**Budget consumed** (default profile):
- Files read: 14 / 40
- Grep calls: 9 / 25
- Git calls: 3 / 10
- Truncated: **no**

**Mode determination**: `auto` → resolved to `enrichment` (new capability, no defect signal).

---

## 8. Provenance

| Field | Value |
|-------|-------|
| Generated by | `/sdd-proposal v1.0` |
| Synthesis prompt | `sdd/templates/synthesis.prompt.md v1.0` |
| Plan prompt | `sdd/templates/research_plan.prompt.md v1.0` |
| Schema versions | state=1.0, synthesis=1.0, research_plan=1.0 |
| Operator | Claude Code (Opus 5.5) with Jesus Lara |
