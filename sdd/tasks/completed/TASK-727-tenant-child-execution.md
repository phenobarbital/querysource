# TASK-727: Resolve nested owners and propagate them across local threads

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-726
**Assigned-to**: unassigned

## Context

Implements M5 local execution; M3 stored expansion of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Apply parent inheritance and explicit tenant/null overrides to saved queries, Query source components and single fallback. No owner fallback/search if slug is missing.
- Keep alias separate from stored slug. Deep-copy config before removing tenant/remote/worker keys and perform policy checks on resolved references before a known batch starts.
- Add store keyword to ThreadQuery and QueryExecutor/LocalExecutor/RemoteExecutor execute. Wire fetch to executor.execute(store=...) and local QueryObject to loop-local repository; maintain exactly one queue put.
- Until versioned transport task lands, reject nonlegacy remote store explicitly. Do not serialize connections or interpret tenant as SQL condition.

**NOT in scope**: Remote server code and mandatory bans on cross-schema SQL.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Parent inheritance and explicit-null override must be resolved before threading. |
| `querysource/queries/multi/sources/query.py` | MODIFY | Only immutable store data crosses threads, not pools or requests rebound to another loop. |
| `querysource/queries/multi/sources/executors.py` | MODIFY | The strategy signature is shared; never silently ignore store in the remote implementation. |
| `tests/tenants/test_tenant_child_execution.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryStore, QueryIdentity
from querysource.queries.obj import QueryObject
from querysource.queries.multi.sources.executors import RemoteConfig
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.queries.obj.QueryObject` → `querysource/queries/obj.py:20`
- `querysource.queries.multi.sources.executors.RemoteConfig` → `querysource/queries/multi/sources/executors.py:22`

### Existing Signatures to Use

```text
querysource/queries/multi/sources/query.py:64
async def fetch(self) -> pd.DataFrame | None:

querysource/queries/multi/sources/executors.py:76
class LocalExecutor(QueryExecutor):

querysource/queries/multi/__init__.py:87
class MultiQS(BaseQuery):
def __init__(self, slug: str=None, queries: Optional[list]=None, files: Optional[list]=None, query: Optional[dict]=None, conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, user_session: Optional[object]=None, **kwargs):
async def query(self):

querysource/queries/multi/sources/query.py:11
class ThreadQuery(ThreadSource):
def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue, remote_config: Optional[RemoteConfig]=None):

querysource/queries/multi/sources/executors.py:43
class QueryExecutor(ABC):
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

1. Apply parent inheritance and explicit tenant/null overrides to saved queries, Query source components and single fallback. No owner fallback/search if slug is missing. **Why:** A missing child must not execute another owner definition.
2. Keep alias separate from stored slug. Deep-copy config before removing tenant/remote/worker keys and perform policy checks on resolved references before a known batch starts. **Why:** Side effects must not start before known child authorization checks.
3. Add store keyword to ThreadQuery and QueryExecutor/LocalExecutor/RemoteExecutor execute. Wire fetch to executor.execute(store=...) and local QueryObject to loop-local repository; maintain exactly one queue put. **Why:** Cross-thread pools and duplicate queue writes are unsafe.
4. Until versioned transport task lands, reject nonlegacy remote store explicitly. Do not serialize connections or interpret tenant as SQL condition. **Why:** Older workers may silently ignore additional keyword arguments.

### `querysource/queries/multi/__init__.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class MultiQS(BaseQuery):` (verified: querysource/queries/multi/__init__.py:87)
async def query(self):
    """Deep-copy pipeline config; resolve stored children to parent/explicit owner before dispatch; preserve output aliases and preflight real identities."""
    # FILL IN: Deep-copy pipeline config; resolve stored children to parent/explicit owner before dispatch; preserve output aliases and preflight real identities.
    raise NotImplementedError
```

**Why:** Parent inheritance and explicit-null override must be resolved before threading.

### `querysource/queries/multi/sources/query.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class ThreadQuery(ThreadSource):` (verified: querysource/queries/multi/sources/query.py:11)
class ThreadQuery:
    """Existing ThreadSource subclass; snapshot child ownership before threading."""

    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue, remote_config: RemoteConfig | None=None, *, store: QueryStore | None=None) -> None:
        """Preserve positional arguments; store is resolved parent/child identity."""
        # FILL IN: Preserve ThreadSource setup; store resolved immutable owner; forward it in executor.execute and keep one queue result.
        raise NotImplementedError
```

The constructor must assign `self._store = store` after existing ThreadSource
initialization and retain executor selection. Replace fetch's dispatch with
`await self._executor.execute(self._name, self._query, self._queue,
self._request, store=self._store)` and return None. The executor owns queue output.

**Why:** Only immutable store data crosses threads, not pools or requests rebound to another loop.

### `querysource/queries/multi/sources/executors.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryExecutor(ABC):` (verified: querysource/queries/multi/sources/executors.py:43)
class QueryExecutor:
    """Existing strategy; implementations preserve the queue output contract."""

    async def execute(self, name: str, query: dict, queue: asyncio.Queue[dict], request: web.Request, *, store: QueryStore | None=None) -> None:
        """Put {alias: DataFrame}; route metadata never becomes SQL conditions."""
        # FILL IN: Add store keyword to strategy and both implementations; local constructs QueryObject with matching owner and loop.
        raise NotImplementedError
# FILL IN: LocalExecutor.execute forwards resolved store into loop-local QueryObject;
# RemoteExecutor accepts the keyword now and refuses nonlegacy store until versioned transport lands.
```

**Why:** The strategy signature is shared; never silently ignore store in the remote implementation.

### `tests/tenants/test_tenant_child_execution.py` (CREATE)

```python
"""Resolve nested owners and propagate them across local threads regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_parent_explicit_null_and_named_child_owner() -> None:
    """parent explicit null and named child owner."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_input_config_is_not_mutated() -> None:
    """input config is not mutated."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_alias_vs_slug_preflight_before_side_effect() -> None:
    """alias vs slug preflight before side effect."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_thread_loop_owner_and_single_queue_put() -> None:
    """thread loop owner and single queue put."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/queries/multi/__init__.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/multi/sources/query.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/multi/sources/executors.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_child_execution.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Apply parent inheritance and explicit tenant/null overrides to saved queries, Query source components and single fallback. No owner fallback/search if slug is missing.
- [ ] AC-2: Keep alias separate from stored slug. Deep-copy config before removing tenant/remote/worker keys and perform policy checks on resolved references before a known batch starts.
- [ ] AC-3: Add store keyword to ThreadQuery and QueryExecutor/LocalExecutor/RemoteExecutor execute. Wire fetch to executor.execute(store=...) and local QueryObject to loop-local repository; maintain exactly one queue put.
- [ ] AC-4: Until versioned transport task lands, reject nonlegacy remote store explicitly. Do not serialize connections or interpret tenant as SQL condition.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_child_execution.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_parent_explicit_null_and_named_child_owner` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_input_config_is_not_mutated` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_alias_vs_slug_preflight_before_side_effect` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_thread_loop_owner_and_single_queue_put` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

**Author/date**: sdd-worker (orchestrator), 2026-09-15.

**Implementation**: dispatched to seat `gemini` (google-compat, gemini-3.5-flash)
via `parrot-sdd-coder`, attempt 1, merged as commit `4d9dc1b` (outcome `merged`).
Implemented all four blueprint steps: parent inheritance/explicit-null override
resolution for stored child queries (`MultiQS.query()`), deep-copy of pipeline
config before mutation, per-query preflight (`repo.get(QueryIdentity(...))`)
before any thread starts, `store=` keyword threaded through
`ThreadQuery`/`QueryExecutor`/`LocalExecutor`/`RemoteExecutor.execute()`, and
explicit rejection of non-legacy remote stores in `RemoteExecutor` until
versioned transport lands. New focused test file
`tests/tenants/test_tenant_child_execution.py` (4 tests) passed as merged.

**Review findings and fixes (orchestrator, same worktree, commit `4ba92b5`)**:
running the merged change against the broader `tests/multi`/executor/tenant
regression suite (not just the task's own focused file) surfaced two real
bugs in the merged code, both fixed in this worktree before closing the task:

1. The preflight step called `self.get_definition_repository()` (a real DB
   round trip) and computed an unused `parent_store` *unconditionally* —
   even for file-only/source-only pipelines with no stored queries — and it
   ran *before* the existing `MULTIQS_MAX_SOURCES_PER_REQUEST` guardrail, so
   an over-limit request paid for N sequential `repo.get()` calls before
   being rejected. Fixed: scoped the repository fetch to `if self._queries:`
   and moved the cheap guardrail check ahead of the preflight loop.
2. Six pre-existing tests across `tests/test_threadquery_executor.py`,
   `tests/test_multiqs_remote_dispatch.py`,
   `tests/unit/test_multiqs_output_raise.py`, and
   `tests/integration/test_multiquery_output_errors.py` broke because the
   new preflight step now runs before `ThreadQuery` is even constructed —
   these suites previously achieved full DB isolation solely by faking
   `ThreadQuery`. Fixed each to also stub `get_definition_repository`
   (matching the pattern already used in this task's own new test file) and
   added `store=None` to existing `_FakeThread` stand-ins so they accept the
   forwarded keyword. One assertion in `test_threadquery_executor.py` was
   updated for the intended new `store=` keyword on
   `executor.execute(...)` (AC-3).

**Checks run** (`source .venv/bin/activate && python -m pytest ...`):
- `tests/tenants/test_tenant_child_execution.py` — 4/4 passed (AC-5, exact
  command from the task).
- `tests/multi tests/test_abstract_multi.py tests/test_local_executor.py
  tests/test_multi_destinations_subpackage.py
  tests/test_multiqs_column_transforms.py
  tests/test_multiqs_destination_dispatch.py
  tests/test_multiqs_remote_dispatch.py
  tests/test_multiqs_slug_sources_normalize.py
  tests/test_multiqs_sources_integration.py
  tests/test_scheduler_multi_routing.py tests/test_threadquery_executor.py
  tests/handlers/test_multiquery_pbac_smoke.py
  tests/handlers/test_queryexecutor_pbac_smoke.py
  tests/integration/test_multiquery_output_errors.py
  tests/unit/test_multiqs_output_raise.py tests/tenants` — 198 passed, 2
  failed (both confirmed pre-existing, unrelated to this task — see below).
- `tests/tenants tests/handlers` (excluding the pre-existing broken
  `test_airtable_oauth.py` collection error, missing `aioresponses` dep) —
  130 passed.
- `ruff check` on every file touched by this task's fix (the 4 lines/blocks
  actually edited) — 0 new findings; all pre-existing ruff findings in
  `querysource/queries/multi/__init__.py` (F401, RUF013, I001 at line 203,
  BLE001, RUF015, LOG015) fall outside the edited line ranges and were left
  untouched.

**Pre-existing failures confirmed unrelated** (verified via
`git show 15e6bdc:<file>` — the commit immediately before TASK-727 merged —
to confirm each already existed before this task):
- `tests/test_local_executor.py::TestRemoteConfig::test_frozen_dataclass` —
  asserts `RemoteConfig(...).timeout == 60`; this environment's
  `QWORKER_TIMEOUT` env var resolves to `5`. Unrelated to any file this task
  touches.
- `tests/test_multiqs_sources_integration.py::test_guardrail_rejects_too_many_sources`
  — expects `DriverError`, but the pre-existing `self.Error()` helper
  (`querysource/interfaces/queries.py`) always constructs a plain
  `QueryException` (superclass of `DriverError`), never `DriverError`. This
  guardrail's `raise self.Error(...)` call predates TASK-727 verbatim
  (confirmed identical in `15e6bdc`); only its position relative to the new
  preflight code moved.
- `tests/test_remote_executor.py` (5 tests) — patches
  `querysource.queries.multi.sources.executors.QClient` as a module
  attribute, but `QClient` has always been imported lazily inside
  `RemoteExecutor.execute()` (confirmed identical in `15e6bdc`), so the
  patch target never existed either before or after this task.

**Spec deviations**: none. **Deployment gates unverified**: real qworker
(`qw.client.QClient`) remote dispatch end-to-end, and real-Postgres
discovery/preflight against an actual multi-schema deployment — both
require infrastructure unavailable in this sandbox (no network egress).

**Seat**: gemini · **Backend**: google-compat · **Model**: gemini-3.5-flash
· **Attempts**: 1 · **Duration**: 476.5s · **Tokens**: 3,098,500 in /
22,582 out.
