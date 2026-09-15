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

**Author/date**: sdd-worker (orchestrator), 2026-09-15. Implemented directly
("attempt 3") — both dispatched attempts failed to merge.

**Dispatch history**:
- Attempt 1 (seat `codex-spark`, `gpt-5.3-codex-spark`): `DispatchExecutionError`
  — exceeded the 1800s wall-clock cap.
- Attempt 2 (seat `glm`, `zai.glm-4.7-flash`): completed and committed
  (`cb1001b`) but the engine flagged `outcome: fidelity_violation` because
  it wrote `sdd/contracts/qworker-query-handler.md` — even though that file
  is one of this task's own three declared MODIFY targets, any write under
  `sdd/` trips the fidelity check. Per protocol this was never merged by
  hand. Reviewing that unmerged diff (read-only) also surfaced two real
  correctness bugs worth avoiding rather than adopting: (1) it added a
  brand-new `execute_tenant()` method that nothing in the codebase ever
  calls — `ThreadQuery.fetch()` only ever calls `executor.execute(...)`, so
  AC-1's versioned dispatch would never actually fire; (2) it deleted
  TASK-727's interim "reject non-legacy remote store" guard from
  `execute()` without replacing it with real routing, which would have let
  a tenant-owned remote query silently execute through the legacy handler
  with no owner envelope at all — the exact failure this feature exists to
  prevent.

**Implementation** (fresh, in this worktree):
- `querysource/queries/multi/sources/executors.py`: `RemoteExecutor.execute()`
  itself now branches on `store.contract` (not a separate unused method).
  `"tenant"` → `querysource.remote.tenant_query_handler_v1` with a validated
  `TenantOwnerEnvelope` (`version=1`, `database_namespace`, `schema`,
  `table`, `contract`), passed as `owner=` to `QClient.run()`. `None`/
  `"legacy"` → unchanged `querysource.remote.query_handler` path. Any other
  contract value raises `QueryException` before any dispatch. Routing-key
  stripping (slug/remote/worker) preserved unchanged for both handlers;
  lazy `qw.client.QClient` import and the existing timeout/connection-error
  wrapping and `finally`-block client cleanup all preserved as-is — no
  fallback between handlers or to local execution in any failure path.
- `sdd/contracts/qworker-query-handler.md`: added a new "Versioned Tenant
  Handler — tenant_query_handler_v1" section: required signature, the
  `TenantOwnerEnvelope` field table, worker-side owner-validation
  requirements (reject unsupported version / unrecognized owner tuple
  explicitly, never a silent default-schema execution), an error-contract
  table extending the legacy one, legacy coexistence, and an explicit
  deployment gate stating `querysource/remote.py` is intentionally not
  created by this task/repo.
- `tests/tenants/test_tenant_remote_protocol.py` (new, 4 tests): exact
  callable + owner-envelope assertion; routing-key stripping for both a
  saved (slug-based, legacy store) and a raw (tenant store) child query;
  explicit-error-no-fallback when the fake worker doesn't recognize the
  versioned handler (asserts exactly one dispatch attempt, nothing queued);
  timeout raises `QueryException` and still closes the client, plus a
  healthy call proving the queue result is keyed by the alias (`name`), not
  the stored slug.
- `tests/tenants/test_tenant_child_execution.py`: updated
  `test_thread_loop_owner_and_single_queue_put`, which asserted TASK-727's
  now-superseded "reject non-legacy store" message — this task's whole
  purpose is replacing that interim guard with real dispatch. Removed the
  obsolete assertion (full tenant-dispatch coverage now lives in the new
  test file) and dropped the `QueryException` import that edit left unused.

**Checks run** (`source .venv/bin/activate && python -m pytest ...`):
- `tests/tenants/test_tenant_remote_protocol.py` — 4/4 passed (AC-5, exact
  command from the task).
- `tests/tenants/test_tenant_child_execution.py` — 4/4 passed (no
  regression from TASK-727).
- Full sweep: `tests/multi tests/test_abstract_multi.py
  tests/test_local_executor.py tests/test_multi_destinations_subpackage.py
  tests/test_multiqs_column_transforms.py
  tests/test_multiqs_destination_dispatch.py
  tests/test_multiqs_remote_dispatch.py
  tests/test_multiqs_slug_sources_normalize.py
  tests/test_multiqs_sources_integration.py
  tests/test_scheduler_multi_routing.py tests/test_threadquery_executor.py
  tests/handlers/* (excluding test_airtable_oauth.py — pre-existing missing
  aioresponses dep) tests/integration/test_multiquery_output_errors.py
  tests/unit/test_multiqs_output_raise.py tests/tenants` — 265 passed, 2
  failed. Both failures confirmed pre-existing and unrelated (unchanged
  from the TASK-727 completion note): `tests/test_local_executor.py::
  test_frozen_dataclass` (env `QWORKER_TIMEOUT=5`) and
  `tests/test_multiqs_sources_integration.py::
  test_guardrail_rejects_too_many_sources` (pre-existing `self.Error()`
  always raises `QueryException`, never `DriverError`).
- `ruff check` on every touched file — 0 new findings; remaining findings
  in `querysource/queries/multi/sources/executors.py` (I001 at the
  pre-existing top import block; RET501/PLR1711 inside the untouched
  `LocalExecutor.execute()`) and in `test_tenant_child_execution.py`
  (I001/F401×2/TRY002×2/SIM117) verified byte-for-byte identical to the
  baseline at commit `4d9dc1b` (TASK-727's original merge) — none
  attributable to this task's edits.

**Spec deviations**: none. **Deployment gates unverified**: the actual
external qworker implementation of `tenant_query_handler_v1` (this repo
intentionally does not implement it — see the contract's Deployment Gate
section); real network dispatch against a live worker (sandbox has no
network egress, only a faked `QClient` in tests).

**Seats**: codex-spark (attempt 1, failed — wall-clock cap, usage unknown)
· glm (attempt 2, `zai.glm-4.7-flash`, 528.0s, 1,818,585 in / 9,807 out,
fidelity_violation — not merged) · orchestrator (attempt 3, this
implementation, merged directly).
