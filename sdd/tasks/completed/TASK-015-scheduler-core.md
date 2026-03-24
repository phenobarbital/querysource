# TASK-015: QSScheduler Core

**Feature**: QuerySource Scheduler (QSScheduler)
**Spec**: `sdd/specs/querysource-scheduler.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-014, TASK-016, TASK-017
**Assigned-to**: unassigned

---

## Context

> This is the central task for the feature. The QSScheduler class owns the APScheduler
> lifecycle, reads schedule definitions from `public.queries`, parses triggers, and
> registers jobs. It follows the NavScheduler pattern from FlowTask.
> Implements Spec Module 2 (QSScheduler Core).

---

## Scope

- Create `querysource/scheduler/__init__.py` — export `QSScheduler`
- Create `querysource/scheduler/scheduler.py` — the `QSScheduler` class with:
  - `__init__(loop)` — initialize logger, timezone, notification manager
  - `setup(app)` — register `startup` and `shutdown` on aiohttp signals
  - `startup(app)` — create own PostgreSQL pool via `asyncdb.AsyncDB`, query
    `public.queries` for schedulable rows, parse triggers, register jobs, start scheduler
  - `shutdown(app)` — gracefully stop scheduler, close DB pool
  - `_create_scheduler()` — factory for `AsyncIOScheduler` with `MemoryJobStore`,
    `AsyncIOExecutor`, `coalesce=True`, `max_instances=1`
  - `_parse_trigger(schedule_type, schedule)` — port from NavScheduler: handle
    `cron`, `crontab`, `interval` schedule types, return APScheduler trigger
  - `_load_scheduled_queries(rows)` — iterate rows, register ScheduledQueryJob for
    rows with `attributes->'scheduler'`
  - `_load_cache_refresh_jobs(rows)` — iterate rows, register CacheRefreshJob for
    rows with `cache_options` schedule AND `is_cached=True`
  - `add_notification_callback(callback)` — delegate to NotificationManager
- Write unit tests for trigger parsing (cron, crontab, interval, invalid)

**NOT in scope**: Job callable implementations (TASK-016), notification callbacks (TASK-017), wiring into services.py (TASK-018).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/__init__.py` | CREATE | Package init, export QSScheduler |
| `querysource/scheduler/scheduler.py` | CREATE | QSScheduler class |
| `tests/test_scheduler_core.py` | CREATE | Unit tests for scheduler + trigger parsing |

---

## Implementation Notes

### Pattern to Follow

Port schedule parsing from NavScheduler (`flowtask/scheduler/scheduler.py` lines 531-574):

```python
# Trigger parsing logic to port:
if schedule_type == "interval":
    trigger = IntervalTrigger(**schedule)
elif schedule_type == "crontab":
    crontab_expr = schedule["crontab"]
    tz = schedule.get("timezone", TIMEZONE)
    trigger = CronTrigger.from_crontab(crontab_expr, timezone=tz)
elif schedule_type == "cron":
    trigger = CronTrigger(**schedule)
```

Scheduler creation pattern:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore

scheduler = AsyncIOScheduler(
    jobstores={"default": MemoryJobStore()},
    executors={"default": AsyncIOExecutor()},
    job_defaults={"coalesce": QS_SCHEDULER_COALESCE, "max_instances": QS_SCHEDULER_MAX_INSTANCES},
    timezone=timezone,
)
```

DB query for loading schedulable rows:
```sql
SELECT query_slug, attributes, cache_options, is_cached
FROM public.queries
WHERE (attributes IS NOT NULL AND attributes != '{}')
   OR (cache_options IS NOT NULL AND cache_options != '{}')
```

Own PostgreSQL pool (same pattern as NavScheduler's `update_task_status`):
```python
from asyncdb import AsyncDB
conn = AsyncDB("pg", dsn=default_dsn, loop=loop)
```

### Key Constraints
- Use `AsyncIOScheduler` (shares aiohttp event loop)
- `MemoryJobStore` only — no Redis/SQL persistence
- Own DB pool via `asyncdb.AsyncDB` — do NOT reuse QueryConnection's pool
- Invalid schedule definitions must log error and skip (not crash startup)
- Import job functions from `querysource.scheduler.jobs` (TASK-016)
- Import NotificationManager from `querysource.scheduler.notifications` (TASK-017)
- Use `navconfig.logging` for logger

### References in Codebase
- `flowtask/scheduler/scheduler.py` — NavScheduler (primary reference for pattern)
- `querysource/conf.py` — config values (TASK-014)
- `querysource/connections.py` — reference for AsyncDB pool creation pattern
- `querysource/models.py` — QueryModel fields: `attributes` (line 54), `cache_options` (line 78), `is_cached` (line 73)

---

## Acceptance Criteria

- [ ] `QSScheduler` creates and starts an `AsyncIOScheduler` on startup
- [ ] Trigger parsing handles `cron`, `crontab`, and `interval` correctly
- [ ] Invalid schedule definitions are logged and skipped
- [ ] Queries with `attributes.scheduler` are registered as ScheduledQueryJobs
- [ ] Queries with `cache_options` schedule + `is_cached=True` are registered as CacheRefreshJobs
- [ ] Queries with `cache_options` but `is_cached=False` are skipped
- [ ] Scheduler shuts down cleanly on app shutdown
- [ ] Own PostgreSQL pool is created and closed independently
- [ ] All unit tests pass: `pytest tests/test_scheduler_core.py -v`

---

## Test Specification

```python
# tests/test_scheduler_core.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestTriggerParsing:
    def test_parse_cron_trigger(self):
        """Parses cron schedule into CronTrigger."""
        from querysource.scheduler.scheduler import QSScheduler
        scheduler = QSScheduler.__new__(QSScheduler)
        trigger = scheduler._parse_trigger("cron", {"hour": "*/2", "minute": 27})
        assert trigger is not None

    def test_parse_crontab_trigger(self):
        """Parses crontab expression into CronTrigger."""
        from querysource.scheduler.scheduler import QSScheduler
        scheduler = QSScheduler.__new__(QSScheduler)
        trigger = scheduler._parse_trigger("crontab", {"crontab": "*/5 * * * *"})
        assert trigger is not None

    def test_parse_interval_trigger(self):
        """Parses interval schedule into IntervalTrigger."""
        from querysource.scheduler.scheduler import QSScheduler
        scheduler = QSScheduler.__new__(QSScheduler)
        trigger = scheduler._parse_trigger("interval", {"minutes": 30})
        assert trigger is not None

    def test_parse_invalid_schedule_returns_none(self):
        """Invalid schedule_type returns None."""
        from querysource.scheduler.scheduler import QSScheduler
        scheduler = QSScheduler.__new__(QSScheduler)
        scheduler.logger = MagicMock()
        trigger = scheduler._parse_trigger("unknown", {})
        assert trigger is None


class TestCacheRefreshFiltering:
    def test_skip_when_not_cached(self):
        """Row with cache_options but is_cached=False should not register a job."""
        # Verify the filtering logic in _load_cache_refresh_jobs
        pass

    def test_register_when_cached(self):
        """Row with cache_options and is_cached=True registers a job."""
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/querysource-scheduler.spec.md` for full context
2. **Check dependencies** — TASK-014 (config), TASK-016 (jobs), TASK-017 (notifications) must be done
3. **Read NavScheduler** at `/home/jesuslara/proyectos/parallel/flowtask/flowtask/scheduler/scheduler.py` for the reference pattern
4. **Update status** in `sdd/tasks/.index.json` -> `"in-progress"`
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-015-scheduler-core.md`
8. **Update index** -> `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: claude-session-2026-03-25
**Date**: 2026-03-25
**Notes**: Implemented QSScheduler with full lifecycle (setup/startup/shutdown), trigger parsing (cron/crontab/interval), scheduled query and cache refresh job loading, own DB pool, and notification callback delegation. 13 unit tests pass covering trigger parsing, job registration, cache filtering, and initialization.

**Deviations from spec**: none
