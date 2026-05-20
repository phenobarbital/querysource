# TASK-646: Exclude `provider='multi'` rows from `_load_cache_refresh_jobs`

**Feature**: FEAT-092 — QSScheduler Multi-Query Support
**Spec**: `sdd/specs/qsscheduler-multi-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-645
**Assigned-to**: unassigned

---

## Context

A multi-query slug has no single cache checksum — its sub-queries each
manage their own caches inside the MultiQS pipeline. Registering a
`cache_<slug>` job for a `provider='multi'` row would call
`QS(slug=...).query()` on a multi-query JSON payload, which is exactly
the broken behavior FEAT-092 is fixing for scheduled jobs.

This task adds a one-line guard at the top of
`_load_cache_refresh_jobs` (`querysource/scheduler/scheduler.py:131`) to
skip such rows before any other check.

Implements **Module 3** from the spec
(`sdd/specs/qsscheduler-multi-support.spec.md` §3).

Depends on TASK-645 because both tasks modify
`querysource/scheduler/scheduler.py`; landing them in sequence avoids
merge friction.

---

## Scope

- In `querysource/scheduler/scheduler.py`, inside the for-row loop of
  `_load_cache_refresh_jobs` (starting at line 141), insert
  `if row.get('provider') == 'multi': continue` BEFORE the existing
  `is_cached = row.get("is_cached", False)` check at line 143.
- Add two unit tests in `tests/test_scheduler_core.py`:
  - `provider='multi'` row with `is_cached=True` and `cache_options=
    {"schedule_type": ..., "schedule": ...}` produces NO `cache_<slug>`
    job.
  - `provider='db'` row with the same cache settings still produces a
    `cache_<slug>` job (regression).

**NOT in scope**:
- Routing in `_load_scheduled_queries` → TASK-645.
- The `scheduled_multiqs_job` callable itself → TASK-644.
- Smoke/integration test fixture → TASK-647.
- Documentation outside `scheduler.py` → TASK-648.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | One-line guard at the top of the `_load_cache_refresh_jobs` for-row loop. |
| `tests/test_scheduler_core.py` | MODIFY | Add 2 unit tests for the guard. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

No new imports. The guard uses only `dict.get`.

### Existing Signatures to Use

```python
# querysource/scheduler/scheduler.py:131
def _load_cache_refresh_jobs(self, rows: list) -> int:
    """Register CacheRefreshJob for rows with cache_options schedule
    and is_cached=True.
    """
    count = 0
    for row in rows:                                    # line 141
        slug = row["query_slug"]                        # line 142
        is_cached = row.get("is_cached", False)         # line 143  ← guard goes ABOVE this
        if not is_cached:
            continue
        ...
```

### Does NOT Exist

- ~~`_load_cache_refresh_jobs(self, rows, skip_multi=True)` overload~~ —
  no parameter exists; the guard is unconditional.
- ~~A "multi cache refresh" job variant~~ — rejected in spec §1 Non-Goals
  ("No cache-refresh variant for multi-queries").
- ~~Reusing `_load_cache_refresh_jobs` with a different job callable for
  multi rows~~ — also rejected.

---

## Implementation Notes

### Pattern to Follow

```python
def _load_cache_refresh_jobs(self, rows: list) -> int:
    count = 0
    for row in rows:
        if row.get("provider") == "multi":   # NEW — single line
            continue
        slug = row["query_slug"]
        is_cached = row.get("is_cached", False)
        if not is_cached:
            continue
        # ... rest of the loop body unchanged ...
    return count
```

### Key Constraints

- Use `row.get("provider")`, not `row["provider"]` — legacy rows may
  carry NULL.
- Insert the guard BEFORE `slug = row["query_slug"]` so we don't even
  touch the slug lookup for skipped rows.
- Do NOT log anything at this point — multi rows are an expected case,
  not an error.

### References in Codebase

- `querysource/scheduler/scheduler.py:131-168` — current method body.

---

## Acceptance Criteria

- [ ] `_load_cache_refresh_jobs` first checks
      `row.get("provider") == "multi"` and skips the row immediately
      with `continue`.
- [ ] No other change to the function body.
- [ ] Unit test `test_load_cache_refresh_jobs_skips_multi_rows` passes.
- [ ] Unit test `test_load_cache_refresh_jobs_unchanged_for_single_query`
      passes (regression).
- [ ] Existing scheduler tests still pass:
      `pytest tests/test_scheduler_core.py
      tests/test_scheduler_jobs.py
      tests/test_scheduler_notifications.py -v`.
- [ ] No linting errors: `ruff check querysource/scheduler/scheduler.py`.

---

## Test Specification

```python
# tests/test_scheduler_core.py — additions
from unittest.mock import MagicMock

from querysource.scheduler.scheduler import QSScheduler


def _cache_row(provider="db"):
    return {
        "query_slug": "cached_slug",
        "attributes": {},
        "cache_options": {
            "schedule_type": "interval",
            "schedule": {"minutes": 30},
        },
        "provider": provider,
        "is_cached": True,
    }


class TestLoadCacheRefreshJobsMultiSkip:

    def test_skips_multi_rows(self):
        sched = QSScheduler()
        sched._scheduler = MagicMock()
        count = sched._load_cache_refresh_jobs([_cache_row(provider="multi")])
        sched._scheduler.add_job.assert_not_called()
        assert count == 0

    def test_unchanged_for_single_query(self):
        from querysource.scheduler.jobs import cache_refresh_job
        sched = QSScheduler()
        sched._scheduler = MagicMock()
        count = sched._load_cache_refresh_jobs([_cache_row(provider="db")])
        args, kwargs = sched._scheduler.add_job.call_args
        assert args[0] is cache_refresh_job
        assert kwargs["id"] == "cache_cached_slug"
        assert count == 1
```

---

## Agent Instructions

1. **Read the spec** §2, §3 (Module 3), and §6 (Codebase Contract).
2. **Verify TASK-645 is completed** — its changes must be merged so the
   for-row loop you're modifying matches what this task assumes.
3. **Verify the Codebase Contract** — re-read
   `querysource/scheduler/scheduler.py:131-168` to confirm structure.
4. **Update task status** in
   `sdd/tasks/index/qsscheduler-multi-support.json` → `"in_progress"`.
5. **Implement** the guard.
6. **Run tests**: `pytest tests/test_scheduler_core.py -v`.
7. **Move this file** to
   `sdd/tasks/completed/TASK-646-cache-refresh-multi-skip-guard.md`.
8. **Update index** → `"completed"`.
9. **Fill in Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
