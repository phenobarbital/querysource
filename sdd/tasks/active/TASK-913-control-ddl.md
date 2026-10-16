# TASK-913: Define durable migration and scheduler control tables

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

## Context
This task implements the bounded control-ddl deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Define explicitly deployed management.qs_query_moves, qs_schedule_state, qs_schedule_runs and qs_scheduler_workers with the fields and immutable identity constraints in spec section 2.
- Enforce unique deployment/actor idempotency keys, canonical identity reservations, stable logical schedule IDs, occurrence uniqueness and recovery lease metadata.
- Provide repeatable safe installation/backfill prerequisites and least database privileges; do not create tenant query tables or run DDL at application startup.
- Apply to a disposable PostgreSQL database and verify uniqueness/foreign keys/lease and cursor columns. Never use production metadata or business tables.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/sql/query_move_control.sql` | CREATE | Define durable migration and scheduler control tables; only the scoped changes above |
| `tests/integration/test_query_move_control_schema.py` | CREATE | Define durable migration and scheduler control tables; only the scoped changes above |

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
      "path": "docs/sql/query_move_control.sql",
      "action": "CREATE"
    },
    {
      "path": "tests/integration/test_query_move_control_schema.py",
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
Graph evidence: Exclusive: applies DDL or drives concurrent fault/race scenarios against shared disposable PostgreSQL resources; never production.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Define explicitly deployed management.qs_query_moves, qs_schedule_state, qs_schedule_runs and qs_scheduler_workers with the fields and immutable identity constraints in spec section 2. **Why:** this preserves the bounded control-ddl deliverable while keeping unrelated work and production data unchanged.
2. Enforce unique deployment/actor idempotency keys, canonical identity reservations, stable logical schedule IDs, occurrence uniqueness and recovery lease metadata. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
3. Provide repeatable safe installation/backfill prerequisites and least database privileges; do not create tenant query tables or run DDL at application startup. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
4. Apply to a disposable PostgreSQL database and verify uniqueness/foreign keys/lease and cursor columns. Never use production metadata or business tables. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.

### `docs/sql/query_move_control.sql` (CREATE)
```sql path=docs/sql/query_move_control.sql
-- Explicit deployment artifact; never executed during application startup.
BEGIN;
-- FILL IN: create management.qs_query_moves with canonical request identity,
-- UUID key, state, cutover outcome, actor, lease and timestamps (spec section 2).
-- FILL IN: create qs_schedule_state with stable logical UUID, generation,
-- desired trigger/cursor/pause/gate/max_instances and configuration revision.
-- FILL IN: create qs_schedule_runs with occurrence-unique admission/outcome.
-- FILL IN: create qs_scheduler_workers with protocol/heartbeat metadata.
-- FILL IN: add reviewed constraints, indexes and database grants; ensure safe
-- repeatable installation and no creation/modification of tenant queries tables.
COMMIT;
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `tests/integration/test_query_move_control_schema.py` (CREATE)
```python path=tests/integration/test_query_move_control_schema.py
"""Define durable migration and scheduler control tables: bounded regression coverage, never production writes."""
import pytest


def test_control_ddl():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `docs/sql/query_move_control.sql` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/integration/test_query_move_control_schema.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Define explicitly deployed management.qs_query_moves, qs_schedule_state, qs_schedule_runs and qs_scheduler_workers with the fields and immutable identity constraints in spec section 2.
- [ ] Enforce unique deployment/actor idempotency keys, canonical identity reservations, stable logical schedule IDs, occurrence uniqueness and recovery lease metadata.
- [ ] Provide repeatable safe installation/backfill prerequisites and least database privileges; do not create tenant query tables or run DDL at application startup.
- [ ] Apply to a disposable PostgreSQL database and verify uniqueness/foreign keys/lease and cursor columns. Never use production metadata or business tables.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/integration/test_query_move_control_schema.py -q`

## Test Specification
- Verify: Define explicitly deployed management.qs_query_moves, qs_schedule_state, qs_schedule_runs and qs_scheduler_workers with the fields and immutable identity constraints in spec section 2.
- Verify: Enforce unique deployment/actor idempotency keys, canonical identity reservations, stable logical schedule IDs, occurrence uniqueness and recovery lease metadata.
- Verify: Provide repeatable safe installation/backfill prerequisites and least database privileges; do not create tenant query tables or run DDL at application startup.
- Verify: Apply to a disposable PostgreSQL database and verify uniqueness/foreign keys/lease and cursor columns. Never use production metadata or business tables.
- Use explicit disposable service DSNs with namespaced resources; missing credentials skip external tests rather than default to developer/production databases. No shared conftest mutation is allowed.

## Agent Instructions
1. Start with `$sdd-start TASK-913` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
