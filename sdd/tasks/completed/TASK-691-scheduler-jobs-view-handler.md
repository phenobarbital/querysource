# TASK-691: Implement SchedulerJobsView handler module

**Feature**: FEAT-100 — QSScheduler API Handler — Jobstore Introspection
**Spec**: `sdd/specs/qsscheduler-api-handler.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

QSScheduler (FEAT-008) runs an embedded APScheduler with no external way to
inspect what jobs are registered. This task creates the aiohttp class-based
view that exposes the jobstore as a read-only HTTP resource. The view is
intentionally a `BaseView` subclass so the follow-up spec can add
POST/PUT/PATCH/DELETE on the same class for pause/resume/remove operations
without re-routing.

This task delivers Module 1 of the spec (§3 Module Breakdown).

---

## Scope

- Create `querysource/handlers/scheduler.py` containing
  `SchedulerJobsView(BaseView)`.
- Implement the `get()` HTTP method handling **both** routes:
  - `GET /api/v1/qs/scheduler/jobs` — returns the envelope `{"scheduler":
    {...}, "jobs": [...]}` from spec §2 "List response shape".
  - `GET /api/v1/qs/scheduler/jobs/{job_id}` — returns a single serialized
    job dict, or **404** if not found.
- Implement two internal helpers:
  - `_get_scheduler(self) -> QSScheduler | None` — reads
    `self.request.app["qs_scheduler"]`, returns `None` if missing.
  - `_serialize_job(self, job) -> dict` — produces the per-job shape from
    spec §2 "Job serialization shape".
- Implement a tiny pure helper `_kind_from_id(job_id: str) -> str` that maps
  `"query_*"`/`"multi_*"`/`"cache_*"` → `"query"`/`"multi"`/`"cache"`,
  fallback `"unknown"`. Define it at module scope so unit tests can import
  it without instantiating the view.
- On both endpoints, return **503** with body
  `{"error": "...", "scheduler": {"enabled": True, "running": False}}` when
  `_get_scheduler()` returns `None` (mid-startup race). Use
  `self.Error(..., code=503)` if available on `BaseHandler`, otherwise build
  the `web.HTTPServiceUnavailable` response directly via `self.json_response`
  with `status=503`.
- Do **NOT** implement `post`/`put`/`patch`/`delete` — leave them absent so
  aiohttp's `web.View` returns the default 405. This is verified by the
  integration test in TASK-692.
- Write unit tests in `tests/test_scheduler_handler_unit.py` covering:
  - `_kind_from_id` for the four cases.
  - `_serialize_job` for an `IntervalTrigger` job (asserts `trigger.type ==
    "interval"`, non-empty `repr`, ISO-8601 `next_run_time`).
  - `_serialize_job` for a `CronTrigger` job (asserts `trigger.type ==
    "cron"`).
  - `_serialize_job` with `next_run_time = None` (paused job — output is
    JSON-`null`, not the literal string).
  - `_serialize_job` does NOT include the `notification_manager` from
    `job.kwargs` (only `slug` is extracted explicitly).

**NOT in scope**:
- Modifying `querysource/handlers/__init__.py` (TASK-692).
- Modifying `querysource/services.py` (TASK-692).
- Integration tests that boot a real aiohttp app (TASK-692).
- Implementing POST/PUT/PATCH/DELETE methods (out of scope for FEAT-100
  entirely — follow-up spec).
- Touching `querysource/scheduler/scheduler.py` (the spec explicitly resolved
  to keep `_scheduler` private in v1 — see §8).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/scheduler.py` | CREATE | `SchedulerJobsView(BaseView)` + helpers |
| `tests/test_scheduler_handler_unit.py` | CREATE | Unit tests for `_kind_from_id` and `_serialize_job` |

---

## Completion Note

**Completed by**: sdd-worker agent
**Date**: 2026-05-23
**Notes**: 
- Created `querysource/handlers/scheduler.py` with `SchedulerJobsView(BaseView)` class
- Implemented `_kind_from_id(job_id)` as module-level function
- Implemented `_serialize_job(job)` to handle both interval and cron triggers
- Handles `next_run_time=None` correctly (JSON null)
- Extracts only `slug` from `kwargs`, excluding `notification_manager`
- Implemented `get()` method to handle both list and single-job routes
- Returns 503 with correct envelope when scheduler is missing
- Created `tests/test_scheduler_handler_unit.py` with comprehensive unit tests
- All 13 unit tests pass
- `ruff check` and `mypy` pass
- Code follows project conventions (asyncio-first, Pydantic v2, Google-style docstrings)

**Deviations from spec**: none
