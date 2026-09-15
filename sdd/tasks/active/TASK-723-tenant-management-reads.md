# TASK-723: Add selector parsing and tenant-aware management reads

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-722
**Assigned-to**: unassigned

## Context

Implements M4 selectors, listing and metadata of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Create resolve_request_store in handlers/tenant.py for later handler expansion. Validate URL/query/body selectors including duplicate keys; JSON null selects configured default and literal URL null is a name.
- Route QueryManager get, pagination, :meta and :insert through repository. Remove selectors before field/filter validation. Preserve envelope/count headers, 204 empty behavior and legacy metadata/export conventions.
- Keep pagination limits and legacy allowed columns; tenant fields/search/sort reject program_slug. Do not mutate module-global allowlists per request; use repository store-specific policy.
- Preserve management route shape and existing status/error mapping. Explicit public remains literal and allowlisted even with legacy overrides.

**NOT in scope**: Tenant execution routes, CRUD mutations and scheduler sync.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/tenant.py` | CREATE | One selector parser prevents GET and write handlers from disagreeing about ownership. |
| `querysource/handlers/manager.py` | MODIFY | Replace direct ORM reads without borrowing a request-bound view for tenant listing. |
| `querysource/handlers/_pagination.py` | MODIFY | Do not make global column sets depend on the most recent request. |
| `tests/tenants/test_tenant_management_reads.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from __future__ import annotations
from typing import Any, Mapping
from aiohttp import web
from querysource.tenants import QueryStore, TenantRegistry
import pytest
from querysource.repositories import DefinitionRepository
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.repositories` → planned `querysource/repositories/__init__.py` created by TASK-718; verify after prerequisite

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/handlers/manager.py:47
def get_query_insert(self, query: QueryModel) -> str:

querysource/handlers/_pagination.py:122
def from_query_string(cls, qs: dict) -> 'PaginationParams':

querysource/handlers/manager.py:32
class QueryManager(QueryView):
async def get(self):
async def patch(self):
async def delete(self):
async def put(self):
async def post(self):

querysource/handlers/_pagination.py:73
class PaginationParams(BaseModel):
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

1. Create resolve_request_store in handlers/tenant.py for later handler expansion. Validate URL/query/body selectors including duplicate keys; JSON null selects configured default and literal URL null is a name. **Why:** Omitted and explicitly null selectors have different child semantics.
2. Route QueryManager get, pagination, :meta and :insert through repository. Remove selectors before field/filter validation. Preserve envelope/count headers, 204 empty behavior and legacy metadata/export conventions. **Why:** Management responses must preserve client compatibility.
3. Keep pagination limits and legacy allowed columns; tenant fields/search/sort reject program_slug. Do not mutate module-global allowlists per request; use repository store-specific policy. **Why:** Global field policies would race across owners.
4. Preserve management route shape and existing status/error mapping. Explicit public remains literal and allowlisted even with legacy overrides. **Why:** Explicit public and configured legacy may identify different stores.

### `querysource/handlers/tenant.py` (CREATE)

```python
from __future__ import annotations
from typing import Any, Mapping
from aiohttp import web
from querysource.tenants import QueryStore, TenantRegistry

def resolve_request_store(request: web.Request, registry: TenantRegistry, payload: Mapping[str, Any] | None=None) -> QueryStore:
    """Validate all present routing selectors and reject disagreement with 400."""
    # FILL IN: Apply spec selector matrix: absent differs from null; require agreement; reject empty/non-string; no fallback.
    raise NotImplementedError
```

**Why:** One selector parser prevents GET and write handlers from disagreeing about ownership.

### `querysource/handlers/manager.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryManager(QueryView):` (verified: querysource/handlers/manager.py:32)
async def get(self):
    """Resolve store once; call repository get/list/schema/export_insert; preserve response/header/status paths."""
    # FILL IN: Resolve store once; call repository get/list/schema/export_insert; preserve response/header/status paths.
    raise NotImplementedError

class QueryManager:
    """Existing QueryView subclass; preserve all public HTTP method contracts."""

    async def _paginate_list(self, qp: dict, default_args: dict, *, store: QueryStore | None=None) -> web.StreamResponse:
        """Delegate page/count to repository using resolved persistence fields."""
        # FILL IN: Delegate validated parameters and store to repository; keep current pagination response conventions.
        raise NotImplementedError
```

**Why:** Replace direct ORM reads without borrowing a request-bound view for tenant listing.

### `querysource/handlers/_pagination.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class PaginationParams(BaseModel):` (verified: querysource/handlers/_pagination.py:73)
# FILL IN: preserve legacy validators; make column-policy validation explicit and request-local.
# Bound by spec §2: tenant program_slug is invalid; page/size limits and legacy accepted fields remain unchanged.
```

**Why:** Do not make global column sets depend on the most recent request.

### `tests/tenants/test_tenant_management_reads.py` (CREATE)

```python
"""Add selector parsing and tenant-aware management reads regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_selector_missing_null_duplicates_conflicts() -> None:
    """selector missing null duplicates conflicts."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_metadata_export_tenant_shape() -> None:
    """metadata export tenant shape."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_tenant_pagination_headers_and_empty() -> None:
    """tenant pagination headers and empty."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_overrides_explicit_public() -> None:
    """legacy overrides explicit public."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/handlers/tenant.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/manager.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/_pagination.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_management_reads.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Create resolve_request_store in handlers/tenant.py for later handler expansion. Validate URL/query/body selectors including duplicate keys; JSON null selects configured default and literal URL null is a name.
- [ ] AC-2: Route QueryManager get, pagination, :meta and :insert through repository. Remove selectors before field/filter validation. Preserve envelope/count headers, 204 empty behavior and legacy metadata/export conventions.
- [ ] AC-3: Keep pagination limits and legacy allowed columns; tenant fields/search/sort reject program_slug. Do not mutate module-global allowlists per request; use repository store-specific policy.
- [ ] AC-4: Preserve management route shape and existing status/error mapping. Explicit public remains literal and allowlisted even with legacy overrides.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_management_reads.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_selector_missing_null_duplicates_conflicts` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_metadata_export_tenant_shape` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_tenant_pagination_headers_and_empty` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_overrides_explicit_public` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
