# TASK-918: Coordinate pause resume sync and reserved identities

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-917
**Assigned-to**: unassigned

## Context
This task implements the bounded scheduler-controls deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Route scheduler pause/resume/removal/sync through durable desired state while retaining current HTTP access checks and scheduled-output grants.
- Reject writes to a reserved moving source/destination with move_in_progress; a local pause or sync must not bypass a frozen shared generation.
- Increment configuration revision for desired-state changes but not ordinary due cursor advancement; preserve job IDs and response conventions.
- Exercise custom-default versus literal-public owners with the same slug; never map every legacy-contract store to tenant null.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/scheduler.py` | MODIFY | Coordinate pause resume sync and reserved identities; only the scoped changes above |
| `querysource/scheduler/handoff.py` | MODIFY | Coordinate pause resume sync and reserved identities; only the scoped changes above |
| `tests/tenants/test_handoff_scheduler_controls.py` | CREATE | Coordinate pause resume sync and reserved identities; only the scoped changes above |

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
- `querysource/handlers/scheduler.py:74` — unique attachment `class SchedulerJobsView(BaseView):`; occurrences **1**; SHA-256 `6e318dc312f259b21d6af864006d0de3bd936774cc7497201c7f92437beb1810`.
  Existing import at `querysource/handlers/scheduler.py:20`: `from __future__ import annotations`.
  Existing import at `querysource/handlers/scheduler.py:22`: `import logging`.
  Existing import at `querysource/handlers/scheduler.py:23`: `from typing import TYPE_CHECKING`.
- Wiki-confirmed existing symbol ID: `sym:querysource/handlers/scheduler.py#SchedulerJobsView` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-917 — Bootstrap durable schedules and reconcile scheduler mirrors**: consume its declared files/interfaces only after its index state is done. TASK-917: serialize overlapping writer of querysource/scheduler/handoff.py
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
      "path": "querysource/handlers/scheduler.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/scheduler/handoff.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_handoff_scheduler_controls.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/handlers/scheduler.py#SchedulerJobsView",
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-917: serialize overlapping writer of querysource/scheduler/handoff.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Route scheduler pause/resume/removal/sync through durable desired state while retaining current HTTP access checks and scheduled-output grants. **Why:** the user retained existing management access and current scheduled-output grants.
2. Reject writes to a reserved moving source/destination with move_in_progress; a local pause or sync must not bypass a frozen shared generation. **Why:** this preserves the bounded scheduler-controls deliverable while keeping unrelated work and production data unchanged.
3. Increment configuration revision for desired-state changes but not ordinary due cursor advancement; preserve job IDs and response conventions. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
4. Exercise custom-default versus literal-public owners with the same slug; never map every legacy-contract store to tenant null. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/handlers/scheduler.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/handlers/scheduler.py:74
# class SchedulerJobsView(BaseView):
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

### `tests/tenants/test_handoff_scheduler_controls.py` (CREATE)
```python path=tests/tenants/test_handoff_scheduler_controls.py
"""Coordinate pause resume sync and reserved identities: bounded regression coverage, never production writes."""
import pytest


def test_scheduler_controls():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `querysource/handlers/scheduler.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_handoff_scheduler_controls.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Route scheduler pause/resume/removal/sync through durable desired state while retaining current HTTP access checks and scheduled-output grants.
- [ ] Reject writes to a reserved moving source/destination with move_in_progress; a local pause or sync must not bypass a frozen shared generation.
- [ ] Increment configuration revision for desired-state changes but not ordinary due cursor advancement; preserve job IDs and response conventions.
- [ ] Exercise custom-default versus literal-public owners with the same slug; never map every legacy-contract store to tenant null.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_handoff_scheduler_controls.py -q`

## Test Specification
- Verify: Route scheduler pause/resume/removal/sync through durable desired state while retaining current HTTP access checks and scheduled-output grants.
- Verify: Reject writes to a reserved moving source/destination with move_in_progress; a local pause or sync must not bypass a frozen shared generation.
- Verify: Increment configuration revision for desired-state changes but not ordinary due cursor advancement; preserve job IDs and response conventions.
- Verify: Exercise custom-default versus literal-public owners with the same slug; never map every legacy-contract store to tenant null.

## Agent Instructions
1. Start with `$sdd-start TASK-918` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
