# TASK-803: GraphDriveSource — shared Graph drive-item base class

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-802
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1, part two (G2). `SharepointSource.fetch()` mixes
drive-agnostic Graph code (imports, `/shares`, download, parsing) with
SharePoint site/library resolution. This task adds `GraphDriveSource(ThreadSource)`
to `graph.py` as a template method. TASK-804 (SharePoint) and TASK-807
(OneDrive) then implement only `_resolve_drive_item` and, if needed,
`_credential`.

---

## Scope

- Add `GraphDriveSource` to `graph.py`: shared `source` parsing in
  `__init__`, `_encode_share_url` and `_parse_file_content` moved **verbatim**
  from `sharepoint.py`, `_import_graph`, `_credential`, `_owns_credential`,
  `_resolve_shared_item`, the abstract `_resolve_drive_item`, `_download`,
  and the `fetch` template.
- `fetch()` builds the client **inside** `kiota_platform_version_patch()` and
  closes only credentials it owns.
- Write unit tests using a concrete test subclass and a mocked Graph client.

**NOT in scope**: editing `sharepoint.py` (TASK-804) and OneDrive specifics (TASK-807).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/graph.py` | MODIFY | append `GraphDriveSource` |
| `tests/multi/sources/test_graph_drive_source.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .base import ThreadSource            # verified: querysource/queries/multi/sources/base.py:14
from .file import excel_based             # verified: querysource/queries/multi/sources/sharepoint.py:17
from abc import abstractmethod
import asyncio, pandas as pd
from io import BytesIO
from pathlib import Path
from aiohttp import web
# inside methods only (lazy, optional extra):
from azure.identity.aio import ClientSecretCredential   # verified: sharepoint.py:189
from msgraph import GraphServiceClient                  # verified: sharepoint.py:190
import httpx                                            # verified: sharepoint.py:198
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                                   # :14
    def __init__(self, name, options, request, queue) -> None:              # :25 (pops options['masks'] into self._masks :40-42)
    def resolve_masks(self, text: str) -> str:                               # :72
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                                   # :116

# querysource/queries/multi/sources/sharepoint.py (source of the code to MOVE)
    source parsing                          # :108-125 (filename, directory, sheet_name=0, pd_args={}, url from source or options, masks merge)
    def _encode_share_url(url) -> str       # :127-140 (staticmethod)
    def _parse_file_content(self, content)  # :142-179
    ImportError messages                    # :191-203 ("Install msgraph-sdk and azure-identity for SharePoint support: pip install querysource[sharepoint]")
    /shares resolution                      # :221-236
    download                                # :312-330 (@microsoft.graph.downloadUrl, httpx.AsyncClient(timeout=600.0))
    credential.close() in finally           # :331-332
# from TASK-802 (graph.py): kiota_platform_version_patch(), StaticTokenCredential
```

### Does NOT Exist
- ~~`GraphDriveSource`~~ before this task.
- ~~A shared Graph client factory elsewhere~~: `ToSharepoint._build_graph_client` (destinations) is NOT to be reused or modified.
- ~~`ThreadSource.prepare`~~: added by TASK-806. Do not reference it here.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/graph.py", "action": "MODIFY"},
    {"path": "tests/multi/sources/test_graph_drive_source.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource",
    "sym:querysource/queries/multi/sources/sharepoint.py#SharepointSource._parse_file_content",
    "sym:querysource/queries/multi/sources/sharepoint.py#SharepointSource._encode_share_url"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Introspection (S10)**: `extract_source_schema` regex-walks `__init__`
  bodies across the MRO. Keep literal `source.get('filename', '')` and similar
  calls, with `source = options.get('source', {})`, inside
  `GraphDriveSource.__init__`. Never hide them behind a helper.
- `_extra_name` defaults to `"sharepoint"`, and the ImportError text must stay
  **byte-identical** to `sharepoint.py:191-203` when `_extra_name ==
  "sharepoint"`. `tests/test_source_sharepoint.py` matches `"msgraph-sdk"`.
- `fetch()` order: `_import_graph()` → `credential = await self._credential()`
  → inside `kiota_platform_version_patch()` build
  `GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])`
  → shared or path item → `_download` → `_parse_file_content`. In `finally`,
  close the credential only if `_owns_credential(credential)`.
- The delegated scope for `StaticTokenCredential` is irrelevant (the token is
  already scoped). Keep the one `.default` scopes list.

---

## Implementation Blueprint

### Steps (in order)
1. Append the class below to `graph.py` — *why*: spec §3 M1 fixes these names.
2. Move `_encode_share_url` and `_parse_file_content` bodies **verbatim** from `sharepoint.py:127-179` — *why*: SharePoint behaviour must not change (G2).
3. Complete `fetch`, `_resolve_shared_item` and `_download` from `sharepoint.py:188-332` — *why*: same messages and timeouts.
4. Write the tests.

### `querysource/queries/multi/sources/graph.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'class StaticTokenCredential' querysource/queries/multi/sources/graph.py — created by TASK-802)
# AFTER — append below the StaticTokenCredential class (end of file); add imports at the top:
#   import asyncio; from abc import abstractmethod; from io import BytesIO; from pathlib import Path
#   import pandas as pd; from aiohttp import web; from .base import ThreadSource; from .file import excel_based

class GraphDriveSource(ThreadSource):
    """Download one CSV/Excel file through Microsoft Graph and return a DataFrame."""

    _extra_name: str = "sharepoint"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        source = options.get('source', {})
        self._filename: str = source.get('filename', '')
        self._directory: str = source.get('directory', '')
        self._sheet_name = source.get('sheet_name', 0)
        self._pd_args: dict = source.get('pd_args', {})
        self._url: str = source.get('url', '') or options.get('url', '') or ''
        _source_masks = source.get('masks', {})
        if _source_masks:
            self._masks = {**self._masks, **_source_masks}
        # subclasses set these (app-only credential material):
        self._tenant_id: str = ''
        self._client_id: str = ''
        self._client_secret: str = ''

    @staticmethod
    def _encode_share_url(url: str) -> str:
        """'u!' + unpadded base64url(url) — moved verbatim from sharepoint.py:127-140."""
        # FILL IN: move body verbatim

    def _parse_file_content(self, content: bytes) -> pd.DataFrame:
        """Moved verbatim from sharepoint.py:142-179."""
        # FILL IN: move body verbatim

    def _import_graph(self) -> tuple[type, type]:
        """Return (ClientSecretCredential[aio], GraphServiceClient) or raise ImportError naming the extra."""
        # FILL IN: lazy imports + messages as sharepoint.py:188-203, with querysource[{self._extra_name}]

    async def _credential(self) -> Any:
        """Default app-only credential from self._tenant_id/_client_id/_client_secret."""
        # FILL IN: ClientSecretCredential(self._tenant_id, self._client_id, self._client_secret)

    def _owns_credential(self, credential: Any) -> bool:
        return not isinstance(credential, StaticTokenCredential)

    async def _resolve_shared_item(self, client: Any) -> Any:
        """GET /shares/{u!…}/driveItem; RuntimeError if unresolved; infer filename."""
        # FILL IN: move sharepoint.py:221-236 (message: "Could not resolve SharePoint file from url: …")

    @abstractmethod
    async def _resolve_drive_item(self, client: Any) -> Any:
        """Path-mode resolution; subclasses call resolve_masks on directory/filename first."""

    async def _download(self, item: Any) -> bytes:
        """@microsoft.graph.downloadUrl via httpx.AsyncClient(timeout=600.0)."""
        # FILL IN: move sharepoint.py:312-330 (message: "Could not obtain download URL for file '…'.")

    async def fetch(self) -> pd.DataFrame:
        """Template: imports → credential → client (patched) → item → download → parse."""
        # FILL IN: order and ownership per Key Constraints; close only owned credentials in finally
```
**Why this shape**: the template method keeps a single download/parse path
for both drives. `_owns_credential` implements S9's "close only credentials
the base created". The `/shares` error message keeps the word "SharePoint"
for backward compatibility, and OneDrive may override it later (not required).

### FILL IN checklist
- [ ] `_encode_share_url` / `_parse_file_content`: verbatim moves.
- [ ] `_import_graph`: messages identical for `_extra_name == "sharepoint"`.
- [ ] `_resolve_shared_item` / `_download`: verbatim semantics.
- [ ] `fetch`: ordering, patch scope, owned-credential close.

---

## Acceptance Criteria

- [ ] A concrete test subclass fetches CSV and Excel through mocked Graph and httpx (both url and path modes).
- [ ] `StaticTokenCredential` is never closed. `ClientSecretCredential` is closed on success and on error.
- [ ] `platform.version` is unchanged after `fetch()` (success and failure).
- [ ] `ruff check querysource/queries/multi/sources/graph.py` is clean.

## Validation Commands

- `pytest tests/multi/sources/test_graph_drive_source.py -q`

---

## Test Specification

```python
# tests/multi/sources/test_graph_drive_source.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.queries.multi.sources.graph import GraphDriveSource, StaticTokenCredential


class _Probe(GraphDriveSource):
    async def _resolve_drive_item(self, client):
        return self._item


def _probe(**source):
    return _Probe("p", {"source": source}, None, asyncio.Queue())


def test_encode_share_url_unpadded():
    assert _probe(filename="x.csv")._encode_share_url("https://a/b").startswith("u!")


def test_graph_import_error_names_extra():
    ...  # FILL IN: patch.dict(sys.modules, msgraph/azure → None); message contains 'msgraph-sdk' and 'querysource[sharepoint]'


async def test_fetch_closes_only_owned_credentials():
    ...  # FILL IN: _credential returns StaticTokenCredential → close not awaited; ClientSecretCredential mock → closed


async def test_fetch_path_mode_csv_roundtrip():
    ...  # FILL IN: mock item.additional_data downloadUrl + httpx.AsyncClient → DataFrame with 2 rows
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-803 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

Seat: gpt-5.6-terra · codex · Attempts: 1 · 183s. Graph drive source base. Review fix d874113 (MagicMock name= test helper); feedback coder-feedback:c8ff7057c5e217ebdec5bce4. Merge-tier: 33 passed.
