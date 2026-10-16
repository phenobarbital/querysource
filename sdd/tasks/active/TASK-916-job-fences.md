# TASK-916: Fence query multi and cache job execution

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-911, TASK-915
**Assigned-to**: unassigned

## Context
This task implements the bounded job-fences deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Gate scheduled_query_job, scheduled_multiqs_job and cache_refresh_job before constructing/loading QS/MultiQS; a stale generation skips without business execution.
- Hold the admitted shared owner lock until local query/pipeline and awaited remote work finish; release the connection in finally and record completion/unknown attempts.
- Implement automatic drain waiting for admitted work via ordered exclusive owner locks after the admission gate freezes; never cancel an admitted source run.
- Resolve job owner using canonical configured-default physical equality rather than schema alone, and preserve original delegated run-as and existing notification IDs.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/jobs.py` | MODIFY | Fence query multi and cache job execution; only the scoped changes above |
| `querysource/scheduler/handoff.py` | MODIFY | Fence query multi and cache job execution; only the scoped changes above |
| `tests/tenants/test_live_schedule_handoff.py` | CREATE | Fence query multi and cache job execution; only the scoped changes above |

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
- `querysource/scheduler/jobs.py:61` — unique attachment `async def scheduled_query_job(`; occurrences **1**; SHA-256 `fc9b17dc04728664104b67139dee4dcb6ee793185ef8c4a8f02a015cb9b897d6`.
  Existing import at `querysource/scheduler/jobs.py:10`: `from __future__ import annotations`.
  Existing import at `querysource/scheduler/jobs.py:12`: `from typing import TYPE_CHECKING, Any`.
  Existing import at `querysource/scheduler/jobs.py:14`: `from navconfig.logging import logging`.
- Wiki-confirmed existing symbol ID: `sym:querysource/repositories/definitions.py#DefinitionRepository` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/scheduler/jobs.py#cache_refresh_job` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/scheduler/jobs.py#scheduled_multiqs_job` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/scheduler/jobs.py#scheduled_query_job` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryIdentity` (current source re-read).
- Wiki-confirmed existing symbol ID: `sym:querysource/tenants.py#QueryStore` (current source re-read).

### Dependency-provided Interfaces
- **TASK-911 — Honor configured legacy defaults in tenant discovery**: consume its declared files/interfaces only after its index state is done. TASK-911: consume the interfaces/contract in querysource/tenants.py
- **TASK-915 — Claim schedule occurrences once and preserve trigger cursors**: consume its declared files/interfaces only after its index state is done. TASK-915: serialize overlapping writer of querysource/scheduler/handoff.py
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
      "path": "querysource/scheduler/jobs.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/scheduler/handoff.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/tenants/test_live_schedule_handoff.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository",
    "sym:querysource/scheduler/jobs.py#cache_refresh_job",
    "sym:querysource/scheduler/jobs.py#scheduled_multiqs_job",
    "sym:querysource/scheduler/jobs.py#scheduled_query_job",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#QueryStore"
  ]
}
```

## Implementation Notes
Graph evidence: TASK-911: consume the interfaces/contract in querysource/tenants.py; TASK-915: serialize overlapping writer of querysource/scheduler/handoff.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Gate scheduled_query_job, scheduled_multiqs_job and cache_refresh_job before constructing/loading QS/MultiQS; a stale generation skips without business execution. **Why:** this preserves the bounded job-fences deliverable while keeping unrelated work and production data unchanged.
2. Hold the admitted shared owner lock until local query/pipeline and awaited remote work finish; release the connection in finally and record completion/unknown attempts. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Implement automatic drain waiting for admitted work via ordered exclusive owner locks after the admission gate freezes; never cancel an admitted source run. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Resolve job owner using canonical configured-default physical equality rather than schema alone, and preserve original delegated run-as and existing notification IDs. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/scheduler/jobs.py` (MODIFY)
```python
# occurrences: 1 (literal anchor count reverified at task creation)
# ATTACH at querysource/scheduler/jobs.py:61
# async def scheduled_query_job(
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

### `tests/tenants/test_live_schedule_handoff.py` (CREATE)
```python path=tests/tenants/test_live_schedule_handoff.py
"""Fence query multi and cache job execution: bounded regression coverage, never production writes."""
import pytest


def test_job_fences():
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
    async def drain(self, operation_id: str) -> Mapping[str, Any]:
        """Drain admitted runs without cancellation or replay."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/scheduler/jobs.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_live_schedule_handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Gate scheduled_query_job, scheduled_multiqs_job and cache_refresh_job before constructing/loading QS/MultiQS; a stale generation skips without business execution.
- [ ] Hold the admitted shared owner lock until local query/pipeline and awaited remote work finish; release the connection in finally and record completion/unknown attempts.
- [ ] Implement automatic drain waiting for admitted work via ordered exclusive owner locks after the admission gate freezes; never cancel an admitted source run.
- [ ] Resolve job owner using canonical configured-default physical equality rather than schema alone, and preserve original delegated run-as and existing notification IDs.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_live_schedule_handoff.py -q`

## Test Specification
- Verify: Gate scheduled_query_job, scheduled_multiqs_job and cache_refresh_job before constructing/loading QS/MultiQS; a stale generation skips without business execution.
- Verify: Hold the admitted shared owner lock until local query/pipeline and awaited remote work finish; release the connection in finally and record completion/unknown attempts.
- Verify: Implement automatic drain waiting for admitted work via ordered exclusive owner locks after the admission gate freezes; never cancel an admitted source run.
- Verify: Resolve job owner using canonical configured-default physical equality rather than schema alone, and preserve original delegated run-as and existing notification IDs.

## Agent Instructions
1. Start with `$sdd-start TASK-916` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
