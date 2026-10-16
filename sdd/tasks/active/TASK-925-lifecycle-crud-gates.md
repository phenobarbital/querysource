# TASK-925: Wire recovery lifecycle and ordinary CRUD reservations

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-924
**Assigned-to**: unassigned

## Context
This task implements the bounded lifecycle-crud-gates deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Start/stop the durable operation worker through QuerySource lifecycle independently of ENABLE_QS_SCHEDULER; recover affected schedules before dispatch readiness.
- Guard ordinary CRUD to reserved moving identities with move_in_progress so save/delete/create cannot resurrect a source or overwrite a target during recovery.
- Fix existing _sync_definition_jobs selector by physical-default equality, preserving explicit public independently of other legacy stores.
- Honor disabled scheduler behavior for unscheduled moves and require live-protocol readiness for scheduled moves; no runtime DDL or production migration on startup.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Wire recovery lifecycle and ordinary CRUD reservations; only the scoped changes above |
| `querysource/handlers/manager.py` | MODIFY | Wire recovery lifecycle and ordinary CRUD reservations; only the scoped changes above |
| `tests/tenants/test_tenant_management_writes.py` | MODIFY | Wire recovery lifecycle and ordinary CRUD reservations; only the scoped changes above |
| `tests/tenants/test_query_move_lifecycle.py` | CREATE | Wire recovery lifecycle and ordinary CRUD reservations; only the scoped changes above |

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
- `querysource/handlers/manager.py:47` — unique attachment `class QueryManager(QueryView):`; occurrences **1**; SHA-256 `604697fe45fd3be9fb8dbb59e0261484ec90395f94c21832433a0995b52323c8`.
  Existing import at `querysource/handlers/manager.py:11`: `import json`.
  Existing import at `querysource/handlers/manager.py:12`: `from math import ceil`.
  Existing import at `querysource/handlers/manager.py:13`: `from typing import Any`.
- `tests/tenants/test_tenant_management_writes.py:1` — unique attachment `"""Route management mutations through selected-owner repository regression contracts."""`; occurrences **1**; SHA-256 `7422c813469d0d03f6f0e9ad7ad420ae1b3617ac5b992c897ba91fe138fa5ae8`.
  Existing import at `tests/tenants/test_tenant_management_writes.py:2`: `from unittest.mock import MagicMock`.
  Existing import at `tests/tenants/test_tenant_management_writes.py:4`: `import pytest`.
  Existing import at `tests/tenants/test_tenant_management_writes.py:5`: `from aiohttp import web`.
- Wiki-confirmed existing symbol ID: `sym:querysource/handlers/manager.py#QueryManager` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/services.py#QuerySource` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-924 — Register catalog preview move status and reconciliation routes**: consume its declared files/interfaces only after its index state is done. TASK-924: serialize overlapping writer of querysource/services.py

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
      "path": "querysource/services.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/handlers/manager.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_tenant_management_writes.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_query_move_lifecycle.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/handlers/manager.py#QueryManager",
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/services.py#QuerySource",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-924: serialize overlapping writer of querysource/services.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Start/stop the durable operation worker through QuerySource lifecycle independently of ENABLE_QS_SCHEDULER; recover affected schedules before dispatch readiness. **Why:** this preserves the bounded lifecycle-crud-gates deliverable while keeping unrelated work and production data unchanged.
2. Guard ordinary CRUD to reserved moving identities with move_in_progress so save/delete/create cannot resurrect a source or overwrite a target during recovery. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.
3. Fix existing _sync_definition_jobs selector by physical-default equality, preserving explicit public independently of other legacy stores. **Why:** this preserves the bounded lifecycle-crud-gates deliverable while keeping unrelated work and production data unchanged.
4. Honor disabled scheduler behavior for unscheduled moves and require live-protocol readiness for scheduled moves; no runtime DDL or production migration on startup. **Why:** this preserves the bounded lifecycle-crud-gates deliverable while keeping unrelated work and production data unchanged.

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/services.py:247
#         ### Query Manager ###
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `querysource/handlers/manager.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/handlers/manager.py:47
# class QueryManager(QueryView):
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_tenant_management_writes.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at tests/tenants/test_tenant_management_writes.py:1
# """Route management mutations through selected-owner repository regression contracts."""
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_query_move_lifecycle.py` (CREATE)
```python path=tests/tenants/test_query_move_lifecycle.py
"""Wire recovery lifecycle and ordinary CRUD reservations: bounded regression coverage, never production writes."""
import pytest


def test_lifecycle_crud_gates():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `querysource/services.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/handlers/manager.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_tenant_management_writes.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_move_lifecycle.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Start/stop the durable operation worker through QuerySource lifecycle independently of ENABLE_QS_SCHEDULER; recover affected schedules before dispatch readiness.
- [ ] Guard ordinary CRUD to reserved moving identities with move_in_progress so save/delete/create cannot resurrect a source or overwrite a target during recovery.
- [ ] Fix existing _sync_definition_jobs selector by physical-default equality, preserving explicit public independently of other legacy stores.
- [ ] Honor disabled scheduler behavior for unscheduled moves and require live-protocol readiness for scheduled moves; no runtime DDL or production migration on startup.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_tenant_management_writes.py -q`
- `pytest tests/tenants/test_query_move_lifecycle.py -q`

## Test Specification
- Verify: Start/stop the durable operation worker through QuerySource lifecycle independently of ENABLE_QS_SCHEDULER; recover affected schedules before dispatch readiness.
- Verify: Guard ordinary CRUD to reserved moving identities with move_in_progress so save/delete/create cannot resurrect a source or overwrite a target during recovery.
- Verify: Fix existing _sync_definition_jobs selector by physical-default equality, preserving explicit public independently of other legacy stores.
- Verify: Honor disabled scheduler behavior for unscheduled moves and require live-protocol readiness for scheduled moves; no runtime DDL or production migration on startup.

## Agent Instructions
1. Start with `$sdd-start TASK-925` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
