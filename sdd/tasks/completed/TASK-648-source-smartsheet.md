# TASK-648: SourceSmartSheet Component

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 5: SourceSmartSheet. Downloads a SmartSheet sheet as
Excel via the SmartSheet REST API and returns it as a pandas DataFrame.
Adapted from the flowtask `SmartSheetClient` interface.

---

## Scope

- Create `SourceSmartSheet` class inheriting from `ThreadSource`.
- Authenticate via Bearer token (SmartSheet API key from navconfig or explicit config).
- Fetch a sheet by `file_id` (sheet ID) from `https://api.smartsheet.com/2.0/sheets/{id}`.
- Download the sheet content as Excel and parse into a DataFrame.
- Handle API rate limits (429) and authentication errors.
- Write unit tests with mocked API responses.

**NOT in scope**: Attachment downloads, row filtering, pagination of large sheets.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/smartsheet.py` | CREATE | SourceSmartSheet implementation |
| `tests/test_source_smartsheet.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .base import ThreadSource                # created by TASK-644
import pandas as pd                            # verified: file.py:8
from io import BytesIO                         # verified: file.py:7
import aiohttp                                 # verified: already a project dependency
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

### Reference: Flowtask SmartSheetClient
```python
# flowtask/flowtask/interfaces/smartsheet.py
# Key patterns to adapt:
#   - Auth: Bearer token via SMARTSHEET_API_KEY env var
#   - URL: https://api.smartsheet.com/2.0/sheets/{sheet_id}
#   - Headers: {"Authorization": "Bearer {api_key}", "Accept": "application/vnd.ms-excel"}
#   - SSL context: TLS 1.2+ (ssl.SSLContext with PROTOCOL_TLS_CLIENT)
#   - Download: GET {url} with Accept: application/vnd.ms-excel → binary Excel content
#   - aiohttp session with custom SSL context
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.smartsheet`~~ — this task creates it
- ~~`querysource.integrations.smartsheet`~~ — no such module
- ~~`smartsheet` Python SDK~~ — we use the REST API directly, not the official SDK

---

## Implementation Notes

### Pattern to Follow

```python
import ssl
import aiohttp

class SourceSmartSheet(ThreadSource):
    BASE_URL = "https://api.smartsheet.com/2.0/sheets/"

    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._api_key = self.resolve_credential(
            'api_key', creds.get('api_key', 'SMARTSHEET_API_KEY')
        )
        source = options.get('source', {})
        self._file_id = source.get('file_id')

    async def fetch(self) -> pd.DataFrame:
        url = f"{self.BASE_URL}{self._file_id}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/vnd.ms-excel"
        }
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
                if resp.status == 429:
                    raise RuntimeError("SmartSheet API rate limit exceeded")
                resp.raise_for_status()
                content = await resp.read()

        df = pd.read_excel(BytesIO(content), engine="openpyxl")
        df.infer_objects()
        return df
```

### Key Constraints
- `aiohttp` is already a project dependency — no new dependencies needed.
- SmartSheet API returns the sheet as Excel when `Accept: application/vnd.ms-excel` is set.
- If no credentials are provided, default to resolving `SMARTSHEET_API_KEY` from navconfig.
- Use TLS 1.2+ for security (matching flowtask pattern).

### References in Codebase
- `flowtask/flowtask/interfaces/smartsheet.py` — reference implementation to adapt
- `querysource/queries/multi/sources/file.py` — Excel parsing patterns

---

## Acceptance Criteria

- [ ] `SourceSmartSheet` class at `querysource/queries/multi/sources/smartsheet.py`
- [ ] Inherits from `ThreadSource`
- [ ] Authenticates via Bearer token
- [ ] Downloads sheet as Excel and returns DataFrame
- [ ] Defaults to `SMARTSHEET_API_KEY` from navconfig if no credentials provided
- [ ] Handles 429 rate limit errors
- [ ] Unit tests pass: `pytest tests/test_source_smartsheet.py -v`
- [ ] No linting errors

---

## Test Specification

```python
# tests/test_source_smartsheet.py
import asyncio
import pytest
import pandas as pd
from unittest.mock import patch, AsyncMock, MagicMock
from io import BytesIO
from querysource.queries.multi.sources.smartsheet import SourceSmartSheet
from querysource.queries.multi.sources.base import ThreadSource


class TestSourceSmartSheet:
    def test_inherits_thread_source(self):
        assert issubclass(SourceSmartSheet, ThreadSource)

    def test_parses_config(self):
        options = {"source": {"file_id": 12345}}
        source = SourceSmartSheet("ss_test", options, None, asyncio.Queue())
        assert source._file_id == 12345

    def test_default_credential_resolution(self):
        options = {"source": {"file_id": 12345}}
        source = SourceSmartSheet("ss_test", options, None, asyncio.Queue())
        # Should attempt to resolve SMARTSHEET_API_KEY via navconfig
        assert source._api_key is not None
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Read `flowtask/flowtask/interfaces/smartsheet.py`** for reference patterns
4. **Verify the Codebase Contract** — confirm ThreadSource exists
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-648-source-smartsheet.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
