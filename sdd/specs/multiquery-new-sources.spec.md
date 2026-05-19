---
type: feature
base_branch: dev
---

# Feature Specification: MultiQuery New Sources

**Feature ID**: FEAT-093
**Date**: 2026-05-19
**Author**: Jesus Lara
**Status**: approved
**Target version**: 6.0.0

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQuery currently only supports two source types: **ThreadFile** (loading a static file from a local path) and **ThreadQuery** (executing a QuerySource slug). Many real-world data pipelines need to pull data from external services — SharePoint document libraries, SmartSheet spreadsheets, AWS S3 buckets, and raw database tables — before applying the MultiQuery operator pipeline (Join, Concat, Filter, GroupBy, Transform, Output).

Today, users work around this by using Flowtask interfaces to pre-download files and then point MultiQuery at local paths — an unnecessary two-step process that complicates orchestration and slows iteration.

### Goals

- Add four new MultiQuery source components: **SourceSharepoint**, **SourceSmartSheet**, **SourceS3**, **SourceTable**.
- Each source downloads or fetches data and returns a **pandas DataFrame** into the MultiQuery result queue.
- Introduce a **ThreadSource** base class that encapsulates the common Thread + event loop + queue pattern, and refactor the existing **ThreadFile** and **ThreadQuery** to inherit from it.
- Support credential resolution through explicit config dictionaries **or** navconfig environment variable names.
- Integrate seamlessly with the existing MultiQuery operator pipeline (Join, Concat, Melt, Merge, Filter, GroupBy, Transform, Output).

### Non-Goals (explicitly out of scope)

- Uploading files back to SharePoint, SmartSheet, or S3 — this is download/fetch only.
- Downloading multiple files from a single source declaration (one file per source entry).
- Web UI for configuring sources — config is YAML/JSON only.
- Adding new operators to the MultiQuery pipeline.
- Modifying the HTTP-based `httpSource`/`restSource` hierarchy in `querysource/providers/sources/` — those are a separate abstraction for the Provider system, not MultiQuery sources.

---

## 2. Architectural Design

### Overview

Create a `ThreadSource` abstract base class that encapsulates the boilerplate shared by all MultiQuery source threads: creating an asyncio event loop, managing exceptions, and putting results into the shared queue. Then implement four concrete subclasses — one for each new source type — plus refactor the existing `ThreadFile` and `ThreadQuery` to inherit from `ThreadSource`.

Each new source:
1. Receives its configuration dict from the MultiQuery YAML/JSON definition.
2. Resolves credentials (explicit values or navconfig variable names).
3. Downloads or fetches data asynchronously within its thread's event loop.
4. Parses the result into a pandas DataFrame (CSV/Excel for file-based sources, query result for Table).
5. Puts `{name: DataFrame}` into the shared `asyncio.Queue`.

The `MultiQS` orchestrator is extended to recognize new source keys (`sources`) in addition to `queries` and `files`, and dispatch the appropriate `ThreadSource` subclass based on the source type name.

### Component Diagram

```
MultiQS (orchestrator)
  │
  ├── queries: {...}  ──→ ThreadQuery(ThreadSource)
  ├── files: {...}    ──→ ThreadFile(ThreadSource)
  └── sources: {...}  ──→ SourceSharepoint(ThreadSource)
                          SourceSmartSheet(ThreadSource)
                          SourceS3(ThreadSource)
                          SourceTable(ThreadSource)
                              │
                              ▼
                     asyncio.Queue ──→ Operator Pipeline
                                       (Join/Concat/Filter/...)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `MultiQS` | modified | Add `sources` key parsing + dispatch loop in `query()` |
| `ThreadQuery` | refactored | Inherit from `ThreadSource` base class |
| `ThreadFile` | refactored | Inherit from `ThreadSource` base class |
| `asyncio.Queue` | uses | Shared result queue — unchanged contract |
| `navconfig` | uses | Credential resolution for env variable names |
| `asyncdb.AsyncDB` | uses | Database connections for `SourceTable` |
| `pandas` | uses | DataFrame construction from CSV/Excel/query results |

### Data Models

```python
# YAML/JSON configuration shapes consumed by each source:

# SourceSharepoint
{
    "credentials": {
        "client_id": "SHAREPOINT_APP_ID",       # navconfig var name or literal
        "client_secret": "SHAREPOINT_APP_SECRET",
        "tenant_id": "SHAREPOINT_TENANT_ID",
        "site": "Roadshows"
    },
    "source": {
        "filename": "2025 Events Master Schedule.xlsx",
        "directory": "Shared Documents/General/Schedule"
    }
}

# SourceSmartSheet
{
    "source": {
        "file_id": 8504624500658052
    }
    # credentials optional — defaults to SMARTSHEET_API_KEY from navconfig
}

# SourceS3
{
    "credentials": {
        "region_name": "AWS_REGION_NAME",
        "bucket": "AWS_PLACER_BUCKET",
        "aws_key": "AWS_ACCESS_KEY_ID",
        "aws_secret": "AWS_SECRET_ACCESS_KEY"
    },
    "source": {
        "file": "metrics_2024-12-09_0003.csv.gz",
        "directory": "placer-analytics/bulk-export/monthly-weekly/2024-12-09/metrics/"
    }
}

# SourceTable
{
    "driver": "pg",
    "schema": "troc",
    "table": "stores",
    "filter": {"active": true}
}
```

### New Public Interfaces

```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):
    """Base class for all MultiQuery source threads."""

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...

    def resolve_credential(self, key: str, value: str) -> str:
        """Resolve a credential value: if it looks like an env var name,
        look it up in navconfig; otherwise return the literal."""
        ...

    @abstractmethod
    async def fetch(self) -> pd.DataFrame:
        """Fetch data and return as a DataFrame. Implemented by each source."""
        ...

    def run(self) -> None:
        """Thread entry point — creates event loop, calls fetch(), puts result in queue."""
        ...


# querysource/queries/multi/sources/sharepoint.py
class SourceSharepoint(ThreadSource):
    async def fetch(self) -> pd.DataFrame: ...

# querysource/queries/multi/sources/smartsheet.py
class SourceSmartSheet(ThreadSource):
    async def fetch(self) -> pd.DataFrame: ...

# querysource/queries/multi/sources/s3.py
class SourceS3(ThreadSource):
    async def fetch(self) -> pd.DataFrame: ...

# querysource/queries/multi/sources/table.py
class SourceTable(ThreadSource):
    async def fetch(self) -> pd.DataFrame: ...
```

---

## 3. Module Breakdown

### Module 1: ThreadSource Base Class

- **Path**: `querysource/queries/multi/sources/base.py`
- **Responsibility**: Abstract base class for all MultiQuery thread-based sources. Encapsulates event loop creation, exception capture, queue interaction, and credential resolution via navconfig.
- **Depends on**: `threading`, `asyncio`, `navconfig`

### Module 2: Refactor ThreadFile

- **Path**: `querysource/queries/multi/sources/file.py`
- **Responsibility**: Refactor existing `ThreadFile` to inherit from `ThreadSource`. Move file parsing (CSV/Excel/compressed) into an async `fetch()` method. Preserve backward compatibility.
- **Depends on**: Module 1 (ThreadSource)

### Module 3: Refactor ThreadQuery

- **Path**: `querysource/queries/multi/sources/query.py`
- **Responsibility**: Refactor existing `ThreadQuery` to inherit from `ThreadSource`. Move QueryObject building and execution into `fetch()`. Preserve backward compatibility.
- **Depends on**: Module 1 (ThreadSource)

### Module 4: SourceSharepoint

- **Path**: `querysource/queries/multi/sources/sharepoint.py`
- **Responsibility**: Download a single file (CSV/Excel) from a SharePoint document library via Microsoft Graph API and return it as a DataFrame. Adapted from `flowtask/flowtask/interfaces/Sharepoint.py`.
- **Depends on**: Module 1 (ThreadSource), `msgraph-sdk`, `httpx`, `pandas`

### Module 5: SourceSmartSheet

- **Path**: `querysource/queries/multi/sources/smartsheet.py`
- **Responsibility**: Download a SmartSheet sheet as Excel via the SmartSheet REST API and return it as a DataFrame. Adapted from `flowtask/flowtask/interfaces/smartsheet.py`.
- **Depends on**: Module 1 (ThreadSource), `aiohttp`, `pandas`

### Module 6: SourceS3

- **Path**: `querysource/queries/multi/sources/s3.py`
- **Responsibility**: Download a single file from an S3 bucket and return it as a DataFrame. Uses `aioboto3` for async S3 operations. Supports CSV and compressed CSV (`.gz`).
- **Depends on**: Module 1 (ThreadSource), `aioboto3`, `pandas`

### Module 7: SourceTable

- **Path**: `querysource/queries/multi/sources/table.py`
- **Responsibility**: Connect to a database via `asyncdb.AsyncDB`, execute `SELECT * FROM schema.table [WHERE filters]`, and return the result as a DataFrame. Supports driver aliases (pg, postgresql, bigquery, mysql, etc.).
- **Depends on**: Module 1 (ThreadSource), `asyncdb`

### Module 8: MultiQS Integration

- **Path**: `querysource/queries/multi/__init__.py`
- **Responsibility**: Extend `MultiQS.query()` to parse the `sources` key from the options dict, instantiate the appropriate `ThreadSource` subclass by type name, and join them alongside existing query/file threads.
- **Depends on**: Modules 1–7

### Module 9: Source Registry & __init__ Exports

- **Path**: `querysource/queries/multi/sources/__init__.py`
- **Responsibility**: Export all source classes and provide a registry dict (`SOURCE_REGISTRY`) mapping type names (e.g., `"SourceSharepoint"`) to their classes for dynamic dispatch from MultiQS.
- **Depends on**: Modules 2–7

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_thread_source_resolve_credential_env` | Module 1 | Credential value matching navconfig var is resolved |
| `test_thread_source_resolve_credential_literal` | Module 1 | Literal credential value is returned as-is |
| `test_thread_source_exception_capture` | Module 1 | Exception in `fetch()` is stored on `self.exc` |
| `test_thread_file_backward_compat` | Module 2 | Refactored ThreadFile produces same DataFrame as before |
| `test_thread_query_backward_compat` | Module 3 | Refactored ThreadQuery produces same result as before |
| `test_sharepoint_parse_config` | Module 4 | SourceSharepoint correctly parses credentials and source config |
| `test_smartsheet_parse_config` | Module 5 | SourceSmartSheet correctly parses source config |
| `test_s3_parse_config` | Module 6 | SourceS3 correctly parses credentials and source path |
| `test_s3_gz_decompression` | Module 6 | Compressed .gz file is decompressed before DataFrame creation |
| `test_table_driver_normalization` | Module 7 | Driver aliases (postgresql → pg, bq → bigquery) are normalized |
| `test_table_filter_to_where` | Module 7 | Filter dict is converted to valid WHERE clause |
| `test_source_registry_contains_all` | Module 9 | SOURCE_REGISTRY maps all four new source type names |

### Integration Tests

| Test | Description |
|---|---|
| `test_multiqs_with_source_s3` | End-to-end: MultiQS loads a CSV from S3 (mocked boto3) and returns DataFrame |
| `test_multiqs_with_source_table` | End-to-end: MultiQS queries a table (test PostgreSQL) and returns DataFrame |
| `test_multiqs_mixed_sources_join` | MultiQS combines a file source + table source via Join operator |
| `test_multiqs_source_sharepoint_mock` | MultiQS loads an Excel file via mocked SharePoint Graph API |

### Test Data / Fixtures

```python
@pytest.fixture
def sample_csv_bytes():
    """In-memory CSV content for testing file-based sources."""
    return b"col1,col2\n1,a\n2,b\n3,c"

@pytest.fixture
def sample_excel_bytes():
    """In-memory Excel content for testing Excel-based sources."""
    import io
    df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.getvalue()

@pytest.fixture
def source_table_config():
    return {
        "driver": "pg",
        "schema": "public",
        "table": "test_table",
        "filter": {"active": True}
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `ThreadSource` base class exists at `querysource/queries/multi/sources/base.py` with `resolve_credential()` and abstract `fetch()` methods
- [ ] `ThreadFile` inherits from `ThreadSource` and all existing MultiQuery file tests still pass
- [ ] `ThreadQuery` inherits from `ThreadSource` and all existing MultiQuery query tests still pass
- [ ] `SourceSharepoint` downloads a single Excel/CSV file from SharePoint via Microsoft Graph API and returns a DataFrame
- [ ] `SourceSmartSheet` fetches a sheet via SmartSheet API and returns a DataFrame
- [ ] `SourceS3` downloads a file from an S3 bucket (supports .csv, .csv.gz, .xlsx) and returns a DataFrame
- [ ] `SourceTable` connects via `asyncdb.AsyncDB`, runs `SELECT * FROM schema.table WHERE ...`, and returns a DataFrame
- [ ] `MultiQS.query()` recognizes the `sources` key and dispatches to the correct `ThreadSource` subclass
- [ ] All credential values can be specified as either literal values or navconfig variable names
- [ ] Unit tests pass (`pytest tests/ -v -k "multi"`)
- [ ] No breaking changes to existing MultiQuery behavior (queries and files keys work as before)
- [ ] All new source classes are exported from `querysource/queries/multi/sources/__init__.py`

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# MultiQuery orchestrator
from querysource.queries.multi import MultiQS          # verified: querysource/queries/multi/__init__.py:53
from querysource.queries.multi.sources import ThreadQuery, ThreadFile  # verified: querysource/queries/multi/sources/__init__.py:1-2

# Base query class
from querysource.queries.base import BaseQuery          # verified: querysource/queries/multi/__init__.py:12

# Exceptions
from querysource.exceptions import (
    SlugNotFound, QueryException, DriverError,
    DataNotFound, ParserError
)                                                        # verified: querysource/queries/multi/__init__.py:4-10

# Operators
from querysource.queries.multi.operators.filter import Filter  # verified: querysource/queries/multi/__init__.py:15
from querysource.outputs.tables import TableOutput             # verified: querysource/queries/multi/__init__.py:16

# QueryObject (used by ThreadQuery)
from querysource.queries.obj import QueryObject          # verified: querysource/queries/multi/sources/query.py:4

# External libs
import pandas as pd                                       # verified: querysource/queries/multi/sources/file.py:8
import asyncio                                            # verified: querysource/queries/multi/sources/file.py:1
import threading                                          # verified: querysource/queries/multi/sources/file.py:2
from aiohttp import web                                   # verified: querysource/queries/multi/sources/file.py:3
from pathlib import Path                                  # verified: querysource/queries/multi/sources/file.py:4
from io import BytesIO                                    # verified: querysource/queries/multi/sources/file.py:7
```

### Existing Class Signatures

```python
# querysource/queries/multi/__init__.py:53-104
class MultiQS(BaseQuery):
    def __init__(
        self,
        slug: str = None,
        queries: Optional[list] = None,
        files: Optional[list] = None,
        query: Optional[dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        user_session: Optional[object] = None,
        **kwargs
    ) -> None: ...
    # Key attributes:
    #   self._queue: asyncio.Queue          # line 79
    #   self._queries: queries              # line 84
    #   self._files: files                  # line 85
    #   self._options: dict                 # line 87
    #   self._return_all: bool              # line 89
    #   self._user_session                  # line 103

    async def query(self) -> tuple: ...    # line 105 — returns (result, options)

# querysource/queries/multi/sources/query.py:8-71
class ThreadQuery(threading.Thread):
    def __init__(
        self,
        name: str,
        query: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...                         # line 14-29
    @property
    def slug(self) -> str: ...             # line 31-33
    def run(self) -> None: ...             # line 35-71

# querysource/queries/multi/sources/file.py:19-100
class ThreadFile(threading.Thread):
    def __init__(
        self,
        name: str,
        file_options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...                         # line 21-31
    def _get_file_content(self) -> Union[Path, BytesIO]: ...  # line 33-52
    def run(self) -> None: ...             # line 54-100

# querysource/queries/multi/sources/__init__.py:1-7
# Exports: ThreadQuery, ThreadFile
```

### Reference Code from External Projects

```python
# flowtask/flowtask/interfaces/Sharepoint.py — SharepointClient
# Key patterns to adapt:
#   - Authenticates via SHAREPOINT_APP_ID, SHAREPOINT_APP_SECRET, SHAREPOINT_TENANT_ID
#   - Uses Microsoft Graph SDK (msgraph) for file operations
#   - file_search() / file_lookup() for locating files
#   - download_found_files() with httpx streaming
#   - Async pattern throughout

# flowtask/flowtask/interfaces/smartsheet.py — SmartSheetClient
# Key patterns to adapt:
#   - Bearer token auth via SMARTSHEET_API_KEY env var
#   - REST API at https://api.smartsheet.com/2.0/sheets/{sheet_id}
#   - Attachment download via two-step: get attachment details → download URL
#   - aiohttp + SSL context (TLS 1.2+)

# flowtask/flowtask/interfaces/Boto3Client.py — Boto3Client
# navigator/navigator/utils/file/s3.py — S3FileManager
# Key patterns to adapt:
#   - aioboto3 async S3 client (preferred over sync boto3)
#   - download_file() with streaming to BytesIO
#   - Credential resolution from AWS_CREDENTIALS navconfig dict

# ai-parrot TableSource — parrot/tools/dataset_manager/sources/table.py
# Key patterns to adapt:
#   - AsyncDB(driver, dsn=dsn) or AsyncDB(driver, params=credentials)
#   - conn.output_format('pandas') → conn.query(sql) returns DataFrame
#   - Driver normalization: postgresql/postgres→pg, bq→bigquery, mariadb→mysql
#   - SQL identifier validation regex: ^[a-zA-Z_][a-zA-Z0-9_]*$
#   - Filter dict → WHERE clause construction
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `ThreadSource.resolve_credential()` | `navconfig` settings | attribute lookup | navconfig is used throughout querysource |
| `SourceTable.fetch()` | `asyncdb.AsyncDB` | `AsyncDB(driver, params=...)` | ai-parrot table.py:457-497 pattern |
| `MultiQS.query()` | `SOURCE_REGISTRY` | dict lookup by source type name | New integration — added in Module 8 |
| All `ThreadSource` subclasses | `asyncio.Queue` | `self._queue.put({name: df})` | querysource/queries/multi/sources/file.py:83 |

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.queries.multi.sources.base`~~ — does not exist yet (Module 1 creates it)
- ~~`querysource.queries.multi.sources.sharepoint`~~ — does not exist yet (Module 4 creates it)
- ~~`querysource.queries.multi.sources.smartsheet`~~ — does not exist yet (Module 5 creates it)
- ~~`querysource.queries.multi.sources.s3`~~ — does not exist yet (Module 6 creates it)
- ~~`querysource.queries.multi.sources.table`~~ — does not exist yet (Module 7 creates it)
- ~~`ThreadSource`~~ — does not exist yet (Module 1 creates it)
- ~~`SOURCE_REGISTRY`~~ — does not exist yet (Module 9 creates it)
- ~~`MultiQS._sources`~~ — does not exist yet (Module 8 adds it)
- ~~`ThreadFile.fetch()`~~ — ThreadFile currently uses `run()` directly, no `fetch()` method
- ~~`ThreadQuery.fetch()`~~ — ThreadQuery currently uses `run()` directly, no `fetch()` method
- ~~`querysource.providers.sources.sharepoint`~~ — no SharePoint in the HTTP provider source hierarchy
- ~~`querysource.providers.sources.s3`~~ — no S3 in the HTTP provider source hierarchy

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Thread + event loop pattern**: Each source runs in its own thread with a dedicated `asyncio` event loop (established pattern in `ThreadFile` and `ThreadQuery`). The `ThreadSource` base class must handle loop creation, `fetch()` invocation, exception capture, and loop cleanup.
- **Queue contract**: Every source puts exactly one dict `{self._name: DataFrame}` into the shared queue on success. On failure, the exception is stored in `self.exc` and the thread exits.
- **Credential resolution**: Accept a dict where values are either literal credentials or navconfig variable names (all-caps convention). Use `navconfig` settings lookup to resolve. Fall back to literal if no navconfig match.
- **File parsing reuse**: For file-based sources (SharePoint, SmartSheet, S3), reuse pandas `read_excel()` / `read_csv()` with the same parameters as `ThreadFile` (na_values, na_filter, engine detection).
- **Driver normalization for SourceTable**: Use the same alias map as ai-parrot: `postgresql`/`postgres` → `pg`, `bq` → `bigquery`, `mariadb` → `mysql`.
- **SQL safety for SourceTable**: Validate table and schema names against `^[a-zA-Z_][a-zA-Z0-9_]*$`. Build WHERE clauses from the filter dict with proper quoting.

### Known Risks / Gotchas

- **Microsoft Graph SDK dependency**: `SourceSharepoint` requires `msgraph-sdk` and its auth dependencies. This should be an optional dependency — import errors should produce a clear message ("install msgraph-sdk for SharePoint support").
- **SmartSheet API rate limits**: SmartSheet has rate limits (300 requests/minute). A single sheet download should be well within limits, but error handling for 429 responses is needed.
- **S3 large files**: Downloading large files into memory via `BytesIO` can be expensive. Consider a size warning or streaming approach for files > 100MB.
- **Thread safety of event loop**: Each thread creates its own event loop. Must ensure no cross-thread loop access. The current pattern in `ThreadFile`/`ThreadQuery` already handles this correctly.
- **Backward compatibility**: The refactor of `ThreadFile` and `ThreadQuery` to inherit from `ThreadSource` must not change their constructor signatures or the data they put into the queue.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `msgraph-sdk` | `>=1.0` | SharePoint file access via Microsoft Graph API (optional) |
| `azure-identity` | `>=1.0` | Authentication for Microsoft Graph (optional, with msgraph-sdk) |
| `httpx` | `>=0.24` | Async HTTP downloads for SharePoint files (optional) |
| `aioboto3` | `>=12.0` | Async AWS S3 operations (optional) |
| `aiohttp` | `>=3.8` | SmartSheet API calls (already a dependency) |
| `asyncdb` | `>=2.0` | Database connections for SourceTable (already a dependency) |
| `pandas` | `>=1.5` | DataFrame construction (already a dependency) |
| `navconfig` | any | Credential/settings resolution (already a dependency) |

---

## 8. Open Questions

> Questions that must be resolved before or during implementation.

- [ ] Should optional dependencies (msgraph-sdk, aioboto3) be listed as extras in pyproject.toml (e.g., `pip install querysource[sharepoint]`)? — *Owner: Jesus Lara*: yes
- [ ] Should `SourceSharepoint` support site-relative URLs in addition to library/directory paths? — *Owner: Jesus Lara*: yes
- [ ] For `SourceTable`, should we support custom SQL in addition to `table + filter`? (e.g., a `query` key with raw SQL) — *Owner: Jesus Lara*: no, ThreadQuery already do that.
- [ ] Should there be a timeout per source (e.g., SharePoint downloads can be slow)? If so, what's the default? — *Owner: Jesus Lara*: 30 seconds timeout.

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks).
- All modules share the same source directory (`querysource/queries/multi/sources/`) and the orchestrator (`querysource/queries/multi/__init__.py`), so parallel implementation would cause merge conflicts.
- **Recommended order**: Module 1 (base) → Modules 2-3 (refactors) → Modules 4-7 (new sources, can be parallelized after base) → Module 8 (integration) → Module 9 (registry/exports).
- **Cross-feature dependencies**: None — this spec is self-contained.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-19 | Jesus Lara | Initial draft |
