# TASK-718: Implement repository reads, listing, metadata and revision identity

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-717
**Assigned-to**: unassigned

## Context

Implements M2; M3 pure identity functions of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Create DefinitionRepository with loop-local connection factory; implement get/list/schema/export_insert/schedulable. Use fetch_one/fetch_all/fetchval contracts, mapping None to correct empty/missing behavior.
- Validate tenant rows with TenantQueryDefinition; return persisted fields in listing/export and a fresh detached runtime QueryModel in LoadedDefinition. Derive tenant program_slug from schema only; keep legacy stored value.
- Implement definition_revision and result_cache_key as pure functions. Canonicalize all persisted values including dates, nested JSON, arrays and nulls; hash before runtime mutation.
- Build owner-specific projection/filter/sort/search policy with bound values and independently quoted identifiers. Reject tenant program_slug requests. Count/page share filters and stable sorting; retain current list defaults/limits.
- Provide schema metadata from the right validation model and safe SQL export without writing to a database. Preserve scheduler eligibility predicates from existing startup.

**NOT in scope**: Mutation SQL, request handlers and cache I/O.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/__init__.py` | CREATE | Expose a single repository entry point. |
| `querysource/repositories/definitions.py` | CREATE | One repository avoids hidden schema choices across read/list/export paths. |
| `querysource/cache_identity.py` | CREATE | Pure identity helpers let all cache paths share the same namespace and revision algorithm. |
| `tests/tenants/test_tenant_repository_reads.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from .definitions import DefinitionRepository
from __future__ import annotations
from typing import Any, AsyncContextManager, Awaitable, Callable, Mapping
from asyncdb.drivers.pg import pg
from querysource.tenants import TenantRegistry, QueryStore, QueryIdentity, LoadedDefinition, DefinitionPage
from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition
from querysource.cache_identity import definition_revision
from typing import Any, Mapping
from querysource.tenants import QueryIdentity
import pytest
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.models.QueryModel` → `querysource/models.py:48`
- `querysource.tenant_models` → planned `querysource/tenant_models.py` created by TASK-717; verify after prerequisite
- `querysource.cache_identity` → planned `querysource/cache_identity.py` created by TASK-718; verify after prerequisite

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/handlers/_pagination.py:73
class PaginationParams(BaseModel):

querysource/handlers/manager.py:47
def get_query_insert(self, query: QueryModel) -> str:

querysource/scheduler/scheduler.py:492
async def startup(self, app: web.Application) -> None:
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

1. Create DefinitionRepository with loop-local connection factory; implement get/list/schema/export_insert/schedulable. Use fetch_one/fetch_all/fetchval contracts, mapping None to correct empty/missing behavior. **Why:** One storage boundary prevents inconsistent owner lookup.
2. Validate tenant rows with TenantQueryDefinition; return persisted fields in listing/export and a fresh detached runtime QueryModel in LoadedDefinition. Derive tenant program_slug from schema only; keep legacy stored value. **Why:** Providers still expect the legacy runtime field interface.
3. Implement definition_revision and result_cache_key as pure functions. Canonicalize all persisted values including dates, nested JSON, arrays and nulls; hash before runtime mutation. **Why:** Delayed cache writers must retain their original revision identity.
4. Build owner-specific projection/filter/sort/search policy with bound values and independently quoted identifiers. Reject tenant program_slug requests. Count/page share filters and stable sorting; retain current list defaults/limits. **Why:** Filters and counts must describe the same selected-owner set.
5. Provide schema metadata from the right validation model and safe SQL export without writing to a database. Preserve scheduler eligibility predicates from existing startup. **Why:** Export and scheduler consumers must not bypass storage qualification.

### `querysource/repositories/__init__.py` (CREATE)

```python
"""Saved-definition persistence services."""
from .definitions import DefinitionRepository

__all__ = ("DefinitionRepository",)
```

**Why:** Expose a single repository entry point.

### `querysource/repositories/definitions.py` (CREATE)

```python
from __future__ import annotations
from typing import Any, AsyncContextManager, Awaitable, Callable, Mapping
from asyncdb.drivers.pg import pg
from querysource.tenants import TenantRegistry, QueryStore, QueryIdentity, LoadedDefinition, DefinitionPage
from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition
from querysource.cache_identity import definition_revision

class DefinitionRepository:
    """Use qualified SQL and per-call connections for every definition operation."""

    def __init__(self, registry: TenantRegistry, connection_factory: Callable[[], Awaitable[AsyncContextManager[pg]]]) -> None:
        """Accept a loop-local connection factory, never a shared checked-out connection."""
        self.registry = registry
        self.connection_factory = connection_factory

    async def get(self, identity: QueryIdentity) -> LoadedDefinition:
        """Read current persisted row and return detached runtime model plus revision."""
        # FILL IN: Use qualified identifiers and bound values; preserve legacy data/defaults; see task AC.
        raise NotImplementedError

    async def list(self, store: QueryStore, params: Mapping[str, Any]) -> DefinitionPage:
        """Validate owner-specific projections/filter/sort; bind page/count values."""
        # FILL IN: Use qualified identifiers and bound values; preserve legacy data/defaults; see task AC.
        raise NotImplementedError

    def schema(self, store: QueryStore) -> Mapping[str, Any]:
        """Return persistence metadata, excluding tenant runtime-only program_slug."""
        # FILL IN: Use qualified identifiers and bound values; preserve legacy data/defaults; see task AC.
        raise NotImplementedError

    async def export_insert(self, identity: QueryIdentity) -> str:
        """Render safely escaped SQL for this store using persisted fields only."""
        # FILL IN: Use qualified identifiers and bound values; preserve legacy data/defaults; see task AC.
        raise NotImplementedError

    async def schedulable(self, store: QueryStore) -> tuple[Mapping[str, Any], ...]:
        """Return scheduler candidates from one store using existing eligibility rules."""
        # FILL IN: Use qualified identifiers and bound values; preserve legacy data/defaults; see task AC.
        raise NotImplementedError
```

**Why:** One repository avoids hidden schema choices across read/list/export paths.

### `querysource/cache_identity.py` (CREATE)

```python
from __future__ import annotations
from typing import Any, Mapping
from querysource.tenants import QueryIdentity

def definition_revision(row: Mapping[str, Any]) -> str:
    """Hash canonical persisted data before runtime mutation."""
    # FILL IN: Canonicalize nested mappings, arrays, dates and nulls; hash persisted fields before runtime mutation.
    raise NotImplementedError

def result_cache_key(identity: QueryIdentity, revision: str, provider_checksum: str) -> str:
    """Return the qs:r2 key; never use an unqualified compatibility read."""
    # FILL IN: Hash database/schema/table, slug, revision and checksum into qs:r2; never include credentials.
    raise NotImplementedError
```

**Why:** Pure identity helpers let all cache paths share the same namespace and revision algorithm.

### `tests/tenants/test_tenant_repository_reads.py` (CREATE)

```python
"""Implement repository reads, listing, metadata and revision identity regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_get_returns_detached_runtime_and_revision() -> None:
    """get returns detached runtime and revision."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_list_count_filters_and_tenant_columns() -> None:
    """list count filters and tenant columns."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_metadata_export_only_persisted_fields() -> None:
    """metadata export only persisted fields."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_revision_determinism_and_physical_identity() -> None:
    """revision determinism and physical identity."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/repositories/__init__.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/repositories/definitions.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/cache_identity.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_repository_reads.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Create DefinitionRepository with loop-local connection factory; implement get/list/schema/export_insert/schedulable. Use fetch_one/fetch_all/fetchval contracts, mapping None to correct empty/missing behavior.
- [ ] AC-2: Validate tenant rows with TenantQueryDefinition; return persisted fields in listing/export and a fresh detached runtime QueryModel in LoadedDefinition. Derive tenant program_slug from schema only; keep legacy stored value.
- [ ] AC-3: Implement definition_revision and result_cache_key as pure functions. Canonicalize all persisted values including dates, nested JSON, arrays and nulls; hash before runtime mutation.
- [ ] AC-4: Build owner-specific projection/filter/sort/search policy with bound values and independently quoted identifiers. Reject tenant program_slug requests. Count/page share filters and stable sorting; retain current list defaults/limits.
- [ ] AC-5: Provide schema metadata from the right validation model and safe SQL export without writing to a database. Preserve scheduler eligibility predicates from existing startup.
- [ ] AC-6: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_repository_reads.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_get_returns_detached_runtime_and_revision` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_list_count_filters_and_tenant_columns` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_metadata_export_only_persisted_fields` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_revision_determinism_and_physical_identity` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
