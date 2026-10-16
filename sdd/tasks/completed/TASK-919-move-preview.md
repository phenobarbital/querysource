# TASK-919: Implement read-only move plans and dependency analysis

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-909, TASK-911, TASK-914
**Assigned-to**: unassigned

## Context
This task implements the bounded move-preview deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Create QueryMoveService.preview with explicit source/destination identities, raw source revision, collision inspection and read-only coordinator readiness.
- Inventory supported query/multi/cache schedules; valid live schedules are not blockers. Digest configuration revision but exclude naturally advancing cursor/heartbeats.
- Analyze saved queries and known Query components for omitted tenant inheritance; emit actionable blockers, preserving alias/slug and explicit tenant null distinctions.
- Always report external consumers and runtime program change; enforce exact approved acknowledgement rules. Classify stale unscheduled run-as separately from valid scheduled run-as.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/query_moves.py` | CREATE | Implement read-only move plans and dependency analysis; only the scoped changes above |
| `tests/tenants/test_query_moves.py` | CREATE | Implement read-only move plans and dependency analysis; only the scoped changes above |

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
- **TASK-909 — Restore legacy writes and program-aware revisions**: consume its declared files/interfaces only after its index state is done. TASK-909: consume the interfaces/contract in querysource/repositories/definitions.py
- **TASK-911 — Honor configured legacy defaults in tenant discovery**: consume its declared files/interfaces only after its index state is done. TASK-911: consume the interfaces/contract in querysource/tenants.py
- **TASK-914 — Create shared handoff state and readiness accessors**: consume its declared files/interfaces only after its index state is done. TASK-914: consume the interfaces/contract in querysource/scheduler/handoff.py

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
      "action": "CREATE"
    },
    {
      "path": "tests/tenants/test_query_moves.py",
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
Graph evidence: TASK-909: consume the interfaces/contract in querysource/repositories/definitions.py; TASK-911: consume the interfaces/contract in querysource/tenants.py; TASK-914: consume the interfaces/contract in querysource/scheduler/handoff.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Create QueryMoveService.preview with explicit source/destination identities, raw source revision, collision inspection and read-only coordinator readiness. **Why:** this preserves the bounded move-preview deliverable while keeping unrelated work and production data unchanged.
2. Inventory supported query/multi/cache schedules; valid live schedules are not blockers. Digest configuration revision but exclude naturally advancing cursor/heartbeats. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
3. Analyze saved queries and known Query components for omitted tenant inheritance; emit actionable blockers, preserving alias/slug and explicit tenant null distinctions. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Always report external consumers and runtime program change; enforce exact approved acknowledgement rules. Classify stale unscheduled run-as separately from valid scheduled run-as. **Why:** this preserves the bounded move-preview deliverable while keeping unrelated work and production data unchanged.

### `querysource/query_moves.py` (CREATE)
```python path=querysource/query_moves.py
"""Implement read-only move plans and dependency analysis — new public contracts defined by this task."""
from collections.abc import Mapping
from typing import Any
from aiohttp import web

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore

class QueryMoveService:
    def __init__(self, repository: DefinitionRepository, coordinator: ScheduleHandoffCoordinator) -> None:
        """Inject the existing saved-definition boundary and live coordinator."""
    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Return deterministic read-only live-transfer plan; no source writes."""

# FILL IN: implement only this task's API/SQL branches, using its ordered steps
# and acceptance criteria. No source query execution for move verification.
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `tests/tenants/test_query_moves.py` (CREATE)
```python path=tests/tenants/test_query_moves.py
"""Implement read-only move plans and dependency analysis: bounded regression coverage, never production writes."""
import pytest


def test_move_preview():
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
class QueryMoveService:
    def __init__(self, repository: DefinitionRepository, coordinator: ScheduleHandoffCoordinator) -> None:
        """Inject the existing saved-definition boundary and live coordinator."""
    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Return deterministic read-only live-transfer plan; no source writes."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/query_moves.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_moves.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Create QueryMoveService.preview with explicit source/destination identities, raw source revision, collision inspection and read-only coordinator readiness.
- [ ] Inventory supported query/multi/cache schedules; valid live schedules are not blockers. Digest configuration revision but exclude naturally advancing cursor/heartbeats.
- [ ] Analyze saved queries and known Query components for omitted tenant inheritance; emit actionable blockers, preserving alias/slug and explicit tenant null distinctions.
- [ ] Always report external consumers and runtime program change; enforce exact approved acknowledgement rules. Classify stale unscheduled run-as separately from valid scheduled run-as.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_moves.py -q`

## Test Specification
- Verify: Create QueryMoveService.preview with explicit source/destination identities, raw source revision, collision inspection and read-only coordinator readiness.
- Verify: Inventory supported query/multi/cache schedules; valid live schedules are not blockers. Digest configuration revision but exclude naturally advancing cursor/heartbeats.
- Verify: Analyze saved queries and known Query components for omitted tenant inheritance; emit actionable blockers, preserving alias/slug and explicit tenant null distinctions.
- Verify: Always report external consumers and runtime program change; enforce exact approved acknowledgement rules. Classify stale unscheduled run-as separately from valid scheduled run-as.

## Agent Instructions
1. Start with `$sdd-start TASK-919` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. query_moves.py: QueryMoveService.preview (read-only MovePlan; explicit identities, raw source row digest incl. legacy program/run-as, collision blocker, reservation + coordinator readiness/inspect, schedule inventory without cursor/heartbeat in digest), analyze_references (inherited vs pinned vs explicit-null children, unknown/dynamic forms blocked with path, SQL never scanned), consumer warning always + runtime_program before/after, required_acknowledgements (program ack only if differs), stale unscheduled run-as vs valid scheduled run-as with audit-table readiness. Uses repository._fetch_row for the raw row (no new public persistence API). 13 tests pass (real PostgreSQL control plane, fake repository).
