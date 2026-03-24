# TASK-016: Job Definitions

**Feature**: QuerySource Scheduler (QSScheduler)
**Spec**: `sdd/specs/querysource-scheduler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-014
**Assigned-to**: unassigned

---

## Context

> This task implements the two async callable job types that APScheduler will execute.
> Both jobs use `QS(slug=...).query()` to run queries. The difference is semantic:
> ScheduledQueryJob runs queries on a schedule (result discarded), CacheRefreshJob
> warms the cache for queries with `is_cached=True`.
> Implements Spec Module 3 (Job Definitions).

---

## Scope

- Create `querysource/scheduler/jobs.py` with two async job functions:
  - `async def scheduled_query_job(slug: str, logger=None, notification_manager=None, **kwargs)`:
    Instantiates `QS(slug=slug)`, calls `await qs.query()`, discards result.
    On exception: logs error, calls `notification_manager.notify(...)` if provided.
  - `async def cache_refresh_job(slug: str, logger=None, notification_manager=None, **kwargs)`:
    Same as above — relies on the QS internal pipeline where `save_cache` fires
    when `is_cached=True`.
- Write unit tests mocking QS to verify job behavior and error handling.

**NOT in scope**: Scheduler core (TASK-015), notification implementation (TASK-017), config (TASK-014).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/jobs.py` | CREATE | Job callable definitions |
| `tests/test_scheduler_jobs.py` | CREATE | Unit tests for jobs |

---

## Implementation Notes

### Pattern to Follow
```python
# querysource/scheduler/jobs.py
from navconfig.logging import logging

logger = logging.getLogger("QSScheduler.Jobs")


async def scheduled_query_job(
    slug: str,
    notification_manager=None,
    **kwargs
) -> None:
    """Execute a scheduled query by slug. Result is discarded."""
    try:
        from querysource.queries.qs import QS
        qs = QS(slug=slug)
        await qs.query()
    except Exception as exc:
        logger.warning(f"Scheduled job failed for slug '{slug}': {exc}")
        if notification_manager:
            notification_manager.notify(
                job_id=f"query_{slug}",
                slug=slug,
                error=exc
            )


async def cache_refresh_job(
    slug: str,
    notification_manager=None,
    **kwargs
) -> None:
    """Execute a query to refresh its cache. Relies on QS internal caching."""
    try:
        from querysource.queries.qs import QS
        qs = QS(slug=slug)
        await qs.query()
    except Exception as exc:
        logger.warning(f"Cache refresh job failed for slug '{slug}': {exc}")
        if notification_manager:
            notification_manager.notify(
                job_id=f"cache_{slug}",
                slug=slug,
                error=exc
            )
```

### Key Constraints
- Import `QS` lazily inside the function body (avoid circular imports at module level)
- Both jobs must catch all exceptions — a failing job should NOT crash the scheduler
- Pass `notification_manager` as a keyword argument from the scheduler when registering
- Keep jobs as simple async functions (not classes) for APScheduler compatibility
- Use `navconfig.logging` logger

### References in Codebase
- `querysource/queries/qs.py` — `QS` class, `query()` method, `save_cache` flow
- `flowtask/scheduler/functions.py` — `TaskScheduler` callable pattern (reference)

---

## Acceptance Criteria

- [ ] `scheduled_query_job` calls `QS(slug=slug).query()` and discards result
- [ ] `cache_refresh_job` calls `QS(slug=slug).query()` (cache is saved internally by QS)
- [ ] Both jobs catch exceptions and call notification_manager.notify() on error
- [ ] Both jobs log errors at WARNING level
- [ ] Jobs are importable: `from querysource.scheduler.jobs import scheduled_query_job, cache_refresh_job`
- [ ] All unit tests pass: `pytest tests/test_scheduler_jobs.py -v`

---

## Test Specification

```python
# tests/test_scheduler_jobs.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
class TestScheduledQueryJob:
    @patch("querysource.scheduler.jobs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Job calls QS(slug).query() and discards result."""
        from querysource.scheduler.jobs import scheduled_query_job
        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance
        await scheduled_query_job(slug="test_slug")
        mock_qs_cls.assert_called_once_with(slug="test_slug")
        mock_instance.query.assert_awaited_once()

    @patch("querysource.scheduler.jobs.QS")
    async def test_handles_error(self, mock_qs_cls):
        """Job catches exception and notifies."""
        from querysource.scheduler.jobs import scheduled_query_job
        mock_instance = AsyncMock()
        mock_instance.query.side_effect = RuntimeError("DB error")
        mock_qs_cls.return_value = mock_instance
        notifier = MagicMock()
        await scheduled_query_job(slug="fail_slug", notification_manager=notifier)
        notifier.notify.assert_called_once()


@pytest.mark.asyncio
class TestCacheRefreshJob:
    @patch("querysource.scheduler.jobs.QS")
    async def test_executes_query(self, mock_qs_cls):
        """Cache refresh calls QS(slug).query()."""
        from querysource.scheduler.jobs import cache_refresh_job
        mock_instance = AsyncMock()
        mock_qs_cls.return_value = mock_instance
        await cache_refresh_job(slug="cached_slug")
        mock_qs_cls.assert_called_once_with(slug="cached_slug")
        mock_instance.query.assert_awaited_once()
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/querysource-scheduler.spec.md` for full context
2. **Check dependencies** — TASK-014 (config) must be done
3. **Read QS class** at `querysource/queries/qs.py` to understand the query/caching pipeline
4. **Update status** in `sdd/tasks/.index.json` -> `"in-progress"`
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-016-job-definitions.md`
8. **Update index** -> `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
