# TASK-912: Freeze migration errors and cross-repository API fixtures

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

## Context
This task implements the bounded protocol-fixtures deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Add only approved move error/status mappings; retain every preexisting TenantError mapping.
- Create canonical v1 JSON examples for catalog, preview live-transfer, UUID move request, operation states, status/reconcile, readiness and unknown-outcome errors.
- Include all preparing/draining/cutover/reconciling/completed/failed outcomes with moved false/true/null and pending/ok/failed/not_configured sync.
- Test actual TenantError construction and fixture/schema invariants; this fixture is the frontend parity artifact, not authorization or implementation.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/tenant_errors.py` | MODIFY | Freeze migration errors and cross-repository API fixtures; only the scoped changes above |
| `tests/fixtures/query_move_contract_v1.json` | CREATE | Freeze migration errors and cross-repository API fixtures; only the scoped changes above |
| `tests/tenants/test_query_move_contract.py` | CREATE | Freeze migration errors and cross-repository API fixtures; only the scoped changes above |

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
- `querysource/tenant_errors.py:5` — unique attachment `OWNERSHIP_STATUS = {`; occurrences **1**; SHA-256 `303b0d316b7eca9287cb4e093fe69e4c32bdd2564c8eb59c4ec30f870b066990`.
  Existing import at `querysource/tenant_errors.py:3`: `from querysource.exceptions import QueryException`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenant_errors.py#TenantError` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

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
      "path": "querysource/tenant_errors.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/fixtures/query_move_contract_v1.json",
      "action": "CREATE"
    },
    {
      "path": "tests/tenants/test_query_move_contract.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenant_errors.py#TenantError",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: Independent root task for protocol-fixtures; writes only the declared files and imports no output from another task.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Add only approved move error/status mappings; retain every preexisting TenantError mapping. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Create canonical v1 JSON examples for catalog, preview live-transfer, UUID move request, operation states, status/reconcile, readiness and unknown-outcome errors. **Why:** this preserves the bounded protocol-fixtures deliverable while keeping unrelated work and production data unchanged.
3. Include all preparing/draining/cutover/reconciling/completed/failed outcomes with moved false/true/null and pending/ok/failed/not_configured sync. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
4. Test actual TenantError construction and fixture/schema invariants; this fixture is the frontend parity artifact, not authorization or implementation. **Why:** the user retained existing management access and current scheduled-output grants.

### `querysource/tenant_errors.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/tenant_errors.py:5
# OWNERSHIP_STATUS = {
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `tests/fixtures/query_move_contract_v1.json` (CREATE)
```json path=tests/fixtures/query_move_contract_v1.json
{"version":1,"catalog":{"version":1,"data":[{"tenant":null,"schema":"public","table":"queries","contract":"legacy","is_default":true},{"tenant":"wm_assembly","schema":"wm_assembly","table":"queries","contract":"tenant","is_default":false}]},"move_request":{"tenant":null,"destination_tenant":"wm_assembly","plan_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","idempotency_key":"7b03262d-c028-424d-a127-8b30b018adeb","acknowledge_external_consumers":true,"acknowledge_runtime_program_change":true},"preview_policy":{"schedule_policy":"live-transfer","consumer_review_required":true,"handoff_ready":true},"states":[{"state":"preparing","moved":false,"scheduler_sync":"pending"},{"state":"draining","moved":false,"scheduler_sync":"pending"},{"state":"cutover","moved":true,"scheduler_sync":"pending"},{"state":"reconciling","moved":true,"scheduler_sync":"failed"},{"state":"completed","moved":true,"scheduler_sync":"ok"},{"state":"failed","moved":false,"scheduler_sync":"not_configured"}],"errors":{"move_same_store":400,"move_target_exists":409,"move_plan_stale":409,"move_scheduler_not_ready":409,"move_scheduler_unsupported":409,"move_schedule_invalid":409,"move_in_progress":409,"move_idempotency_conflict":409,"move_stale_run_as":409,"move_outcome_unknown":503,"move_acknowledgement_required":400,"move_validation_failed":400,"move_inherited_references":409,"move_dependency_analysis_unsupported":409}}
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `tests/tenants/test_query_move_contract.py` (CREATE)
```python path=tests/tenants/test_query_move_contract.py
"""Freeze migration errors and cross-repository API fixtures: bounded regression coverage, never production writes."""
import pytest


def test_protocol_fixtures():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `querysource/tenant_errors.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/fixtures/query_move_contract_v1.json` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_move_contract.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Add only approved move error/status mappings; retain every preexisting TenantError mapping.
- [ ] Create canonical v1 JSON examples for catalog, preview live-transfer, UUID move request, operation states, status/reconcile, readiness and unknown-outcome errors.
- [ ] Include all preparing/draining/cutover/reconciling/completed/failed outcomes with moved false/true/null and pending/ok/failed/not_configured sync.
- [ ] Test actual TenantError construction and fixture/schema invariants; this fixture is the frontend parity artifact, not authorization or implementation.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_move_contract.py -q`

## Test Specification
- Verify: Add only approved move error/status mappings; retain every preexisting TenantError mapping.
- Verify: Create canonical v1 JSON examples for catalog, preview live-transfer, UUID move request, operation states, status/reconcile, readiness and unknown-outcome errors.
- Verify: Include all preparing/draining/cutover/reconciling/completed/failed outcomes with moved false/true/null and pending/ok/failed/not_configured sync.
- Verify: Test actual TenantError construction and fixture/schema invariants; this fixture is the frontend parity artifact, not authorization or implementation.

## Agent Instructions
1. Start with `$sdd-start TASK-912` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. Added 14 move_* error codes with spec statuses to OWNERSHIP_STATUS (preexisting retained), optional TenantError.details (backward compatible), canonical v1 fixture tests/fixtures/query_move_contract_v1.json and invariant tests (5 passed).
