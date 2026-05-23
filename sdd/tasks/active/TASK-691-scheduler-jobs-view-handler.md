# TASK-691: Implement SchedulerJobsView handler module

**Feature**: FEAT-100 — QSScheduler API Handler — Jobstore Introspection
**Spec**: `sdd/specs/qsscheduler-api-handler.spec.md`
**Status**: pending
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

## Codebase Contract (Anti-Hallucination)

> Verified against `dev @ 5896cd0` (after the FEAT-100 spec commit).

### Verified Imports

```python
from aiohttp import web                                                  # standard
from navigator.views import BaseView
# verified: .venv/lib/python3.11/site-packages/navigator/views/base.py:619

from querysource.scheduler import QSScheduler
# verified: querysource/scheduler/__init__.py:2 (only for type annotations / TYPE_CHECKING)

from apscheduler.job import Job  # only if needed for type hints
# APScheduler 3.11.x is already a dependency — see querysource/scheduler/scheduler.py:21-25
```

The Job class itself does NOT need to be imported at runtime in this file
— `_serialize_job` accepts an `apscheduler.job.Job` instance and reads
attributes. Use a `TYPE_CHECKING` block for the annotation.

### Existing Signatures to Use

```python
# .venv/lib/python3.11/site-packages/navigator/views/base.py
class BaseView(aiohttp_cors.CorsViewMixin, BaseHandler, web.View):  # line 619
    def __init__(self, request, *args, **kwargs):                   # line 631

# BaseHandler (line 42) provides:
    def json_response(self, ...):                                   # line 144
    def match_parameters(self, request: web.Request = None) -> dict: # line 345
    def get_arguments(self, request: web.Request = None) -> dict:   # line 358
    def Error(self, ..., code: int = 400):                           # line 109
    def Except(self, ..., code: int = 500):                          # line 165
```

Use `self.request` (a `web.Request`) and `self.request.app` (a
`web.Application` dict-like) inside view methods. `web.View` populates
`self.request` automatically.

```python
# querysource/scheduler/scheduler.py
class QSScheduler:                                                  # line 46
    def __init__(self, loop=None):                                  # line 53
        self._scheduler: AsyncIOScheduler = None                    # line 57
        self._timezone = QS_SCHEDULER_TIMEZONE                      # line 56

    async def startup(self, app: web.Application) -> None:          # line 252
        ...
        app["qs_scheduler"] = self                                  # line 317
```

Read order in `get()`:
1. `scheduler = self.request.app.get("qs_scheduler")` (returns `None`
   if missing — preferred over `app["qs_scheduler"]` which would
   `KeyError`).
2. `aps = scheduler._scheduler`
3. `aps.running` — bool, exposed by `AsyncIOScheduler`.
4. `aps.get_jobs()` — list of `apscheduler.job.Job`.
5. `aps.get_job(job_id)` — single `Job` or `None`.

### apscheduler.job.Job attributes (verified via runtime inspection)

The implementation MUST only read these attributes — confirmed present on
APScheduler 3.11.x:

```
id, name, next_run_time, trigger, args, kwargs,
coalesce, max_instances, misfire_grace_time, pending
```

Specifically:
- `job.id`: str
- `job.name`: str
- `job.next_run_time`: `datetime.datetime | None` (None → JSON null)
- `job.trigger`: `IntervalTrigger | CronTrigger` instance — read
  `type(job.trigger).__name__` to derive `"interval"`/`"cron"` (strip the
  `Trigger` suffix), and `str(job.trigger)` for the human-readable repr.
- `job.kwargs`: dict — extract only `"slug"` for output. **Do NOT include
  the raw dict** (it contains `notification_manager`, which is not
  JSON-serializable and is internal state).
- `job.coalesce`: bool
- `job.max_instances`: int
- `job.misfire_grace_time`: int | None
- `job.pending`: bool

### Pattern Reference: existing class-based view

`querysource/handlers/variables.py:9` — `VariablesService(BaseView)`. Mirror
its overall shape (subclass `BaseView`, define async `get(self)`, use
`self.json_response(...)`, use `self.query_parameters(self.request)` and
`self.get_args()` when applicable).

`querysource/handlers/integrations/airtable.py:92` — `AirtableConnectView`
uses `AbstractHandler` (slightly different base), but the response patterns
(`web.Response(status=..., text=..., content_type=...)`) are useful when
`BaseHandler.Error` is not the right fit.

### Does NOT Exist

- ~~`QSScheduler.get_jobs()`~~ / ~~`QSScheduler.get_job()`~~ — there is no
  public method on `QSScheduler` exposing jobs. Read `_scheduler` directly
  (spec §8 Q4: resolved to defer promotion).
- ~~`QSScheduler.scheduler`~~ — the attribute is `_scheduler` (with
  underscore), see `querysource/scheduler/scheduler.py:57`.
- ~~`app["scheduler"]`~~ — the key is `"qs_scheduler"`, see
  `querysource/scheduler/scheduler.py:317`.
- ~~`Job.to_dict()` / `Job.json()`~~ — APScheduler `Job` has no
  serialization method. Must be hand-rolled in `_serialize_job`.
- ~~`querysource.handlers.scheduler`~~ — does not exist yet; this task
  creates it.
- ~~`self.app`~~ on a `BaseView` instance — `BaseView` inherits from
  `web.View`, so use `self.request.app`, NOT `self.app`.

---

## Implementation Notes

### Suggested module skeleton

```python
"""Scheduler jobstore introspection handler — FEAT-100.

Read-only HTTP surface on top of QSScheduler's APScheduler jobstore.

Routes (registered in querysource/services.py when ENABLE_QS_SCHEDULER=True):
    GET /api/v1/qs/scheduler/jobs            -> list all jobs
    GET /api/v1/qs/scheduler/jobs/{job_id}   -> single job, or 404

Implements only GET. POST/PUT/PATCH/DELETE are intentionally absent so
aiohttp's web.View returns 405 — a follow-up spec will add them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from aiohttp import web
from navconfig.logging import logging
from navigator.views import BaseView

if TYPE_CHECKING:
    from apscheduler.job import Job
    from querysource.scheduler import QSScheduler

logger = logging.getLogger("QS.SchedulerJobsView")


def _kind_from_id(job_id: str) -> str:
    """Map job ID prefix to its kind.

    Args:
        job_id: APScheduler job ID, e.g. ``"query_foo"``.

    Returns:
        One of ``"query"``, ``"multi"``, ``"cache"``, ``"unknown"``.
    """
    if job_id.startswith("query_"):
        return "query"
    if job_id.startswith("multi_"):
        return "multi"
    if job_id.startswith("cache_"):
        return "cache"
    return "unknown"


class SchedulerJobsView(BaseView):
    """Read-only HTTP view over the QSScheduler APScheduler jobstore."""

    def _get_scheduler(self) -> Optional["QSScheduler"]:
        return self.request.app.get("qs_scheduler")

    def _serialize_job(self, job: "Job") -> dict:
        nrt = job.next_run_time
        trigger_type = type(job.trigger).__name__.lower().removesuffix("trigger")
        slug = (job.kwargs or {}).get("slug")
        return {
            "id": job.id,
            "name": job.name,
            "kind": _kind_from_id(job.id),
            "slug": slug,
            "next_run_time": nrt.isoformat() if nrt is not None else None,
            "trigger": {
                "type": trigger_type,
                "repr": str(job.trigger),
            },
            "coalesce": bool(job.coalesce),
            "max_instances": int(job.max_instances),
            "misfire_grace_time": job.misfire_grace_time,
            "pending": bool(job.pending),
        }

    async def get(self):
        scheduler = self._get_scheduler()
        if scheduler is None:
            return self.json_response(
                response={
                    "error": (
                        "QSScheduler is not available yet — the scheduler is "
                        "enabled but the application has not finished starting up."
                    ),
                    "scheduler": {"enabled": True, "running": False},
                },
                status=503,
            )

        aps = scheduler._scheduler
        params = self.match_parameters(self.request)
        job_id = params.get("job_id")

        if job_id:
            job = aps.get_job(job_id) if aps is not None else None
            if job is None:
                return self.json_response(
                    response={"error": f"Job '{job_id}' not found."},
                    status=404,
                )
            return self.json_response(
                response=self._serialize_job(job),
                status=200,
            )

        jobs = aps.get_jobs() if aps is not None else []
        return self.json_response(
            response={
                "scheduler": {
                    "enabled": True,
                    "running": bool(aps.running) if aps is not None else False,
                    "timezone": str(scheduler._timezone),
                    "job_count": len(jobs),
                },
                "jobs": [self._serialize_job(j) for j in jobs],
            },
            status=200,
        )
```

This skeleton is illustrative — the agent should adapt it to match
`json_response`'s actual signature (verify by reading
`.venv/lib/python3.11/site-packages/navigator/views/base.py:144`).

### Key Constraints

- Async `get()` only — no other HTTP verbs.
- Use `self.request.app.get("qs_scheduler")`, NOT `self.request.app["qs_scheduler"]`.
- `_kind_from_id` must be a module-level function so unit tests can import
  and call it without constructing a `BaseView` (which needs a real
  `web.Request`).
- Never include `job.kwargs` verbatim in the output — `notification_manager`
  is in there and is not JSON-serializable.
- `next_run_time.isoformat()` is the right serialization — pytz-aware
  datetimes serialize cleanly.

### References in Codebase

- `querysource/handlers/variables.py` — class-based view pattern.
- `querysource/handlers/integrations/airtable.py` — alternate handler style.
- `querysource/scheduler/scheduler.py:315-317` — only place that currently
  calls `_scheduler.get_jobs()`.

---

## Acceptance Criteria

- [ ] File `querysource/handlers/scheduler.py` exists and defines
      `SchedulerJobsView(BaseView)` and module-level `_kind_from_id`.
- [ ] `SchedulerJobsView.get()` handles both list and single-job routes,
      dispatched on the presence of `job_id` in `match_parameters`.
- [ ] List response matches the spec envelope (`scheduler.enabled`,
      `scheduler.running`, `scheduler.timezone`, `scheduler.job_count`,
      `jobs: [...]`).
- [ ] Each serialized job exposes `id`, `name`, `kind`, `slug`,
      `next_run_time` (ISO 8601 or `null`), `trigger.type`, `trigger.repr`,
      `coalesce`, `max_instances`, `misfire_grace_time`, `pending`.
- [ ] Single-job 404 returns `{"error": "..."}` with status 404.
- [ ] Missing scheduler returns 503 with the body shape from the spec.
- [ ] No POST/PUT/PATCH/DELETE methods are defined on the class.
- [ ] `job.kwargs` is never included verbatim in the response.
- [ ] Unit tests in `tests/test_scheduler_handler_unit.py` pass:
      `source .venv/bin/activate && pytest tests/test_scheduler_handler_unit.py -v`.
- [ ] `ruff check querysource/handlers/scheduler.py` passes.
- [ ] Import works: `from querysource.handlers.scheduler import SchedulerJobsView`.

---

## Test Specification

```python
# tests/test_scheduler_handler_unit.py
"""Unit tests for the SchedulerJobsView serialization helpers."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from querysource.handlers.scheduler import SchedulerJobsView, _kind_from_id


class TestKindFromId:
    @pytest.mark.parametrize("job_id, expected", [
        ("query_foo", "query"),
        ("multi_foo", "multi"),
        ("cache_foo", "cache"),
        ("weird_foo", "unknown"),
        ("", "unknown"),
    ])
    def test_prefix_mapping(self, job_id, expected):
        assert _kind_from_id(job_id) == expected


class TestSerializeJob:
    def _fake_job(self, *, job_id="query_foo", trigger=None,
                  next_run_time=None, kwargs=None):
        job = MagicMock()
        job.id = job_id
        job.name = f"Scheduled job: {job_id}"
        job.trigger = trigger or IntervalTrigger(seconds=30)
        job.next_run_time = next_run_time
        job.kwargs = kwargs or {"slug": "foo",
                                "notification_manager": object()}
        job.coalesce = True
        job.max_instances = 1
        job.misfire_grace_time = 1
        job.pending = False
        return job

    def _view(self):
        v = SchedulerJobsView.__new__(SchedulerJobsView)  # bypass __init__
        return v

    def test_interval_trigger(self):
        out = self._view()._serialize_job(self._fake_job(
            trigger=IntervalTrigger(seconds=30),
            next_run_time=datetime(2026, 5, 23, 14, 30,
                                   tzinfo=timezone.utc),
        ))
        assert out["id"] == "query_foo"
        assert out["kind"] == "query"
        assert out["slug"] == "foo"
        assert out["trigger"]["type"] == "interval"
        assert out["trigger"]["repr"]  # non-empty
        assert out["next_run_time"] == "2026-05-23T14:30:00+00:00"
        assert "notification_manager" not in out
        assert "kwargs" not in out

    def test_cron_trigger(self):
        out = self._view()._serialize_job(self._fake_job(
            trigger=CronTrigger(hour="*/2"),
        ))
        assert out["trigger"]["type"] == "cron"
        assert out["trigger"]["repr"]

    def test_next_run_time_none(self):
        out = self._view()._serialize_job(self._fake_job(
            next_run_time=None,
        ))
        assert out["next_run_time"] is None

    def test_kwargs_leakage(self):
        out = self._view()._serialize_job(self._fake_job(
            kwargs={"slug": "baz",
                    "notification_manager": MagicMock()},
        ))
        # `slug` is the only kwargs-derived field
        assert out["slug"] == "baz"
        # The full kwargs dict must not be present
        assert "kwargs" not in out
        assert "notification_manager" not in out
```

---

## Agent Instructions

1. Read the spec `sdd/specs/qsscheduler-api-handler.spec.md` end-to-end.
2. Verify the Codebase Contract by `read`-ing the cited line numbers in
   `querysource/scheduler/scheduler.py` and the navigator `base.py`. If
   any signature drifted, update this task's contract first, then
   implement.
3. Create `querysource/handlers/scheduler.py` following the suggested
   skeleton but adapting to the real `json_response` signature.
4. Create `tests/test_scheduler_handler_unit.py` and ensure all tests pass:
   `source .venv/bin/activate && pytest tests/test_scheduler_handler_unit.py -v`.
5. Run `ruff check querysource/handlers/scheduler.py`. Fix any findings.
6. Move this file to `sdd/tasks/completed/TASK-691-scheduler-jobs-view-handler.md`.
7. Update `sdd/tasks/index/qsscheduler-api-handler.json` — set this task's
   `status` to `"done"` and fill in `completed_at`.
8. Fill in the Completion Note below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
