# TASK-730: Propagate job ownership and synchronize committed CRUD

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-729
**Assigned-to**: unassigned

## Context

Implements M6 job/API/CRUD synchronization of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Add required keyword owner context to all three scheduled job callables, preserving legacy kwargs/default behavior. Revalidate deserialized envelope against registry, then execute selected owner.
- Extend scheduler API serialized records and register/filter/control requests with tenant/store identity. Pause/resume/delete act on full job ID; changing tenant selector must never affect a same-slug different-owner job.
- After successful committed management mutations call register_slug for the selected owner; deletion removes that owner jobs. Preserve response status; report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback.
- Keep notify(job_id, slug, error) unchanged; qualified IDs/log fields carry ownership. Refresh uses the current definition revision key.

**NOT in scope**: External message delivery and notification subscription redesign.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/jobs.py` | MODIFY | Owner revalidation belongs at execution time because registry/allowlist can differ after restart. |
| `querysource/handlers/scheduler.py` | MODIFY | API clients need enough identity to distinguish overlapping slugs. |
| `querysource/handlers/manager.py` | MODIFY | Database success and scheduler synchronization are separate outcomes and must be reported honestly. |
| `tests/tenants/test_tenant_scheduler_api_jobs.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.tenants import QueryIdentity, TenantOwnerEnvelope
from querysource.scheduler.notifications import NotificationManager
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.scheduler.notifications.NotificationManager` → `querysource/scheduler/notifications.py:21`

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/scheduler/jobs.py:94
async def cache_refresh_job(slug: str, notification_manager: Optional['NotificationManager']=None, **kwargs) -> None:

querysource/scheduler/notifications.py:37
def notify(self, job_id: str, slug: str, error: Exception) -> None:

querysource/handlers/scheduler.py:189
async def post(self) -> web.Response:

querysource/scheduler/jobs.py:22
async def scheduled_query_job(slug: str, notification_manager: Optional['NotificationManager']=None, **kwargs) -> None:

querysource/handlers/scheduler.py:53
class SchedulerJobsView(BaseView):
def _serialize_job(self, job: 'Job') -> dict:
async def get(self) -> web.Response:
async def post(self) -> web.Response:
async def delete(self) -> web.Response:
async def patch(self) -> web.Response:

querysource/handlers/manager.py:32
class QueryManager(QueryView):
async def get(self):
async def patch(self):
async def delete(self):
async def put(self):
async def post(self):
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

1. Add required keyword owner context to all three scheduled job callables, preserving legacy kwargs/default behavior. Revalidate deserialized envelope against registry, then execute selected owner. **Why:** Registry/allowlist configuration may differ after restart.
2. Extend scheduler API serialized records and register/filter/control requests with tenant/store identity. Pause/resume/delete act on full job ID; changing tenant selector must never affect a same-slug different-owner job. **Why:** Clients need full identity to control overlapping slugs.
3. After successful committed management mutations call register_slug for the selected owner; deletion removes that owner jobs. Preserve response status; report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback. **Why:** A synchronization failure cannot roll back an already committed row.
4. Keep notify(job_id, slug, error) unchanged; qualified IDs/log fields carry ownership. Refresh uses the current definition revision key. **Why:** Existing notification callback signatures are public compatibility.

### `querysource/scheduler/jobs.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `async def scheduled_query_job(` (verified: querysource/scheduler/jobs.py:22)
async def scheduled_query_job(slug: str, notification_manager: NotificationManager | None=None, *, owner: TenantOwnerEnvelope | None=None, **kwargs: Any) -> None:
    """Revalidate owner and execute QS with matching runtime/cache context."""
    # FILL IN: Revalidate envelope against initialized registry; pass owner to QS/MultiQS; preserve error notification and refresh semantics.
    raise NotImplementedError

async def scheduled_multiqs_job(slug: str, notification_manager: NotificationManager | None=None, *, owner: TenantOwnerEnvelope | None=None, **kwargs: Any) -> None:
    """Revalidate owner and preserve it through all pipeline children."""
    # FILL IN: Revalidate envelope against initialized registry; pass owner to QS/MultiQS; preserve error notification and refresh semantics.
    raise NotImplementedError

async def cache_refresh_job(slug: str, notification_manager: NotificationManager | None=None, *, owner: TenantOwnerEnvelope | None=None, **kwargs: Any) -> None:
    """Refresh only this owner's current definition revision."""
    # FILL IN: Revalidate envelope against initialized registry; pass owner to QS/MultiQS; preserve error notification and refresh semantics.
    raise NotImplementedError
```

**Why:** Owner revalidation belongs at execution time because registry/allowlist can differ after restart.

### `querysource/handlers/scheduler.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class SchedulerJobsView(BaseView):` (verified: querysource/handlers/scheduler.py:53)
def _serialize_job(self, job: 'Job') -> dict:
    """Include canonical owner/tenant identity from validated job kwargs; preserve current response fields."""
    # FILL IN: Include canonical owner/tenant identity from validated job kwargs; preserve current response fields.
    raise NotImplementedError
# FILL IN: propagate selector on post/get; control exact job IDs on patch/delete.
```

**Why:** API clients need enough identity to distinguish overlapping slugs.

### `querysource/handlers/manager.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryManager(QueryView):` (verified: querysource/handlers/manager.py:32)
async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:
    """Synchronize jobs only after the definition transaction commits."""
    # FILL IN: No scheduler => success; otherwise selected-owner register/remove; failure logs and returns False; callers set failure header.
    raise NotImplementedError
```

**Why:** Database success and scheduler synchronization are separate outcomes and must be reported honestly.

### `tests/tenants/test_tenant_scheduler_api_jobs.py` (CREATE)

```python
"""Propagate job ownership and synchronize committed CRUD regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_query_multi_refresh_envelope_roundtrip() -> None:
    """query multi refresh envelope roundtrip."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_api_serialization_filter_pause_resume_delete() -> None:
    """api serialization filter pause resume delete."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_crud_commit_then_sync_failure_header() -> None:
    """crud commit then sync failure header."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_notification_callback_arity_preserved() -> None:
    """notification callback arity preserved."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/scheduler/jobs.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/scheduler.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/manager.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_scheduler_api_jobs.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Add required keyword owner context to all three scheduled job callables, preserving legacy kwargs/default behavior. Revalidate deserialized envelope against registry, then execute selected owner.
- [ ] AC-2: Extend scheduler API serialized records and register/filter/control requests with tenant/store identity. Pause/resume/delete act on full job ID; changing tenant selector must never affect a same-slug different-owner job.
- [ ] AC-3: After successful committed management mutations call register_slug for the selected owner; deletion removes that owner jobs. Preserve response status; report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback.
- [ ] AC-4: Keep notify(job_id, slug, error) unchanged; qualified IDs/log fields carry ownership. Refresh uses the current definition revision key.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_scheduler_api_jobs.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_query_multi_refresh_envelope_roundtrip` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_api_serialization_filter_pause_resume_delete` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_crud_commit_then_sync_failure_header` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_notification_callback_arity_preserved` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

**Author/date**: sdd-worker (orchestrator), 2026-09-15. Merged (`outcome:
merged`, seat `qwen`, attempt 2 — attempt 1 with `glm` hit the 60-turn cap
with no `final_output`), then reviewed and fixed in this worktree.

**Dispatch history**: attempt 1 (`glm`, `zai.glm-4.7-flash`) failed —
`max_turns=60` exceeded with no recovered result. Attempt 2 (`qwen`,
`qwen.qwen3-coder-480b-a35b-instruct`) completed and merged cleanly (file
fidelity matched the declared list exactly: `querysource/scheduler/jobs.py`,
`querysource/handlers/scheduler.py`, `querysource/handlers/manager.py`,
`tests/tenants/test_tenant_scheduler_api_jobs.py`).

**Review findings and fixes** (same worktree, commit `967f372`): running the
full multi/scheduler/handlers regression suite (not just the task's own
focused file) and re-reading the diff against AC-1/AC-2 surfaced three real
gaps in the merged code:

1. `querysource/scheduler/jobs.py` — every one of the three job callables
   shipped with a self-confessed stub comment ("In a real implementation,
   we would validate the owner against the registry / For now, we'll just
   pass it through"), directly contradicting AC-1's "Revalidate
   deserialized envelope against registry, then execute selected owner"
   and the codebase's "no TODOs/stubs" convention. Fixed by adding a
   shared `_revalidate_owner()` helper: re-resolves the envelope's schema
   against the CURRENT registry (via the constructed QS/MultiQS's own
   `get_definition_repository()`) and compares
   `database_namespace`/`table`/`contract` before executing — a registry
   that has drifted since this job was registered (e.g. after a restart
   with a changed allowlist) now fails the job and notifies, instead of
   silently executing against a stale store.
2. `querysource/handlers/scheduler.py` — `_kind_from_id()` was left
   unchanged, matching only the legacy `<kind>_<slug>` prefix. TASK-729
   registers every non-default-store job under the
   `qsj2-<kind>-<digest>-<slug>` shape, so every tenant-owned job's
   serialized `kind` field silently came back `"unknown"` — a genuine
   cross-task AC-2 regression from TASK-729 landing after this task's
   blueprint was authored. Fixed to also parse the qsj2 shape.
3. `tests/tenants/test_tenant_scheduler_api_jobs.py` — two of the four
   required tests were literal `assert True  # Placeholder` stubs, and a
   third monkeypatched away the exact function (`_kind_from_id`) it
   claimed to test. None of the four, as merged, actually exercised
   AC-1/AC-2/AC-3/AC-4. Rewrote all four with real fixtures: envelope
   roundtrip + revalidation-mismatch rejection for all three job
   callables; real `_kind_from_id`/`_serialize_job` plus pause/resume/
   delete acting on the exact job id (proving a same-slug different-owner
   job is never touched); a committed-then-sync-failure-header round trip
   through `QueryManager.patch()` (plus the success/no-scheduler cases for
   contrast); and a real failing job proving `notify(job_id, slug,
   error)`'s 3-arg shape is unchanged (AC-4).
4. `tests/test_scheduler_jobs.py` — three pre-existing exact-call
   assertions (`QS(slug=...)` / `MultiQS(slug=...)`) needed `tenant=None`
   added: an intended, harmless consequence of AC-1 (the `tenant` kwarg's
   default is already `None`, so runtime behavior is unchanged; only the
   pre-existing mock assertion was now too strict for the new,
   always-present keyword).

**Checks run** (`source .venv/bin/activate && python -m pytest ...`):
- `tests/tenants/test_tenant_scheduler_api_jobs.py` — 4/4 passed (AC-5,
  exact command from the task, real assertions after the rewrite).
- `tests/test_scheduler_jobs.py` — 11/11 passed after the 3 assertion
  fixes.
- Full scheduler/tenants/handlers/multi/executor regression sweep — 338
  passed, 4 failed (all four confirmed pre-existing and unrelated,
  documented in TASK-727/728/729's own completion notes: `test_frozen_
  dataclass`, `test_guardrail_rejects_too_many_sources`, `test_scheduler_
  not_imported_when_disabled`, `test_post_returns_405`).
- `ruff check` on every touched file, cross-checked against the immediate
  pre-task baseline (`161c8e8`): `handlers/scheduler.py` now passes
  cleanly (0 findings, down from baseline's 6, via the merged diff's own
  incidental type-hint cleanups plus the `_kind_from_id` fix);
  `scheduler/jobs.py` down to 4 findings (from baseline's 9 — net
  improvement from the rewrite's consistent `X | None` typing); `handlers/
  manager.py`'s one genuinely new finding (a `BLE001` broad except inside
  `_sync_definition_jobs`) matches this file's own dominant, pre-existing
  18-instance broad-except-and-log convention exactly, and is required by
  AC-3's own design (any scheduler-side failure, however it manifests,
  must become a header — never propagate and look like a DB rollback); the
  new test file is fully clean.

**Spec deviations**: none. **Deployment gates unverified**: real
multi-process scheduler synchronization under load, and the owner-
revalidation path against an actual reconfigured/restarted registry
(sandbox has no live Postgres); `notification_manager`'s pluggable
Telegram/Slack/webhook callbacks (explicitly out of scope per this task's
"NOT in scope: External message delivery and notification subscription
redesign").
