# TASK-720: Wire startup, direct usage and compatible slug lookup

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-719
**Assigned-to**: unassigned

## Context

Implements M1 lifecycle; M2 lookup of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Add tenant_allowlist and initialize_tenants to QuerySource without changing its Singleton metaclass. Freeze initialization configuration and reject incompatible reinitialization.
- Publish app["qs_tenant_registry"] and app["qs_definition_repository"] after metadata connection startup, before scheduler startup/readiness. Failed discovery fails readiness; lazy/programmatic initialization uses the same service.
- Provide Connection.get_definition_repository() as an internal async helper. Obtain the service snapshot lazily to avoid the services/connections import cycle, and bind repository acquisition to the calling event loop.
- Replace direct QueryModel.get in get_query_slug/get_slug with repository lookup while preserving positional arguments, retries and missing-slug mapping. Program remains a compatibility argument, not owner routing.
- Ensure threaded execution never reuses an HTTP-loop pool; create/release direct connections on its own loop. Do not mutate shared pgargs or Meta.

**NOT in scope**: Tenant HTTP routes and result cache changes.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Merge new methods/signature into the existing class; do not replace the singleton service. |
| `querysource/connections.py` | MODIFY | This internal factory normalizes pool and direct connector APIs for repository calls. |
| `querysource/interfaces/connections.py` | MODIFY | Existing external callers retain their lookup interface and gain only explicit keyword ownership. |
| `tests/tenants/test_tenant_bootstrap_lookup.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from typing import Any, AsyncContextManager, Sequence
from asyncdb.drivers.pg import pg
from querysource.tenants import TenantRegistry, QueryIdentity
from querysource.repositories import DefinitionRepository
from querysource.models import QueryModel
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
- `querysource.models.QueryModel` → `querysource/models.py:48`

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/connections.py:123
async def acquire(self):

querysource/connections.py:156
async def start(self, app: Union[web.Application, None]=None):

querysource/interfaces/connections.py:444
async def get_query_slug(self, slug: str, evt: asyncio.AbstractEventLoop=None, max_retries: int=3) -> BaseModel:

querysource/services.py:50
class QuerySource(metaclass=Singleton):
def __init__(self, **kwargs):

querysource/connections.py:38
class QueryConnection(Connection, metaclass=Singleton):
def __init__(self, **kwargs):
async def acquire(self):
async def start(self, app: Union[web.Application, None]=None):

querysource/interfaces/connections.py:52
class Connection:
def __init__(self, loop: Optional[asyncio.AbstractEventLoop]=None, **kwargs):
async def get_query_slug(self, slug: str, evt: asyncio.AbstractEventLoop=None, max_retries: int=3) -> BaseModel:
async def get_slug(self, slug: str, program: str=None, evt: asyncio.AbstractEventLoop=None):
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

1. Add tenant_allowlist and initialize_tenants to QuerySource without changing its Singleton metaclass. Freeze initialization configuration and reject incompatible reinitialization. **Why:** Singleton reinitialization must not silently broaden access.
2. Publish app["qs_tenant_registry"] and app["qs_definition_repository"] after metadata connection startup, before scheduler startup/readiness. Failed discovery fails readiness; lazy/programmatic initialization uses the same service. **Why:** Jobs and requests must see a complete registry.
3. Provide Connection.get_definition_repository() as an internal async helper. Obtain the service snapshot lazily to avoid the services/connections import cycle, and bind repository acquisition to the calling event loop. **Why:** Service imports and loop-specific pools cannot be shared blindly.
4. Replace direct QueryModel.get in get_query_slug/get_slug with repository lookup while preserving positional arguments, retries and missing-slug mapping. Program remains a compatibility argument, not owner routing. **Why:** Existing callers rely on positional arguments and missing-slug behavior.
5. Ensure threaded execution never reuses an HTTP-loop pool; create/release direct connections on its own loop. Do not mutate shared pgargs or Meta. **Why:** A pool belongs to the event loop that created it.

### `querysource/services.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QuerySource(metaclass=Singleton):` (verified: querysource/services.py:50)
class QuerySource:
    """Existing service; preserve Singleton metaclass and lifecycle."""

    def __init__(self, *, tenant_allowlist: Sequence[str] | None=None, **kwargs: Any) -> None:
        """Freeze ownership configuration; reject incompatible reinitialization."""
        # FILL IN: Preserve all existing provider/filter setup; initialize registry once on current loop and attach app services.
        raise NotImplementedError

    async def initialize_tenants(self) -> TenantRegistry:
        """Idempotently initialize on the current loop for HTTP or Python use."""
        # FILL IN: Preserve all existing provider/filter setup; initialize registry once on current loop and attach app services.
        raise NotImplementedError

    async def qs_start(self, app: WebApp) -> None:
        """Initialize registry/repository after connection startup and before jobs."""
        # FILL IN: Preserve all existing provider/filter setup; initialize registry once on current loop and attach app services.
        raise NotImplementedError
```

**Why:** Merge new methods/signature into the existing class; do not replace the singleton service.

### `querysource/connections.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryConnection(Connection, metaclass=Singleton):` (verified: querysource/connections.py:38)
async def definition_connection(self) -> AsyncContextManager[pg]:
    """Acquire metadata access bound to the running loop."""
    # FILL IN: Wrap current acquire/direct-connection patterns; close/release on failure; never share a checked-out connection.
    raise NotImplementedError
```

**Why:** This internal factory normalizes pool and direct connector APIs for repository calls.

### `querysource/interfaces/connections.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class Connection:` (verified: querysource/interfaces/connections.py:52)
class Connection:
    """Preserve existing lookup callers and retry/missing-slug behavior."""

    async def get_query_slug(self, slug: str, evt: asyncio.AbstractEventLoop | None=None, max_retries: int=3, *, tenant: str | None=None) -> BaseModel:
        """Delegate to repository; return compatible detached runtime definition."""
        # FILL IN: Use get_definition_repository and QueryIdentity; keep retry/error compatibility without public fallback.
        raise NotImplementedError

    async def get_slug(self, slug: str, program: str | None=None, evt: asyncio.AbstractEventLoop | None=None, *, tenant: str | None=None) -> BaseModel:
        """Preserve program argument; only tenant selects ownership."""
        # FILL IN: Use get_definition_repository and QueryIdentity; keep retry/error compatibility without public fallback.
        raise NotImplementedError

async def get_definition_repository(self) -> DefinitionRepository:
    """Get registry and a loop-local definition repository."""
    # FILL IN: Use lazy service access; no import cycle or implicit discovery on each lookup.
    raise NotImplementedError
```

**Why:** Existing external callers retain their lookup interface and gain only explicit keyword ownership.

### `tests/tenants/test_tenant_bootstrap_lookup.py` (CREATE)

```python
"""Wire startup, direct usage and compatible slug lookup regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_startup_order_before_scheduler() -> None:
    """startup order before scheduler."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_singleton_allowlist_conflict() -> None:
    """singleton allowlist conflict."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_lazy_lookup_without_request() -> None:
    """lazy lookup without request."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_connection_retry_and_cross_loop_cleanup() -> None:
    """connection retry and cross loop cleanup."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/services.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/connections.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/interfaces/connections.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_bootstrap_lookup.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Add tenant_allowlist and initialize_tenants to QuerySource without changing its Singleton metaclass. Freeze initialization configuration and reject incompatible reinitialization.
- [ ] AC-2: Publish app["qs_tenant_registry"] and app["qs_definition_repository"] after metadata connection startup, before scheduler startup/readiness. Failed discovery fails readiness; lazy/programmatic initialization uses the same service.
- [ ] AC-3: Provide Connection.get_definition_repository() as an internal async helper. Obtain the service snapshot lazily to avoid the services/connections import cycle, and bind repository acquisition to the calling event loop.
- [ ] AC-4: Replace direct QueryModel.get in get_query_slug/get_slug with repository lookup while preserving positional arguments, retries and missing-slug mapping. Program remains a compatibility argument, not owner routing.
- [ ] AC-5: Ensure threaded execution never reuses an HTTP-loop pool; create/release direct connections on its own loop. Do not mutate shared pgargs or Meta.
- [ ] AC-6: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_bootstrap_lookup.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_startup_order_before_scheduler` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_singleton_allowlist_conflict` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_lazy_lookup_without_request` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_connection_retry_and_cross_loop_cleanup` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
