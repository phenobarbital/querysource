# TASK-804: Rebase SharepointSource on GraphDriveSource (behaviour-preserving)

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-803
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 (G2, S8, S10). `SharepointSource` becomes a thin subclass
of `GraphDriveSource`. Its public config, error messages and generated schema
must stay **identical**: `tests/test_source_sharepoint.py` passes without
edits, and its introspected schema equals `generated/SharepointSource.json`
(baseline verified on 2026-09-30: 13 attributes, equal).

---

## Scope

- Change the base class to `GraphDriveSource`. Keep credential and tenant/site
  parsing (`sharepoint.py:86-107`) in `SharepointSource.__init__`, with
  literal `creds.get('client_id', 'SHAREPOINT_APP_ID')` and similar calls.
- Remove the moved code (source parsing, `_encode_share_url`,
  `_parse_file_content`, `fetch`). It is inherited now.
- Move the site→drives→library→`root:/path:` block (`sharepoint.py:237-310`)
  into `_resolve_drive_item(client)` unchanged, including the `resolve_masks`
  calls and every error message.
- Add a schema-snapshot test and mocked drive-resolution tests.

**NOT in scope**: `ToSharepoint` (destinations) and OneDrive.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/sharepoint.py` | MODIFY | rebase on `GraphDriveSource` |
| `tests/multi/sources/test_sharepoint_graph_paths.py` | CREATE | schema snapshot + drive resolution |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .graph import GraphDriveSource                                    # created by TASK-803
from querysource.queries.multi._introspect import extract_source_schema  # verified: _introspect.py:990
from querysource.queries.multi.sources import SharepointSource         # verified: sources/__init__.py:7
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/sharepoint.py
from .base import ThreadSource                        # :16  (replace with: from .graph import GraphDriveSource)
from .file import excel_based                         # :17  (no longer needed here once parsing moved — remove if unused)
class SharepointSource(ThreadSource):                 # :20
    creds parsing                                     # :86-107 (keep)
    tenant check + masks + site/drives/library/item   # :238-310 (becomes _resolve_drive_item)
    messages to keep verbatim: "SharePoint tenant_name must be configured …" (:239-242),
      "SharePoint tenant_name and site must be specified to locate the site." (:254-256),
      "No document library found for site '…' matching '…'." (:291-294),
      "File '…' not found in SharePoint directory '…'." (:307-310)
# generated/SharepointSource.json — 13 attributes; extract_source_schema(SharepointSource) equals it today
```

### Does NOT Exist
- ~~`SharepointSource._resolve_drive_item`~~ before this task.
- ~~Any need to change `generated/SharepointSource.json`~~: if it changes, the refactor broke introspection. Fix the code, not the JSON.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/sharepoint.py", "action": "MODIFY"},
    {"path": "tests/multi/sources/test_sharepoint_graph_paths.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/sharepoint.py#SharepointSource",
    "sym:querysource/queries/multi/_introspect.py#extract_source_schema"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The `tenant_name` check (`:238-242`) currently runs only in path mode (not
  `url`). Keep it at the top of `_resolve_drive_item`.
- The directory normalisation rules (single segment → `Documents` + subfolder;
  `Shared Documents` → `Documents`; fallback to the first drive) must not change.
- `SharepointSource._encode_share_url` keeps working through inheritance
  (existing callers or tests may use the class attribute).

---

## Implementation Blueprint

### Steps (in order)
1. Swap the import and base class — *why*: G2.
2. Delete the moved members and keep the credential parsing — *why*: introspection needs literal `creds.get` calls in this `__init__`.
3. Create `_resolve_drive_item` from `:238-310` — *why*: the only SharePoint-specific part.
4. Run the existing SharePoint tests **unchanged**, then add the new tests.

### `querysource/queries/multi/sources/sharepoint.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from .base import ThreadSource' …/sharepoint.py) — line 16
from .graph import GraphDriveSource
# occurrences: 1 (verified: grep -c 'class SharepointSource(ThreadSource):' …/sharepoint.py) — line 20
class SharepointSource(GraphDriveSource):
    # (class docstring kept verbatim)
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        # FILL IN: keep sharepoint.py:87-107 verbatim (client_id/secret/tenant_id/tenant_name/site)
        # (source/masks parsing now lives in GraphDriveSource.__init__ — delete it here)

    async def _resolve_drive_item(self, client: Any) -> Any:
        """Site → drives → library/subfolder → root:/path: (sharepoint.py:238-310, unchanged)."""
        # FILL IN: move :238-310 verbatim, returning `item` instead of falling through to download
```
**Why**: every behaviour-bearing line is moved, not rewritten. The snapshot
test protects the introspected schema.

### FILL IN checklist
- [ ] `__init__`: credential and tenant/site parsing verbatim.
- [ ] `_resolve_drive_item`: verbatim move, returns the drive item.

---

## Acceptance Criteria

- [ ] `pytest tests/test_source_sharepoint.py` passes with **no edits** to that file.
- [ ] `extract_source_schema(SharepointSource)["attributes"]` (name/default/required) equals `generated/SharepointSource.json`.
- [ ] Mocked drive resolution covers library normalisation, the first-drive fallback and not-found messages.
- [ ] `ruff check querysource/queries/multi/sources/sharepoint.py` is clean.

## Validation Commands

- `pytest tests/test_source_sharepoint.py -q`
- `pytest tests/multi/sources/test_sharepoint_graph_paths.py -q`

---

## Test Specification

```python
# tests/multi/sources/test_sharepoint_graph_paths.py
import json

from querysource.queries.multi._introspect import extract_source_schema
from querysource.queries.multi.sources import SharepointSource


def _triples(attrs):
    return [(a["name"], a.get("default"), a.get("required")) for a in attrs]


def test_sharepoint_schema_unchanged():
    live = extract_source_schema(SharepointSource)["attributes"]
    gen = json.load(open("generated/SharepointSource.json"))["attributes"]
    assert _triples(live) == _triples(gen)


async def test_sharepoint_resolve_drive_item_paths():
    ...  # FILL IN: MagicMock client; drives [Documents, Other]; directory "Shared Documents/Gen" → Documents + "Gen/file"


async def test_sharepoint_file_not_found_message():
    ...  # FILL IN: items.get → None → RuntimeError "not found in SharePoint directory"
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-804 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
