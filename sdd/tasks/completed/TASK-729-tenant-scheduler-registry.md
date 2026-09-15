# TASK-729: Load and identify scheduled definitions per physical store

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-728
**Assigned-to**: unassigned

## Context

Implements M6 scheduler core of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Replace hardcoded public startup reads and runtime fetches with repository schedulable/get over unique registry stores. Honor configured legacy overrides and complete discovery before jobs.
- Preserve legacy IDs for configured default and use qsj2 kind/store digest/URL-safe slug for every other store. Include validated owner envelope in all job kwargs; physical aliases schedule once.
- Extend register_slug and _slug_job_ids/_fetch_slug_row exactly as spec. Update _register_query_row and _register_cache_row to receive resolved store internally; mutation of one owner must not remove another owner job.
- Distinguish missing definition from metadata connection/store failures and preserve existing scheduler flags/cron interpretation. Existing deployment remains responsible for one scheduler process.

**NOT in scope**: Distributed leader election and changing notification callback arity.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | Central identity prevents slug-based removals from affecting other stores. |
| `tests/tenants/test_tenant_scheduler_registry.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryStore, TenantOwnerEnvelope
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
querysource/scheduler/scheduler.py:221
def _register_query_row(self, row: dict) -> Optional[str]:

querysource/scheduler/scheduler.py:323
def _register_cache_row(self, row: dict) -> Optional[str]:

querysource/scheduler/scheduler.py:492
async def startup(self, app: web.Application) -> None:

querysource/scheduler/scheduler.py:117
class QSScheduler:
def __init__(self, loop: asyncio.AbstractEventLoop=None):
async def register_slug(self, slug: str) -> dict:
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

1. Replace hardcoded public startup reads and runtime fetches with repository schedulable/get over unique registry stores. Honor configured legacy overrides and complete discovery before jobs. **Why:** Scheduler startup currently hardcodes public storage.
2. Preserve legacy IDs for configured default and use qsj2 kind/store digest/URL-safe slug for every other store. Include validated owner envelope in all job kwargs; physical aliases schedule once. **Why:** Identical slugs must not collide in the scheduler.
3. Extend register_slug and _slug_job_ids/_fetch_slug_row exactly as spec. Update _register_query_row and _register_cache_row to receive resolved store internally; mutation of one owner must not remove another owner job. **Why:** Selected-owner updates must not remove another owner jobs.
4. Distinguish missing definition from metadata connection/store failures and preserve existing scheduler flags/cron interpretation. Existing deployment remains responsible for one scheduler process. **Why:** Store outages must not masquerade as deleted definitions.

### `querysource/scheduler/scheduler.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QSScheduler:` (verified: querysource/scheduler/scheduler.py:117)
class QSScheduler:
    """Existing scheduler; enumerate registry stores through shared repository."""

    async def register_slug(self, slug: str, *, tenant: str | None=None) -> dict:
        """Synchronize only selected owner; response includes tenant/store identity."""
        # FILL IN: Read through repository, use canonical identity, preserve legacy IDs and carry owner envelopes.
        raise NotImplementedError

    def _slug_job_ids(self, slug: str, *, store: QueryStore | None=None) -> list[str]:
        """Retain configured-default IDs and qualify all other stores."""
        # FILL IN: Read through repository, use canonical identity, preserve legacy IDs and carry owner envelopes.
        raise NotImplementedError

    async def _fetch_slug_row(self, slug: str, *, tenant: str | None=None) -> dict | None:
        """Use repository; distinguish missing row from unavailable store."""
        # FILL IN: Read through repository, use canonical identity, preserve legacy IDs and carry owner envelopes.
        raise NotImplementedError
# FILL IN: wire startup and both row registration helpers to these identities;
# preserve scheduler lifecycle and existing eligibility/cron semantics.
```

**Why:** Central identity prevents slug-based removals from affecting other stores.

### `tests/tenants/test_tenant_scheduler_registry.py` (CREATE)

```python
"""Load and identify scheduled definitions per physical store regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_startup_all_stores_and_legacy_override() -> None:
    """startup all stores and legacy override."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_alias_dedup_and_same_slug_job_ids() -> None:
    """alias dedup and same slug job ids."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_register_update_remove_only_owner() -> None:
    """register update remove only owner."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_runtime_store_error_not_missing() -> None:
    """runtime store error not missing."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/scheduler/scheduler.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_scheduler_registry.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Replace hardcoded public startup reads and runtime fetches with repository schedulable/get over unique registry stores. Honor configured legacy overrides and complete discovery before jobs.
- [ ] AC-2: Preserve legacy IDs for configured default and use qsj2 kind/store digest/URL-safe slug for every other store. Include validated owner envelope in all job kwargs; physical aliases schedule once.
- [ ] AC-3: Extend register_slug and _slug_job_ids/_fetch_slug_row exactly as spec. Update _register_query_row and _register_cache_row to receive resolved store internally; mutation of one owner must not remove another owner job.
- [ ] AC-4: Distinguish missing definition from metadata connection/store failures and preserve existing scheduler flags/cron interpretation. Existing deployment remains responsible for one scheduler process.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_scheduler_registry.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_startup_all_stores_and_legacy_override` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_alias_dedup_and_same_slug_job_ids` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_register_update_remove_only_owner` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_runtime_store_error_not_missing` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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
("attempt 3") after the dispatched native attempt was rejected.

**Dispatch history**: the task was prepared as a native seat
(`coder_prepare_native`) and run as a background `sdd-coder` agent
(model `haiku`). It completed and committed real work (`ea73b95` +
`cf6b9c3`), but `coder_merge` returned `outcome: fidelity_violation`
because both commits also modified `sdd/tasks/index/per-tenant-queries.json`
and moved the task file under `sdd/tasks/` — SDD-state bookkeeping that is
the orchestrator's exclusive responsibility, never a task coder's, per its
own mandate ("commits code only, never touches sdd/"). Per protocol this
was never merged by hand.

Reviewing that unmerged diff (read-only, for reference) surfaced two real
bugs worth fixing rather than adopting outright:
- `DefinitionRepository(self._registry)` was constructed with only one
  positional argument — the real constructor requires a second, required
  `connection_factory` argument (`querysource/repositories/definitions.py:
  59-66`). This would raise `TypeError` immediately at `QSScheduler()`
  construction time.
- `self._registry = TenantRegistry()` was constructed fresh in `__init__`
  and never discovered anywhere in the diff. `TenantRegistry.stores()` on
  an undiscovered registry is always `()` (confirmed by reading
  `querysource/tenants.py`), so `startup()`'s "enumerate every store" loop
  would have silently iterated zero stores in any real deployment, never
  loading a single scheduled job — a complete, silent functional failure
  of this task's entire purpose.

**Implementation** (fresh, in this worktree):
- `querysource/scheduler/scheduler.py`: `startup()` now reads
  `app["qs_tenant_registry"]` / `app["qs_definition_repository"]` —
  published before scheduler startup by the app's `on_startup` ordering
  (established by TASK-720, confirmed by the existing
  `tests/tenants/test_tenant_bootstrap_lookup.py::
  test_startup_order_before_scheduler`) — and enumerates every discovered
  store via `repository.schedulable(store)`, replacing the old hardcoded
  `public.queries` SELECT and the scheduler-owned `AsyncPool` entirely
  (AC-1). `_qualified_job_id()` retains legacy ids for the registry's
  configured default store (via the registry's own public `resolve()`,
  never a private `_default_store` reach-in) and uses
  `qsj2-<kind>-<store digest>-<url-safe-slug>` for every other store;
  every registered job's kwargs now carries a validated
  `TenantOwnerEnvelope`; `startup()` deduplicates identical slugs across
  stores so a physical alias schedules once (AC-2). `register_slug`/
  `_slug_job_ids`/`_fetch_slug_row` extended with `tenant`/`store`
  parameters; `_register_query_row`/`_register_cache_row` now accept and
  use the resolved store internally (AC-3). `_fetch_slug_row` distinguishes
  a genuinely missing definition (`TenantError(error_code="query_not_found")`,
  logged DEBUG) from a store/connection failure (any other exception,
  logged WARNING) — a down store is never mistaken for a deleted
  definition (AC-4).
- A real bug was caught by this task's OWN new focused test
  (`test_register_update_remove_only_owner`): the first draft of
  `_slug_job_ids` returned BOTH the legacy ids AND the qualified ids for a
  non-default store, so syncing a tenant-owned slug via `register_slug`
  would ALSO remove the DEFAULT store's identically-named job as a side
  effect — a direct violation of AC-3. Fixed to return only the ids that
  actually belong to the resolved store.
- `tests/tenants/test_tenant_scheduler_registry.py` (new, 4 tests):
  multi-store startup with a non-"public" configured default, proving
  legacy-id preservation, qsj2-qualification, and owner envelopes;
  alias dedup across stores plus disjoint per-store job-id sets;
  register/update/remove scoped to exactly one owner (the regression test
  that caught the bug above); and `_fetch_slug_row`'s missing-vs-unavailable
  log-level distinction.
- `tests/test_scheduler_core.py`, `tests/test_scheduler_multi_routing.py`:
  the pre-existing `QSScheduler.__new__(QSScheduler)` raw-construction test
  helpers that reach `_register_query_row`/`_register_cache_row` now also
  set `_registry = TenantRegistry()`, matching the same minimal-attribute
  pattern they already use for `_notification_manager`/`_scheduler`.

**Checks run** (`source .venv/bin/activate && python -m pytest ...`):
- `tests/tenants/test_tenant_scheduler_registry.py` — 4/4 passed (AC-5,
  exact command from the task).
- Full pre-existing scheduler suite (`test_scheduler_core`,
  `test_scheduler_multi_routing`, `tests/scheduler/`,
  `test_scheduler_integration`, `test_scheduler_handler_integration`,
  `test_scheduler_jobs`) — 67 passed, 2 pre-existing unrelated failures
  (see below) after the two fixture fixes above.
- Full tenants/handlers/multi/executor regression sweep — 334 passed, 4
  failed.
- `ruff check` on every touched file — cross-checked line-by-line against
  commit `2b2d6db` (the state immediately before this task): 0 new
  findings; every remaining finding (import-sort, pre-existing
  `Optional`/`Union` style, broad-except in code this task did not touch)
  traces to an untouched line or the file's own established broad-except
  philosophy (5 pre-existing instances reduced to 3 net, by removing the
  old raw-SQL/AsyncPool try/except blocks this task replaced).

**Pre-existing failures confirmed unrelated** (verified against `2b2d6db`):
`test_scheduler_not_imported_when_disabled` (this environment's
`ENABLE_QS_SCHEDULER` resolves `True`, not the test's assumed default),
`test_post_returns_405` (unrelated 400-vs-405 routing behavior),
`test_frozen_dataclass` and `test_guardrail_rejects_too_many_sources`
(both already documented as pre-existing in TASK-727/728's completion
notes).

**Spec deviations**: none. **Deployment gates unverified**: real
multi-schema Postgres discovery and scheduled execution against actual
tenant stores (sandbox has no network egress); the scheduler no longer
owns a DB pool at all (removed — every read now goes through the shared,
already-tested `DefinitionRepository`), so this is a net simplification
rather than a new gate.
