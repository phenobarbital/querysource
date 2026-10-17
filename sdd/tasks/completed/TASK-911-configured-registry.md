# TASK-911: Honor configured legacy defaults in tenant discovery

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

## Context
This task implements the bounded configured-registry deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Discover the exact QueryModel.Meta.schema/name in addition to eligible per-schema queries tables; classify configured table and literal public.queries as legacy.
- Resolve null to the configured physical store and explicit schema to schema.queries; keep public distinct even with a custom default or custom table name.
- Keep configured legacy registration independent of the tenant allowlist and deduplicate physical aliases; preserve exact-name tenant filtering.
- Fail explicitly when configured storage is missing instead of choosing another tenant/public by catalog order. Exercise empty allowlist and duplicate same-name definitions.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/tenants.py` | MODIFY | Honor configured legacy defaults in tenant discovery; only the scoped changes above |
| `tests/tenants/test_tenant_registry.py` | MODIFY | Honor configured legacy defaults in tenant discovery; only the scoped changes above |

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
- `querysource/tenants.py:80` — unique attachment `class TenantRegistry:`; occurrences **1**; SHA-256 `d3547c1560de3b8351226effe025a4d4fd01be0872cd8f23ac4194a7c11ac78a`.
  Existing import at `querysource/tenants.py:3`: `from __future__ import annotations`.
  Existing import at `querysource/tenants.py:5`: `import logging`.
  Existing import at `querysource/tenants.py:6`: `from collections.abc import Mapping, Sequence`.
- `tests/tenants/test_tenant_registry.py:19` — unique attachment `async def test_discovery_preserves_legacy_program_slug_when_loading() -> None:`; occurrences **1**; SHA-256 `775f9a3cc716d0442aa36d407d1e6a76776464875f15099daf4ddfbda74a716b`.
  Existing import at `tests/tenants/test_tenant_registry.py:3`: `import pytest`.
  Existing import at `tests/tenants/test_tenant_registry.py:4`: `from asyncdb.drivers.pg import pg, pgPool`.
  Existing import at `tests/tenants/test_tenant_registry.py:6`: `from querysource.repositories.definitions import DefinitionRepository`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#TenantRegistry` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:tests/tenants/test_tenant_registry.py#test_discovery_preserves_legacy_program_slug_when_loading` (current source re-read).

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
      "path": "querysource/tenants.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_tenant_registry.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore",
    "sym:querysource/tenants.py#TenantRegistry",
    "sym:tests/tenants/test_tenant_registry.py#test_discovery_preserves_legacy_program_slug_when_loading"
  ]
}
```

## Implementation Notes
Graph evidence: Independent root task for configured-registry; writes only the declared files and imports no output from another task.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Discover the exact QueryModel.Meta.schema/name in addition to eligible per-schema queries tables; classify configured table and literal public.queries as legacy. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Resolve null to the configured physical store and explicit schema to schema.queries; keep public distinct even with a custom default or custom table name. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Keep configured legacy registration independent of the tenant allowlist and deduplicate physical aliases; preserve exact-name tenant filtering. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Fail explicitly when configured storage is missing instead of choosing another tenant/public by catalog order. Exercise empty allowlist and duplicate same-name definitions. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/tenants.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/tenants.py:80
# class TenantRegistry:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/tenants/test_tenant_registry.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at tests/tenants/test_tenant_registry.py:19
# async def test_discovery_preserves_legacy_program_slug_when_loading() -> None:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### FILL IN checklist
- [ ] `querysource/tenants.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_tenant_registry.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Discover the exact QueryModel.Meta.schema/name in addition to eligible per-schema queries tables; classify configured table and literal public.queries as legacy.
- [ ] Resolve null to the configured physical store and explicit schema to schema.queries; keep public distinct even with a custom default or custom table name.
- [ ] Keep configured legacy registration independent of the tenant allowlist and deduplicate physical aliases; preserve exact-name tenant filtering.
- [ ] Fail explicitly when configured storage is missing instead of choosing another tenant/public by catalog order. Exercise empty allowlist and duplicate same-name definitions.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_tenant_registry.py -q`

## Test Specification
- Verify: Discover the exact QueryModel.Meta.schema/name in addition to eligible per-schema queries tables; classify configured table and literal public.queries as legacy.
- Verify: Resolve null to the configured physical store and explicit schema to schema.queries; keep public distinct even with a custom default or custom table name.
- Verify: Keep configured legacy registration independent of the tenant allowlist and deduplicate physical aliases; preserve exact-name tenant filtering.
- Verify: Fail explicitly when configured storage is missing instead of choosing another tenant/public by catalog order. Exercise empty allowlist and duplicate same-name definitions.

## Agent Instructions
1. Start with `$sdd-start TASK-911` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. discover() now also scans QueryModel.Meta.schema/name, registers it as legacy regardless of allowlist/schema filters, default = that physical store; literal public.queries legacy and distinct; schema selector addresses schema.queries only; after discover a missing configured table makes resolve(None) raise tenant_store_unavailable (never-discovered bare registry keeps its synthetic fallback for scheduler bootstrap). Updated 4 existing registry tests whose expectations contradicted the spec (empty allowlist now keeps configured store; missing public default now unavailable). tests/tenants + tests/scheduler: 14 failures identical to the pre-change baseline (pre-existing).
