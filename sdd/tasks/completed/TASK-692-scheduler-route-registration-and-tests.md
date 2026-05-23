# TASK-692: Wire up scheduler routes + integration tests

**Feature**: FEAT-100 — QSScheduler API Handler — Jobstore Introspection
**Spec**: `sdd/specs/qsscheduler-api-handler.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-691
**Assigned-to**: unassigned

---

## Context

Once `SchedulerJobsView` exists (TASK-691), it must be:

1. Re-exported from `querysource.handlers` for the public API surface
   (Module 2 of the spec).
2. Mounted on the aiohttp router at
   `/api/v1/qs/scheduler/jobs` and
   `/api/v1/qs/scheduler/jobs/{job_id}`, **only when**
   `ENABLE_QS_SCHEDULER=True` (Module 3 of the spec).
3. Exercised end-to-end through aiohttp's test client to confirm 200/404/503
   and the **405 default** for unimplemented verbs — the latter is the
   load-bearing contract that proves the class-based view is ready for
   POST/PUT/PATCH/DELETE in a follow-up spec.

This task delivers Modules 2 and 3 plus all integration tests from spec §4.

---

## Scope

- Modify `querysource/handlers/__init__.py` to re-export `SchedulerJobsView`
  from `querysource.handlers.scheduler`. If a `__all__` list exists, append
  the new name; otherwise just add the import (matching the existing
  style of `from .X import Y` re-exports in that file).
- Modify `querysource/services.py` so that **inside the existing
  `if ENABLE_QS_SCHEDULER:` block** (currently at lines 317-320) and
  **after** `self._scheduler.setup(self.app)`, two routes are registered:
  - `self.app.router.add_view('/api/v1/qs/scheduler/jobs', SchedulerJobsView)`
  - `self.app.router.add_view('/api/v1/qs/scheduler/jobs/{job_id}', SchedulerJobsView)`

  Both must be appended to the existing `routes` list to match the
  surrounding pattern. Use a **lazy import** inside the block:
  `from .handlers.scheduler import SchedulerJobsView` — mirror the existing
  pattern at services.py:217 (`from .handlers.components import ...`).
- Write integration tests in `tests/test_scheduler_handler_integration.py`
  using `aiohttp.test_utils.AioHTTPTestCase` or `aiohttp_pytest_plugin`
  (whichever the rest of the test suite uses — discover by `grep`-ing
  existing tests). Tests required:
  1. `test_get_jobs_lists_registered_jobs` — build an app where
     `app["qs_scheduler"]` is a stub `QSScheduler` with a real
     `AsyncIOScheduler` containing two jobs (one with `id="query_foo"`,
     one with `id="multi_bar"`). `GET /api/v1/qs/scheduler/jobs` → 200,
     body has `scheduler.job_count == 2`, both job IDs present.
  2. `test_get_single_job_by_id` — same fixture; `GET .../query_foo` → 200,
     body is the single serialized job (no envelope).
  3. `test_get_single_job_missing_returns_404` — `GET .../does_not_exist`
     → 404.
  4. `test_get_jobs_returns_503_when_scheduler_missing` — app where
     `app["qs_scheduler"]` is absent → 503, body has
     `scheduler.running == false`.
  5. `test_post_returns_405` — `POST /api/v1/qs/scheduler/jobs` → 405
     (aiohttp default behavior — proves the class-based view leaves future
     verbs unimplemented but routable).
  6. `test_route_not_registered_when_flag_off` — boot a fresh QuerySource
     with `ENABLE_QS_SCHEDULER=False` (monkeypatch the conf flag); `GET
     /api/v1/qs/scheduler/jobs` → 404 from the router itself (no view
     instantiated). Verify the response shape is the default aiohttp 404,
     not the handler's JSON 503/404.

**NOT in scope**:
- Modifying `querysource/scheduler/` (the spec resolved to keep
  `_scheduler` private — see §8 Q4).
- Adding PBAC enforcement (spec §8 Q1: resolved — no PBAC in v1).
- Adding `?kind` / `?slug` filter query params (spec §8 Q2: resolved —
  no filters in v1).
- Adding any new HTTP verbs (POST/PUT/PATCH/DELETE) on the view (out of
  scope for FEAT-100 entirely).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/__init__.py` | MODIFY | Re-export `SchedulerJobsView` |
| `querysource/services.py` | MODIFY | Register two routes inside the `if ENABLE_QS_SCHEDULER:` block |
| `tests/test_scheduler_handler_integration.py` | CREATE | Integration tests covering all 6 cases above |

---

## Completion Note

**Completed by**: sdd-worker agent
**Date**: 2026-05-23
**Notes**:
- Modified `querysource/handlers/__init__.py` to re-export `SchedulerJobsView`
- Modified `querysource/services.py` to register two routes inside the existing `if ENABLE_QS_SCHEDULER:` block
- Routes registered lazily inside the flag block as specified
- Created `tests/test_scheduler_handler_integration.py` with 6 integration tests
- Uses `aiohttp_client` fixture pattern (pytest-asyncio compatible)
- Fixture creates a StubQSScheduler with real AsyncIOScheduler (in-memory, no external dependencies)
- All 6 integration tests pass:
  - test_get_jobs_lists_registered_jobs (200, correct job count)
  - test_get_single_job_by_id (200, correct single job)
  - test_get_single_job_missing_returns_404 (404)
  - test_get_jobs_returns_503_when_scheduler_missing (503)
  - test_post_returns_405 (405 as expected)
  - test_route_not_registered_when_flag_off (router-level 404 when flag disabled)
- All existing scheduler tests still pass (no regression)
- `ruff check` passes on all modified/created files
- `mypy` passes

**Deviations from spec**: none
