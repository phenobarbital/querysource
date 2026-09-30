# TASK-807: OneDriveSource — mode matrix, credential fallback, delegated prepare

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-803, TASK-805, TASK-806
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5 (G1, S2). `OneDriveSource(GraphDriveSource)` implements the
§2 mode matrix: app-only `/users/{user}/drive`, sharing `url` (both modes),
and delegated `/me/drive`. The provider is fixed to `onedrive`, and there is
no `provider:` option.

---

## Scope

- Create `onedrive.py` with `OneDriveSource`: literal config parsing (introspection),
  `ONEDRIVE_*` → `SHAREPOINT_*` credential fallback, `_validate_mode()`,
  `prepare()` (delegated → `resolve_delegated_token`), `_credential()`
  (delegated → `StaticTokenCredential`) and `_resolve_drive_item()`.
- Write mocked-Graph unit tests for every mode and every invalid combination.

**NOT in scope**: registry, extra or generated JSON (TASK-813), and MultiQS wiring (TASK-806).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/onedrive.py` | CREATE | `OneDriveSource` |
| `tests/multi/sources/test_onedrive_source.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .graph import GraphDriveSource, StaticTokenCredential                      # TASK-802/803
from ....auth.identity_tokens import SourceIdentityContext, resolve_delegated_token, DelegatedToken  # TASK-805
```

### Existing Signatures to Use
```python
# ThreadSource.resolve_credential(key, value) -> str   # base.py:45 — ALL_CAPS name → navconfig value, else returns the literal (the name itself)
# ThreadSource.resolve_masks(text) -> str              # base.py:72
# ThreadSource.prepare(context) -> None                # TASK-806 (override here)
# GraphDriveSource: self._filename/_directory/_url/_sheet_name/_pd_args, _extra_name, _credential(), _resolve_drive_item(client)  # TASK-803
# msgraph builders (verified 2026-09-30):
client.users.by_user_id(user).drive.get()                                   # → Drive (has .id)
client.me.drive.get()                                                       # client.me → UserItemRequestBuilder
client.drives.by_drive_id(drive_id).items.by_drive_item_id(f"root:/{path}:").get()   # as sharepoint.py:300-304
```

### Does NOT Exist
- ~~`msgraph.generated.me`~~ (module): use `client.me`.
- ~~`options['provider']`~~: fixed provider, not configurable.
- ~~`OneDriveSource`~~ before this task.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/onedrive.py", "action": "CREATE"},
    {"path": "tests/multi/sources/test_onedrive_source.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.resolve_credential",
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.resolve_masks"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Mode matrix (`ValueError` at construction)**: unknown `auth` (not
  `app`/`delegated`); `auth == "app"` with no `url` and no `user`;
  `auth == "delegated"` with `user`; neither `url` nor `filename`.
- **Fallback**: `self.resolve_credential('client_id', creds.get('client_id', 'ONEDRIVE_APP_ID'))`.
  If the result still equals `'ONEDRIVE_APP_ID'` (unresolved), resolve
  `'SHAREPOINT_APP_ID'`. Do the same for `_SECRET` and `_TENANT_ID`. Keep the
  literal `creds.get('…', 'ONEDRIVE_…')` calls for introspection.
- **Delegated**: `prepare()` stores `self._token: DelegatedToken`, and
  `_credential()` returns `StaticTokenCredential(token.access_token, token.expires_at)`.
  If `prepare()` was never called (a direct `fetch`), raise
  `RuntimeError("OneDriveSource delegated mode requires prepare() on the caller loop")`.
- Path: `resolve_masks` on directory and filename, then
  `f"{directory.strip('/')}/{filename}"` (just the filename when the directory is empty).
- Not found: `RuntimeError("File '<f>' not found in OneDrive directory '<d>'.")`.

---

## Implementation Blueprint

### Steps (in order)
1. Create the class below — *why*: spec §3 M5 names.
2. Complete the FILL INs following the Key Constraints.
3. Write the tests with a `MagicMock` Graph client (async `.get` via `AsyncMock`).

### `querysource/queries/multi/sources/onedrive.py` (CREATE)
```python
"""OneDriveSource — download one CSV/Excel file from Microsoft OneDrive (FEAT-159)."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from aiohttp import web

from ....auth.identity_tokens import DelegatedToken, SourceIdentityContext, resolve_delegated_token
from .graph import GraphDriveSource, StaticTokenCredential


class OneDriveSource(GraphDriveSource):
    """Download one CSV/Excel file from OneDrive.

    Modes: ``auth: app`` (default) with ``user`` + path, or ``url``;
    ``auth: delegated`` (the requesting / run-as user's own drive via a linked
    ``onedrive`` identity) with path or ``url``.
    """

    _extra_name = "onedrive"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        self._auth_mode: str = options.get('auth', 'app')
        self._user: str = options.get('user', '')
        creds = options.get('credentials', {})
        self._client_id = self._with_fallback('client_id', creds.get('client_id', 'ONEDRIVE_APP_ID'), 'SHAREPOINT_APP_ID')
        self._client_secret = self._with_fallback('client_secret', creds.get('client_secret', 'ONEDRIVE_APP_SECRET'), 'SHAREPOINT_APP_SECRET')
        self._tenant_id = self._with_fallback('tenant_id', creds.get('tenant_id', 'ONEDRIVE_TENANT_ID'), 'SHAREPOINT_TENANT_ID')
        self._token: Optional[DelegatedToken] = None
        self._validate_mode()

    def _with_fallback(self, key: str, value: str, fallback_name: str) -> str:
        """Resolve value; when it stays the unresolved ONEDRIVE_* name, resolve fallback_name."""
        # FILL IN: per Key Constraints

    def _validate_mode(self) -> None:
        """ValueError on any invalid mode combination (spec §2 matrix)."""
        # FILL IN: four rules from Key Constraints

    async def prepare(self, context: SourceIdentityContext | None) -> None:
        """Delegated only: resolve the user's access token on the caller's loop."""
        if self._auth_mode == 'delegated':
            self._token = await resolve_delegated_token(context)

    async def _credential(self) -> Any:
        if self._auth_mode == 'delegated':
            # FILL IN: RuntimeError when self._token is None; else StaticTokenCredential(...)
            raise NotImplementedError
        return await super()._credential()

    async def _resolve_drive_item(self, client: Any) -> Any:
        """users/{user}/drive (app) or me/drive (delegated) → root:/path:."""
        # FILL IN: masks, drive lookup, item lookup, not-found message
```
**Why this shape**: the provider is fixed (S2). `prepare()` is the only place
the token is resolved (G4). Literal `options.get`/`creds.get` calls keep the
generated schema complete (S10).

### FILL IN checklist
- [ ] `_with_fallback`
- [ ] `_validate_mode` (4 rules)
- [ ] `_credential` delegated branch
- [ ] `_resolve_drive_item` (both modes)

---

## Acceptance Criteria

- [ ] Every invalid combination raises `ValueError`, and all valid combinations construct.
- [ ] App mode resolves `users.by_user_id(user).drive`. Delegated mode resolves `me.drive` with a `StaticTokenCredential`.
- [ ] A `url` in either mode goes through `/shares` (inherited).
- [ ] Unresolved `ONEDRIVE_*` names fall back to `SHAREPOINT_*`.
- [ ] `ruff check querysource/queries/multi/sources/onedrive.py` is clean.

## Validation Commands

- `pytest tests/multi/sources/test_onedrive_source.py -q`

---

## Test Specification

```python
# tests/multi/sources/test_onedrive_source.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.queries.multi.sources.onedrive import OneDriveSource


def _src(**opts):
    return OneDriveSource("od", opts, None, asyncio.Queue())


@pytest.mark.parametrize("opts", [
    {"auth": "bogus", "source": {"filename": "f.csv"}, "user": "u"},
    {"auth": "app", "source": {"filename": "f.csv"}},
    {"auth": "delegated", "user": "u", "source": {"filename": "f.csv"}},
    {"auth": "app", "user": "u", "source": {}},
])
def test_onedrive_mode_matrix_invalid(opts):
    with pytest.raises(ValueError):
        _src(**opts)


def test_onedrive_credential_fallback():
    ...  # FILL IN: patch resolve_credential so ONEDRIVE_* return themselves and SHAREPOINT_* return values


async def test_onedrive_app_user_drive_path():
    ...  # FILL IN: client.users.by_user_id("u").drive.get → drive(id="D"); items path "root:/dir/f.csv:"


async def test_onedrive_delegated_me_drive():
    ...  # FILL IN: prepare(ctx) with patched resolve_delegated_token; _credential is StaticTokenCredential; me.drive used


async def test_onedrive_delegated_without_prepare_raises():
    ...  # FILL IN
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-807 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
