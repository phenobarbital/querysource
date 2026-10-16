# TASK-910: Restore contract-specific listing metadata and export

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-909
**Assigned-to**: unassigned

## Context
This task implements the bounded legacy-projections deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Correct repository list, schema and export_insert to retain the persisted legacy program and exclude runtime-only tenant program.
- Keep raw persisted updated_at and nulls unchanged when serializing reads; avoid runtime-model round trips that stamp fields.
- Export safely escaped SQL against the selected physical schema/table and verify tenant output never exports program_slug.
- Confirm tenant filters/projections still reject program_slug and existing list response shapes remain compatible.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | Restore contract-specific listing metadata and export; only the scoped changes above |
| `tests/tenants/test_tenant_repository_reads.py` | MODIFY | Restore contract-specific listing metadata and export; only the scoped changes above |
| `tests/tenants/test_query_store_exports.py` | CREATE | Restore contract-specific listing metadata and export; only the scoped changes above |

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
- `tests/tenants/test_tenant_repository_reads.py:12` — unique attachment `def _tenant_store(schema: str = "tenant1") -> QueryStore:`; occurrences **1**; SHA-256 `34c32f3df811a9acbc2adf8bf95759706763e6b13011d580f867bb6b4c561cf2`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:2`: `import datetime`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:4`: `import pytest`.
  Existing import at `tests/tenants/test_tenant_repository_reads.py:6`: `from querysource.cache_identity import definition_revision, result_cache_key`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:tests/tenants/test_tenant_repository_reads.py#_tenant_store` (current source re-read).

### Dependency-provided Interfaces
- **TASK-909 — Restore legacy writes and program-aware revisions**: consume its declared files/interfaces only after its index state is done. TASK-909: serialize overlapping writer of querysource/repositories/definitions.py, tests/tenants/test_tenant_repository_reads.py

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
      "path": "tests/tenants/test_tenant_repository_reads.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_query_store_exports.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore",
    "sym:tests/tenants/test_tenant_repository_reads.py#_tenant_store"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-909: serialize overlapping writer of querysource/repositories/definitions.py, tests/tenants/test_tenant_repository_reads.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Correct repository list, schema and export_insert to retain the persisted legacy program and exclude runtime-only tenant program. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Keep raw persisted updated_at and nulls unchanged when serializing reads; avoid runtime-model round trips that stamp fields. **Why:** this preserves the bounded legacy-projections deliverable while keeping unrelated work and production data unchanged.
3. Export safely escaped SQL against the selected physical schema/table and verify tenant output never exports program_slug. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Confirm tenant filters/projections still reject program_slug and existing list response shapes remain compatible. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/repositories/definitions.py:94
# class DefinitionRepository:
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

### `tests/tenants/test_query_store_exports.py` (CREATE)
```python path=tests/tenants/test_query_store_exports.py
"""Restore contract-specific listing metadata and export: bounded regression coverage, never production writes."""
import pytest


def test_legacy_projections():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `querysource/repositories/definitions.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_tenant_repository_reads.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_store_exports.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Correct repository list, schema and export_insert to retain the persisted legacy program and exclude runtime-only tenant program.
- [ ] Keep raw persisted updated_at and nulls unchanged when serializing reads; avoid runtime-model round trips that stamp fields.
- [ ] Export safely escaped SQL against the selected physical schema/table and verify tenant output never exports program_slug.
- [ ] Confirm tenant filters/projections still reject program_slug and existing list response shapes remain compatible.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_tenant_repository_reads.py -q`
- `pytest tests/tenants/test_query_store_exports.py -q`

## Test Specification
- Verify: Correct repository list, schema and export_insert to retain the persisted legacy program and exclude runtime-only tenant program.
- Verify: Keep raw persisted updated_at and nulls unchanged when serializing reads; avoid runtime-model round trips that stamp fields.
- Verify: Export safely escaped SQL against the selected physical schema/table and verify tenant output never exports program_slug.
- Verify: Confirm tenant filters/projections still reject program_slug and existing list response shapes remain compatible.

## Agent Instructions
1. Start with `$sdd-start TASK-910` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. Legacy list/write returns/schema/export keep stored program_slug; tenant projections exclude it; export pops run-as column and uses QueryModel for legacy. Tests: reads/writes/exports 16 passed.
