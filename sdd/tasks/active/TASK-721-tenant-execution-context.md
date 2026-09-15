# TASK-721: Carry immutable ownership through query construction

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-720
**Assigned-to**: unassigned

## Context

Implements M3 execution context of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Add the specified keyword-only tenant parameter to AbstractQuery, BaseQuery, QS, QueryObject and MultiQS. Preserve existing positional defaults/types and kwargs handling.
- Store _tenant_selector and resolved _definition_identity/_definition_revision on each execution object. Retrieve LoadedDefinition directly for provider construction; compatibility get_slug alone discards revision.
- Keep conditions[tenant]/conditions[program] separate from the Python routing argument. Tenant runtime program context comes only from the detached definition; explicit datasource credentials/targets stay unchanged.
- Cover single-query fallback and raw queries with an owner context; derive raw identity from store and existing raw checksum, never a invented saved slug lookup. Child expansion/transport is completed in its later task.

**NOT in scope**: Cache I/O, child transport and HTTP routing.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/queries.py` | MODIFY | Extend the existing class methods; preserve public positional arguments and provider behavior. |
| `querysource/queries/base.py` | MODIFY | Extend the existing class methods; preserve public positional arguments and provider behavior. |
| `querysource/queries/qs.py` | MODIFY | Extend the existing class methods; preserve public positional arguments and provider behavior. |
| `querysource/queries/obj.py` | MODIFY | Extend the existing class methods; preserve public positional arguments and provider behavior. |
| `querysource/queries/multi/__init__.py` | MODIFY | Extend the existing class methods; preserve public positional arguments and provider behavior. |
| `tests/tenants/test_tenant_execution_context.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryIdentity, LoadedDefinition
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

### Existing Signatures to Use

```text
querysource/providers/abstract.py:23
class BaseProvider(ABC):
def __init__(self, slug: str='', query: Any=None, qstype: str='', connection: Callable=None, definition: Union[QueryModel, dict]=None, conditions: dict=None, request: web.Request=None, **kwargs) -> None:
async def query(self):

querysource/parsers/abstract.pyx:162: cdef void _program_slug_sync(self):

querysource/interfaces/queries.py:43
class AbstractQuery(Connection):
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, **kwargs) -> None:

querysource/queries/base.py:19
class BaseQuery(AbstractQuery):
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, **kwargs) -> None:
async def build_provider(self):
async def query(self):

querysource/queries/qs.py:36
class QS(BaseQuery):
def __init__(self, slug: str='', conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, **kwargs) -> None:
async def build_provider(self):
async def query(self, output_format: Optional[str]=None):

querysource/queries/obj.py:20
class QueryObject(BaseQuery):
def __init__(self, name: str, query: Optional[Union[list, dict]], conditions: dict=None, request: web.Request=None, queue: asyncio.Queue=None, loop: asyncio.AbstractEventLoop=None, **kwargs) -> None:
async def build_provider(self):
async def query(self):

querysource/queries/multi/__init__.py:87
class MultiQS(BaseQuery):
def __init__(self, slug: str=None, queries: Optional[list]=None, files: Optional[list]=None, query: Optional[dict]=None, conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, user_session: Optional[object]=None, **kwargs) -> None:
async def query(self):
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

1. Add the specified keyword-only tenant parameter to AbstractQuery, BaseQuery, QS, QueryObject and MultiQS. Preserve existing positional defaults/types and kwargs handling. **Why:** Existing positional callers must remain compatible.
2. Store _tenant_selector and resolved _definition_identity/_definition_revision on each execution object. Retrieve LoadedDefinition directly for provider construction; compatibility get_slug alone discards revision. **Why:** Cache identity must survive detached model/provider mutation.
3. Keep conditions[tenant]/conditions[program] separate from the Python routing argument. Tenant runtime program context comes only from the detached definition; explicit datasource credentials/targets stay unchanged. **Why:** SQL parameters must not become storage routing selectors.
4. Cover single-query fallback and raw queries with an owner context; derive raw identity from store and existing raw checksum, never a invented saved slug lookup. Child expansion/transport is completed in its later task. **Why:** Raw statements must not trigger nonexistent saved-definition reads.

### `querysource/interfaces/queries.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class AbstractQuery(Connection):` (verified: querysource/interfaces/queries.py:43)
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, *, tenant: str | None=None, **kwargs) -> None:
    """Preserve current initialization; forward keyword tenant and keep owner separate from conditions."""
    self._tenant_selector = tenant
    # FILL IN: Preserve current initialization; forward keyword tenant and keep owner separate from conditions.
    raise NotImplementedError
```

**Why:** Extend the existing class methods; preserve public positional arguments and provider behavior.

### `querysource/queries/base.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class BaseQuery(AbstractQuery):` (verified: querysource/queries/base.py:19)
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, *, tenant: str | None=None, **kwargs) -> None:
    """Preserve current initialization; forward keyword tenant and keep owner separate from conditions."""
    self._tenant_selector = tenant
    # FILL IN: Preserve current initialization; forward keyword tenant and keep owner separate from conditions.
    raise NotImplementedError
```

**Why:** Extend the existing class methods; preserve public positional arguments and provider behavior.

### `querysource/queries/qs.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QS(BaseQuery):` (verified: querysource/queries/qs.py:36)
def __init__(self, slug: str='', conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, *, tenant: str | None=None, **kwargs) -> None:
    """Preserve current initialization; forward keyword tenant and keep owner separate from conditions."""
    self._tenant_selector = tenant
    # FILL IN: Preserve current initialization; forward keyword tenant and keep owner separate from conditions.
    raise NotImplementedError

async def build_provider(self):
    """Load LoadedDefinition through repository; retain _definition_identity and _definition_revision; give only detached runtime to provider."""
    # FILL IN: Load LoadedDefinition through repository; retain _definition_identity and _definition_revision; give only detached runtime to provider.
    raise NotImplementedError
```

**Why:** Extend the existing class methods; preserve public positional arguments and provider behavior.

### `querysource/queries/obj.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryObject(BaseQuery):` (verified: querysource/queries/obj.py:20)
def __init__(self, name: str, query: Optional[Union[list, dict]], conditions: dict=None, request: web.Request=None, queue: asyncio.Queue=None, loop: asyncio.AbstractEventLoop=None, *, tenant: str | None=None, **kwargs) -> None:
    """Preserve current initialization; forward keyword tenant and keep owner separate from conditions."""
    self._tenant_selector = tenant
    # FILL IN: Preserve current initialization; forward keyword tenant and keep owner separate from conditions.
    raise NotImplementedError

async def build_provider(self):
    """Load LoadedDefinition through repository; retain _definition_identity and _definition_revision; give only detached runtime to provider."""
    # FILL IN: Load LoadedDefinition through repository; retain _definition_identity and _definition_revision; give only detached runtime to provider.
    raise NotImplementedError
```

**Why:** Extend the existing class methods; preserve public positional arguments and provider behavior.

### `querysource/queries/multi/__init__.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class MultiQS(BaseQuery):` (verified: querysource/queries/multi/__init__.py:87)
def __init__(self, slug: str=None, queries: Optional[list]=None, files: Optional[list]=None, query: Optional[dict]=None, conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, user_session: Optional[object]=None, *, tenant: str | None=None, **kwargs) -> None:
    """Preserve current initialization; forward keyword tenant and keep owner separate from conditions."""
    self._tenant_selector = tenant
    # FILL IN: Preserve current initialization; forward keyword tenant and keep owner separate from conditions.
    raise NotImplementedError
```

**Why:** Extend the existing class methods; preserve public positional arguments and provider behavior.

### `tests/tenants/test_tenant_execution_context.py` (CREATE)

```python
"""Carry immutable ownership through query construction regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_constructor_compatibility_and_forwarding() -> None:
    """constructor compatibility and forwarding."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_runtime_model_program_derived() -> None:
    """runtime model program derived."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_raw_owner_context_without_saved_lookup() -> None:
    """raw owner context without saved lookup."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_provider_mutation_cannot_change_revision() -> None:
    """provider mutation cannot change revision."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/interfaces/queries.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/base.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/qs.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/obj.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/multi/__init__.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_execution_context.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Add the specified keyword-only tenant parameter to AbstractQuery, BaseQuery, QS, QueryObject and MultiQS. Preserve existing positional defaults/types and kwargs handling.
- [ ] AC-2: Store _tenant_selector and resolved _definition_identity/_definition_revision on each execution object. Retrieve LoadedDefinition directly for provider construction; compatibility get_slug alone discards revision.
- [ ] AC-3: Keep conditions[tenant]/conditions[program] separate from the Python routing argument. Tenant runtime program context comes only from the detached definition; explicit datasource credentials/targets stay unchanged.
- [ ] AC-4: Cover single-query fallback and raw queries with an owner context; derive raw identity from store and existing raw checksum, never a invented saved slug lookup. Child expansion/transport is completed in its later task.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_execution_context.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_constructor_compatibility_and_forwarding` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_runtime_model_program_derived` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_raw_owner_context_without_saved_lookup` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_provider_mutation_cannot_change_revision` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
