# TASK-647: SourceSharepoint Component

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 4: SourceSharepoint. Downloads a single Excel/CSV file
from a SharePoint document library via Microsoft Graph API and returns it as
a pandas DataFrame. Adapted from the flowtask `SharepointClient` interface.

---

## Scope

- Create `SourceSharepoint` class inheriting from `ThreadSource`.
- Authenticate with Microsoft Graph using client credentials (client_id, client_secret, tenant_id).
- Locate a file in a SharePoint site's document library by directory + filename.
- Download the file and parse it as Excel or CSV into a pandas DataFrame.
- Support credential resolution via navconfig variable names.
- Handle import errors for `msgraph-sdk` gracefully (optional dependency).
- Write unit tests with mocked Graph API responses.

**NOT in scope**: File upload, multi-file download, search with wildcards.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/sharepoint.py` | CREATE | SourceSharepoint implementation |
| `tests/test_source_sharepoint.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .base import ThreadSource                # created by TASK-644
import pandas as pd                            # verified: file.py:8
from io import BytesIO                         # verified: file.py:7
from pathlib import Path                       # verified: file.py:4
```

### Existing Signatures to Use
```python
# ThreadSource base (created by TASK-644):
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue): ...
    def resolve_credential(self, key: str, value: str) -> str: ...
    async def fetch(self) -> pd.DataFrame: ...  # abstract — implement this
    def run(self) -> None: ...  # inherited — do NOT override
```

### Reference: Flowtask SharepointClient
```python
# flowtask/flowtask/interfaces/Sharepoint.py
# Key patterns to adapt:
#   - Auth: uses SHAREPOINT_APP_ID, SHAREPOINT_APP_SECRET, SHAREPOINT_TENANT_ID, SHAREPOINT_TENANT_NAME
#   - Site URL: https://{tenant}.sharepoint.com/sites/{site}/
#   - Graph SDK: msgraph.generated.models (DriveItem, etc.)
#   - File lookup: file_lookup() resolves by library + path
#   - Download: httpx streaming download to BytesIO
#   - _parse_directory_path(): splits "library/path" into library name + subfolder
#   - _resolve_drive(): case-insensitive library name matching
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.sharepoint`~~ — this task creates it
- ~~`querysource.integrations.sharepoint`~~ — no such module
- ~~`querysource.utils.graph_client`~~ — no such module

---

## Implementation Notes

### Pattern to Follow

The flowtask `SharepointClient` is ~1450 lines with upload, search, and many features.
For MultiQuery we only need the download path, simplified:

```python
class SourceSharepoint(ThreadSource):
    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._client_id = self.resolve_credential('client_id', creds.get('client_id', 'SHAREPOINT_APP_ID'))
        self._client_secret = self.resolve_credential('client_secret', creds.get('client_secret', 'SHAREPOINT_APP_SECRET'))
        self._tenant_id = self.resolve_credential('tenant_id', creds.get('tenant_id', 'SHAREPOINT_TENANT_ID'))
        self._site = creds.get('site', '')
        source = options.get('source', {})
        self._filename = source.get('filename')
        self._directory = source.get('directory', '')

    async def fetch(self) -> pd.DataFrame:
        try:
            from azure.identity.aio import ClientSecretCredential
            from msgraph import GraphServiceClient
        except ImportError:
            raise ImportError("Install msgraph-sdk and azure-identity for SharePoint support")

        credential = ClientSecretCredential(self._tenant_id, self._client_id, self._client_secret)
        client = GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])
        # 1. Get site by name
        # 2. Get drive (document library)
        # 3. Navigate to directory
        # 4. Download file content
        # 5. Parse as Excel/CSV into DataFrame
        ...
```

### Key Constraints
- `msgraph-sdk` and `azure-identity` are optional dependencies — wrap in try/except ImportError.
- File type detection: use the filename extension to decide `pd.read_excel()` vs `pd.read_csv()`.
- Download into `BytesIO` — no temp files on disk.
- The `site` credential field is NOT an env var — it's the SharePoint site name.

### References in Codebase
- `flowtask/flowtask/interfaces/Sharepoint.py` — reference implementation to adapt
- `querysource/queries/multi/sources/file.py` — Excel/CSV parsing patterns (excel_based tuple, engine detection)

---

## Acceptance Criteria

- [ ] `SourceSharepoint` class at `querysource/queries/multi/sources/sharepoint.py`
- [ ] Inherits from `ThreadSource`
- [ ] Authenticates via Microsoft Graph client credentials
- [ ] Downloads a single file from a SharePoint document library
- [ ] Parses Excel (.xlsx, .xls) and CSV files into DataFrames
- [ ] Credentials resolve via navconfig variable names
- [ ] Graceful error on missing `msgraph-sdk` dependency
- [ ] Unit tests pass: `pytest tests/test_source_sharepoint.py -v`
- [ ] No linting errors

---

## Test Specification

```python
# tests/test_source_sharepoint.py
import asyncio
import pytest
import pandas as pd
from unittest.mock import patch, AsyncMock, MagicMock
from querysource.queries.multi.sources.sharepoint import SourceSharepoint
from querysource.queries.multi.sources.base import ThreadSource


class TestSourceSharepoint:
    def test_inherits_thread_source(self):
        assert issubclass(SourceSharepoint, ThreadSource)

    def test_parses_credentials(self):
        options = {
            "credentials": {
                "client_id": "test_id",
                "client_secret": "test_secret",
                "tenant_id": "test_tenant",
                "site": "TestSite"
            },
            "source": {
                "filename": "test.xlsx",
                "directory": "Shared Documents"
            }
        }
        source = SourceSharepoint("sp_test", options, None, asyncio.Queue())
        assert source._client_id == "test_id"
        assert source._filename == "test.xlsx"

    def test_missing_msgraph_raises_import_error(self):
        # Test that missing optional dep gives clear message
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Read `flowtask/flowtask/interfaces/Sharepoint.py`** for reference patterns
4. **Read `querysource/queries/multi/sources/file.py`** for Excel/CSV parsing patterns
5. **Verify the Codebase Contract** — confirm ThreadSource exists with expected methods
6. **Implement** following the scope and notes above
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-647-source-sharepoint.md`
9. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
