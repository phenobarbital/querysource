# TASK-655: ToS3 Destination

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-653
**Assigned-to**: unassigned

---

## Context

This task implements the `ToS3` destination component. It converts a pandas DataFrame to a file (CSV, Parquet, or Excel based on filename extension) and uploads it to an AWS S3 bucket. It uses the `S3FileManager` from navigator or direct `aioboto3` for the upload.

Implements spec §3 Module 4.

---

## Scope

- Implement `ToS3(AbstractDestination)` in `querysource/outputs/destinations/s3.py`
- DataFrame conversion based on file extension:
  - `.csv` or `.csv.gz` → CSV (with optional gzip compression)
  - `.parquet` → Parquet via pyarrow
  - `.xlsx` → Excel via openpyxl
  - Default: CSV if extension is ambiguous
- S3 upload using `aioboto3` (direct, no navigator dependency required):
  - Construct full S3 key from `destination.directory` + `destination.file`
  - Create async S3 client with resolved credentials
  - Upload file bytes via `put_object` or multipart for large files
- Credential resolution: `region_name`, `bucket`, `aws_key`, `aws_secret` from config or navconfig variables
- Register `ToS3` in `DESTINATION_REGISTRY`
- Write unit tests with mocked S3 calls

**NOT in scope**: Downloading from S3 (that's FEAT-093), S3 bucket creation, S3 lifecycle policies.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/s3.py` | CREATE | ToS3 implementation |
| `querysource/outputs/destinations/__init__.py` | MODIFY | Register ToS3 in DESTINATION_REGISTRY |
| `tests/test_destination_s3.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# From TASK-653 (must be completed first)
from querysource.outputs.destinations.abstract import AbstractDestination  # created by TASK-653

# Exceptions
from querysource.exceptions import OutputError  # verified: querysource/outputs/tables/TableOutput/table.py:9

# Logging
from navconfig.logging import logging  # verified: used across all modules

# External libraries
import aioboto3  # external: aioboto3 package — async S3 client
```

### Existing Signatures to Use
```python
# AbstractDestination (created by TASK-653):
class AbstractDestination(ABC):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
    def resolve_credentials(self, credentials: dict) -> dict:
    @abstractmethod
    async def run(self) -> Union[dict, pd.DataFrame]:
    async def close(self) -> None:
```

### S3FileManager Reference (Optional — can use directly or via aioboto3)
```python
# navigator/navigator/utils/file/s3.py:35
class S3FileManager(FileManagerInterface):
    def __init__(self, bucket_name=None, aws_id="default", region_name=None, prefix="", **kwargs):  # line 46
    async def upload_file(self, source: Union[BinaryIO, Path], destination: str) -> FileMetadata:  # line 212
    # Multipart threshold: 100MB, chunk size: 10MB
```

### Does NOT Exist
- ~~`querysource.interfaces.s3`~~ — no S3 code exists in querysource
- ~~`querysource.outputs.destinations.s3`~~ — does not exist yet; this task creates it
- ~~`AbstractDestination.upload_file()`~~ — no upload method on the base class
- ~~`S3FileManager` in querysource~~ — only in navigator; prefer direct aioboto3 to avoid dependency

---

## Implementation Notes

### Pattern to Follow

YAML configuration:
```yaml
- ToS3:
    credentials:
      region_name: "AWS_REGION_NAME"
      bucket: "AWS_PLACER_BUCKET"
      aws_key: "AWS_ACCESS_KEY_ID"
      aws_secret: "AWS_SECRET_ACCESS_KEY"
    destination:
      file: "metrics_2024-12-09_0003.csv.gz"
      directory: "placer-analytics/bulk-export/monthly-weekly/2024-12-09/metrics/"
```

Key implementation flow:
1. `__init__`: Parse credentials and destination from kwargs, resolve credentials
2. `run()`:
   a. Convert DataFrame to bytes based on filename extension
   b. Construct S3 key: `directory/file` (strip leading/trailing slashes)
   c. Create aioboto3 session and S3 client with resolved credentials
   d. Upload via `put_object` (or multipart for very large files)
   e. Return original `self.data`

### Key Constraints
- Use `aioboto3` directly — do not import navigator's `S3FileManager` to avoid cross-project dependency
- Handle `.gz` extension: if filename ends with `.csv.gz`, gzip the CSV bytes before upload
- Content-Type header should match the file type (e.g., `text/csv`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)
- `run()` must return original data unchanged

---

## Acceptance Criteria

- [ ] `ToS3` class exists at `querysource/outputs/destinations/s3.py`
- [ ] Converts DataFrame to CSV, CSV.GZ, Parquet, or Excel based on filename extension
- [ ] Uploads to correct S3 bucket/key using resolved credentials
- [ ] Credentials resolved from navconfig variables when ALL_CAPS pattern detected
- [ ] Registered in `DESTINATION_REGISTRY` under `"ToS3"`
- [ ] Returns original DataFrame after upload (pass-through)
- [ ] All tests pass: `pytest tests/test_destination_s3.py -v`
- [ ] No linting errors: `ruff check querysource/outputs/destinations/s3.py`

---

## Test Specification

```python
# tests/test_destination_s3.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.s3 import ToS3


@pytest.fixture
def sample_df():
    return pd.DataFrame({"id": [1, 2], "value": [10.5, 20.3]})


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
            "directory": "exports/",
        },
    }


class TestToS3:
    def test_initialization(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        assert dest.data is sample_df

    def test_csv_conversion(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv")
        assert b"id" in file_bytes
        assert b"value" in file_bytes

    def test_gzip_conversion(self, sample_df, s3_config):
        s3_config["destination"]["file"] = "output.csv.gz"
        dest = ToS3(data=sample_df, **s3_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv.gz")
        # gzip magic bytes
        assert file_bytes[:2] == b'\x1f\x8b'

    def test_s3_key_construction(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        key = dest._build_s3_key()
        assert key == "exports/output.csv"

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, s3_config):
        dest = ToS3(data=sample_df, **s3_config)
        with patch.object(dest, "_upload_to_s3", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 is in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — confirm AbstractDestination exists (from TASK-653)
4. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-655-to-s3-destination.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
