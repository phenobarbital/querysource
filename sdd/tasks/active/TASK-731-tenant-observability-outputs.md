# TASK-731: Add ownership diagnostics and isolate implicit artifacts

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-730
**Assigned-to**: unassigned

## Context

Implements M7 runtime diagnostics/output names of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Implement ownership_fields and implicit_artifact_name. Use canonical owner and unique request/execution ID for internal tenant names; legacy and explicitly configured destinations remain unchanged.
- Attach ownership to HTTP/direct/child/scheduled/remote timing/failure events, passing context from query objects rather than guessing from request URL or alias.
- Wire output naming at DataOutput and relevant event producers; preserve download compatibility and never prefix explicitly configured database/table/S3 destination identifiers.
- Inspect non-HTTP output producers before editing; if a additional producer exists, add its verified path to this packet before implementation. No unreviewed filesystem paths from tenant names.

**NOT in scope**: Changing explicitly configured output destinations or sending notifications externally.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/ownership_logging.py` | CREATE | One context formatter prevents inconsistent ownership across events and output naming. |
| `querysource/outputs/output.py` | MODIFY | Output ownership comes from executed definition, not a mutable URL field. |
| `querysource/handlers/log.py` | MODIFY | HTTP audit joins the same identity used by background work. |
| `querysource/interfaces/queries.py` | MODIFY | The shared query base covers direct and background execution context. |
| `tests/tenants/test_tenant_observability_outputs.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |
| `querysource/queries/qs.py` | MODIFY | This execution boundary emits its own failures and must retain canonical ownership. |
| `querysource/queries/multi/__init__.py` | MODIFY | This execution boundary emits its own failures and must retain canonical ownership. |
| `querysource/scheduler/jobs.py` | MODIFY | This execution boundary emits its own failures and must retain canonical ownership. |
| `querysource/queries/multi/sources/executors.py` | MODIFY | This execution boundary emits its own failures and must retain canonical ownership. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from __future__ import annotations
from typing import Mapping
from querysource.tenants import QueryIdentity
import pytest
from querysource.ownership_logging import ownership_fields, implicit_artifact_name
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.ownership_logging` → planned `querysource/ownership_logging.py` created by TASK-731; verify after prerequisite

### Existing Signatures to Use

```text
querysource/outputs/output.py:64
def __init__(self, request: web.Request, query: Union[AbstractQuery, 'DataFrame', list], ctype: str='json', slug: str=None, **kwargs) -> None:

querysource/outputs/writers/abstract.py:69
def get_filename(self, filename: str, extension: str=None):

querysource/scheduler/notifications.py:37
def notify(self, job_id: str, slug: str, error: Exception) -> None:

querysource/outputs/output.py:60
class DataOutput:
def __init__(self, request: web.Request, query: Union[AbstractQuery, 'DataFrame', list], ctype: str='json', slug: str=None, **kwargs) -> None:

querysource/handlers/log.py:26
class LoggingService(BaseHandler):

querysource/interfaces/queries.py:43
class AbstractQuery(Connection):
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, **kwargs):
```

Additional integration anchor: `querysource/queries/qs.py:36` — `class QS(BaseQuery):`.

Additional integration anchor: `querysource/queries/multi/__init__.py:87` — `class MultiQS(BaseQuery):`.

Additional integration anchor: `querysource/scheduler/jobs.py:19` — `logger = logging.getLogger("QSScheduler.Jobs")`.

Additional integration anchor: `querysource/queries/multi/sources/executors.py:116` — `class RemoteExecutor(QueryExecutor):`.

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

1. Implement ownership_fields and implicit_artifact_name. Use canonical owner and unique request/execution ID for internal tenant names; legacy and explicitly configured destinations remain unchanged. **Why:** Implicit artifacts need collision protection while explicit targets are intentional.
2. Attach ownership to HTTP/direct/child/scheduled/remote timing/failure events, passing context from query objects rather than guessing from request URL or alias. **Why:** Not every execution originates from an HTTP request.
3. Wire output naming at DataOutput and relevant event producers; preserve download compatibility and never prefix explicitly configured database/table/S3 destination identifiers. **Why:** Owner routing must not rewrite output destinations.
4. Inspect non-HTTP output producers before editing; if a additional producer exists, add its verified path to this packet before implementation. No unreviewed filesystem paths from tenant names. **Why:** Additional producer edits need grounded source references.

### `querysource/ownership_logging.py` (CREATE)

```python
from __future__ import annotations
from typing import Mapping
from querysource.tenants import QueryIdentity

def ownership_fields(identity: QueryIdentity) -> Mapping[str, str]:
    """Return stable owner/schema/table/slug fields without credentials or SQL."""
    # FILL IN: Return stable owner/schema/table/slug identifiers only; no credentials/SQL.
    raise NotImplementedError

def implicit_artifact_name(identity: QueryIdentity, request_id: str, filename: str) -> str:
    """Namespace generated tenant artifacts; preserve explicit destinations."""
    # FILL IN: Namespace implicit tenant outputs by owner and request; preserve legacy names and explicit destinations.
    raise NotImplementedError
```

**Why:** One context formatter prevents inconsistent ownership across events and output naming.

### `querysource/outputs/output.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class DataOutput:` (verified: querysource/outputs/output.py:60)
def __init__(self, request: web.Request, query: Union[AbstractQuery, 'DataFrame', list], ctype: str='json', slug: str=None, **kwargs) -> None:
    """Preserve all arguments; use query identity and execution ID only for implicit tenant artifact names."""
    # FILL IN: Preserve all arguments; use query identity and execution ID only for implicit tenant artifact names.
    raise NotImplementedError
```

**Why:** Output ownership comes from executed definition, not a mutable URL field.

### `querysource/handlers/log.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class LoggingService(BaseHandler):` (verified: querysource/handlers/log.py:26)
async def request_info(self, request: web.Request):
    """Add verified ownership fields when available; preserve request audit structure and redact secrets."""
    # FILL IN: Add verified ownership fields when available; preserve request audit structure and redact secrets.
    raise NotImplementedError
```

**Why:** HTTP audit joins the same identity used by background work.

### `querysource/interfaces/queries.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class AbstractQuery(Connection):` (verified: querysource/interfaces/queries.py:43)
# FILL IN: assign execution ID once per query; include ownership_fields in timing/failure events.
# Preserve user aliases and use the same context in thread callbacks; bounded by spec M7.
```

**Why:** The shared query base covers direct and background execution context.

### `tests/tenants/test_tenant_observability_outputs.py` (CREATE)

```python
"""Add ownership diagnostics and isolate implicit artifacts regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_ownership_fields_exclude_secrets() -> None:
    """ownership fields exclude secrets."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_same_slug_distinct_implicit_names() -> None:
    """same slug distinct implicit names."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_and_explicit_filename_compatibility() -> None:
    """legacy and explicit filename compatibility."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_event_context_non_http_and_nested() -> None:
    """event context non http and nested."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.


### `querysource/queries/qs.py` (MODIFY)

```python
# occurrences: 1 (verified by source-line matching)
# MODIFY — attach within `class QS(BaseQuery):` (verified: querysource/queries/qs.py:36)
from querysource.ownership_logging import ownership_fields
# FILL IN: attach ownership_fields from the resolved immutable identity to
# existing timing/failure events at this boundary; use explicit owner envelope
# for remote/jobs and preserve original exception/status handling.
# Do not derive stored slug from output alias or log SQL/connection credentials.
```

**Why:** This execution boundary emits its own failures and must retain canonical ownership.


### `querysource/queries/multi/__init__.py` (MODIFY)

```python
# occurrences: 1 (verified by source-line matching)
# MODIFY — attach within `class MultiQS(BaseQuery):` (verified: querysource/queries/multi/__init__.py:87)
from querysource.ownership_logging import ownership_fields
# FILL IN: attach ownership_fields from the resolved immutable identity to
# existing timing/failure events at this boundary; use explicit owner envelope
# for remote/jobs and preserve original exception/status handling.
# Do not derive stored slug from output alias or log SQL/connection credentials.
```

**Why:** This execution boundary emits its own failures and must retain canonical ownership.


### `querysource/scheduler/jobs.py` (MODIFY)

```python
# occurrences: 1 (verified by source-line matching)
# MODIFY — attach within `logger = logging.getLogger("QSScheduler.Jobs")` (verified: querysource/scheduler/jobs.py:19)
from querysource.ownership_logging import ownership_fields
# FILL IN: attach ownership_fields from the resolved immutable identity to
# existing timing/failure events at this boundary; use explicit owner envelope
# for remote/jobs and preserve original exception/status handling.
# Do not derive stored slug from output alias or log SQL/connection credentials.
```

**Why:** This execution boundary emits its own failures and must retain canonical ownership.


### `querysource/queries/multi/sources/executors.py` (MODIFY)

```python
# occurrences: 1 (verified by source-line matching)
# MODIFY — attach within `class RemoteExecutor(QueryExecutor):` (verified: querysource/queries/multi/sources/executors.py:116)
from querysource.ownership_logging import ownership_fields
# FILL IN: attach ownership_fields from the resolved immutable identity to
# existing timing/failure events at this boundary; use explicit owner envelope
# for remote/jobs and preserve original exception/status handling.
# Do not derive stored slug from output alias or log SQL/connection credentials.
```

**Why:** This execution boundary emits its own failures and must retain canonical ownership.

### FILL IN checklist

- [ ] `querysource/queries/multi/sources/executors.py` — implement bounded branch/wiring and validate the declared contract.
- [ ] `querysource/scheduler/jobs.py` — implement bounded branch/wiring and validate the declared contract.
- [ ] `querysource/queries/multi/__init__.py` — implement bounded branch/wiring and validate the declared contract.
- [ ] `querysource/queries/qs.py` — implement bounded branch/wiring and validate the declared contract.
- [ ] `querysource/ownership_logging.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/outputs/output.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/log.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/interfaces/queries.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_observability_outputs.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Implement ownership_fields and implicit_artifact_name. Use canonical owner and unique request/execution ID for internal tenant names; legacy and explicitly configured destinations remain unchanged.
- [ ] AC-2: Attach ownership to HTTP/direct/child/scheduled/remote timing/failure events, passing context from query objects rather than guessing from request URL or alias.
- [ ] AC-3: Wire output naming at DataOutput and relevant event producers; preserve download compatibility and never prefix explicitly configured database/table/S3 destination identifiers.
- [ ] AC-4: Inspect non-HTTP output producers before editing; if a additional producer exists, add its verified path to this packet before implementation. No unreviewed filesystem paths from tenant names.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_observability_outputs.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_ownership_fields_exclude_secrets` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_same_slug_distinct_implicit_names` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_and_explicit_filename_compatibility` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_event_context_non_http_and_nested` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
