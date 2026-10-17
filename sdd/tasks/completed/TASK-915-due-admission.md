# TASK-915: Claim schedule occurrences once and preserve trigger cursors

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-914
**Assigned-to**: unassigned

## Context
This task implements the bounded due-admission deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Implement admit(logical_schedule_id,generation) against durable due time; atomically claim one occurrence, enforce max_instances and advance the next-unclaimed cursor.
- Acquire the owner shared advisory lock before rechecking gate/generation or claiming; release on rejected/paused/not-due work and retain through admitted execution.
- Preserve trigger timezone, interval/biweekly anchor, coalescing/misfire behavior and pause state; do not reset a schedule to migration time.
- Use the unique logical schedule/fire-time key to prevent duplicate admission across process wakeups. Never auto-replay an attempt with unknown external effects.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/handoff.py` | MODIFY | Claim schedule occurrences once and preserve trigger cursors; only the scoped changes above |
| `tests/tenants/test_handoff_admission.py` | CREATE | Claim schedule occurrences once and preserve trigger cursors; only the scoped changes above |

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
- **TASK-914 — Create shared handoff state and readiness accessors**: consume its declared files/interfaces only after its index state is done. TASK-914: serialize overlapping writer of querysource/scheduler/handoff.py
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
      "path": "querysource/scheduler/handoff.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_handoff_admission.py",
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
Graph evidence: TASK-914: serialize overlapping writer of querysource/scheduler/handoff.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Implement admit(logical_schedule_id,generation) against durable due time; atomically claim one occurrence, enforce max_instances and advance the next-unclaimed cursor. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
2. Acquire the owner shared advisory lock before rechecking gate/generation or claiming; release on rejected/paused/not-due work and retain through admitted execution. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Preserve trigger timezone, interval/biweekly anchor, coalescing/misfire behavior and pause state; do not reset a schedule to migration time. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
4. Use the unique logical schedule/fire-time key to prevent duplicate admission across process wakeups. Never auto-replay an attempt with unknown external effects. **Why:** this preserves the bounded due-admission deliverable while keeping unrelated work and production data unchanged.

### `querysource/scheduler/handoff.py` (MODIFY)
```python
# Planned attachment from TASK-914; not current source.
# Expected unique anchor: class ScheduleHandoffCoordinator:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `tests/tenants/test_handoff_admission.py` (CREATE)
```python path=tests/tenants/test_handoff_admission.py
"""Claim schedule occurrences once and preserve trigger cursors: bounded regression coverage, never production writes."""
import pytest


def test_due_admission():
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
    async def admit(self, logical_schedule_id: str, generation: int) -> Mapping[str, Any] | None:
        """Claim one due occurrence while holding the shared owner fence."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_handoff_admission.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Implement admit(logical_schedule_id,generation) against durable due time; atomically claim one occurrence, enforce max_instances and advance the next-unclaimed cursor.
- [ ] Acquire the owner shared advisory lock before rechecking gate/generation or claiming; release on rejected/paused/not-due work and retain through admitted execution.
- [ ] Preserve trigger timezone, interval/biweekly anchor, coalescing/misfire behavior and pause state; do not reset a schedule to migration time.
- [ ] Use the unique logical schedule/fire-time key to prevent duplicate admission across process wakeups. Never auto-replay an attempt with unknown external effects.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_handoff_admission.py -q`

## Test Specification
- Verify: Implement admit(logical_schedule_id,generation) against durable due time; atomically claim one occurrence, enforce max_instances and advance the next-unclaimed cursor.
- Verify: Acquire the owner shared advisory lock before rechecking gate/generation or claiming; release on rejected/paused/not-due work and retain through admitted execution.
- Verify: Preserve trigger timezone, interval/biweekly anchor, coalescing/misfire behavior and pause state; do not reset a schedule to migration time.
- Verify: Use the unique logical schedule/fire-time key to prevent duplicate admission across process wakeups. Never auto-replay an attempt with unknown external effects.

## Agent Instructions
1. Start with `$sdd-start TASK-915` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. ScheduleHandoffCoordinator.admit(): shared owner advisory lock (try, session-level, dedicated connection) FIRST, then short FOR UPDATE transaction revalidating generation/owner/gate/pause/due, claims the slot via the unique (logical_schedule_id, scheduled_fire_at) key, enforces max_instances (admitted/running; unknown runs retained, never replayed) and advances the cursor with the trigger's own get_next_fire_time (timezone/anchor preserved; coalesce + misfire grace, default = APScheduler 1s). Returns AdmittedRun (dict) holding the lock until finish(). Real PostgreSQL tests (concurrent wakeups admit once, exclusive holder rejects, fence retained until finish, coalescing/misfire, no-coalesce ordering, max_instances, biweekly Europe/Madrid via the scheduler's real parser): 7 passed.
