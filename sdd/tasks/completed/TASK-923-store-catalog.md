# TASK-923: Expose canonical management store descriptors

**Feature**: FEAT-176 — Query Store Management and Explicit Migration
**Spec**: `sdd/specs/query-store-management-and-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-911, TASK-912
**Assigned-to**: unassigned

## Context
This task implements the bounded store-catalog deliverable from approved FEAT-176, spec §§2–6. The user explicitly chose live schedule transfer and existing management permissions. No administrator-only restriction or manual per-slug deactivation may be added.

## Scope
- Implement catalog list with exact v1 credential-free descriptors and configured-default tenant null preference; deduplicate physical aliases.
- Apply current management read access; do not infer write grants, administrators-only roles or tenant schemas from auth program names.
- Return explicit unavailable/default errors instead of fabricating public readiness. Do not create tables or rediscover dynamically on each request.
- Cover custom default, same-schema custom default table versus tenant queries, allowlist, no-secrets and unsupported store cases.

**NOT in scope**: implementing other task outputs, changing the approved public contract, production data access, applying production DDL, automatic legacy migration, or creating a task worktree during planning.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/query_stores.py` | CREATE | Expose canonical management store descriptors; only the scoped changes above |
| `tests/tenants/test_query_store_catalog.py` | CREATE | Expose canonical management store descriptors; only the scoped changes above |

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
- **TASK-911 — Honor configured legacy defaults in tenant discovery**: consume its declared files/interfaces only after its index state is done. TASK-911: consume the interfaces/contract in querysource/tenants.py
- **TASK-912 — Freeze migration errors and cross-repository API fixtures**: consume its declared files/interfaces only after its index state is done. TASK-912: consume the interfaces/contract in querysource/tenant_errors.py

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
      "path": "querysource/handlers/query_stores.py",
      "action": "CREATE"
    },
    {
      "path": "tests/tenants/test_query_store_catalog.py",
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
Graph evidence: TASK-911: consume the interfaces/contract in querysource/tenants.py; TASK-912: consume the interfaces/contract in querysource/tenant_errors.py
Use existing asyncdb connection/transaction/advisory-lock conventions. New integration fixtures must be isolated and opt-in; do not reuse an unconfigured developer public schema. All source- and destination-row cutover mutations belong to one transaction.

## Implementation Blueprint

### Steps (in order)
1. Implement catalog list with exact v1 credential-free descriptors and configured-default tenant null preference; deduplicate physical aliases. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.
2. Apply current management read access; do not infer write grants, administrators-only roles or tenant schemas from auth program names. **Why:** the user retained existing management access and current scheduled-output grants.
3. Return explicit unavailable/default errors instead of fabricating public readiness. Do not create tables or rediscover dynamically on each request. **Why:** this preserves the bounded store-catalog deliverable while keeping unrelated work and production data unchanged.
4. Cover custom default, same-schema custom default table versus tenant queries, allowlist, no-secrets and unsupported store cases. **Why:** metadata and physical storage identity differ, especially for legacy and duplicate slug names.

### `querysource/handlers/query_stores.py` (CREATE)
```python path=querysource/handlers/query_stores.py
"""Expose canonical management store descriptors — new public contracts defined by this task."""
from collections.abc import Mapping
from typing import Any
from aiohttp import web

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore

class QueryStoreCatalog:
    async def list(self, request: web.Request) -> web.Response:
        """Return registered v1 store descriptors under current management access."""

# FILL IN: implement only this task's API/SQL branches, using its ordered steps
# and acceptance criteria. No source query execution for move verification.
```
**Why this shape:** this is the whole-file starting point for the scoped deliverable, not a completed implementation. Fill in only the branches/assertions bounded by this task; keep approved signatures and payload fields.

### `tests/tenants/test_query_store_catalog.py` (CREATE)
```python path=tests/tenants/test_query_store_catalog.py
"""Expose canonical management store descriptors: bounded regression coverage, never production writes."""
import pytest


def test_store_catalog():
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
class QueryStoreCatalog:
    async def list(self, request: web.Request) -> web.Response:
        """Return registered v1 store descriptors under current management access."""
```
This interface is created/extended by this task and consumed by its dependents; it is not an already-existing verified source symbol. Complete bodies using the scope and acceptance criteria.

### FILL IN checklist
- [ ] `querysource/handlers/query_stores.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.
- [ ] `tests/tenants/test_query_store_catalog.py` — implement only its scoped branches/assertions, preserving the approved owner/live-transfer/error contract.

## Acceptance Criteria
- [ ] Implement catalog list with exact v1 credential-free descriptors and configured-default tenant null preference; deduplicate physical aliases.
- [ ] Apply current management read access; do not infer write grants, administrators-only roles or tenant schemas from auth program names.
- [ ] Return explicit unavailable/default errors instead of fabricating public readiness. Do not create tables or rediscover dynamically on each request.
- [ ] Cover custom default, same-schema custom default table versus tenant queries, allowlist, no-secrets and unsupported store cases.
- [ ] Every declared validation command passes; tests cover success, denied/invalid input and relevant recovery/race behavior, not implementation stubs.
- [ ] No unfinished blueprint marker remains in completed implementation files; unrelated files and user data are unchanged.

## Validation Commands
- `pytest tests/tenants/test_query_store_catalog.py -q`

## Test Specification
- Verify: Implement catalog list with exact v1 credential-free descriptors and configured-default tenant null preference; deduplicate physical aliases.
- Verify: Apply current management read access; do not infer write grants, administrators-only roles or tenant schemas from auth program names.
- Verify: Return explicit unavailable/default errors instead of fabricating public readiness. Do not create tables or rediscover dynamically on each request.
- Verify: Cover custom default, same-schema custom default table versus tenant queries, allowlist, no-secrets and unsupported store cases.

## Agent Instructions
1. Start with `$sdd-start TASK-923` so the implementation machine provisions the feature worktree; this planning run creates no task worktree.
2. Read `sdd/specs/query-store-management-and-migration.spec.md` and check every dependency is done in `sdd/tasks/index/query-store-management-and-migration.json`.
3. Verify current source attachments and dependency-provided APIs; update stale task evidence before editing. Do not invent missing interfaces.
4. Mark in-progress, implement only declared files, run the scoped Validation Commands and required framework checks.
5. Commit only scoped implementation/SDD state and close the task using the repository close-task tooling; fill in Completion Note.

## Completion Note
Sequential sdd-worker. handlers/query_stores.py: QueryStoreCatalog.list + catalog_descriptors — exact v1 {version:1,data:[StoreDescriptor]}, configured default first with tenant null, physical aliases deduplicated, only selectors that round-trip through registry.resolve are offered (custom default table, literal public and same-schema tenant queries stay distinct), registry snapshot only (no rediscovery/DDL), no host/DSN/credentials/diagnostics/run-as, 503 tenant_store_unavailable when the registry is unpublished or the configured default is unavailable (never public). Auth: same app-level management boundary as CRUD; listing grants nothing. 8 tests pass incl. shared v1 fixture equality.
