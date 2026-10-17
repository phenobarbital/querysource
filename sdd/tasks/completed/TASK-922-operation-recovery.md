# TASK-922: Recover live transfer operations with durable leases

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-921
**Assigned-to**: unassigned

## Context
This task implements the bounded operation-recovery deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Advance preparing/draining/cutover/reconciling/completed states via lease-owned background orchestration and idempotent reconcile calls.
- Recover unfinished operations on restart before affected dispatch. Restore source gate only for proven pre-cutover failure; keep old generation fenced after committed cutover.
- Poll durable state even when optional notification delivery is lost; lease expiry alone must not imply remote work completed or allow replay.
- Handle pool exhaustion, lock-connection loss, invalidated delegated identity and unknown remote attempts as recoverable visible states; never repeat cutover or activate a paused schedule.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/query_moves.py` | MODIFY | Recover live transfer operations with durable leases; only the scoped changes above |
| `querysource/scheduler/handoff.py` | MODIFY | Recover live transfer operations with durable leases; only the scoped changes above |
| `tests/tenants/test_query_move_recovery.py` | CREATE | Recover live transfer operations with durable leases; only the scoped changes above |

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
- **TASK-921 — Commit definition and live schedule ownership atomically**: consume its declared files/interfaces only after its index state is done. TASK-921: serialize overlapping writer of querysource/query_moves.py
- `querysource/query_moves.py` is a planned output of **TASK-919**, not an existing source file at decomposition time. Its creation blueprint fixes the class/module anchor; verify actual source/hash/count after that dependency lands before editing.
- `querysource/scheduler/handoff.py` is a planned output of **TASK-914**, not an existing source file at decomposition time. Its creation blueprint fixes the class/module anchor; verify actual source/hash/count after that dependency lands before editing.

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
      "path": "querysource/query_moves.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/scheduler/handoff.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_query_move_recovery.py",
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
Graph evidence: TASK-921: serialize overlapping writer of querysource/query_moves.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Advance preparing/draining/cutover/reconciling/completed states via lease-owned background orchestration and idempotent reconcile calls. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
2. Recover unfinished operations on restart before affected dispatch. Restore source gate only for proven pre-cutover failure; keep old generation fenced after committed cutover. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
3. Poll durable state even when optional notification delivery is lost; lease expiry alone must not imply remote work completed or allow replay. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.
4. Handle pool exhaustion, lock-connection loss, invalidated delegated identity and unknown remote attempts as recoverable visible states; never repeat cutover or activate a paused schedule. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.

### `querysource/query_moves.py` (MODIFY)
```python
# Planned attachment from TASK-919; not current source.
# Expected unique anchor: class QueryMoveService:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `querysource/scheduler/handoff.py` (MODIFY)
```python
# Planned attachment from TASK-914; not current source.
# Expected unique anchor: class ScheduleHandoffCoordinator:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `tests/tenants/test_query_move_recovery.py` (CREATE)
```python path=tests/tenants/test_query_move_recovery.py
"""Recover live transfer operations with durable leases: bounded regression coverage, never production writes."""
import pytest


def test_operation_recovery():
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
    async def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        """Resume an existing journal operation without repeating cutover."""
    async def recover(self) -> None:
        """Take recovery leases and resume unfinished transfers safely."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/query_moves.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_move_recovery.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Advance preparing/draining/cutover/reconciling/completed states via lease-owned background orchestration and idempotent reconcile calls.
- [ ] Recover unfinished operations on restart before affected dispatch. Restore source gate only for proven pre-cutover failure; keep old generation fenced after committed cutover.
- [ ] Poll durable state even when optional notification delivery is lost; lease expiry alone must not imply remote work completed or allow replay.
- [ ] Handle pool exhaustion, lock-connection loss, invalidated delegated identity and unknown remote attempts as recoverable visible states; never repeat cutover or activate a paused schedule.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_move_recovery.py -q`

## Test Specification
- Verify: Advance preparing/draining/cutover/reconciling/completed states via lease-owned background orchestration and idempotent reconcile calls.
- Verify: Recover unfinished operations on restart before affected dispatch. Restore source gate only for proven pre-cutover failure; keep old generation fenced after committed cutover.
- Verify: Poll durable state even when optional notification delivery is lost; lease expiry alone must not imply remote work completed or allow replay.
- Verify: Handle pool exhaustion, lock-connection loss, invalidated delegated identity and unknown remote attempts as recoverable visible states; never repeat cutover or activate a paused schedule.

## Agent Instructions
1. Start with `$sdd-start TASK-922` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. handoff.py: claim/release_operation_lease (expiring exclusivity token only) and list_recoverable_operations (polled). query_moves.py: QueryMoveService.advance (lease-owned stage machine preparing->draining->cutover->reconcile, every stage re-reads the journal so takeover never repeats a committed cutover or replays a run; pending drain/unknown run outcome/unknown cutover/infrastructure errors become visible messages and stay recoverable), reconcile (idempotent, resolves indeterminate cutover first, never moves rows), recover (startup/poll), MoveWorker (bounded concurrency, durable polling, optional wake accelerator, independent of ENABLE_QS_SCHEDULER). Source gate is restored only by the proven pre-cutover failure path; after a committed cutover the old generation stays fenced and paused schedules stay paused. 10 real-PostgreSQL recovery tests pass. Not wired into application lifecycle here (TASK-924/925 own services.py wiring).
