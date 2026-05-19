# TASK-649: SourceS3 Component

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 6: SourceS3. Downloads a single file from an AWS S3 bucket
and returns it as a pandas DataFrame. Uses `aioboto3` for async S3 operations.
Supports CSV, compressed CSV (.gz), and Excel files.

---

## Scope

- Create `SourceS3` class inheriting from `ThreadSource`.
- Authenticate with AWS using explicit credentials or navconfig-resolved env vars.
- Download a single file from an S3 bucket (by bucket + directory + file).
- Decompress `.gz` files before parsing.
- Parse CSV and Excel files into pandas DataFrames.
- Handle `aioboto3` as optional dependency with clear error message.
- Write unit tests with mocked S3 responses.

**NOT in scope**: S3 file listing, multi-file download, upload, multipart download.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/s3.py` | CREATE | SourceS3 implementation |
| `tests/test_source_s3.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .base import ThreadSource                # created by TASK-644
import pandas as pd                            # verified: file.py:8
from io import BytesIO                         # verified: file.py:7
import gzip                                    # verified: file.py:6
from pathlib import Path                       # verified: file.py:4
```

### Existing Signatures to Use
```python
# ThreadSource base (created by TASK-644):
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue): ...
    def resolve_credential(self, key: str, value: str) -> str: ...
    async def fetch(self) -> pd.DataFrame: ...  # abstract — implement this
    def run(self) -> None: ...  # inherited
```

### Reference: Navigator S3FileManager
```python
# navigator/navigator/utils/file/s3.py
# Key patterns to adapt:
#   - aioboto3 async client: async with session.client('s3', ...) as client
#   - download_file(): client.get_object(Bucket=bucket, Key=key)
#   - Content read: response['Body'].read() → bytes
#   - Credential resolution from navconfig AWS_CREDENTIALS dict
```

### Reference: Flowtask Boto3Client
```python
# flowtask/flowtask/interfaces/Boto3Client.py
# Key patterns:
#   - Credentials: aws_key, aws_secret, region_name, bucket from config
#   - S3 key construction: directory + "/" + filename
#   - get_s3_object(): client.get_object(Bucket=bucket, Key=key)
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.s3`~~ — this task creates it
- ~~`querysource.integrations.s3`~~ — no such module
- ~~`querysource.utils.s3`~~ — no such module
- ~~`boto3` usage in querysource~~ — querysource does not currently use boto3/aioboto3

---

## Implementation Notes

### Pattern to Follow

```python
class SourceS3(ThreadSource):
    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._region = self.resolve_credential('region_name', creds.get('region_name', 'AWS_REGION_NAME'))
        self._bucket = self.resolve_credential('bucket', creds.get('bucket', 'AWS_S3_BUCKET'))
        self._aws_key = self.resolve_credential('aws_key', creds.get('aws_key', 'AWS_ACCESS_KEY_ID'))
        self._aws_secret = self.resolve_credential('aws_secret', creds.get('aws_secret', 'AWS_SECRET_ACCESS_KEY'))
        source = options.get('source', {})
        self._file = source.get('file')
        self._directory = source.get('directory', '')

    async def fetch(self) -> pd.DataFrame:
        try:
            import aioboto3
        except ImportError:
            raise ImportError("Install aioboto3 for S3 source support")

        session = aioboto3.Session()
        s3_key = f"{self._directory.rstrip('/')}/{self._file}" if self._directory else self._file

        async with session.client(
            's3',
            region_name=self._region,
            aws_access_key_id=self._aws_key,
            aws_secret_access_key=self._aws_secret
        ) as client:
            response = await client.get_object(Bucket=self._bucket, Key=s3_key)
            content = await response['Body'].read()

        # Decompress if needed
        if self._file.endswith('.gz'):
            content = gzip.decompress(content)
            inner_name = self._file[:-3]  # strip .gz
        else:
            inner_name = self._file

        # Parse based on file extension
        buf = BytesIO(content)
        if inner_name.endswith(('.xlsx', '.xls')):
            engine = 'xlrd' if inner_name.endswith('.xls') else 'openpyxl'
            df = pd.read_excel(buf, engine=engine, na_values=["NULL", "TBD"], keep_default_na=False)
        else:
            df = pd.read_csv(buf, na_values=["NULL", "TBD"], keep_default_na=False)
        df.infer_objects()
        return df
```

### Key Constraints
- `aioboto3` is an optional dependency — wrap import in try/except.
- S3 key = directory + "/" + filename. Strip trailing slashes from directory.
- Support `.csv`, `.csv.gz`, `.xlsx`, `.xls` file formats.
- Download entire file into memory (BytesIO). For very large files this may be an issue — log a warning if content > 100MB.

### References in Codebase
- `navigator/navigator/utils/file/s3.py` — async S3 pattern to follow
- `flowtask/flowtask/interfaces/Boto3Client.py` — credential resolution pattern
- `querysource/queries/multi/sources/file.py` — file decompression + CSV/Excel parsing

---

## Acceptance Criteria

- [ ] `SourceS3` class at `querysource/queries/multi/sources/s3.py`
- [ ] Inherits from `ThreadSource`
- [ ] Downloads a file from S3 using aioboto3
- [ ] Supports CSV, CSV.gz, and Excel files
- [ ] Decompresses `.gz` files before parsing
- [ ] Credentials resolve via navconfig variable names
- [ ] Graceful error on missing `aioboto3` dependency
- [ ] Unit tests pass: `pytest tests/test_source_s3.py -v`
- [ ] No linting errors

---

## Test Specification

```python
# tests/test_source_s3.py
import asyncio
import gzip
import pytest
import pandas as pd
from unittest.mock import patch, AsyncMock, MagicMock
from io import BytesIO
from querysource.queries.multi.sources.s3 import SourceS3
from querysource.queries.multi.sources.base import ThreadSource


class TestSourceS3:
    def test_inherits_thread_source(self):
        assert issubclass(SourceS3, ThreadSource)

    def test_parses_credentials(self):
        options = {
            "credentials": {
                "region_name": "us-east-1",
                "bucket": "my-bucket",
                "aws_key": "AKIATEST",
                "aws_secret": "secret123"
            },
            "source": {
                "file": "data.csv",
                "directory": "exports/"
            }
        }
        source = SourceS3("s3_test", options, None, asyncio.Queue())
        assert source._bucket == "my-bucket"
        assert source._file == "data.csv"
        assert source._directory == "exports/"

    def test_s3_key_construction(self):
        options = {
            "credentials": {"region_name": "us-east-1", "bucket": "b", "aws_key": "k", "aws_secret": "s"},
            "source": {"file": "data.csv", "directory": "path/to/"}
        }
        source = SourceS3("test", options, None, asyncio.Queue())
        # Key should be path/to/data.csv
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Read `navigator/navigator/utils/file/s3.py`** for async S3 patterns
4. **Verify the Codebase Contract** — confirm ThreadSource exists
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-649-source-s3.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
