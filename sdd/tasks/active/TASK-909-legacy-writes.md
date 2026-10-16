# TASK-909: Restore legacy writes and program-aware revisions

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

## Context
This task implements the bounded legacy-writes deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Choose QueryModel validation for legacy and TenantQueryDefinition for tenants in create/upsert/PATCH; tenant program_slug returns structured invalid_tenant before SQL.
- Preserve stored legacy program_slug when omitted on PATCH or upsert conflict; apply its established default only on insert. Preserve explicit PATCH null semantics and reject unknown immutable fields.
- Include the raw stored legacy program_slug in definition_revision inputs while retaining owner-qualified cache keys and current run-as rejection/audit behavior.
- Exercise unchanged/changed/omitted legacy program, tenant rejection, unknown keys and program-only revision changes without real business queries.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | Restore legacy writes and program-aware revisions; only the scoped changes above |
| `tests/tenants/test_tenant_repository_writes.py` | MODIFY | Restore legacy writes and program-aware revisions; only the scoped changes above |
| `tests/tenants/test_tenant_repository_reads.py` | MODIFY | Restore legacy writes and program-aware revisions; only the scoped changes above |

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
- `querysource/repositories/definitions.py:94` — unique attachment `class DefinitionRepository:`; occurrences **1**; SHA-256 `88bb318311d60086dcea69d6e011126bf5ba20f960ed2331ca5d179c989834aa`.
  Existing import at `querysource/repositories/definitions.py:12`: `import json`.
  Existing import at `querysource/repositories/definitions.py:13`: `import logging`.
  Existing import at `querysource/repositories/definitions.py:14`: `from collections.abc import Awaitable, Callable, Mapping`.
- `tests/tenants/test_tenant_repository_writes.py:210` — unique attachment `async def test_patch_null_and_immutable_keys() -> None:`; occurrences **1**; SHA-256 `be40a0730110f107554359ed7543b6493e564ff58883f357907ccaa5e8274c9d`.
  Existing import at `tests/tenants/test_tenant_repository_writes.py:2`: `import datetime`.
  Existing import at `tests/tenants/test_tenant_repository_writes.py:4`: `import pytest`.
  Existing import at `tests/tenants/test_tenant_repository_writes.py:5`: `from asyncdb.drivers.pg import UndefinedTableError`.
- `tests/tenants/test_tenant_repository_reads.py:12` — unique attachment `def _tenant_store(schema: str = "tenant1") -> QueryStore:`; occurrences **1**; SHA-256 `34c32f3df811a9acbc2adf8bf95759706763e6b13011d580f867bb6b4c561cf2`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:2`: `import datetime`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:4`: `import pytest`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:6`: `from querysource.cache_identity import definition_revision, result_cache_key`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:tests/tenants/test_tenant_repository_reads.py#_tenant_store` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:tests/tenants/test_tenant_repository_writes.py#test_patch_null_and_immutable_keys` (current source re-read).

### Dependency-provided Interfaces
- None; this root task does not import another task output.

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
      "path": "querysource/repositories/definitions.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_tenant_repository_writes.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_tenant_repository_reads.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore",
    "sym:tests/tenants/test_tenant_repository_reads.py#_tenant_store",
    "sym:tests/tenants/test_tenant_repository_writes.py#test_patch_null_and_immutable_keys"
  ]
}
```

## Implementation Notes
Graph evidence: Independent root task for legacy-writes; writes only the declared files and imports no output from another task.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Choose QueryModel validation for legacy and TenantQueryDefinition for tenants in create/upsert/PATCH; tenant program_slug returns structured invalid_tenant before SQL. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Preserve stored legacy program_slug when omitted on PATCH or upsert conflict; apply its established default only on insert. Preserve explicit PATCH null semantics and reject unknown immutable fields. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Include the raw stored legacy program_slug in definition_revision inputs while retaining owner-qualified cache keys and current run-as rejection/audit behavior. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Exercise unchanged/changed/omitted legacy program, tenant rejection, unknown keys and program-only revision changes without real business queries. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/repositories/definitions.py:94
# class DefinitionRepository:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_tenant_repository_writes.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at tests/tenants/test_tenant_repository_writes.py:210
# async def test_patch_null_and_immutable_keys() -> None:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_tenant_repository_reads.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at tests/tenants/test_tenant_repository_reads.py:12
# def _tenant_store(schema: str = "tenant1") -> QueryStore:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### FILL IN checklist
- [ ] `querysource/repositories/definitions.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_tenant_repository_writes.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_tenant_repository_reads.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Choose QueryModel validation for legacy and TenantQueryDefinition for tenants in create/upsert/PATCH; tenant program_slug returns structured invalid_tenant before SQL.
- [ ] Preserve stored legacy program_slug when omitted on PATCH or upsert conflict; apply its established default only on insert. Preserve explicit PATCH null semantics and reject unknown immutable fields.
- [ ] Include the raw stored legacy program_slug in definition_revision inputs while retaining owner-qualified cache keys and current run-as rejection/audit behavior.
- [ ] Exercise unchanged/changed/omitted legacy program, tenant rejection, unknown keys and program-only revision changes without real business queries.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_tenant_repository_writes.py -q`
- `pytest tests/tenants/test_tenant_repository_reads.py -q`

## Test Specification
- Verify: Choose QueryModel validation for legacy and TenantQueryDefinition for tenants in create/upsert/PATCH; tenant program_slug returns structured invalid_tenant before SQL.
- Verify: Preserve stored legacy program_slug when omitted on PATCH or upsert conflict; apply its established default only on insert. Preserve explicit PATCH null semantics and reject unknown immutable fields.
- Verify: Include the raw stored legacy program_slug in definition_revision inputs while retaining owner-qualified cache keys and current run-as rejection/audit behavior.
- Verify: Exercise unchanged/changed/omitted legacy program, tenant rejection, unknown keys and program-only revision changes without real business queries.

## Agent Instructions
1. Start with `$sdd-start TASK-909` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
