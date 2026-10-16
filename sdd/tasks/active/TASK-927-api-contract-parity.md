# TASK-927: Verify runtime API responses against shared v1 fixtures

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-925
**Assigned-to**: unassigned

## Context
This task implements the bounded api-contract-parity deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Validate actual catalog/preview/submission/status/reconcile responses against the frozen v1 fixture, including every operation state and structured failure.
- Confirm management operators retain current access and numeric actor alone is never treated as a grant; no new admin-only rule.
- Assert no test/run calls are used by verification and API roundtrips do not expose raw delegated-user IDs, DSNs or worker secrets.
- Publish fixture path/content hash in completion evidence for the frontend release gate; do not modify the approved v1 contract silently.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/tenants/test_query_move_contract.py` | MODIFY | Verify runtime API responses against shared v1 fixtures; only the scoped changes above |
| `tests/tenants/test_query_move_http.py` | MODIFY | Verify runtime API responses against shared v1 fixtures; only the scoped changes above |

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
- No existing target is modified; every source attachment is either a verified common import above or the dependency-provided API below.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-925 — Wire recovery lifecycle and ordinary CRUD reservations**: consume its declared files/interfaces only after its index state is done. TASK-925: consume the interfaces/contract in querysource/services.py
- `tests/tenants/test_query_move_contract.py` is a planned output of **TASK-912**, not an existing source file at decomposition time. Its creation blueprint fixes the class/module anchor; verify actual source/hash/count after that dependency lands before editing.
- `tests/tenants/test_query_move_http.py` is a planned output of **TASK-924**, not an existing source file at decomposition time. Its creation blueprint fixes the class/module anchor; verify actual source/hash/count after that dependency lands before editing.

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
      "path": "tests/tenants/test_query_move_contract.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_query_move_http.py",
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
Graph evidence: TASK-925: consume the interfaces/contract in querysource/services.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Validate actual catalog/preview/submission/status/reconcile responses against the frozen v1 fixture, including every operation state and structured failure. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.
2. Confirm management operators retain current access and numeric actor alone is never treated as a grant; no new admin-only rule. **Why:** the user retained existing management access and current scheduled-output grants.
3. Assert no test/run calls are used by verification and API roundtrips do not expose raw delegated-user IDs, DSNs or worker secrets. **Why:** this preserves the bounded api-contract-parity deliverable while keeping unrelated work and production data unchanged.
4. Publish fixture path/content hash in completion evidence for the frontend release gate; do not modify the approved v1 contract silently. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.

### `tests/tenants/test_query_move_contract.py` (MODIFY)
```python
# Planned attachment from TASK-912; not current source.
# Expected unique anchor: test_query_move_contract
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `tests/tenants/test_query_move_http.py` (MODIFY)
```python
# Planned attachment from TASK-924; not current source.
# Expected unique anchor: class QueryMoveHandler:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### FILL IN checklist
- [ ] `tests/tenants/test_query_move_contract.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_move_http.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Validate actual catalog/preview/submission/status/reconcile responses against the frozen v1 fixture, including every operation state and structured failure.
- [ ] Confirm management operators retain current access and numeric actor alone is never treated as a grant; no new admin-only rule.
- [ ] Assert no test/run calls are used by verification and API roundtrips do not expose raw delegated-user IDs, DSNs or worker secrets.
- [ ] Publish fixture path/content hash in completion evidence for the frontend release gate; do not modify the approved v1 contract silently.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_move_contract.py -q`
- `pytest tests/tenants/test_query_move_http.py -q`

## Test Specification
- Verify: Validate actual catalog/preview/submission/status/reconcile responses against the frozen v1 fixture, including every operation state and structured failure.
- Verify: Confirm management operators retain current access and numeric actor alone is never treated as a grant; no new admin-only rule.
- Verify: Assert no test/run calls are used by verification and API roundtrips do not expose raw delegated-user IDs, DSNs or worker secrets.
- Verify: Publish fixture path/content hash in completion evidence for the frontend release gate; do not modify the approved v1 contract silently.

## Agent Instructions
1. Start with `$sdd-start TASK-927` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
