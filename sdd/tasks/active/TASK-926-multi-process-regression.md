# TASK-926: Prove live transfer drain deduplication and crash recovery

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-925
**Assigned-to**: unassigned

## Context
This task implements the bounded multi-process-regression deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Use disposable PostgreSQL and two independently executing scheduler processes to race the same due occurrence and a source-to-target transfer.
- Hold an admitted source run during drain; verify target does not execute until it finishes and later due slots retain cadence/coalescing/paused state.
- Crash workers before freeze, during drain and after cutover; simulate lost commit acknowledgement, stale mirrors and missed notifications; assert one admitted occurrence and no cutover replay.
- Exercise query/multi/cache, same slug across stores, original run-as audit, control-pool exhaustion and unknown remote effects. Never execute the incident DELETE/append against business data.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_live_schedule_handoff_postgres.py` | CREATE | Prove live transfer drain deduplication and crash recovery; only the scoped changes above |

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
      "path": "tests/integration/test_live_schedule_handoff_postgres.py",
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
Graph evidence: TASK-925: consume the interfaces/contract in querysource/services.py; Exclusive: applies DDL or drives concurrent fault/race scenarios against shared disposable PostgreSQL resources; never production.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Use disposable PostgreSQL and two independently executing scheduler processes to race the same due occurrence and a source-to-target transfer. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
2. Hold an admitted source run during drain; verify target does not execute until it finishes and later due slots retain cadence/coalescing/paused state. **Why:** a live move must retain cadence and cannot admit a due occurrence twice.
3. Crash workers before freeze, during drain and after cutover; simulate lost commit acknowledgement, stale mirrors and missed notifications; assert one admitted occurrence and no cutover replay. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
4. Exercise query/multi/cache, same slug across stores, original run-as audit, control-pool exhaustion and unknown remote effects. Never execute the incident DELETE/append against business data. **Why:** this preserves the bounded multi-process-regression deliverable while keeping unrelated work and production data unchanged.

### `tests/integration/test_live_schedule_handoff_postgres.py` (CREATE)
```python path=tests/integration/test_live_schedule_handoff_postgres.py
"""Prove live transfer drain deduplication and crash recovery: bounded regression coverage, never production writes."""
import pytest


def test_multi_process_regression():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `tests/integration/test_live_schedule_handoff_postgres.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Use disposable PostgreSQL and two independently executing scheduler processes to race the same due occurrence and a source-to-target transfer.
- [ ] Hold an admitted source run during drain; verify target does not execute until it finishes and later due slots retain cadence/coalescing/paused state.
- [ ] Crash workers before freeze, during drain and after cutover; simulate lost commit acknowledgement, stale mirrors and missed notifications; assert one admitted occurrence and no cutover replay.
- [ ] Exercise query/multi/cache, same slug across stores, original run-as audit, control-pool exhaustion and unknown remote effects. Never execute the incident DELETE/append against business data.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/integration/test_live_schedule_handoff_postgres.py -q`

## Test Specification
- Verify: Use disposable PostgreSQL and two independently executing scheduler processes to race the same due occurrence and a source-to-target transfer.
- Verify: Hold an admitted source run during drain; verify target does not execute until it finishes and later due slots retain cadence/coalescing/paused state.
- Verify: Crash workers before freeze, during drain and after cutover; simulate lost commit acknowledgement, stale mirrors and missed notifications; assert one admitted occurrence and no cutover replay.
- Verify: Exercise query/multi/cache, same slug across stores, original run-as audit, control-pool exhaustion and unknown remote effects. Never execute the incident DELETE/append against business data.
- Use explicit disposable service DSNs with namespaced resources; missing credentials skip external tests rather than default to developer/production databases. No shared conftest mutation is allowed.

## Agent Instructions
1. Start with `$sdd-start TASK-926` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
