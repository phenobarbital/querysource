# TASK-917: Bootstrap durable schedules and reconcile scheduler mirrors

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-916
**Assigned-to**: unassigned

## Context
This task implements the bounded scheduler-mirrors deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Backfill shared logical schedule state from existing registration under a serialized bootstrap; preserve cursor/anchor/paused state and detect conflicting process mirrors.
- Make register_slug and startup consume durable desired owner/generation; APScheduler becomes a wakeup mirror, not a second due-time authority.
- Implement idempotent reconcile(operation_id) to remove stale source mirrors and install target mirrors without authorizing stale generation dispatch.
- Retain cadence and occurrence IDs through cutover; process wakeups and restart reconciliation cannot create an extra catch-up run beyond existing misfire policy.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | Bootstrap durable schedules and reconcile scheduler mirrors; only the scoped changes above |
| `querysource/scheduler/handoff.py` | MODIFY | Bootstrap durable schedules and reconcile scheduler mirrors; only the scoped changes above |
| `tests/tenants/test_handoff_scheduler_mirrors.py` | CREATE | Bootstrap durable schedules and reconcile scheduler mirrors; only the scoped changes above |

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
- `querysource/scheduler/scheduler.py:121` — unique attachment `class QSScheduler:`; occurrences **1**; SHA-256 `a62e85569e06deba56e47ba95543d67485533d5215d507faee4e01b6d245d5db`.
  Existing import at `querysource/scheduler/scheduler.py:17`: `import asyncio`.
  Existing import at `querysource/scheduler/scheduler.py:18`: `import hashlib`.
  Existing import at `querysource/scheduler/scheduler.py:19`: `import json`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/scheduler/scheduler.py#QSScheduler` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-916 — Fence query multi and cache job execution**: consume its declared files/interfaces only after its index state is done. TASK-916: serialize overlapping writer of querysource/scheduler/handoff.py
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
      "path": "querysource/scheduler/scheduler.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/scheduler/handoff.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_handoff_scheduler_mirrors.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/scheduler/scheduler.py#QSScheduler",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-916: serialize overlapping writer of querysource/scheduler/handoff.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Backfill shared logical schedule state from existing registration under a serialized bootstrap; preserve cursor/anchor/paused state and detect conflicting process mirrors. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
2. Make register_slug and startup consume durable desired owner/generation; APScheduler becomes a wakeup mirror, not a second due-time authority. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Implement idempotent reconcile(operation_id) to remove stale source mirrors and install target mirrors without authorizing stale generation dispatch. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.
4. Retain cadence and occurrence IDs through cutover; process wakeups and restart reconciliation cannot create an extra catch-up run beyond existing misfire policy. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/scheduler/scheduler.py:121
# class QSScheduler:
# FILL IN: apply only the scoped steps above at this verified attachment.
# Preserve unrelated members, signatures, behavior and prior task edits.
```
**Why:** the attachment is unique in the reviewed source; merge the scoped change into the existing file rather than overwriting its unrelated implementation. Reverify after prerequisite writers land.

### `querysource/scheduler/handoff.py` (MODIFY)
```python
# Planned attachment from TASK-914; not current source.
# Expected unique anchor: class ScheduleHandoffCoordinator:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `tests/tenants/test_handoff_scheduler_mirrors.py` (CREATE)
```python path=tests/tenants/test_handoff_scheduler_mirrors.py
"""Bootstrap durable schedules and reconcile scheduler mirrors: bounded regression coverage, never production writes."""
import pytest


def test_scheduler_mirrors():
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
        """Mirror durable owner/generation without redoing row migration."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/scheduler/scheduler.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_handoff_scheduler_mirrors.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Backfill shared logical schedule state from existing registration under a serialized bootstrap; preserve cursor/anchor/paused state and detect conflicting process mirrors.
- [ ] Make register_slug and startup consume durable desired owner/generation; APScheduler becomes a wakeup mirror, not a second due-time authority.
- [ ] Implement idempotent reconcile(operation_id) to remove stale source mirrors and install target mirrors without authorizing stale generation dispatch.
- [ ] Retain cadence and occurrence IDs through cutover; process wakeups and restart reconciliation cannot create an extra catch-up run beyond existing misfire policy.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_handoff_scheduler_mirrors.py -q`

## Test Specification
- Verify: Backfill shared logical schedule state from existing registration under a serialized bootstrap; preserve cursor/anchor/paused state and detect conflicting process mirrors.
- Verify: Make register_slug and startup consume durable desired owner/generation; APScheduler becomes a wakeup mirror, not a second due-time authority.
- Verify: Implement idempotent reconcile(operation_id) to remove stale source mirrors and install target mirrors without authorizing stale generation dispatch.
- Verify: Retain cadence and occurrence IDs through cutover; process wakeups and restart reconciliation cannot create an extra catch-up run beyond existing misfire policy.

## Agent Instructions
1. Start with `$sdd-start TASK-917` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker (resumed from WIP). handoff.py: bootstrap_row (serialized per owner/slug/kind, anchors persisted once, config change bumps config_revision, vanished schedules disabled), reconcile(operation_id) idempotent/retryable (scheduler_sync=failed keeps state reconciling), attach_mirror, heartbeat/unregister; ensure_schedule_state gained observed_paused/flag_trigger_change. scheduler.py: durable owner/generation consumed by register_slug/startup, fail-closed when bootstrap fails, mirror poller + reconcile_owner_change; APScheduler only wakes, admission via shared fence. 7 real-PostgreSQL tests pass.
