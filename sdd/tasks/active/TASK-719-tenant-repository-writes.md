# TASK-719: Implement atomic definition mutations

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-718
**Assigned-to**: unassigned

## Context

Implements M2 mutation boundary of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Add create/upsert/patch/delete to the existing dependency repository. Bind every data value and return persisted row mappings, not detached runtime objects.
- Implement atomic upsert with an accurate created flag for existing PUT/POST statuses. Protect the absent-row race using PostgreSQL conflict/transaction handling, and retain database defaults for omitted fields.
- PATCH updates only provided mutable fields; reject owner/slug movement and tenant program_slug. Keep null distinct from omitted data. Respect created/updated timestamp behavior.
- Translate write grant failures to tenant_write_forbidden and runtime table loss to tenant_store_unavailable; do not turn validation or missing-row errors into fallback queries.

**NOT in scope**: HTTP wiring and scheduler synchronization.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | Add methods to the dependency-created class; preserve read methods and shared validation. |
| `tests/tenants/test_tenant_repository_writes.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryIdentity, QueryStore
from querysource.tenant_models import TenantQueryDefinition
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.tenant_models` → planned `querysource/tenant_models.py` created by TASK-717; verify after prerequisite

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/handlers/manager.py:397
async def put(self):

querysource/handlers/manager.py:461
async def post(self):

querysource/handlers/manager.py:253
async def patch(self):
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

1. Add create/upsert/patch/delete to the existing dependency repository. Bind every data value and return persisted row mappings, not detached runtime objects. **Why:** Detached runtime fields must never be persisted to tenant tables.
2. Implement atomic upsert with an accurate created flag for existing PUT/POST statuses. Protect the absent-row race using PostgreSQL conflict/transaction handling, and retain database defaults for omitted fields. **Why:** Concurrent upserts cannot infer creation from a racy preliminary SELECT.
3. PATCH updates only provided mutable fields; reject owner/slug movement and tenant program_slug. Keep null distinct from omitted data. Respect created/updated timestamp behavior. **Why:** Partial updates must not erase omitted fields or move ownership.
4. Translate write grant failures to tenant_write_forbidden and runtime table loss to tenant_store_unavailable; do not turn validation or missing-row errors into fallback queries. **Why:** Permission/store failures are different from missing slugs.

### `querysource/repositories/definitions.py` (MODIFY)

```python
# Planned dependency anchor: `class DefinitionRepository:` in TASK-718 CREATE blueprint.
# occurrences: 1 in dependency blueprint; live source is not yet present.
# FILL IN: verify live anchor/count after dependency lands before editing.
class DefinitionRepository:
    """Use qualified SQL and per-call connections for every definition operation."""

    async def create(self, store: QueryStore, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate and insert persisted fields, retaining database constraints."""
        # FILL IN: Implement transaction-safe, parameterized mutation per task scope; preserve created flag and error mapping.
        raise NotImplementedError

    async def upsert(self, identity: QueryIdentity, data: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
        """Return persisted row and created flag atomically for PUT/POST statuses."""
        # FILL IN: Implement transaction-safe, parameterized mutation per task scope; preserve created flag and error mapping.
        raise NotImplementedError

    async def patch(self, identity: QueryIdentity, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Update supplied mutable fields only; owner and slug cannot change."""
        # FILL IN: Implement transaction-safe, parameterized mutation per task scope; preserve created flag and error mapping.
        raise NotImplementedError

    async def delete(self, identity: QueryIdentity) -> bool:
        """Delete exactly this owner's row; report missing without fallback."""
        # FILL IN: Implement transaction-safe, parameterized mutation per task scope; preserve created flag and error mapping.
        raise NotImplementedError
```

**Why:** Add methods to the dependency-created class; preserve read methods and shared validation.

### `tests/tenants/test_tenant_repository_writes.py` (CREATE)

```python
"""Implement atomic definition mutations regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_create_and_upsert_created_flag() -> None:
    """create and upsert created flag."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_concurrent_upserts_same_identity() -> None:
    """concurrent upserts same identity."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_patch_null_and_immutable_keys() -> None:
    """patch null and immutable keys."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_delete_missing_permission_and_store_loss() -> None:
    """delete missing permission and store loss."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/repositories/definitions.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_repository_writes.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Add create/upsert/patch/delete to the existing dependency repository. Bind every data value and return persisted row mappings, not detached runtime objects.
- [ ] AC-2: Implement atomic upsert with an accurate created flag for existing PUT/POST statuses. Protect the absent-row race using PostgreSQL conflict/transaction handling, and retain database defaults for omitted fields.
- [ ] AC-3: PATCH updates only provided mutable fields; reject owner/slug movement and tenant program_slug. Keep null distinct from omitted data. Respect created/updated timestamp behavior.
- [ ] AC-4: Translate write grant failures to tenant_write_forbidden and runtime table loss to tenant_store_unavailable; do not turn validation or missing-row errors into fallback queries.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_repository_writes.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_create_and_upsert_created_flag` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_concurrent_upserts_same_identity` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_patch_null_and_immutable_keys` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_delete_missing_permission_and_store_loss` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
