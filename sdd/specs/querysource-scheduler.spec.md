# Feature Specification: QuerySource Scheduler (QSScheduler)

**Feature ID**: FEAT-008
**Date**: 2026-03-25
**Author**: Jesus Lara / Claude
**Status**: approved
**Target version**: 5.x

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource has no built-in mechanism to execute queries on a scheduled basis.
Users must rely on external cron jobs or orchestrators to trigger periodic query
execution and cache warming. This creates two pain points:

1. **Scheduled query execution**: Queries that need to run at fixed intervals
   (ETL refreshes, periodic reports) require external tooling outside QuerySource.

2. **Stale cache**: Queries with `is_cached=True` only refresh when a user
   requests them. Infrequently accessed queries suffer stale caches and slow
   first-hit latency.

### Goals
- Provide an embedded APScheduler-based scheduler (`QSScheduler`) that reads
  schedule definitions from `public.queries` and registers jobs on startup.
- Support two job types: **scheduled query execution** (from `attributes.scheduler`)
  and **cache refresh** (from `cache_options`).
- Gated behind `ENABLE_QS_SCHEDULER` config flag (default `False`).
- Pluggable notification callback system (logging-only for v1).

### Non-Goals (explicitly out of scope)
- Runtime add/remove/reschedule API (v2 consideration).
- Distributed scheduler coordination across multiple QS instances.
- Persistent jobstore (Redis/SQL) — jobs rebuild from DB on each startup.
- Minimal/optimized cache-only execution path (v2 — currently uses full `QS.query()` pipeline).
- DWH-specific scheduling via `dwh_scheduler` column (separate future feature).

---

## 2. Architectural Design

### Overview

QSScheduler is an embedded `AsyncIOScheduler` (APScheduler) that lives inside the
QuerySource process. On startup, it creates its own PostgreSQL connection pool, reads
`public.queries` for rows with schedule definitions, parses triggers (cron, crontab,
interval), and registers async jobs. The scheduler shares the aiohttp event loop.

This follows the proven NavScheduler pattern from FlowTask
(`flowtask/scheduler/scheduler.py`), adapted for QuerySource's simpler requirements
(no Redis jobstore, no runtime API, no Telegram notifications in v1).

### Component Diagram
```
QuerySource.setup()
       │
       ├── registers on_startup ──→ QSScheduler.startup(app)
       │                                │
       │                                ├── Creates own AsyncDB PostgreSQL pool
       │                                ├── SELECT queries with schedules
       │                                ├── Parse triggers (cron/crontab/interval)
       │                                ├── Register ScheduledQueryJob(s)
       │                                ├── Register CacheRefreshJob(s)
       │                                └── scheduler.start()
       │
       └── registers on_shutdown ──→ QSScheduler.shutdown(app)
                                        ├── scheduler.shutdown(wait=True)
                                        └── Close PostgreSQL pool

AsyncIOScheduler (MemoryJobStore)
       │
       ├── ScheduledQueryJob ──→ QS(slug=X).query() ──→ discard result
       │
       └── CacheRefreshJob ──→ QS(slug=X).query() ──→ save_cache (if is_cached=True)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/services.py:QuerySource` | modifies | Conditionally creates QSScheduler in `setup()`, registers startup/shutdown |
| `querysource/conf.py` | extends | Add `ENABLE_QS_SCHEDULER` flag |
| `querysource/queries/qs.py:QS` | depends on (no changes) | Jobs call `QS(slug=...).query()` for execution and caching |
| `querysource/models.py:QueryModel` | depends on (no changes) | Uses `attributes`, `cache_options`, `is_cached` columns |
| `querysource/connections.py:QueryConnection` | reference only | QSScheduler creates its own pool, does NOT share QueryConnection's pool |
| `asyncdb.AsyncDB` | uses | For the scheduler's dedicated PostgreSQL pool |
| `APScheduler` | uses | `AsyncIOScheduler`, triggers, `MemoryJobStore`, `AsyncIOExecutor` |

### Data Models

**Schedule definition in `attributes` column** (jsonb):
```python
# attributes = {"scheduler": {"schedule_type": "cron", "schedule": {...}}}
# Parsed into:
ScheduleConfig = {
    "schedule_type": str,  # "cron" | "crontab" | "interval"
    "schedule": dict       # Trigger-specific kwargs
}
```

**Schedule definition in `cache_options` column** (jsonb):
```python
# cache_options = {"schedule_type": "interval", "schedule": {"minutes": 30}}
CacheScheduleConfig = {
    "schedule_type": str,  # "cron" | "crontab" | "interval"
    "schedule": dict       # Trigger-specific kwargs
}
```

**Trigger parsing rules** (ported from NavScheduler):

| `schedule_type` | APScheduler Trigger | `schedule` example |
|---|---|---|
| `"interval"` | `IntervalTrigger(**schedule)` | `{"minutes": 30}` |
| `"crontab"` | `CronTrigger.from_crontab(schedule["crontab"], timezone=...)` | `{"crontab": "*/5 * * * *", "timezone": "UTC"}` |
| `"cron"` | `CronTrigger(**schedule)` | `{"hour": "*/2", "minute": 27, "timezone": "America/New_York"}` |

### New Public Interfaces

```python
class QSScheduler:
    """Embedded APScheduler for QuerySource.

    Creates scheduled jobs from public.queries definitions.
    Gated behind ENABLE_QS_SCHEDULER config flag.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop = None):
        ...

    def setup(self, app: web.Application) -> None:
        """Register startup/shutdown hooks on the aiohttp app."""
        ...

    async def startup(self, app: web.Application) -> None:
        """Initialize DB pool, load jobs from public.queries, start scheduler."""
        ...

    async def shutdown(self, app: web.Application) -> None:
        """Gracefully stop scheduler and close DB pool."""
        ...

    def add_notification_callback(self, callback: Callable) -> None:
        """Register a callback invoked on job errors.
        Signature: callback(job_id: str, slug: str, error: Exception) -> None
        """
        ...
```

---

## 3. Module Breakdown

### Module 1: Configuration
- **Path**: `querysource/conf.py` (modify existing)
- **Responsibility**: Add `ENABLE_QS_SCHEDULER` boolean flag (default `False`).
  Add `QS_SCHEDULER_TIMEZONE` default timezone for scheduler (fallback to existing `TIMEZONE` or `'UTC'`).
- **Depends on**: nothing

### Module 2: QSScheduler Core
- **Path**: `querysource/scheduler/__init__.py`, `querysource/scheduler/scheduler.py`
- **Responsibility**: The `QSScheduler` class. Creates `AsyncIOScheduler` with
  `MemoryJobStore`, `AsyncIOExecutor`, `coalesce=True`, `max_instances=1`.
  Manages its own PostgreSQL pool via `asyncdb.AsyncDB`. On startup, queries
  `public.queries` for schedulable rows, parses trigger definitions, registers jobs.
  Provides `setup()`, `startup()`, `shutdown()` lifecycle methods and the
  notification callback registry.
- **Depends on**: Module 1 (conf), Module 3 (jobs), Module 4 (notifications)

### Module 3: Job Definitions
- **Path**: `querysource/scheduler/jobs.py`
- **Responsibility**: Two async callable job classes/functions:
  - `scheduled_query_job(slug: str, **kwargs)` — instantiates `QS(slug=slug)`,
    calls `.query()`, discards result.
  - `cache_refresh_job(slug: str, **kwargs)` — same pipeline, relies on
    `is_cached=True` for `save_cache` to fire internally.
  Both catch exceptions and invoke notification callbacks.
- **Depends on**: `querysource/queries/qs.py` (no changes to QS)

### Module 4: Notification Callbacks
- **Path**: `querysource/scheduler/notifications.py`
- **Responsibility**: Callback registry and the default `logging_callback`.
  Provides `NotificationManager` with `add_callback()` and `notify()` methods.
  v1 ships with a single `logging_callback(job_id, slug, error)` that logs
  at WARNING level.
- **Depends on**: nothing (uses standard logging)

### Module 5: QuerySource Integration
- **Path**: `querysource/services.py` (modify existing)
- **Responsibility**: In `QuerySource.setup()`, conditionally import and create
  `QSScheduler` when `ENABLE_QS_SCHEDULER is True`. Register
  `scheduler.startup` on `app.on_startup` and `scheduler.shutdown` on
  `app.on_shutdown`. Store scheduler reference in `app["qs_scheduler"]`.
- **Depends on**: Module 1, Module 2

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_parse_cron_trigger` | Module 2 | Parses `{"schedule_type": "cron", "schedule": {"hour": "*/2"}}` into `CronTrigger` |
| `test_parse_crontab_trigger` | Module 2 | Parses `{"schedule_type": "crontab", "schedule": {"crontab": "*/5 * * * *"}}` into `CronTrigger` |
| `test_parse_interval_trigger` | Module 2 | Parses `{"schedule_type": "interval", "schedule": {"minutes": 30}}` into `IntervalTrigger` |
| `test_parse_invalid_schedule` | Module 2 | Invalid/missing schedule_type logs error and skips job |
| `test_cache_refresh_skipped_when_not_cached` | Module 2 | Row with `cache_options` but `is_cached=False` does NOT register a job |
| `test_cache_refresh_registered_when_cached` | Module 2 | Row with `cache_options` and `is_cached=True` registers a CacheRefreshJob |
| `test_notification_callback_called_on_error` | Module 4 | Job error triggers all registered callbacks |
| `test_logging_callback` | Module 4 | Default logging callback logs at WARNING level |
| `test_scheduler_disabled_by_default` | Module 1 | `ENABLE_QS_SCHEDULER` defaults to `False` |

### Integration Tests
| Test | Description |
|---|---|
| `test_scheduler_startup_shutdown` | QSScheduler starts with mock DB rows, registers jobs, shuts down cleanly |
| `test_scheduled_query_executes` | End-to-end: schedule fires, `QS.query()` is called with correct slug |
| `test_cache_refresh_executes` | Schedule fires, query result is cached via `save_cache` |

### Test Data / Fixtures
```python
@pytest.fixture
def sample_query_with_schedule():
    return {
        "query_slug": "test_scheduled_query",
        "attributes": {
            "scheduler": {
                "schedule_type": "interval",
                "schedule": {"seconds": 5}
            }
        },
        "cache_options": {},
        "is_cached": False,
    }

@pytest.fixture
def sample_query_with_cache_refresh():
    return {
        "query_slug": "test_cache_query",
        "attributes": {},
        "cache_options": {
            "schedule_type": "interval",
            "schedule": {"seconds": 5}
        },
        "is_cached": True,
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `QSScheduler` starts and shuts down cleanly within QuerySource lifecycle
- [ ] Queries with `attributes.scheduler` are registered as scheduled jobs on startup
- [ ] Queries with `cache_options` schedule AND `is_cached=True` are registered as cache refresh jobs
- [ ] Queries with `cache_options` but `is_cached=False` are skipped (no cache refresh job)
- [ ] Schedule types `cron`, `crontab`, and `interval` are parsed correctly
- [ ] Invalid schedule definitions are logged and skipped (do not prevent other jobs from loading)
- [ ] Jobs use `coalesce=True` and `max_instances=1`
- [ ] QSScheduler uses its own PostgreSQL pool (not QuerySource's)
- [ ] `ENABLE_QS_SCHEDULER=False` (default) means zero scheduler overhead
- [ ] Notification callback system works; logging callback logs job errors at WARNING level
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] No breaking changes to existing QuerySource API or behavior

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Port schedule parsing logic from `flowtask/scheduler/scheduler.py` (lines 531-574:
  `cron`, `crontab`, `interval` trigger creation from job schedule dicts)
- Use `AsyncIOScheduler` (not `BackgroundScheduler`) to share the aiohttp event loop
- Use `asyncdb.AsyncDB("pg", dsn=...)` for the scheduler's own PostgreSQL pool
  (same pattern as NavScheduler's `update_task_status` method)
- Follow QuerySource's existing logging pattern via `navconfig.logging`
- Configuration via `navconfig.config` with `.getboolean()` / `.get()` fallbacks

### Known Risks / Gotchas
- **Single-instance assumption**: If multiple QS instances run with `ENABLE_QS_SCHEDULER=True`,
  the same jobs will fire from each instance. Mitigation: document that only one instance
  should enable the scheduler, or add distributed locking in v2.
- **Full QS pipeline overhead**: Cache refresh jobs run the full `QS.query()` pipeline
  including all validations. Mitigation: acceptable for v1; v2 can add a minimal path.
- **Startup ordering**: QSScheduler must start AFTER `QueryConnection` has initialized
  (so that `QS(slug=...).query()` can work when jobs fire). Mitigation: register
  scheduler startup hook after connection startup hook in `setup()`.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `APScheduler` | `>=3.11.0,<3.12.0` | Already in pyproject.toml — scheduler engine |
| `asyncdb` | existing | Already a dependency — for scheduler's own PostgreSQL pool |

---

## 7. Open Questions

- [ ] Should `dwh_scheduler` column be used for DWH-specific scheduled jobs in a future feature, or is `attributes.scheduler` the universal mechanism? — *Owner: Jesus Lara*: like cache_options, dwh_scheduler will be used in a future feature for moving data into DWH.
- [ ] For v2: should cache refresh jobs support a minimal "execute + cache only" path that bypasses QS validation overhead? — *Owner: Jesus Lara*: yes
- [ ] Should there be a global maximum number of concurrent scheduled jobs, or is per-job `max_instances=1` sufficient? — *Owner: Jesus Lara*: max_instances=1 is sufficient.

---

## Worktree Strategy

- **Default isolation**: `per-spec` — all tasks run sequentially in one worktree.
- **Rationale**: The feature has a clear dependency chain (conf → core scheduler → jobs
  → notifications → integration). Modules are small and tightly coupled. Sequential
  execution in one worktree is more efficient than coordinating parallel worktrees.
- **Cross-feature dependencies**: None. This is a new package (`querysource/scheduler/`)
  with minimal touchpoints in existing code (`conf.py` and `services.py` only).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-25 | Jesus Lara / Claude | Initial draft from brainstorm |
