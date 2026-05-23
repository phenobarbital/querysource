# TASK-692: Wire up scheduler routes + integration tests

**Feature**: FEAT-100 — QSScheduler API Handler — Jobstore Introspection
**Spec**: `sdd/specs/qsscheduler-api-handler.spec.md`
**Status**: pending
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

## Codebase Contract (Anti-Hallucination)

> Verified against `dev @ 5896cd0` (after FEAT-100 spec commit).

### Verified Imports

```python
# In querysource/services.py — add inside the existing
# `if ENABLE_QS_SCHEDULER:` block as a lazy import (mirrors line 217):
from .handlers.scheduler import SchedulerJobsView
# Created by TASK-691.

# In tests:
from aiohttp import web                                              # standard
import pytest                                                        # standard
# The scheduler module under test:
from querysource.handlers.scheduler import SchedulerJobsView         # from TASK-691
```

### Existing Signatures to Use

```python
# querysource/services.py
class QuerySource(metaclass=Singleton):
    def setup(self, app: web.Application) -> web.Application:    # line 98
        ...
        if ENABLE_QS_SCHEDULER:                                  # line 317
            from .scheduler import QSScheduler                   # line 318 (existing lazy import)
            self._scheduler = QSScheduler()                      # line 319
            self._scheduler.setup(self.app)                      # line 320
            # ↑ TASK-692 adds the two add_view() calls here, appending to `routes`.
```

The `routes = []` list is initialized at services.py:136 and is used
throughout `setup()` — every `add_view` / `add_get` / `add_post` call's
return value is appended to it. Match this pattern.

```python
# querysource/handlers/__init__.py
# Current file lists re-exports as plain `from .module import Name`.
# Inspect the file before editing — match its style. The task should:
#   from .scheduler import SchedulerJobsView
# (and, if __all__ is present, append "SchedulerJobsView").
```

### Existing route-registration patterns to mirror

```python
# querysource/services.py:277-285  (DatasourceView)
r = self.app.router.add_view('/api/v1/datasources', DatasourceView)
routes.append(r)
r = self.app.router.add_view('/api/v1/datasources/{filter}', DatasourceView)
routes.append(r)

# services.py:290-297  (VariablesService)
self.app.router.add_view("/api/v2/qs/variables", VariablesService)
self.app.router.add_view("/api/v2/qs/variables/{program}", VariablesService)
```

### Test pattern (existing convention)

```bash
grep -l "AioHTTPTestCase\|aiohttp_client\|test_client" tests/ -r
```

Run this first to discover which pattern the existing suite uses for HTTP
tests. The Airtable handler tests are a likely reference:
`tests/test_airtable_oauth_handlers.py` (check before assuming).

If no precedent exists, prefer `aiohttp.test_utils.TestServer` +
`TestClient` with `pytest-asyncio`'s `@pytest.mark.asyncio` — these are
already in the dependency tree (aiohttp ships them; `pytest-asyncio` is a
test-time dep).

### Stubbing the scheduler in tests

The tests should NOT boot a real PostgreSQL pool. Instead, construct a
real `AsyncIOScheduler` (lightweight — in-memory only), add a couple of
jobs to it, wrap it in a stub object exposing `_scheduler`, `_timezone`,
and assign that stub to `app["qs_scheduler"]` directly:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.interval import IntervalTrigger

class StubQSScheduler:
    def __init__(self, jobs):
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": AsyncIOExecutor()},
            timezone="UTC",
        )
        self._timezone = "UTC"
        for job_id, kwargs in jobs:
            self._scheduler.add_job(
                lambda: None,
                trigger=IntervalTrigger(seconds=30),
                id=job_id,
                name=f"Scheduled job: {job_id}",
                kwargs=kwargs,
            )
        # Do NOT call .start() — running scheduler is not required to
        # exercise get_jobs(); avoiding start() keeps tests sync-safe.

async def make_app_with_scheduler(jobs):
    app = web.Application()
    app["qs_scheduler"] = StubQSScheduler(jobs)
    app.router.add_view(
        "/api/v1/qs/scheduler/jobs", SchedulerJobsView)
    app.router.add_view(
        "/api/v1/qs/scheduler/jobs/{job_id}", SchedulerJobsView)
    return app
```

For the "flag off" test, build a `QuerySource()` instance with
`monkeypatch.setattr("querysource.services.ENABLE_QS_SCHEDULER", False)`
and confirm no route is mounted.

### Does NOT Exist

- ~~`QuerySource.register_scheduler_routes()`~~ — there is no such helper.
  Routes are registered inline in `setup()`.
- ~~`querysource.handlers.register_routes()`~~ — does not exist; the
  package only re-exports view classes.
- ~~`app["scheduler"]`~~ — the key is `"qs_scheduler"` (see
  `querysource/scheduler/scheduler.py:317`).
- ~~`AsyncIOScheduler.start()` in tests~~ — calling `.start()` schedules
  job execution on the event loop and complicates teardown. The test fixture
  must NOT call `.start()` — `get_jobs()` works on an unstarted scheduler.

---

## Implementation Notes

### Order of operations in `services.py`

The new view-registration block must go **inside** the existing
`if ENABLE_QS_SCHEDULER:` block and **after**
`self._scheduler.setup(self.app)` because:
- `setup(self.app)` registers the scheduler's startup/shutdown hooks, which
  populate `app["qs_scheduler"]` at startup time. The route MUST also be
  registered inside the same flag block (so it does not exist when the
  scheduler is off).
- The route registration order in aiohttp does not affect the runtime
  behavior of `app["qs_scheduler"]` lookups — only the `if` gate matters.

### `__init__.py` style

Inspect `querysource/handlers/__init__.py` before editing. If the file
uses explicit re-exports (e.g. `from .manager import QueryManager`), match
that. Do NOT introduce a new convention. Wildcard imports are forbidden by
the project's lint rules.

### Test naming

Match the existing test naming convention. If existing tests use
`test_<feature>_<behavior>.py`, follow that; if they use
`test_<module>.py`, follow that.

### Verifying the 405 default

Aiohttp's `web.View` automatically responds with **405 Method Not Allowed**
when a request arrives for an HTTP verb the subclass does not define. This
is the load-bearing contract proving the class-based view is ready for
follow-up verbs. The test must assert the status code is exactly 405 (NOT
404 — a 404 would indicate the route isn't registered for POST).

### Key Constraints

- Lazy-import `SchedulerJobsView` inside the `if ENABLE_QS_SCHEDULER:`
  block — do not introduce a top-level import in `services.py`.
- Do not call `AsyncIOScheduler.start()` in tests.
- Do not touch `querysource/scheduler/`.
- Test fixtures must NOT require PostgreSQL or any other external resource.

### References in Codebase

- `querysource/services.py:277-285` — `DatasourceView` registration
  pattern.
- `querysource/services.py:217-228` — lazy-import pattern for handler
  classes inside `setup()`.
- `tests/test_scheduler_*` — existing scheduler tests; reuse fixtures
  if applicable.

---

## Acceptance Criteria

- [ ] `querysource/handlers/__init__.py` re-exports `SchedulerJobsView`.
- [ ] `from querysource.handlers import SchedulerJobsView` works.
- [ ] `querysource/services.py` registers both routes inside the existing
      `if ENABLE_QS_SCHEDULER:` block.
- [ ] When `ENABLE_QS_SCHEDULER=False`, neither route is registered (the
      `routes` list does not grow).
- [ ] All 6 integration tests in
      `tests/test_scheduler_handler_integration.py` pass:
      `source .venv/bin/activate && pytest tests/test_scheduler_handler_integration.py -v`.
- [ ] The 405 test passes — POST returns 405 (not 404).
- [ ] The 503 test passes — missing `app["qs_scheduler"]` returns 503 with
      `scheduler.running == false`.
- [ ] Full scheduler test suite still passes (no regression):
      `source .venv/bin/activate && pytest tests/test_scheduler_*.py -v`.
- [ ] `ruff check querysource/services.py querysource/handlers/__init__.py
      tests/test_scheduler_handler_integration.py` passes.

---

## Test Specification

```python
# tests/test_scheduler_handler_integration.py
"""Integration tests for the QSScheduler jobs API (FEAT-100)."""
import pytest
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.interval import IntervalTrigger

from querysource.handlers.scheduler import SchedulerJobsView


class StubQSScheduler:
    def __init__(self, jobs):
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": AsyncIOExecutor()},
            timezone="UTC",
        )
        self._timezone = "UTC"
        for job_id, slug in jobs:
            self._scheduler.add_job(
                lambda: None,
                trigger=IntervalTrigger(seconds=30),
                id=job_id,
                name=f"Scheduled job: {job_id}",
                kwargs={"slug": slug},
            )


def _build_app(with_scheduler=True, jobs=None):
    app = web.Application()
    if with_scheduler:
        app["qs_scheduler"] = StubQSScheduler(jobs or [])
    app.router.add_view("/api/v1/qs/scheduler/jobs", SchedulerJobsView)
    app.router.add_view(
        "/api/v1/qs/scheduler/jobs/{job_id}", SchedulerJobsView)
    return app


@pytest.fixture
async def client_with_jobs(aiohttp_client):
    return await aiohttp_client(_build_app(
        with_scheduler=True,
        jobs=[("query_foo", "foo"), ("multi_bar", "bar")],
    ))


@pytest.fixture
async def client_no_scheduler(aiohttp_client):
    return await aiohttp_client(_build_app(with_scheduler=False))


class TestSchedulerJobsAPI:
    async def test_get_jobs_lists_registered_jobs(self, client_with_jobs):
        resp = await client_with_jobs.get("/api/v1/qs/scheduler/jobs")
        assert resp.status == 200
        body = await resp.json()
        assert body["scheduler"]["job_count"] == 2
        ids = {j["id"] for j in body["jobs"]}
        assert ids == {"query_foo", "multi_bar"}

    async def test_get_single_job_by_id(self, client_with_jobs):
        resp = await client_with_jobs.get(
            "/api/v1/qs/scheduler/jobs/query_foo")
        assert resp.status == 200
        body = await resp.json()
        assert body["id"] == "query_foo"
        assert body["kind"] == "query"
        assert body["slug"] == "foo"

    async def test_get_single_job_missing_returns_404(self, client_with_jobs):
        resp = await client_with_jobs.get(
            "/api/v1/qs/scheduler/jobs/does_not_exist")
        assert resp.status == 404

    async def test_get_jobs_returns_503_when_scheduler_missing(
        self, client_no_scheduler
    ):
        resp = await client_no_scheduler.get("/api/v1/qs/scheduler/jobs")
        assert resp.status == 503
        body = await resp.json()
        assert body["scheduler"]["running"] is False

    async def test_post_returns_405(self, client_with_jobs):
        resp = await client_with_jobs.post("/api/v1/qs/scheduler/jobs")
        assert resp.status == 405  # MUST be 405, not 404

    async def test_route_not_registered_when_flag_off(
        self, monkeypatch, aiohttp_client
    ):
        """Build a fresh QuerySource with the flag OFF and confirm
        neither scheduler route is mounted."""
        monkeypatch.setattr(
            "querysource.services.ENABLE_QS_SCHEDULER", False)
        from querysource.services import QuerySource

        # Reset the Singleton so a new instance is built honoring the
        # patched flag.
        QuerySource._instances = {}  # type: ignore[attr-defined]
        qs = QuerySource()
        app = web.Application()
        qs.setup(app)
        client = await aiohttp_client(app)

        resp = await client.get("/api/v1/qs/scheduler/jobs")
        assert resp.status == 404  # Router-level 404, not handler 503/404
```

The Singleton-reset trick may vary depending on how `datamodel.typedefs.Singleton`
stores instances. Inspect `Singleton`'s implementation if `_instances`
is not the right attribute, and adapt the reset accordingly.

---

## Agent Instructions

1. Confirm TASK-691 is in `sdd/tasks/completed/`. If not, stop and
   request that it be implemented first.
2. Read the spec `sdd/specs/qsscheduler-api-handler.spec.md` and TASK-691's
   completion note for any deviations.
3. Verify the Codebase Contract — `read` services.py:317-320 and
   `__init__.py` to confirm signatures and current style.
4. Modify `querysource/handlers/__init__.py` to re-export `SchedulerJobsView`.
5. Modify `querysource/services.py` to register the two routes inside the
   existing `if ENABLE_QS_SCHEDULER:` block.
6. Create `tests/test_scheduler_handler_integration.py` with the 6 tests
   above. Run them — they MUST pass.
7. Run the existing scheduler test suite to confirm no regression:
   `pytest tests/test_scheduler_*.py -v`.
8. Run `ruff check` on the modified files and the new test file.
9. Move this file to
   `sdd/tasks/completed/TASK-692-scheduler-route-registration-and-tests.md`.
10. Update `sdd/tasks/index/qsscheduler-api-handler.json` — set this
    task's `status` to `"done"` and fill in `completed_at`.
11. Fill in the Completion Note below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
