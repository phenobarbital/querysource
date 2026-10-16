# TASK-928: Document deployment rollout and live transfer recovery

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-926, TASK-927
**Assigned-to**: unassigned

## Context
This task implements the bounded operator-documentation deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Document explicit control DDL/backfill and all-process protocol rollout readiness; no manual per-slug schedule deactivation or new admin restriction.
- Describe live operation states, run-as preservation, original job drain, due/misfire behavior, idempotent retry and recovery for unknown commit/remote effects.
- Explain external consumers, explicit tenant child pinning and unsupported external scheduler blockers without claiming exactly-once business effects.
- Include safe rollback/disablement and disposable verification evidence; do not change real client query tables or run production migrations.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/PER_TENANT_QUERIES.md` | MODIFY | Document deployment rollout and live transfer recovery; only the scoped changes above |

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
- `docs/PER_TENANT_QUERIES.md:1` — unique attachment `# Per-tenant queries`; occurrences **1**; SHA-256 `a68cabd810a533bc5ac0ecf54c31054464be25defaeb5af36fa2710c80b5b58b`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-926 — Prove live transfer drain deduplication and crash recovery**: consume its declared files/interfaces only after its index state is done. TASK-926: consume the interfaces/contract in tests/integration/test_live_schedule_handoff_postgres.py
- **TASK-927 — Verify runtime API responses against shared v1 fixtures**: consume its declared files/interfaces only after its index state is done. TASK-927: consume the interfaces/contract in tests/tenants/test_query_move_contract.py

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
      "path": "docs/PER_TENANT_QUERIES.md",
      "action": "MODIFY"
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
Graph evidence: TASK-926: consume the interfaces/contract in tests/integration/test_live_schedule_handoff_postgres.py; TASK-927: consume the interfaces/contract in tests/tenants/test_query_move_contract.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Document explicit control DDL/backfill and all-process protocol rollout readiness; no manual per-slug schedule deactivation or new admin restriction. **Why:** this preserves the bounded operator-documentation deliverable while keeping unrelated work and production data unchanged.
2. Describe live operation states, run-as preservation, original job drain, due/misfire behavior, idempotent retry and recovery for unknown commit/remote effects. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
3. Explain external consumers, explicit tenant child pinning and unsupported external scheduler blockers without claiming exactly-once business effects. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Include safe rollback/disablement and disposable verification evidence; do not change real client query tables or run production migrations. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.

### `docs/PER_TENANT_QUERIES.md` (MODIFY)
```python
// occurrences: 1 (literal anchor count reverified at task creation)
// ATTACH at docs/PER_TENANT_QUERIES.md:1
// # Per-tenant queries
// FILL IN: apply only the scoped steps above at this verified attachment.
// Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### FILL IN checklist
- [ ] `docs/PER_TENANT_QUERIES.md` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Document explicit control DDL/backfill and all-process protocol rollout readiness; no manual per-slug schedule deactivation or new admin restriction.
- [ ] Describe live operation states, run-as preservation, original job drain, due/misfire behavior, idempotent retry and recovery for unknown commit/remote effects.
- [ ] Explain external consumers, explicit tenant child pinning and unsupported external scheduler blockers without claiming exactly-once business effects.
- [ ] Include safe rollback/disablement and disposable verification evidence; do not change real client query tables or run production migrations.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_tenant_registry.py -q`
- `pytest tests/tenants/test_tenant_management_writes.py -q`

## Test Specification
- Verify: Document explicit control DDL/backfill and all-process protocol rollout readiness; no manual per-slug schedule deactivation or new admin restriction.
- Verify: Describe live operation states, run-as preservation, original job drain, due/misfire behavior, idempotent retry and recovery for unknown commit/remote effects.
- Verify: Explain external consumers, explicit tenant child pinning and unsupported external scheduler blockers without claiming exactly-once business effects.
- Verify: Include safe rollback/disablement and disposable verification evidence; do not change real client query tables or run production migrations.

## Agent Instructions
1. Start with `$sdd-start TASK-928` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
