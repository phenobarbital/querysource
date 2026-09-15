# TASK-724: Route management mutations through selected-owner repository

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-723
**Assigned-to**: unassigned

## Context

Implements M4 CRUD of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves.
- Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions.
- Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back.
- Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes.

**NOT in scope**: Scheduler identities and startup; those are separate tasks. Do not create a permanent stub for the later sync hook.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | Each verb resolves ownership before its existence checks and mutations. |
| `tests/tenants/test_tenant_management_writes.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.handlers.tenant import resolve_request_store
from querysource.tenants import QueryIdentity
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.handlers.tenant` → planned `querysource/handlers/tenant.py` created by TASK-723; verify after prerequisite
- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/handlers/manager.py:253
async def patch(self):

querysource/handlers/manager.py:323
async def delete(self):

querysource/handlers/manager.py:32
class QueryManager(QueryView):
async def get(self):
async def patch(self):
async def delete(self):
async def put(self):
async def post(self):
```

### Does NOT Exist

- The new tenant registry, repository, model, handler and identity helpers do not
  exist at decomposition time except as dependency blueprints. Do not import them
  until the creating prerequisite is complete.
- No `querysource.remote` implementation exists in this checkout. The versioned
  tenant callable is an external contract, never a public fallback.
- No `tenant` parameter on the current provider checksum or compiled parser
  interface exists; use immutable execution identity and the runtime adapter.

## Implementation Notes

### Pattern to Follow

Use the exact interfaces in spec §3 and the verified existing signatures above.
Modify existing classes in place; a class wrapper in a MODIFY block locates new
methods, not permission to replace the entire class. Preserve unrelated methods,
metaclasses, inheritance, decorators and initialization behavior. CREATE blocks
are whole-file starting points; all bounded FILL IN markers must be completed.

### Key Constraints

- No per-request shared Meta, search_path, global field-policy or pooled-connection
  mutation. No owner fallback. Preserve configured legacy defaults and overrides.
- Tenant is structural definition ownership; SQL may read other permitted schemas.
- Keep strict new type hints, async I/O, existing logging, black formatting and
  existing dependencies. Do not add mandatory PBAC/membership requirements.
- Do not change fixed spec signatures. Refresh anchors after dependencies because
  earlier tasks modify shared files. If a fixed contract cannot work, report the
  concrete mismatch instead of silently changing ownership behavior.

## Implementation Blueprint

### Steps (in order)

1. Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves. **Why:** Routing metadata must not reach model fields or WHERE predicates.
2. Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions. **Why:** Existing clients depend on verb-specific statuses and upsert behavior.
3. Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back. **Why:** Scheduler synchronization occurs after database commit.
4. Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes. **Why:** Error handling must not reveal other owners or SQL.

### `querysource/handlers/manager.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryManager(QueryView):` (verified: querysource/handlers/manager.py:32)
async def patch(self):
    """Resolve selected store; call repository patch; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository patch; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def delete(self):
    """Resolve selected store; call repository delete; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository delete; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def put(self):
    """Resolve selected store; call repository upsert; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository upsert; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def post(self):
    """Resolve selected store; call repository upsert; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository upsert; preserve statuses and strip routing metadata.
    raise NotImplementedError
```

**Why:** Each verb resolves ownership before its existence checks and mutations.

### `tests/tenants/test_tenant_management_writes.py` (CREATE)

```python
"""Route management mutations through selected-owner repository regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_all_crud_methods_target_selected_store() -> None:
    """all crud methods target selected store."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_null_and_conflicting_write_selectors() -> None:
    """null and conflicting write selectors."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_patch_immutable_slug_and_program_rejection() -> None:
    """patch immutable slug and program rejection."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_upsert_and_delete_statuses() -> None:
    """legacy upsert and delete statuses."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/handlers/manager.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_management_writes.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves.
- [ ] AC-2: Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions.
- [ ] AC-3: Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back.
- [ ] AC-4: Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_management_writes.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_all_crud_methods_target_selected_store` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_null_and_conflicting_write_selectors` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_patch_immutable_slug_and_program_rejection` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_upsert_and_delete_statuses` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

## Agent Instructions

1. Read the approved spec and completed dependency packet(s).
2. Refresh source anchors and planned imports after dependencies land; update this
   packet before coding if actual source moved.
3. Set this task in `sdd/tasks/index/per-tenant-queries.json` to `in-progress`.
4. Apply each bounded blueprint, complete every FILL IN branch and test, and
   preserve the five user decisions and fixed spec interfaces.
5. Run focused checks, record results and any unavailable external integration.
6. Move this task file to `sdd/tasks/completed/`, set index status to `done`, and
   fill the completion note. Do not update the historical monolithic index.

## Completion Note

To be completed by the implementing agent: author/date, exact checks and results,
files changed, deployment gates still unverified, and any approved spec deviations.
