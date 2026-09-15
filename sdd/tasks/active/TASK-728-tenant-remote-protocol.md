# TASK-728: Dispatch tenant work through a versioned worker contract

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-727
**Assigned-to**: unassigned

## Context

Implements M5 remote boundary of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Use querysource.remote.tenant_query_handler_v1 only for tenant-context dispatch. Include required owner envelope with version/database namespace/schema/table/contract and preserve alias queue output.
- Keep legacy querysource.remote.query_handler dispatch unchanged for default legacy execution. Strip routing keys from conditions for saved and raw child cases; worker uses its own credentials.
- On missing handler, unsupported version/owner, timeout or worker failure, propagate the specified error and never fall back to public handler or local execution. Preserve lazy optional qworker import and client cleanup.
- Update external contract with exact required signature, owner validation and deployment gate. Do not create querysource/remote.py in this task.

**NOT in scope**: Deploying or implementing an external worker server.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/executors.py` | MODIFY | A different callable prevents old **kwargs-tolerant workers from ignoring ownership. |
| `sdd/contracts/qworker-query-handler.md` | MODIFY | Document an external implementation requirement without pretending the worker exists in this repo. |
| `tests/tenants/test_tenant_remote_protocol.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryStore, TenantOwnerEnvelope
from querysource.exceptions import QueryException
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.exceptions.QueryException` → `querysource/exceptions.py:6`

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/queries/multi/sources/executors.py:116
class RemoteExecutor(QueryExecutor):
def __init__(self, host: str, port: int, timeout: int=QWORKER_TIMEOUT, workers: list | None=None) -> None:

querysource/queries/multi/sources/executors.py:116
class RemoteExecutor(QueryExecutor):
def __init__(self, host: str, port: int, timeout: int=QWORKER_TIMEOUT, workers: list | None=None) -> None:

sdd/contracts/qworker-query-handler.md:1: # QWorker Query Handler — Interface Contract
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

1. Use querysource.remote.tenant_query_handler_v1 only for tenant-context dispatch. Include required owner envelope with version/database namespace/schema/table/contract and preserve alias queue output. **Why:** A distinct callable gives old workers an explicit compatibility failure.
2. Keep legacy querysource.remote.query_handler dispatch unchanged for default legacy execution. Strip routing keys from conditions for saved and raw child cases; worker uses its own credentials. **Why:** Data conditions and worker routing have separate meanings.
3. On missing handler, unsupported version/owner, timeout or worker failure, propagate the specified error and never fall back to public handler or local execution. Preserve lazy optional qworker import and client cleanup. **Why:** Falling back would execute the wrong definition.
4. Update external contract with exact required signature, owner validation and deployment gate. Do not create querysource/remote.py in this task. **Why:** The worker implementation is outside this repository.

### `querysource/queries/multi/sources/executors.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class RemoteExecutor(QueryExecutor):` (verified: querysource/queries/multi/sources/executors.py:116)
async def execute(self, name: str, query: dict, queue: asyncio.Queue[dict], request: web.Request, *, store: QueryStore | None = None) -> None:
    """Preserve timeout and cleanup; dispatch correct versioned handler with validated envelope; no legacy fallback."""
    # FILL IN: Preserve timeout and cleanup; dispatch correct versioned handler with validated envelope; no legacy fallback.
    raise NotImplementedError
# FILL IN: retain keyword-only store added by the dependency; do not revert its signature.
```

**Why:** A different callable prevents old **kwargs-tolerant workers from ignoring ownership.

### `sdd/contracts/qworker-query-handler.md` (MODIFY)

```markdown
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `# QWorker Query Handler — Interface Contract` (verified: sdd/contracts/qworker-query-handler.md:1)
async def tenant_query_handler_v1(slug: str | None=None, conditions: dict | None=None, *, owner: TenantOwnerEnvelope, **options: Any) -> DataFrame:
    """Validate protocol/registry on worker and execute exactly the selected owner."""
    # FILL IN: External worker must validate version and exact registry owner before execution; return DataFrame or explicit error.
    raise NotImplementedError

# FILL IN: document owner schema, errors, legacy coexistence and tested deployment matrix.
```

**Why:** Document an external implementation requirement without pretending the worker exists in this repo.

### `tests/tenants/test_tenant_remote_protocol.py` (CREATE)

```python
"""Dispatch tenant work through a versioned worker contract regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_exact_callable_and_owner_envelope() -> None:
    """exact callable and owner envelope."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_saved_raw_conditions_strip_routing() -> None:
    """saved raw conditions strip routing."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_old_worker_explicit_error_no_fallback() -> None:
    """old worker explicit error no fallback."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_timeout_cleanup_and_queue_alias() -> None:
    """timeout cleanup and queue alias."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/queries/multi/sources/executors.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `sdd/contracts/qworker-query-handler.md` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_remote_protocol.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Use querysource.remote.tenant_query_handler_v1 only for tenant-context dispatch. Include required owner envelope with version/database namespace/schema/table/contract and preserve alias queue output.
- [ ] AC-2: Keep legacy querysource.remote.query_handler dispatch unchanged for default legacy execution. Strip routing keys from conditions for saved and raw child cases; worker uses its own credentials.
- [ ] AC-3: On missing handler, unsupported version/owner, timeout or worker failure, propagate the specified error and never fall back to public handler or local execution. Preserve lazy optional qworker import and client cleanup.
- [ ] AC-4: Update external contract with exact required signature, owner validation and deployment gate. Do not create querysource/remote.py in this task.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_remote_protocol.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_exact_callable_and_owner_envelope` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_saved_raw_conditions_strip_routing` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_old_worker_explicit_error_no_fallback` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_timeout_cleanup_and_queue_alias` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
