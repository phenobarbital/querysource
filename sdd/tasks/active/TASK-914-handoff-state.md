# TASK-914: Create shared handoff state and readiness accessors

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-912, TASK-913
**Assigned-to**: unassigned

## Context
This task implements the bounded handoff-state deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Create ScheduleHandoffCoordinator with an injected async connection factory and read-only state/readiness helpers backed by the approved control tables.
- Implement canonical owner/slug/kind identity, stable logical schedule UUID, configuration revision, desired pause state, worker heartbeat protocol and deployment readiness checks.
- Provide connection-bound journal/state helpers so cutover can use one caller-owned transaction rather than nesting public operations.
- Make preview readiness read-only. Unscheduled moves need journal/recovery support but no enabled QSScheduler; unsupported engines or old worker rollout block scheduled handoff.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/handoff.py` | CREATE | Create shared handoff state and readiness accessors; only the scoped changes above |
| `tests/tenants/test_handoff_state.py` | CREATE | Create shared handoff state and readiness accessors; only the scoped changes above |

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
- **TASK-912 — Freeze migration errors and cross-repository API fixtures**: consume its declared files/interfaces only after its index state is done. TASK-912: consume the interfaces/contract in querysource/tenant_errors.py
- **TASK-913 — Define durable migration and scheduler control tables**: consume its declared files/interfaces only after its index state is done. TASK-913: consume the interfaces/contract in docs/sql/query_move_control.sql

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
      "action": "CREATE"
    },
    {
      "path": "tests/tenants/test_handoff_state.py",
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
Graph evidence: TASK-912: consume the interfaces/contract in querysource/tenant_errors.py; TASK-913: consume the interfaces/contract in docs/sql/query_move_control.sql
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Create ScheduleHandoffCoordinator with an injected async connection factory and read-only state/readiness helpers backed by the approved control tables. **Why:** this preserves the bounded handoff-state deliverable while keeping unrelated work and production data unchanged.
2. Implement canonical owner/slug/kind identity, stable logical schedule UUID, configuration revision, desired pause state, worker heartbeat protocol and deployment readiness checks. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
3. Provide connection-bound journal/state helpers so cutover can use one caller-owned transaction rather than nesting public operations. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
4. Make preview readiness read-only. Unscheduled moves need journal/recovery support but no enabled QSScheduler; unsupported engines or old worker rollout block scheduled handoff. **Why:** lost acknowledgements and process restarts cannot authorize a second move or replay.

### `querysource/scheduler/handoff.py` (CREATE)
```python path=querysource/scheduler/handoff.py
"""Create shared handoff state and readiness accessors — new public contracts defined by this task."""
from collections.abc import Mapping
from typing import Any
from aiohttp import web

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore

class ScheduleHandoffCoordinator:
    def __init__(self, repository: DefinitionRepository, *, deployment_id: str, worker_id: str, protocol_version: int = 1) -> None:
        """Use repository.registry/factory for shared state and owner resolution."""
    async def inspect(self, source: QueryIdentity, destination: QueryStore) -> dict:
        """Read readiness, desired schedules and blockers without mutation."""
    async def prepare(self, operation_id: str) -> Mapping[str, Any]:
        """Freeze new admissions for the existing durable operation."""

# FILL IN: implement only this task's API/SQL branches, using its ordered steps
# and acceptance criteria. No source query execution for move verification.
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `tests/tenants/test_handoff_state.py` (CREATE)
```python path=tests/tenants/test_handoff_state.py
"""Create shared handoff state and readiness accessors: bounded regression coverage, never production writes."""
import pytest


def test_handoff_state():
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
class ScheduleHandoffCoordinator:
    def __init__(self, repository: DefinitionRepository, *, deployment_id: str, worker_id: str, protocol_version: int = 1) -> None:
        """Use repository.registry/factory for shared state and owner resolution."""
    async def inspect(self, source: QueryIdentity, destination: QueryStore) -> dict:
        """Read readiness, desired schedules and blockers without mutation."""
    async def prepare(self, operation_id: str) -> Mapping[str, Any]:
        """Freeze new admissions for the existing durable operation."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/scheduler/handoff.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_handoff_state.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Create ScheduleHandoffCoordinator with an injected async connection factory and read-only state/readiness helpers backed by the approved control tables.
- [ ] Implement canonical owner/slug/kind identity, stable logical schedule UUID, configuration revision, desired pause state, worker heartbeat protocol and deployment readiness checks.
- [ ] Provide connection-bound journal/state helpers so cutover can use one caller-owned transaction rather than nesting public operations.
- [ ] Make preview readiness read-only. Unscheduled moves need journal/recovery support but no enabled QSScheduler; unsupported engines or old worker rollout block scheduled handoff.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_handoff_state.py -q`

## Test Specification
- Verify: Create ScheduleHandoffCoordinator with an injected async connection factory and read-only state/readiness helpers backed by the approved control tables.
- Verify: Implement canonical owner/slug/kind identity, stable logical schedule UUID, configuration revision, desired pause state, worker heartbeat protocol and deployment readiness checks.
- Verify: Provide connection-bound journal/state helpers so cutover can use one caller-owned transaction rather than nesting public operations.
- Verify: Make preview readiness read-only. Unscheduled moves need journal/recovery support but no enabled QSScheduler; unsupported engines or old worker rollout block scheduled handoff.

## Agent Instructions
1. Start with `$sdd-start TASK-914` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
