# TASK-921: Commit definition and live schedule ownership atomically

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-910, TASK-918, TASK-920
**Assigned-to**: unassigned

## Context
This task implements the bounded atomic-cutover deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- After automatic prepare/drain, acquire ordered exclusive fences and source FOR UPDATE on one connection; revalidate source, target, grants and confirmed plan.
- Use plain target INSERT/readback and exactly-one source DELETE in the same transaction as journal cutover and durable schedule owner/generation/cursor changes.
- Preserve all business fields/provenance and original scheduled run-as. Append source clear and target set audit events attributed to mover only for a nonnull transfer.
- Fault-inject insert/readback/delete and acknowledged/pre-ack commit failures. Roll back known precommit failures; resolve ambiguous COMMIT through the same durable journal, never claim automatic rollback.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/query_moves.py` | MODIFY | Commit definition and live schedule ownership atomically; only the scoped changes above |
| `tests/integration/test_query_moves_postgres.py` | CREATE | Commit definition and live schedule ownership atomically; only the scoped changes above |

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
- **TASK-910 — Restore contract-specific listing metadata and export**: consume its declared files/interfaces only after its index state is done. TASK-910: consume the interfaces/contract in querysource/repositories/definitions.py
- **TASK-918 — Coordinate pause resume sync and reserved identities**: consume its declared files/interfaces only after its index state is done. TASK-918: consume the interfaces/contract in querysource/handlers/scheduler.py
- **TASK-920 — Reserve durable move operations and expose status**: consume its declared files/interfaces only after its index state is done. TASK-920: serialize overlapping writer of querysource/query_moves.py
- `querysource/query_moves.py` is a planned output of **TASK-919**, not an existing source file at decomposition time. Its creation blueprint fixes the class/module anchor; verify actual source/hash/count after that dependency lands before editing.

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
      "path": "tests/integration/test_query_moves_postgres.py",
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
Graph evidence: TASK-910: consume the interfaces/contract in querysource/repositories/definitions.py; TASK-918: consume the interfaces/contract in querysource/handlers/scheduler.py; TASK-920: serialize overlapping writer of querysource/query_moves.py; Exclusive: applies DDL or drives concurrent fault/race scenarios against shared disposable PostgreSQL resources; never production.
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. After automatic prepare/drain, acquire ordered exclusive fences and source FOR UPDATE on one connection; revalidate source, target, grants and confirmed plan. **Why:** the user retained existing management access and current scheduled-output grants.
2. Use plain target INSERT/readback and exactly-one source DELETE in the same transaction as journal cutover and durable schedule owner/generation/cursor changes. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.
3. Preserve all business fields/provenance and original scheduled run-as. Append source clear and target set audit events attributed to mover only for a nonnull transfer. **Why:** this preserves the bounded atomic-cutover deliverable while keeping unrelated work and production data unchanged.
4. Fault-inject insert/readback/delete and acknowledged/pre-ack commit failures. Roll back known precommit failures; resolve ambiguous COMMIT through the same durable journal, never claim automatic rollback. **Why:** definition ownership and schedule state must agree after both successful and interrupted operations.

### `querysource/query_moves.py` (MODIFY)
```python
# Planned attachment from TASK-919; not current source.
# Expected unique anchor: class QueryMoveService:
# FILL IN: verify actual anchor/hash/count after prerequisite completion, then
# implement only this task scope without reverting earlier task behavior.
```
**Why:** the file does not exist before its creator task. The expected anchor comes from that task's explicit creation blueprint, not a fabricated source reference.

### `tests/integration/test_query_moves_postgres.py` (CREATE)
```python path=tests/integration/test_query_moves_postgres.py
"""Commit definition and live schedule ownership atomically: bounded regression coverage, never production writes."""
import pytest


def test_atomic_cutover():
    """Exercise the concrete success/failure cases in Test Specification."""
    # FILL IN: arrange isolated fixtures, perform the scoped operation and assert
    # business behavior; use pytest.mark.asyncio where asynchronous execution is needed.
    # FILL IN: each assertion must cover the acceptance criteria below, not
    # merely reproduce the implementation or check that a stub exists.
    ...
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### FILL IN checklist
- [ ] `querysource/query_moves.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/integration/test_query_moves_postgres.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] After automatic prepare/drain, acquire ordered exclusive fences and source FOR UPDATE on one connection; revalidate source, target, grants and confirmed plan.
- [ ] Use plain target INSERT/readback and exactly-one source DELETE in the same transaction as journal cutover and durable schedule owner/generation/cursor changes.
- [ ] Preserve all business fields/provenance and original scheduled run-as. Append source clear and target set audit events attributed to mover only for a nonnull transfer.
- [ ] Fault-inject insert/readback/delete and acknowledged/pre-ack commit failures. Roll back known precommit failures; resolve ambiguous COMMIT through the same durable journal, never claim automatic rollback.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/integration/test_query_moves_postgres.py -q`

## Test Specification
- Verify: After automatic prepare/drain, acquire ordered exclusive fences and source FOR UPDATE on one connection; revalidate source, target, grants and confirmed plan.
- Verify: Use plain target INSERT/readback and exactly-one source DELETE in the same transaction as journal cutover and durable schedule owner/generation/cursor changes.
- Verify: Preserve all business fields/provenance and original scheduled run-as. Append source clear and target set audit events attributed to mover only for a nonnull transfer.
- Verify: Fault-inject insert/readback/delete and acknowledged/pre-ack commit failures. Roll back known precommit failures; resolve ambiguous COMMIT through the same durable journal, never claim automatic rollback.
- Use explicit disposable service DSNs with namespaced resources; missing credentials skip external tests rather than default to developer/production databases. No shared conftest mutation is allowed.

## Agent Instructions
1. Start with `$sdd-start TASK-921` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Pending implementation. Record implementer, commit, validation evidence, deviations and any recovery/rollout evidence here when done.
