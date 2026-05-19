# TASK-654: ToSharepoint Destination

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-653
**Assigned-to**: unassigned

---

## Context

This task implements the `ToSharepoint` destination component. It converts a pandas DataFrame into an Excel or CSV file and uploads it to a SharePoint document library using the Microsoft Graph SDK. The upload logic is ported from `flowtask/flowtask/interfaces/Sharepoint.py` (the `SharepointClient` class).

Implements spec §3 Module 3.

---

## Scope

- Implement `ToSharepoint(AbstractDestination)` in `querysource/outputs/destinations/sharepoint.py`
- Port the SharePoint upload logic from Flowtask's `SharepointClient`:
  - Authentication via `ClientSecretCredential` (app-only, client_id + client_secret + tenant_id)
  - Site and drive resolution
  - Directory path parsing and folder creation
  - Small file upload (≤4MB) via PUT
  - Large file upload (>4MB) via resumable upload sessions
- DataFrame conversion:
  - `.xlsx` filename → convert to Excel bytes using `openpyxl`
  - `.csv` filename → convert to CSV bytes
  - Infer format from `destination.filename` extension
- Register `ToSharepoint` in `DESTINATION_REGISTRY`
- Write unit tests with mocked Graph API calls

**NOT in scope**: Downloading from SharePoint (that's FEAT-093), OneDrive support, user-interactive auth flows.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/sharepoint.py` | CREATE | ToSharepoint implementation |
| `querysource/outputs/destinations/__init__.py` | MODIFY | Register ToSharepoint in DESTINATION_REGISTRY |
| `tests/test_destination_sharepoint.py` | CREATE | Unit tests |

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

# External libraries (must be installed)
from azure.identity import ClientSecretCredential  # external: azure-identity package
from msgraph import GraphServiceClient  # external: msgraph-sdk package
import openpyxl  # external: openpyxl package
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

### Flowtask Reference Code (Port From)
```python
# flowtask/flowtask/interfaces/Sharepoint.py — key patterns to port:
# - SharepointClient.__init__() handles credentials: client_id, client_secret, tenant_id, site
# - SharepointClient._create_credential() → ClientSecretCredential(tenant_id, client_id, client_secret)
# - SharepointClient._create_graph_client() → GraphServiceClient(credential, scopes)
# - SharepointClient._resolve_site() → get site info from Graph API
# - SharepointClient._resolve_drive() → get document library drive
# - SharepointClient._ensure_folder() → create folders recursively
# - SharepointClient._upload_small_file() → PUT for files ≤4MB
# - SharepointClient._upload_large_file() → resumable upload for files >4MB
# - Threshold: SMALL_FILE = 4 * 1024 * 1024 (4MB)
# - Chunk size: 10 * 1024 * 1024 (10MB)
```

### Does NOT Exist
- ~~`querysource.interfaces.sharepoint`~~ — no SharePoint code exists in querysource
- ~~`SharepointClient` in querysource~~ — only exists in flowtask
- ~~`AbstractDestination.upload()`~~ — no upload method on the base class
- ~~`office365.sharepoint`~~ — the old Office365 library is NOT used; use msgraph-sdk instead

---

## Implementation Notes

### Pattern to Follow

The YAML configuration for ToSharepoint:
```yaml
- ToSharepoint:
    credentials:
      client_id: SHAREPOINT_APP_ID
      client_secret: SHAREPOINT_APP_SECRET
      tenant_id: SHAREPOINT_TENANT_ID
      site: Roadshows
    destination:
      filename: "2025 Events Master Schedule.xlsx"
      directory: "Shared Documents/General/Schedule"
```

Key implementation flow:
1. `__init__`: Parse credentials and destination from kwargs, resolve credentials via `resolve_credentials()`
2. `run()`:
   a. Convert DataFrame to bytes (Excel or CSV based on filename extension)
   b. Authenticate with Graph API using `ClientSecretCredential`
   c. Resolve site and drive
   d. Ensure target folder exists (create if needed)
   e. Upload file (small or large path based on size)
   f. Return original `self.data`

### Key Constraints
- Must be fully async — use `httpx` or `aiohttp` for any HTTP calls not covered by Graph SDK
- The `site` credential field is the SharePoint site name, not the full URL
- Graph API scopes: `["https://graph.microsoft.com/.default"]` for client credentials flow
- Handle both single DataFrame and dict of DataFrames (convert each to separate file if dict)
- `run()` must return original data unchanged

### References in Codebase
- `flowtask/flowtask/interfaces/Sharepoint.py` — source of upload logic to port
- `flowtask/flowtask/interfaces/O365Client.py` — parent class with auth logic
- `querysource/outputs/destinations/abstract.py` — base class (TASK-653)

---

## Acceptance Criteria

- [ ] `ToSharepoint` class exists at `querysource/outputs/destinations/sharepoint.py`
- [ ] Converts DataFrame to Excel (`.xlsx`) or CSV (`.csv`) based on filename extension
- [ ] Authenticates with Microsoft Graph using `ClientSecretCredential`
- [ ] Uploads file to correct SharePoint site/drive/folder
- [ ] Handles small files (≤4MB) and large files (>4MB) with appropriate upload method
- [ ] Credentials resolved from navconfig variables when ALL_CAPS pattern detected
- [ ] Registered in `DESTINATION_REGISTRY` under `"ToSharepoint"`
- [ ] Returns original DataFrame after upload (pass-through)
- [ ] All tests pass: `pytest tests/test_destination_sharepoint.py -v`
- [ ] No linting errors: `ruff check querysource/outputs/destinations/sharepoint.py`

---

## Test Specification

```python
# tests/test_destination_sharepoint.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.sharepoint import ToSharepoint


@pytest.fixture
def sample_df():
    return pd.DataFrame({"col_a": [1, 2, 3], "col_b": ["x", "y", "z"]})


@pytest.fixture
def sharepoint_config():
    return {
        "credentials": {
            "client_id": "test-id",
            "client_secret": "test-secret",
            "tenant_id": "test-tenant",
            "site": "TestSite",
        },
        "destination": {
            "filename": "output.xlsx",
            "directory": "Shared Documents/Reports",
        },
    }


class TestToSharepoint:
    def test_initialization(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        assert dest.data is sample_df

    def test_excel_conversion(self, sample_df, sharepoint_config):
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.xlsx")
        assert len(file_bytes) > 0

    def test_csv_conversion(self, sample_df, sharepoint_config):
        sharepoint_config["destination"]["filename"] = "output.csv"
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        file_bytes = dest._convert_dataframe(sample_df, "output.csv")
        assert b"col_a" in file_bytes

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, sharepoint_config):
        """run() returns original DataFrame after upload."""
        dest = ToSharepoint(data=sample_df, **sharepoint_config)
        with patch.object(dest, "_upload_to_sharepoint", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 is in `sdd/tasks/completed/`
3. **Read the Flowtask Sharepoint code** at `../flowtask/flowtask/interfaces/Sharepoint.py` to understand the upload patterns to port
4. **Verify the Codebase Contract** — confirm AbstractDestination exists (from TASK-653)
5. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
6. **Implement** following the scope, codebase contract, and notes above
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-654-to-sharepoint-destination.md`
9. **Update index** → `"done"`
10. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any

---
**Completed by**: SDD Worker (Claude)
**Date**: 2026-05-19
**Notes**: Implemented ToSharepoint with Excel/CSV conversion, ClientSecretCredential auth, site/drive resolution, folder creation, small-file PUT and large-file resumable upload. All 10 unit tests pass.
**Deviations from spec**: none
