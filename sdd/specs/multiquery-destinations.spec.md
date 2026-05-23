---
type: feature
base_branch: dev
---

# Feature Specification: MultiQuery New Destinations

**Feature ID**: FEAT-094
**Date**: 2026-05-19
**Author**: Jesus Lara
**Status**: approved
**Target version**: 6.0.0

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQuery currently supports only one destination component — `TableOutput` — which writes pandas DataFrames to relational and NoSQL databases. Real-world data pipelines frequently need to push results to other storage backends: SharePoint document libraries (Excel/CSV uploads), AWS S3 buckets, raw database tables with flexible write modes (append/upsert/truncate), and managed data warehouses (BigQuery, DocumentDB, DynamoDB).

Today users must write custom post-processing scripts or use Flowtask interfaces outside of QuerySource to achieve these destinations, breaking the single-pipeline model that MultiQuery provides.

FEAT-093 (MultiQuery New Sources) adds new _source_ components using a `ThreadSource` base class pattern. This spec complements it by adding new _destination_ components on the output side.

### Goals

- Add four new MultiQuery destination components: **ToSharepoint**, **ToS3**, **Table**, **DWH**.
- Each destination receives a pandas DataFrame (or dict of DataFrames) from the MultiQuery pipeline and writes it to the target backend.
- Introduce a **destination registry** pattern so new destinations are discovered dynamically (not hardcoded `if/elif` checks).
- Support credential resolution through explicit config dictionaries **or** navconfig environment variable names.
- Each destination follows an `AbstractDestination` base class with a common `async run(data)` interface.
- Integrate seamlessly with the existing MultiQuery operator pipeline — destinations replace/extend the current `TableOutput` step.

### Non-Goals (explicitly out of scope)

- Reading data from SharePoint or S3 — that is the job of FEAT-093 source components.
- Building a UI for configuring destinations — config is YAML/JSON only.
- Replacing the existing `TableOutput` class — it continues to work as-is. The new `Table` destination is a separate component with different semantics (auto-create tables, flexible write modes).
- Modifying the `DataOutput` writer system (`querysource/outputs/writers/`) — that handles HTTP response formatting, not data persistence.
- Adding new operators or transformations to the MultiQuery pipeline.

---

## 2. Architectural Design

### Overview

Create an `AbstractDestination` base class that defines the common interface for all MultiQuery destination components. Then implement four concrete subclasses — `ToSharepoint`, `ToS3`, `Table`, and `DWH`. Introduce a destination registry (a dict mapping YAML step names to classes) so `MultiQS.query()` and `QueryHandler` can dispatch to any registered destination without hardcoded conditionals.

The existing `TableOutput` will be registered in the new registry under its current names (`tableOutput`, `TableOutput`) for backward compatibility.

Each destination:
1. Receives its configuration dict from the MultiQuery YAML/JSON definition under the `Output` key.
2. Resolves credentials (explicit values or navconfig variable names).
3. Converts the DataFrame to the target format (Excel file, CSV, database records).
4. Writes to the target backend asynchronously.
5. Returns the original DataFrame (pass-through) so subsequent destinations in the pipeline can also receive it.

### Component Diagram

```
MultiQS.query() result (DataFrame)
        │
        ▼
   Output Pipeline (list of steps)
        │
        ├──→ DESTINATION_REGISTRY lookup by step_name
        │         │
        │         ├──→ "TableOutput"  → TableOutput (existing, backward-compat)
        │         ├──→ "ToSharepoint" → ToSharepoint (new)
        │         ├──→ "ToS3"         → ToS3 (new)
        │         ├──→ "Table"        → TableDestination (new)
        │         └──→ "DWH"          → DWHDestination (new)
        │
        ▼
   Return DataFrame (pass-through)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `MultiQS.query()` — lines 378-385 in `querysource/queries/multi/__init__.py` | **modify** | Replace hardcoded `TableOutput` dispatch with registry lookup |
| `QueryHandler` — lines 342-350 in `querysource/handlers/multi.py` | **modify** | Same registry-based dispatch |
| `TableOutput` — `querysource/outputs/tables/TableOutput/table.py` | **register** | Wrap existing class in registry, no changes to internals |
| `AbstractOutput` — `querysource/outputs/tables/TableOutput/abstract.py` | **reference** | Existing DB-engine interface; `Table` and `DWH` destinations reuse these engines |
| `SharepointClient` — `flowtask/flowtask/interfaces/Sharepoint.py` | **port** | Copy/adapt upload logic into querysource |
| `S3FileManager` — `navigator/navigator/utils/file/s3.py` | **use** | Import and use directly for S3 uploads |
| `asyncdb.AsyncDB` | **use** | Used by `DWH` destination for BigQuery/DocumentDB/DynamoDB drivers |

### Data Models

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class DestinationCredentials:
    """Credentials resolved from config or navconfig variables."""
    raw: dict = field(default_factory=dict)

    def resolve(self) -> dict:
        """Replace navconfig variable names with actual values."""
        ...

@dataclass
class DestinationConfig:
    """Parsed destination step from YAML."""
    credentials: Optional[dict] = None
    destination: Optional[dict] = None
```

### New Public Interfaces

```python
class AbstractDestination(ABC):
    """Base class for all MultiQuery destination components."""

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        ...

    def resolve_credentials(self, credentials: dict) -> dict:
        """Resolve navconfig variable names to actual values."""
        ...

    @abstractmethod
    async def run(self) -> Union[dict, pd.DataFrame]:
        """Write data to destination. Returns original data (pass-through)."""
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


class ToSharepoint(AbstractDestination):
    """Upload DataFrame as Excel/CSV to a SharePoint document library."""

    async def run(self) -> Union[dict, pd.DataFrame]:
        ...


class ToS3(AbstractDestination):
    """Upload DataFrame as CSV/Excel/Parquet to an S3 bucket."""

    async def run(self) -> Union[dict, pd.DataFrame]:
        ...


class TableDestination(AbstractDestination):
    """Write DataFrame to a database table with append/upsert/truncate modes."""

    async def run(self) -> Union[dict, pd.DataFrame]:
        ...


class DWHDestination(AbstractDestination):
    """Write DataFrame to a data warehouse (BigQuery, DocumentDB, DynamoDB)."""

    async def run(self) -> Union[dict, pd.DataFrame]:
        ...


# Registry
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {
    "tableOutput": TableOutputAdapter,
    "TableOutput": TableOutputAdapter,
    "ToSharepoint": ToSharepoint,
    "ToS3": ToS3,
    "Table": TableDestination,
    "DWH": DWHDestination,
}
```

---

## 3. Module Breakdown

### Module 1: AbstractDestination Base Class
- **Path**: `querysource/outputs/destinations/abstract.py`
- **Responsibility**: Define the `AbstractDestination` ABC and credential resolution logic. All destinations inherit from this.
- **Depends on**: `navconfig` for credential variable resolution.

### Module 2: Destination Registry
- **Path**: `querysource/outputs/destinations/__init__.py`
- **Responsibility**: Export `DESTINATION_REGISTRY` dict mapping YAML step names to destination classes. Provide `get_destination(step_name)` factory function.
- **Depends on**: Module 1, all destination modules.

### Module 3: ToSharepoint Destination
- **Path**: `querysource/outputs/destinations/sharepoint.py`
- **Responsibility**: Convert DataFrame to Excel/CSV file and upload to a SharePoint document library using Microsoft Graph SDK. Port upload logic from `flowtask/flowtask/interfaces/Sharepoint.py`.
- **Depends on**: Module 1. External: `msgraph-sdk`, `azure-identity`, `msal`, `openpyxl`.

### Module 4: ToS3 Destination
- **Path**: `querysource/outputs/destinations/s3.py`
- **Responsibility**: Convert DataFrame to CSV/Parquet/Excel and upload to an AWS S3 bucket using `navigator.utils.file.S3FileManager` or direct `aioboto3`.
- **Depends on**: Module 1. External: `aioboto3` (via `navigator.utils.file.s3`).

### Module 5: Table Destination
- **Path**: `querysource/outputs/destinations/table.py`
- **Responsibility**: Write DataFrame to a database table with configurable write mode (append, upsert, truncate). Supports auto-table-creation. Uses asyncdb for connection management and reuses the existing `TableOutput` engine backends (`PgOutput`, `BigQueryOutput`, etc.) where applicable.
- **Depends on**: Module 1. Internal: existing `AbstractOutput` engines, `asyncdb.AsyncDB`.

### Module 6: DWH Destination
- **Path**: `querysource/outputs/destinations/dwh.py`
- **Responsibility**: Extension of Table for data warehouse targets — BigQuery, DocumentDB, DynamoDB. Uses asyncdb drivers directly with auto-table-creation and schema inference from DataFrame dtypes.
- **Depends on**: Module 1, Module 5 (shares some logic). Internal: `asyncdb.AsyncDB`, existing DWH-specific drivers.

### Module 7: Integration — MultiQS Output Dispatch
- **Path**: `querysource/queries/multi/__init__.py` (modify lines 378-385)
- **Responsibility**: Replace hardcoded `TableOutput` check with `DESTINATION_REGISTRY` lookup. Also update `querysource/handlers/multi.py` lines 342-350.
- **Depends on**: Module 2.

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_abstract_destination_credential_resolution` | Module 1 | Verifies navconfig variable names are resolved to values |
| `test_abstract_destination_passthrough` | Module 1 | Verifies `run()` returns original DataFrame |
| `test_registry_lookup` | Module 2 | All registered names resolve to correct classes |
| `test_registry_unknown_name` | Module 2 | Unknown step name raises `OutputError` |
| `test_to_sharepoint_excel_conversion` | Module 3 | DataFrame → Excel bytes conversion works correctly |
| `test_to_sharepoint_csv_conversion` | Module 3 | DataFrame → CSV bytes for `.csv` filenames |
| `test_to_sharepoint_credential_resolution` | Module 3 | Sharepoint credentials resolved from navconfig |
| `test_to_s3_upload_csv` | Module 4 | DataFrame → CSV uploaded to mock S3 |
| `test_to_s3_upload_parquet` | Module 4 | DataFrame → Parquet uploaded to mock S3 |
| `test_to_s3_credential_resolution` | Module 4 | S3 credentials resolved from navconfig |
| `test_table_append_mode` | Module 5 | Append writes new rows without conflict |
| `test_table_upsert_mode` | Module 5 | Upsert performs INSERT ON CONFLICT UPDATE |
| `test_table_truncate_mode` | Module 5 | Truncate clears table before insert |
| `test_table_auto_create` | Module 5 | Table is created if it doesn't exist |
| `test_dwh_bigquery_write` | Module 6 | DataFrame written to BigQuery dataset.table |
| `test_dwh_dynamodb_write` | Module 6 | DataFrame written to DynamoDB table |
| `test_dispatch_registry_integration` | Module 7 | MultiQS output loop dispatches to correct destination |

### Integration Tests

| Test | Description |
|---|---|
| `test_multiqs_to_sharepoint_e2e` | Full MultiQuery pipeline → ToSharepoint upload (mocked Graph API) |
| `test_multiqs_to_s3_e2e` | Full MultiQuery pipeline → ToS3 upload (mocked S3) |
| `test_multiqs_table_pg_e2e` | Full MultiQuery pipeline → Table with PostgreSQL append |
| `test_multiqs_dwh_bigquery_e2e` | Full MultiQuery pipeline → DWH BigQuery write |
| `test_multiqs_multiple_destinations` | Single pipeline with two Output steps (Table + ToS3) |
| `test_backward_compat_table_output` | Existing `tableOutput` YAML configs continue to work |

### Test Data / Fixtures

```python
@pytest.fixture
def sample_dataframe():
    return pd.DataFrame({
        "store_id": [1, 2, 3],
        "name": ["Store A", "Store B", "Store C"],
        "revenue": [100.0, 200.0, 300.0],
    })

@pytest.fixture
def sharepoint_config():
    return {
        "credentials": {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
            "tenant_id": "test-tenant",
            "site": "TestSite",
        },
        "destination": {
            "filename": "test_output.xlsx",
            "directory": "Shared Documents/General",
        },
    }

@pytest.fixture
def s3_config():
    return {
        "credentials": {
            "region_name": "us-east-1",
            "bucket": "test-bucket",
            "aws_key": "AKIATEST",
            "aws_secret": "testsecret",
        },
        "destination": {
            "file": "output.csv",
            "directory": "exports/2025/",
        },
    }

@pytest.fixture
def table_config():
    return {
        "driver": "pg",
        "schema": "troc",
        "table": "stores",
        "method": "append",
        "pk": ["store_id"],
    }
```

---

## 5. Acceptance Criteria

- [ ] `AbstractDestination` base class with credential resolution exists at `querysource/outputs/destinations/abstract.py`
- [ ] `DESTINATION_REGISTRY` in `querysource/outputs/destinations/__init__.py` maps all step names to classes
- [ ] `ToSharepoint` converts DataFrame to Excel/CSV and uploads to SharePoint via Microsoft Graph SDK
- [ ] `ToS3` converts DataFrame to CSV/Parquet and uploads to S3 bucket
- [ ] `Table` destination writes DataFrame with `append`, `upsert`, and `truncate` modes; auto-creates table if missing
- [ ] `DWH` destination writes to BigQuery, DocumentDB, and DynamoDB via asyncdb drivers
- [ ] `MultiQS.query()` and `QueryHandler` use `DESTINATION_REGISTRY` instead of hardcoded `TableOutput` dispatch
- [ ] Existing `tableOutput`/`TableOutput` YAML configs continue to work without modification (backward compatibility)
- [ ] Credential values can be either literal values or navconfig variable names that get resolved at runtime
- [ ] All unit tests pass (`pytest tests/ -v -k destination`)
- [ ] No breaking changes to existing MultiQuery pipelines
- [ ] Each destination returns the original DataFrame (pass-through) so multiple destinations can chain

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# MultiQuery orchestrator
from querysource.queries.multi import MultiQS  # verified: querysource/queries/multi/__init__.py:53

# Existing TableOutput
from querysource.outputs.tables import TableOutput  # verified: querysource/outputs/tables/__init__.py:1
from querysource.outputs.tables.TableOutput.abstract import AbstractOutput  # verified: querysource/outputs/tables/TableOutput/abstract.py:7
from querysource.outputs.tables.TableOutput.postgres import PgOutput  # verified: querysource/outputs/tables/TableOutput/postgres.py:40 (ReflectionHelper), actual PgOutput class further down
from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput  # verified: querysource/outputs/tables/TableOutput/bigquery.py:9
from querysource.outputs.tables.TableOutput.mongodb import MongoDBOutput  # verified: querysource/outputs/tables/TableOutput/mongodb.py
from querysource.outputs.tables.TableOutput.documentdb import DocumentDBOutput  # verified: querysource/outputs/tables/TableOutput/documentdb.py

# Exceptions
from querysource.exceptions import DataNotFound, DriverError, OutputError  # verified: querysource/outputs/tables/TableOutput/table.py:5-9

# Logging
from navconfig.logging import logging  # verified: used across all modules

# AsyncDB
from asyncdb import AsyncDB  # verified: querysource/connections.py usage pattern

# S3 (external, navigator package)
from navigator.utils.file.s3 import S3FileManager  # verified: navigator/navigator/utils/file/s3.py:35
```

### Existing Class Signatures

```python
# querysource/outputs/tables/TableOutput/table.py
class TableOutput:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 20
        self._backend = 'pandas'  # line 21
        self.data = data  # line 22
        self._pk: list = []  # line 23
        self._engine = None  # line 25
        self.flavor: str = kwargs.pop('flavor', 'postgresql')  # line 29
        self._truncate: bool = kwargs.get('truncate', False)  # line 30

    async def table_output(self, elem, datasource: pd.DataFrame):  # line 53
    async def run(self):  # line 118
        # Returns self.data (the original data)

# querysource/outputs/tables/TableOutput/abstract.py
class AbstractOutput(metaclass=ABCMeta):
    def __init__(self, parent, dsn=None, do_update=True, only_update=False, external=False, **kwargs):  # line 13
    @property
    def is_external(self) -> bool:  # line 41
    @abstractmethod
    def connect(self):  # line 56
    @abstractmethod
    def db_upsert(self, table, conn, keys, data_iter):  # line 63
    @abstractmethod
    def write(self, table, schema, data, on_conflict='replace', pk=None):  # line 77
    @abstractmethod
    async def close(self):  # line 101

# querysource/queries/multi/__init__.py — Output dispatch (lines 378-385)
# Current hardcoded pattern:
#   if _output:
#       for step in _output:
#           for step_name, component in step.items():
#               if step_name in ('tableOutput', 'TableOutput'):
#                   obj = TableOutput(data=result, **component)
#                   result = await obj.run()

# querysource/handlers/multi.py — Output dispatch (lines 342-350)
# Same hardcoded pattern duplicated in the handler.

# navigator/navigator/utils/file/s3.py
class S3FileManager(FileManagerInterface):
    def __init__(self, bucket_name=None, aws_id="default", region_name=None, prefix="", **kwargs):  # line 46
    async def upload_file(self, source: Union[BinaryIO, Path], destination: str) -> FileMetadata:  # line 212
    async def download_file(self, source: str, destination: Union[Path, BinaryIO]) -> Path:  # line 383
    async def list_files(self, path: str = "", pattern: str = "*") -> List[FileMetadata]:  # line 164
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `AbstractDestination` | navconfig | `from navconfig import config` | pattern used across codebase |
| `DESTINATION_REGISTRY` | `MultiQS.query()` | replace lines 378-385 | `querysource/queries/multi/__init__.py:378` |
| `DESTINATION_REGISTRY` | `QueryHandler` | replace lines 342-350 | `querysource/handlers/multi.py:342` |
| `ToSharepoint` | Microsoft Graph SDK | `msgraph-sdk`, `azure-identity` | external dependency |
| `ToS3` | `S3FileManager` | `navigator.utils.file.s3` | `navigator/navigator/utils/file/s3.py:35` |
| `TableDestination` | `AbstractOutput` engines | PgOutput, BigQueryOutput, etc. | `querysource/outputs/tables/TableOutput/` |
| `DWHDestination` | `asyncdb.AsyncDB` | driver-based connections | `querysource/connections.py` |

### Configuration References

Navconfig variables used for credential resolution:

| Variable | Used By | Verified |
|---|---|---|
| `SHAREPOINT_APP_ID` | ToSharepoint | `flowtask/flowtask/conf.py:356` |
| `SHAREPOINT_APP_SECRET` | ToSharepoint | `flowtask/flowtask/conf.py:357` |
| `SHAREPOINT_TENANT_ID` | ToSharepoint | `flowtask/flowtask/conf.py:358` |
| `AWS_ACCESS_KEY_ID` | ToS3 | common AWS env var |
| `AWS_SECRET_ACCESS_KEY` | ToS3 | common AWS env var |
| `AWS_REGION_NAME` | ToS3 | common AWS env var |
| `default_dsn` | Table (pg) | `querysource/conf.py` |
| `BIGQUERY_CREDENTIALS` | DWH (bigquery) | `querysource/datasources/drivers/bigquery.py` |

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.outputs.destinations`~~ — this module does NOT exist yet; it will be created by this feature
- ~~`querysource.outputs.tables.TableOutput.table.TableOutput.register()`~~ — TableOutput has no registration mechanism
- ~~`querysource.queries.multi.get_output_module()`~~ — there is no dynamic output loader; only `get_operator_module()` and `get_transform_module()` exist
- ~~`AbstractDestination`~~ — does not exist yet; will be created
- ~~`querysource.interfaces.sharepoint`~~ — no SharePoint code exists in querysource
- ~~`querysource.interfaces.s3`~~ — no S3 code exists in querysource
- ~~`TableOutput.method`~~ — TableOutput has no `method` attribute; it uses `if_exists` for write mode
- ~~`flowtask.interfaces.Sharepoint.upload_dataframe()`~~ — SharepointClient has `upload_files()` but no direct DataFrame upload method

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Follow the `AbstractOutput` pattern from `querysource/outputs/tables/TableOutput/abstract.py` for engine-based backends.
- Use async-first design throughout — all I/O must be async.
- Credential resolution: check if a value looks like a navconfig variable name (ALL_CAPS_SNAKE_CASE) and resolve it via `navconfig.config.get()`. If the value doesn't match that pattern, use it as a literal.
- Each destination's `run()` method must return the original DataFrame unchanged for pipeline chaining.
- Use `logging.getLogger('QS.Output.<ClassName>')` consistent with TableOutput.

### YAML Configuration Examples

```yaml
# SharePoint destination
Output:
  - ToSharepoint:
      credentials:
        client_id: SHAREPOINT_APP_ID
        client_secret: SHAREPOINT_APP_SECRET
        tenant_id: SHAREPOINT_TENANT_ID
        site: Roadshows
      destination:
        filename: "2025 Events Master Schedule.xlsx"
        directory: "Shared Documents/General/Schedule"

# S3 destination
Output:
  - ToS3:
      credentials:
        region_name: "AWS_REGION_NAME"
        bucket: "AWS_PLACER_BUCKET"
        aws_key: "AWS_ACCESS_KEY_ID"
        aws_secret: "AWS_SECRET_ACCESS_KEY"
      destination:
        file: "metrics_2024-12-09_0003.csv.gz"
        directory: "placer-analytics/bulk-export/monthly-weekly/2024-12-09/metrics/"

# Table destination
Output:
  - Table:
      driver: pg
      schema: troc
      table: stores
      method: append
      pk:
        - store_id

# DWH destination
Output:
  - DWH:
      driver: bigquery
      schema: analytics
      table: daily_metrics
      method: upsert
      pk:
        - date
        - store_id

# Multiple destinations in one pipeline
Output:
  - Table:
      driver: pg
      schema: troc
      table: stores
      method: upsert
      pk:
        - store_id
  - ToS3:
      credentials:
        bucket: "AWS_BACKUP_BUCKET"
      destination:
        file: "stores_backup.parquet"
        directory: "backups/daily/"
```

### Known Risks / Gotchas

- **SharePoint Graph SDK auth complexity**: Microsoft Graph authentication has multiple modes (client credentials, user credentials, on-behalf-of). Start with `ClientSecretCredential` (app-only) as the simplest path.
- **Large file uploads**: SharePoint has a 4MB threshold for simple PUT vs. resumable upload sessions. The Flowtask code handles this — port the logic.
- **S3 multipart uploads**: `S3FileManager` auto-handles multipart for files >100MB, but most DataFrames will be under this threshold.
- **Table auto-creation**: For the `Table` destination, auto-creating tables requires mapping pandas dtypes to SQL column types. The existing `PgOutput` with SQLAlchemy reflection handles this for PostgreSQL; other drivers may need explicit dtype mapping.
- **DWH driver availability**: BigQuery, DocumentDB, and DynamoDB drivers must be installed. These are optional dependencies — handle `ImportError` gracefully.
- **Backward compatibility**: The existing `tableOutput`/`TableOutput` names in YAML must continue to work exactly as before. The `TableOutputAdapter` wrapper ensures this.
- **Thread safety**: `MultiQS` uses threads for source execution. Destination execution happens after all sources complete, on the main event loop, so thread safety is not a concern for destinations.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `msgraph-sdk` | `>=1.20.0` | Microsoft Graph API for SharePoint uploads |
| `azure-identity` | `>=1.18.0` | Azure credential classes for Graph auth |
| `msal` | `>=1.30.0` | Microsoft Authentication Library |
| `aioboto3` | `>=12.0` | Async S3 client (already used by navigator) |
| `openpyxl` | `>=3.1.0` | DataFrame → Excel conversion |
| `asyncdb` | (existing) | Database driver management |
| `navconfig` | (existing) | Credential variable resolution |

---

## 8. Open Questions

- [x] Should `ToSharepoint` support both Excel and CSV output formats based on filename extension, or always default to Excel? — *Owner: Jesus*: both based on filename extension.
- [ ] For the `Table` destination, should auto-table-creation use SQLAlchemy DDL (via the existing PgOutput reflection) or raw CREATE TABLE statements via asyncdb? — *Owner: Jesus*: statements via AsyncDB, because we are expecting using Table with other drivers as DocumentDB or BigQuery, auto-table-creation need to be agnostic to databse driver.
- [ ] Should `DWH` be a separate destination class or a mode/flag on the `Table` destination (since both write DataFrames to databases)? — *Owner: Jesus*: Yes
- [ ] What is the desired behavior when a destination fails mid-pipeline with multiple destinations? Should subsequent destinations still execute, or should the pipeline abort? — *Owner: Jesus*: still execute.
- [ ] Should credentials in the `Table` destination accept a `dsn` string as an alternative to individual driver/host/port params? — *Owner: Jesus*: Yes.
- [ ] For `ToS3`, should the output format (CSV, Parquet, Excel) be inferred from the filename extension or require an explicit `format` parameter? — *Owner: Jesus*: format can be provided, infer from filename if missing.

---

## Worktree Strategy

- **Isolation unit**: `per-spec` — all tasks run sequentially in one worktree.
- **Rationale**: The modules have dependencies (Module 2 depends on Module 1, Modules 3-6 depend on Module 1+2, Module 7 depends on all). Sequential execution avoids merge conflicts in shared files like `__init__.py` and the dispatch code.
- **Cross-feature dependencies**: FEAT-093 (MultiQuery New Sources) should ideally be merged first since it introduces the `ThreadSource` base class pattern and may modify `MultiQS.query()`. However, this spec's changes to the output dispatch (lines 378-385) are in a different code section than FEAT-093's source additions, so parallel development is possible with a merge coordination step.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-19 | Jesus Lara | Initial draft |
