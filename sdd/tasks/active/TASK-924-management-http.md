# TASK-924: Register catalog preview move status and reconciliation routes

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-922, TASK-923
**Assigned-to**: unassigned

## Context
This task implements the bounded management-http deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Implement QueryMoveHandler.preview/move/status/reconcile and register exact static routes before broad tenant execution routes, including the catalog.
- Reuse current management authentication/authorization and numeric audit actor resolution; retain datasource:use/pg_admin gates for scheduled write-capable multi.
- Validate explicit tenant null/string selectors, destination, UUID key, acknowledgements and response status: read-only preview/status 200, durable submission 202.
- Reauthorize retries/status/reconcile; translate structured ownership/move errors without raw secrets. A 202 or reconciling moved true is not final completed success.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/query_moves.py` | CREATE | Register catalog preview move status and reconciliation routes; only the scoped changes above |
| `querysource/services.py` | MODIFY | Register catalog preview move status and reconciliation routes; only the scoped changes above |
| `tests/tenants/test_query_move_http.py` | CREATE | Register catalog preview move status and reconciliation routes; only the scoped changes above |

## Codebase Contract (Anti-Hallucination)

### Verified Imports and Existing Attachments
```python
from querysource.repositories.definitions import DefinitionRepository  # existing saved-definition boundary
from querysource.tenants import QueryIdentity, QueryStore  # existing ownership types
from collections.abc import Mapping  # Python standard library
from typing import Any  # Python standard library
from aiohttp import web  # existing HTTP dependency; handlers/manager.py
import pytest  # existing test dependency
```
Declarations verified at `querysource/repositories/definitions.py:94` and `querysource/tenants.py:35` / `querysource/tenants.py:46`; recheck line locations if dependencies modify the files.
- `querysource/services.py:247` — unique attachment `        ### Query Manager ###`; occurrences **1**; SHA-256 `3849b6c77c4f828b3ed2cc4c614d442629cd0601818ca69c43f704dba3784c52`.
  Existing import at `querysource/services.py:1`: `import asyncio`.
  Existing import at `querysource/services.py:2`: `from collections.abc import Callable, Sequence`.
  Existing import at `querysource/services.py:3`: `from importlib import import_module`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-922 — Recover live transfer operations with durable leases**: consume its declared files/interfaces only after its index state is done. TASK-922: consume the interfaces/contract in querysource/query_moves.py
- **TASK-923 — Expose canonical management store descriptors**: consume its declared files/interfaces only after its index state is done. TASK-923: consume the interfaces/contract in querysource/handlers/query_stores.py

### Does NOT Exist
- No global move/tenant selector is injected by the shared HTTP client; schema ownership cannot be inferred from Program or hostname.
- No existing live-transfer journal/coordinator or move UI is assumed. Interfaces created by prerequisite tasks must be verified after they land.
- No new `slug:move` action, administrator-only policy, exactly-once external-effect guarantee or automatic replay of an indeterminate admitted run is authorized.

## Complexity Contract
```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/handlers/query_moves.py",
      "action": "CREATE"
    },
    {
      "path": "querysource/services.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_query_move_http.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-922: consume the interfaces/contract in querysource/query_moves.py; TASK-923: consume the interfaces/contract in querysource/handlers/query_stores.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Implement QueryMoveHandler.preview/move/status/reconcile and register exact static routes before broad tenant execution routes, including the catalog. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Reuse current management authentication/authorization and numeric audit actor resolution; retain datasource:use/pg_admin gates for scheduled write-capable multi. **Why:** the user retained existing management access and current scheduled-output grants.
3. Validate explicit tenant null/string selectors, destination, UUID key, acknowledgements and response status: read-only preview/status 200, durable submission 202. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Reauthorize retries/status/reconcile; translate structured ownership/move errors without raw secrets. A 202 or reconciling moved true is not final completed success. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/handlers/query_moves.py` (CREATE)
```python path=querysource/handlers/query_moves.py
"""Register catalog preview move status and reconciliation routes — new public contracts defined by this task."""
from collections.abc import Mapping
from typing import Any
from aiohttp import web

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore

class QueryMoveHandler:
    async def preview(self, request: web.Request) -> web.Response:
        """Authorize and return read-only MovePlan."""
    async def move(self, request: web.Request) -> web.Response:
        """Authorize and return a durable 202 MoveOperation."""
    async def status(self, request: web.Request) -> web.Response:
        """Return durable progress under current read permissions."""
    async def reconcile(self, request: web.Request) -> web.Response:
        """Resume the same operation under current write permissions."""

# FILL IN: implement only this task's API/SQL branches, using its ordered steps
# and acceptance criteria. No source query execution for move verification.
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/services.py:247
#         ### Query Manager ###
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_query_move_http.py` (CREATE)
```python path=tests/tenants/test_query_move_http.py
"""Register catalog preview move status and reconciliation routes: bounded regression coverage, never production writes."""
import pytest


def test_management_http():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### Fixed Interface Skeleton
```python
class QueryMoveHandler:
    async def preview(self, request: web.Request) -> web.Response:
        """Authorize and return read-only MovePlan."""
    async def move(self, request: web.Request) -> web.Response:
        """Authorize and return a durable 202 MoveOperation."""
    async def status(self, request: web.Request) -> web.Response:
        """Return durable progress under current read permissions."""
    async def reconcile(self, request: web.Request) -> web.Response:
        """Resume the same operation under current write permissions."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/handlers/query_moves.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/services.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_move_http.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Implement QueryMoveHandler.preview/move/status/reconcile and register exact static routes before broad tenant execution routes, including the catalog.
- [ ] Reuse current management authentication/authorization and numeric audit actor resolution; retain datasource:use/pg_admin gates for scheduled write-capable multi.
- [ ] Validate explicit tenant null/string selectors, destination, UUID key, acknowledgements and response status: read-only preview/status 200, durable submission 202.
- [ ] Reauthorize retries/status/reconcile; translate structured ownership/move errors without raw secrets. A 202 or reconciling moved true is not final completed success.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_move_http.py -q`

## Test Specification
- Verify: Implement QueryMoveHandler.preview/move/status/reconcile and register exact static routes before broad tenant execution routes, including the catalog.
- Verify: Reuse current management authentication/authorization and numeric audit actor resolution; retain datasource:use/pg_admin gates for scheduled write-capable multi.
- Verify: Validate explicit tenant null/string selectors, destination, UUID key, acknowledgements and response status: read-only preview/status 200, durable submission 202.
- Verify: Reauthorize retries/status/reconcile; translate structured ownership/move errors without raw secrets. A 202 or reconciling moved true is not final completed success.

## Agent Instructions
1. Start with `$sdd-start TASK-924` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
