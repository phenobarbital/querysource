---
type: feature
base_branch: dev
---

# Feature Specification: QSScheduler API Handler — Jobstore Introspection

**Feature ID**: FEAT-100
**Date**: 2026-05-23
**Author**: Jesus Lara / Claude
**Status**: approved
**Target version**: 5.x

---

## 1. Motivation & Business Requirements

### Problem Statement

`QSScheduler` (FEAT-008) runs as an embedded APScheduler inside the QuerySource
aiohttp process, but it currently exposes **no external visibility** into the
jobstore. Operators have no way to:

- Confirm which slugs were picked up at startup
- Verify a job's next scheduled run time
- Audit job IDs (`query_<slug>`, `multi_<slug>`, `cache_<slug>`) registered
  on a given instance
- Diagnose "is my schedule live?" without `grep`-ing logs or attaching a
  Python REPL to the live process

The only current visibility is a one-shot log line at startup
(`"QSScheduler started with N active job(s)"`) — no per-job data is surfaced.

### Goals

- Add a **GET** endpoint that returns the full list of jobs currently
  registered in the in-memory APScheduler jobstore, with their schedule
  metadata and next run time.
- Use an **aiohttp class-based View** (`web.View` via Navigator's `BaseView`)
  so future endpoints — POST to add ad-hoc jobs, PUT/PATCH to
  pause/resume/reschedule, DELETE to remove a job — can be added to the same
  class without re-routing.
- Mount the endpoint **only when** `ENABLE_QS_SCHEDULER=True`. When the flag
  is off, the scheduler does not exist and the route should not be exposed.

### Non-Goals (explicitly out of scope)

- POST / PUT / PATCH / DELETE endpoints (pause, resume, add ad-hoc, remove)
  — class-based view exists so these can be added in a follow-up spec, but
  none of these verbs are implemented in v1.
- Persistent jobstore introspection — `MemoryJobStore` is per-process; the
  endpoint returns the local instance's view only.
- Cross-instance aggregation — if multiple QS instances run with the
  scheduler enabled, each instance answers only for its own jobstore.
- PBAC policy authoring — see Open Questions; v1 ships without PBAC
  enforcement and lands behind the existing `ENABLE_QS_SCHEDULER` gate only.
- Pagination — expected job count is small (one row per schedulable
  `public.queries` entry).

---

## 2. Architectural Design

### Overview

A new aiohttp class-based view `SchedulerJobsView(BaseView)` is registered
at `/api/v1/qs/scheduler/jobs` (and `/api/v1/qs/scheduler/jobs/{job_id}` for
single-job lookup) when `ENABLE_QS_SCHEDULER=True`. The view reads the live
`QSScheduler` instance from `app["qs_scheduler"]` (set during scheduler
startup) and serializes the APScheduler `Job` objects returned by
`scheduler.get_jobs()` to JSON.

Only the `get()` method is implemented in v1. Future HTTP verbs (`post`,
`put`, `patch`, `delete`) are deliberately left to the framework's default
**405 Method Not Allowed** response so a later spec can add them as
additional methods on the same class without touching route registration.

### Component Diagram

```
GET /api/v1/qs/scheduler/jobs              ┐
GET /api/v1/qs/scheduler/jobs/{job_id}     ┘──→ SchedulerJobsView(BaseView)
                                                       │
                                                       ▼
                                            app["qs_scheduler"]: QSScheduler
                                                       │
                                                       ▼
                                            QSScheduler._scheduler.get_jobs()
                                                       │
                                                       ▼
                                            list[apscheduler.job.Job]
                                                       │
                                                       ▼
                                            _serialize_job(job) → dict
                                                       │
                                                       ▼
                                            self.json_response({...})
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/services.py:QuerySource.setup` | modifies | Conditionally registers the view route inside the existing `if ENABLE_QS_SCHEDULER:` block (next to `self._scheduler.setup(self.app)`). |
| `querysource/scheduler/scheduler.py:QSScheduler.startup` | reads only | Reads the `app["qs_scheduler"]` key it already sets at line 317. No changes to the scheduler. |
| `navigator.views:BaseView` | extends | Class-based view base — provides `json_response`, `get_arguments`, `match_parameters`, CORS config. |
| `querysource/handlers/__init__.py` | re-exports | Add `SchedulerJobsView` to the package's public surface for symmetry with other handlers. |
| `apscheduler.job.Job` | reads | Reads `id`, `name`, `next_run_time`, `trigger`, `kwargs`, `coalesce`, `max_instances`, `misfire_grace_time`, `pending`. No mutation. |

### Data Models

**Job serialization shape** (returned in the `jobs` array, or as the body of
the single-job endpoint):

```python
{
    "id": "query_my_slug",                # str — APScheduler job ID
    "name": "Scheduled query: my_slug",   # str — human-readable name
    "kind": "query",                      # str — "query" | "multi" | "cache" (derived from id prefix)
    "slug": "my_slug",                    # str | None — from job.kwargs.get("slug")
    "next_run_time": "2026-05-23T14:30:00+00:00",  # ISO 8601 str | None (None if paused)
    "trigger": {
        "type": "interval",               # str — "interval" | "cron"
        "repr": "interval[0:00:30]"       # str — str(job.trigger), for human readability
    },
    "coalesce": true,                     # bool
    "max_instances": 1,                   # int
    "misfire_grace_time": 1,              # int | None
    "pending": false                      # bool — True if scheduler not yet started
}
```

**List response shape** (`GET /api/v1/qs/scheduler/jobs`):

```python
{
    "scheduler": {
        "enabled": true,                  # mirrors ENABLE_QS_SCHEDULER
        "running": true,                  # scheduler._scheduler.running
        "timezone": "UTC",                # scheduler._timezone
        "job_count": 3                    # len(jobs)
    },
    "jobs": [ <serialized_job>, ... ]
}
```

### New Public Interfaces

```python
# querysource/handlers/scheduler.py
from aiohttp import web
from navigator.views import BaseView

class SchedulerJobsView(BaseView):
    """Class-based aiohttp view exposing the QSScheduler jobstore.

    v1 implements GET only:
        GET  /api/v1/qs/scheduler/jobs            — list all jobs
        GET  /api/v1/qs/scheduler/jobs/{job_id}   — single job by ID

    Future verbs (POST/PUT/PATCH/DELETE) will be added as methods on this
    class in a follow-up spec — see Non-Goals.
    """

    async def get(self) -> web.Response: ...

    # Internal helpers (not part of the HTTP surface)
    def _get_scheduler(self) -> "QSScheduler | None": ...
    def _serialize_job(self, job: "apscheduler.job.Job") -> dict: ...
```

---

## 3. Module Breakdown

### Module 1: `SchedulerJobsView` handler

- **Path**: `querysource/handlers/scheduler.py` (new file)
- **Responsibility**:
  - Define `SchedulerJobsView(BaseView)` with `get()` only.
  - Resolve `app["qs_scheduler"]` and return **503** with a clear message
    when the scheduler is not present (defensive — should not happen
    because the route is only registered when the flag is on, but the
    scheduler may be mid-startup or mid-shutdown).
  - For the list endpoint: iterate `scheduler._scheduler.get_jobs()`,
    serialize via `_serialize_job`, return the envelope shown in §2.
  - For the single-job endpoint: read `job_id` via
    `self.match_parameters(self.request)`, call
    `scheduler._scheduler.get_job(job_id)`, return **404** when None.
  - Derive `kind` from the job ID prefix
    (`query_` → `"query"`, `multi_` → `"multi"`, `cache_` → `"cache"`,
    fallback `"unknown"`).
  - Serialize `next_run_time` as ISO 8601 (`None` when paused).
- **Depends on**: `navigator.views.BaseView`,
  `querysource.scheduler.QSScheduler` (read-only via `app["qs_scheduler"]`),
  `apscheduler.job.Job` (read-only).

### Module 2: Handler package re-export

- **Path**: `querysource/handlers/__init__.py` (modify)
- **Responsibility**: Add `from .scheduler import SchedulerJobsView` and
  include it in `__all__` (if `__all__` exists) so it is importable as
  `from querysource.handlers import SchedulerJobsView`.
- **Depends on**: Module 1.

### Module 3: Route registration in `QuerySource.setup`

- **Path**: `querysource/services.py` (modify)
- **Responsibility**: Inside the existing
  `if ENABLE_QS_SCHEDULER:` block (currently lines 317–320), and **after**
  `self._scheduler.setup(self.app)`, register two routes:
  - `self.app.router.add_view('/api/v1/qs/scheduler/jobs', SchedulerJobsView)`
  - `self.app.router.add_view('/api/v1/qs/scheduler/jobs/{job_id}', SchedulerJobsView)`

  Lazy-import `SchedulerJobsView` (`from .handlers.scheduler import SchedulerJobsView`)
  to keep the import cost off the default code path. Append both to the
  existing `routes` list for consistency with surrounding code.
- **Depends on**: Module 1, Module 2.

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_serialize_job_query_kind` | Module 1 | Job with id `"query_foo"` serializes with `kind="query"`, `slug="foo"`. |
| `test_serialize_job_multi_kind` | Module 1 | Job with id `"multi_foo"` serializes with `kind="multi"`. |
| `test_serialize_job_cache_kind` | Module 1 | Job with id `"cache_foo"` serializes with `kind="cache"`. |
| `test_serialize_job_unknown_kind` | Module 1 | Job with id `"weird_foo"` serializes with `kind="unknown"`. |
| `test_serialize_job_next_run_time_iso` | Module 1 | `next_run_time` is ISO 8601 string; `None` when job is paused. |
| `test_serialize_job_trigger_interval` | Module 1 | `IntervalTrigger` serializes with `type="interval"` and a non-empty `repr`. |
| `test_serialize_job_trigger_cron` | Module 1 | `CronTrigger` serializes with `type="cron"` and a non-empty `repr`. |

### Integration Tests

| Test | Description |
|---|---|
| `test_get_jobs_returns_503_when_scheduler_missing` | Hit endpoint on an app where `app["qs_scheduler"]` is absent — expect 503 with a clear `error` field. |
| `test_get_jobs_lists_registered_jobs` | Start an aiohttp test app with `ENABLE_QS_SCHEDULER=True` and a mocked DB returning two rows (one query, one multi); `GET /api/v1/qs/scheduler/jobs` returns 200 with `job_count=2` and both jobs present. |
| `test_get_single_job_by_id` | Same fixture; `GET /api/v1/qs/scheduler/jobs/query_foo` returns 200 with that one job's serialized payload. |
| `test_get_single_job_missing_returns_404` | `GET /api/v1/qs/scheduler/jobs/does_not_exist` returns 404. |
| `test_post_returns_405` | `POST /api/v1/qs/scheduler/jobs` returns 405 Method Not Allowed (verifying the class-based view leaves future verbs unimplemented but routable). |
| `test_route_not_registered_when_flag_off` | App built with `ENABLE_QS_SCHEDULER=False` does not register the route — `GET` returns 404 from the router itself. |

### Test Data / Fixtures

```python
@pytest.fixture
async def app_with_scheduler(monkeypatch):
    """Build an aiohttp app with QSScheduler enabled and a mocked DB pool."""
    monkeypatch.setenv("ENABLE_QS_SCHEDULER", "True")
    # build QuerySource, monkeypatch _db.acquire().query() to return:
    rows = [
        {
            "query_slug": "foo",
            "attributes": {"scheduler": {"schedule_type": "interval",
                                          "schedule": {"seconds": 30}}},
            "cache_options": {},
            "provider": "pg",
            "is_cached": False,
            "query_raw": "",
        },
        {
            "query_slug": "bar",
            "attributes": {"scheduler": {"schedule_type": "cron",
                                          "schedule": {"hour": "*/2"}}},
            "cache_options": {},
            "provider": "multi",
            "is_cached": False,
            "query_raw": '{"queries": []}',
        },
    ]
    ...
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] When `ENABLE_QS_SCHEDULER=True`, `GET /api/v1/qs/scheduler/jobs`
      returns **200** with the envelope from §2 (`scheduler` block + `jobs` array).
- [ ] When `ENABLE_QS_SCHEDULER=True`, `GET /api/v1/qs/scheduler/jobs/{job_id}`
      returns **200** with the single serialized job, or **404** if the job
      does not exist.
- [ ] Each serialized job exposes `id`, `name`, `kind`, `slug`,
      `next_run_time` (ISO 8601 or `null`), `trigger.type`, `trigger.repr`,
      `coalesce`, `max_instances`, `misfire_grace_time`, `pending`.
- [ ] `kind` is correctly derived from the job ID prefix
      (`query_` → `"query"`, `multi_` → `"multi"`, `cache_` → `"cache"`,
      otherwise `"unknown"`).
- [ ] When `ENABLE_QS_SCHEDULER=False`, the route is **not registered**;
      requests return 404 from the router (not from the handler).
- [ ] When the scheduler is enabled but `app["qs_scheduler"]` is missing
      (e.g. mid-startup), the handler returns **503** with a JSON body
      explaining the state.
- [ ] `POST/PUT/PATCH/DELETE` on the endpoint return **405** (aiohttp default
      for an unimplemented method on a class-based view), demonstrating that
      future verbs can be added on the same class.
- [ ] `SchedulerJobsView` is importable from `querysource.handlers`
      (re-exported via `querysource/handlers/__init__.py`).
- [ ] All unit tests pass (`pytest tests/unit/ -v`).
- [ ] All integration tests pass (`pytest tests/integration/ -v`).
- [ ] No breaking changes to the existing QuerySource HTTP surface.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Every entry below was verified at the cited line at spec time
> (`HEAD = 0249b60` on `dev`). Re-verify if implementation lags.

### Verified Imports

```python
from aiohttp import web
                                                # verified: aiohttp installed
from navigator.views import BaseView
                                                # verified: .venv/lib/python3.11/site-packages/navigator/views/base.py:619
from querysource.conf import ENABLE_QS_SCHEDULER
                                                # verified: querysource/conf.py:343
from querysource.scheduler import QSScheduler
                                                # verified: querysource/scheduler/__init__.py:2
from apscheduler.job import Job                 # verified: APScheduler is an existing dependency (querysource/scheduler/scheduler.py:21-25)
```

### Existing Class Signatures

```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def __init__(self, loop: asyncio.AbstractEventLoop = None):  # line 53
        self._scheduler: AsyncIOScheduler = None                 # line 57
        self._timezone = QS_SCHEDULER_TIMEZONE                   # line 56

    async def startup(self, app: web.Application) -> None:       # line 252
        ...
        app["qs_scheduler"] = self                               # line 317

    async def shutdown(self, app: web.Application) -> None:      # line 319
```

```python
# .venv/lib/python3.11/site-packages/navigator/views/base.py
class BaseView(aiohttp_cors.CorsViewMixin, BaseHandler, web.View):  # line 619
    def __init__(self, request, *args, **kwargs):                   # line 631

# BaseHandler (line 42 of same file) provides:
    def json_response(self, ...) -> web.Response:                   # line 144
    def match_parameters(self, request: web.Request = None) -> dict: # line 345
    def get_arguments(self, request: web.Request = None) -> dict:    # line 358
```

```python
# apscheduler.job.Job — attributes confirmed via runtime inspection
# (apscheduler==3.11.x per querysource/scheduler/scheduler.py imports):
#   id, name, next_run_time, trigger, args, kwargs,
#   coalesce, max_instances, misfire_grace_time, pending, executor
```

```python
# querysource/services.py
class QuerySource(metaclass=Singleton):
    def setup(self, app: web.Application) -> web.Application:   # line 98
        ...
        if ENABLE_QS_SCHEDULER:                                 # line 317
            from .scheduler import QSScheduler
            self._scheduler = QSScheduler()
            self._scheduler.setup(self.app)                     # line 320
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `SchedulerJobsView.get()` | `app["qs_scheduler"]` | dict lookup on `self.request.app` | `querysource/scheduler/scheduler.py:317` |
| `SchedulerJobsView.get()` | `QSScheduler._scheduler.get_jobs()` | direct attribute access | `querysource/scheduler/scheduler.py:315` (existing call site) |
| `QuerySource.setup()` | `SchedulerJobsView` | `app.router.add_view(path, cls)` | `querysource/services.py:277,279,282,284,290,294,295` (existing pattern for `DatasourceView`, `VariablesService`) |

### Does NOT Exist (Anti-Hallucination)

- ~~`QSScheduler.get_jobs()`~~ — there is no public method on `QSScheduler`
  exposing the jobs. The view MUST go through `scheduler._scheduler.get_jobs()`
  or — preferred for v2 — a small public method we add. For v1 we read
  `_scheduler` directly to avoid touching `QSScheduler`.
- ~~`QSScheduler.scheduler`~~ — the attribute is named `_scheduler`
  (leading underscore), not `scheduler`. See line 57.
- ~~`app["scheduler"]`~~ — the app key is `"qs_scheduler"`, not `"scheduler"`.
  See line 317.
- ~~`querysource.handlers.scheduler`~~ — does not exist yet; this spec
  creates it.
- ~~`Job.to_dict()`~~ — APScheduler `Job` has no `to_dict` /
  `json()` method. Serialization must be hand-rolled (`_serialize_job`).
- ~~`scheduler.pause_job()` / `scheduler.remove_job()` via HTTP~~ — these
  exist on APScheduler but are NOT exposed in v1; they belong to the
  follow-up POST/PUT/DELETE spec.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Class-based view pattern** — model the new handler on
  `DatasourceView` and `VariablesService`: subclass `BaseView`, define one
  async method per HTTP verb, register with `app.router.add_view(path, cls)`.
- **Conditional route registration** — keep the route registration inside
  the existing `if ENABLE_QS_SCHEDULER:` block in `services.py` so the
  endpoint is invisible when the scheduler is off.
- **`json_response`** — use `self.json_response(...)` from `BaseHandler`
  rather than building `web.Response` manually.
- **Lazy import in `services.py`** — match the existing pattern of
  `from .handlers.components import ComponentHandler` inside `setup()` to
  avoid circular import risk at module load time.
- **Reading `_scheduler` directly** is acceptable for v1; if the follow-up
  spec adds POST/PUT/DELETE, consider promoting `get_jobs()` to a public
  method on `QSScheduler` at that point.

### Known Risks / Gotchas

- **Per-process visibility only.** `MemoryJobStore` lives in the running
  process — multiple QS instances each report their own jobs. Document this
  in the endpoint docstring and (eventually) in the API docs.
- **`next_run_time` may be `None`** when a job is paused or pending. ISO
  serializer must guard against `None` (return JSON `null`).
- **Job kwargs leakage.** `job.kwargs` contains the live
  `NotificationManager` reference. Do NOT include `kwargs` in the serialized
  output verbatim — only extract `slug` explicitly.
- **Startup race.** Between `QuerySource.setup()` finishing and
  `QSScheduler.startup` running on `on_startup`, the route exists but
  `app["qs_scheduler"]` is not yet set. The 503 branch handles this.
- **Method-not-implemented behavior.** aiohttp's `web.View` returns a 405
  automatically for verbs not defined as methods. Verify this in the
  `test_post_returns_405` integration test — if a future framework upgrade
  changes the default, the contract must update.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `APScheduler` | `>=3.11.0,<3.12.0` | Already a dependency (FEAT-008) — read-only access to `Job` objects. |
| `aiohttp` | existing | Already a dependency — `web.View` and route registration. |
| `navigator` (navigator-api) | existing | Already a dependency — `BaseView`. |

No new dependencies.

---

## 8. Open Questions

> Resolved questions are checked off with the agreed answer; unresolved
> questions remain `[ ]` for the implementing agent or follow-up specs.

- [x] **PBAC enforcement** — *Resolved (conservative default for v1)*:
      No PBAC enforcement in v1. The endpoint is gated only by
      `ENABLE_QS_SCHEDULER` (same posture as `/api/v1/qs/audit_log`).
      Adding PBAC is deferred to the follow-up spec that introduces
      mutating verbs (POST/PUT/DELETE) — those are the verbs that warrant
      a policy.
- [x] **Filtering query params** — *Resolved (conservative default for v1)*:
      No `?kind` or `?slug` filters in v1. Clients can filter client-side
      because the expected job count is small. Filters can be added later
      without breaking the response shape.
- [x] **404 vs 503 when scheduler missing** — *Resolved*: Return **503**
      with a JSON body `{"error": "...", "scheduler": {"enabled": true,
      "running": false}}` when `ENABLE_QS_SCHEDULER=True` but
      `app["qs_scheduler"]` is missing (mid-startup race or partial
      failure). The route itself is not registered when the flag is off,
      so an inactive scheduler never produces a 200 with an empty list.
- [x] **Promote `get_jobs()` to public QSScheduler API** — *Resolved
      (defer)*: The handler reads `scheduler._scheduler.get_jobs()` and
      `scheduler._scheduler.get_job(job_id)` directly in v1. Promotion to
      a public API on `QSScheduler` is reconsidered when the follow-up
      mutating-verbs spec lands — that one will need `add_job` /
      `remove_job` / `pause_job` wrappers anyway, and the read methods
      should be promoted alongside them for symmetry.

---

## Worktree Strategy

- **Default isolation**: `per-spec` — all tasks run sequentially in one
  worktree.
- **Rationale**: Three small modules with a strict dependency chain
  (handler → __init__ re-export → services.py route). No parallel work
  is meaningful at this size; coordination overhead would dominate.
- **Cross-feature dependencies**: Depends on FEAT-008 (QSScheduler) being
  already merged on `dev` — verified (`querysource/scheduler/` exists).
  No blocker.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-23 | Jesus Lara / Claude | Initial draft. No brainstorm — scoped directly from user input + codebase research. |
