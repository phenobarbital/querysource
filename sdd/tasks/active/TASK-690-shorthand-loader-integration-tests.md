# TASK-690: Integration tests for shorthand schedules via QSScheduler loaders

**Feature**: FEAT-098 — QSScheduler Shorthand Variants (hourly / daily / weekly / monthly / biweekly)
**Spec**: `sdd/specs/qsscheduler-add-variants.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-689
**Assigned-to**: unassigned

---

## Context

TASK-689 adds the five shorthand `schedule_type` values to
`QSScheduler._parse_trigger`. Because both `_load_scheduled_queries`
(`querysource/scheduler/scheduler.py:105`) and `_load_cache_refresh_jobs`
(`querysource/scheduler/scheduler.py:196`) call `_parse_trigger` opaquely,
the loader-level behavior is implicitly extended — but only an integration
test confirms that a row containing, say,
`{"attributes": {"scheduler": {"schedule_type": "daily", "schedule": {...}}}}`
actually results in a registered APScheduler job at the expected
`query_<slug>` / `cache_<slug>` id.

This task covers the **Integration Tests** subset of Module 2 from the
spec (§4 Integration Tests, 4 test rows).

---

## Scope

- Add an integration test module
  `tests/scheduler/test_shorthand_loaders.py` that drives
  `_load_scheduled_queries` and `_load_cache_refresh_jobs` directly with
  in-memory row fixtures (no DB, no aiohttp app).
- The test harness must instantiate a `QSScheduler`, manually attach an
  `AsyncIOScheduler` (via `QSScheduler._create_scheduler`) to
  `scheduler._scheduler`, then call the loader methods with hand-crafted
  row lists.
- Cover the four integration tests listed in the spec §4 table:
    1. `test_load_scheduled_queries_with_hourly_shorthand` — row with
       `attributes.scheduler.schedule_type == "hourly"` registers a
       `query_<slug>` job.
    2. `test_load_cache_refresh_with_daily_shorthand` — row with
       `cache_options.schedule_type == "daily"` and `is_cached=True`
       registers a `cache_<slug>` job.
    3. `test_mixed_shorthand_and_legacy_load_together` — startup with two
       rows (one legacy `cron`, one new `biweekly`) registers two jobs.
    4. `test_invalid_shorthand_skips_job_but_loads_others` — three rows, one
       with `monthly` missing `day`; loader registers two jobs and skips one.
- Each test asserts:
    - The expected return count from the loader (number of jobs registered).
    - The expected job id(s) exist in `scheduler._scheduler.get_jobs()` by
      calling `scheduler._scheduler.get_job("<id>")`.

**NOT in scope**:
- The shorthand implementation itself (TASK-689).
- Tests at the `_parse_trigger` unit level (TASK-689).
- End-to-end tests that boot the aiohttp app or read from PostgreSQL.
- Changes to loader code in `scheduler.py`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/scheduler/test_shorthand_loaders.py` | CREATE | Four integration tests per spec §4 |
| `tests/scheduler/__init__.py` | DEPENDS | Already created by TASK-689; do not recreate |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.scheduler.scheduler import QSScheduler  # verified: querysource/scheduler/scheduler.py:46
```

### Existing Signatures to Use

```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def __init__(self, loop=None):  # line 53

    def _create_scheduler(self) -> AsyncIOScheduler:  # line 61
        # Returns an AsyncIOScheduler with MemoryJobStore + AsyncIOExecutor.
        # Safe to call without DB pool — test harness uses this to attach
        # an in-memory scheduler before calling the loaders.

    def _load_scheduled_queries(self, rows: list) -> int:  # line 105
        # Iterates rows; for each row with attributes.scheduler.{schedule_type,schedule},
        # parses the trigger and registers a job (query_<slug> or multi_<slug>).
        # Returns the count of jobs registered.

    def _load_cache_refresh_jobs(self, rows: list) -> int:  # line 196
        # Iterates rows; for each non-multi row with cache_options.{schedule_type,schedule}
        # AND is_cached=True, parses the trigger and registers cache_<slug>.
        # Returns the count of jobs registered.
```

### Row shape expected by the loaders

```python
# Minimum keys consumed by _load_scheduled_queries (per scheduler.py:120-194):
row = {
    "query_slug": str,        # required, used to build job id
    "attributes": dict | None,  # optional; .get("scheduler") is read
    "provider": str | None,    # "multi" routes to scheduled_multiqs_job
    "query_raw": str | None,   # only inspected when provider == "multi"
    "is_cached": bool,         # consumed by _load_cache_refresh_jobs
    "cache_options": dict | None,  # consumed by _load_cache_refresh_jobs
}
```

### Does NOT Exist

- ~~`QSScheduler.load_jobs(...)`~~ — no public single-entry loader; tests
  must call `_load_scheduled_queries` / `_load_cache_refresh_jobs` directly.
- ~~`QSScheduler.start()` synchronous wrapper~~ — only `startup(app)`
  exists and it's async; tests must NOT call it (it opens a DB pool).
- ~~A `fixtures` or `conftest` module under `tests/scheduler/`~~ — none
  yet exists. If you add one, keep its scope local to this directory.

---

## Implementation Notes

### Pattern to Follow

```python
import pytest

from querysource.scheduler.scheduler import QSScheduler


@pytest.fixture
def scheduler_with_inmemory_jobstore():
    s = QSScheduler(loop=None)
    s._scheduler = s._create_scheduler()  # attach AsyncIOScheduler; do NOT .start()
    return s


def test_load_scheduled_queries_with_hourly_shorthand(scheduler_with_inmemory_jobstore):
    rows = [
        {
            "query_slug": "test_hourly",
            "attributes": {
                "scheduler": {
                    "schedule_type": "hourly",
                    "schedule": {"minute": 15},
                }
            },
            "provider": None,
            "query_raw": None,
            "is_cached": False,
            "cache_options": None,
        }
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 1
    assert (
        scheduler_with_inmemory_jobstore._scheduler.get_job("query_test_hourly")
        is not None
    )
```

### Mixed-load test

```python
def test_mixed_shorthand_and_legacy_load_together(scheduler_with_inmemory_jobstore):
    rows = [
        {
            "query_slug": "legacy_cron",
            "attributes": {"scheduler": {
                "schedule_type": "cron",
                "schedule": {"hour": "*/2", "minute": 0},
            }},
            "provider": None, "query_raw": None,
            "is_cached": False, "cache_options": None,
        },
        {
            "query_slug": "new_biweekly",
            "attributes": {"scheduler": {
                "schedule_type": "biweekly",
                "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
            }},
            "provider": None, "query_raw": None,
            "is_cached": False, "cache_options": None,
        },
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 2
```

### Invalid-skip test

```python
def test_invalid_shorthand_skips_job_but_loads_others(scheduler_with_inmemory_jobstore):
    rows = [
        {  # valid
            "query_slug": "ok_daily",
            "attributes": {"scheduler": {
                "schedule_type": "daily",
                "schedule": {"hour": 0, "minute": 0},
            }},
            "provider": None, "query_raw": None,
            "is_cached": False, "cache_options": None,
        },
        {  # invalid — monthly missing 'day'
            "query_slug": "bad_monthly",
            "attributes": {"scheduler": {
                "schedule_type": "monthly",
                "schedule": {"hour": 0, "minute": 0},
            }},
            "provider": None, "query_raw": None,
            "is_cached": False, "cache_options": None,
        },
        {  # valid
            "query_slug": "ok_weekly",
            "attributes": {"scheduler": {
                "schedule_type": "weekly",
                "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
            }},
            "provider": None, "query_raw": None,
            "is_cached": False, "cache_options": None,
        },
    ]
    count = scheduler_with_inmemory_jobstore._load_scheduled_queries(rows)
    assert count == 2
    assert scheduler_with_inmemory_jobstore._scheduler.get_job("query_bad_monthly") is None
```

### Cache-refresh test

Mirror the same pattern but populate `cache_options` instead of
`attributes.scheduler`, and set `is_cached=True`:

```python
def test_load_cache_refresh_with_daily_shorthand(scheduler_with_inmemory_jobstore):
    rows = [{
        "query_slug": "cached_slug",
        "attributes": None,
        "provider": None,
        "query_raw": None,
        "is_cached": True,
        "cache_options": {
            "schedule_type": "daily",
            "schedule": {"hour": 3, "minute": 0},
        },
    }]
    count = scheduler_with_inmemory_jobstore._load_cache_refresh_jobs(rows)
    assert count == 1
    assert scheduler_with_inmemory_jobstore._scheduler.get_job("cache_cached_slug") is not None
```

### Key Constraints

- The fixture MUST NOT call `scheduler._scheduler.start()`. APScheduler's
  `add_job` works on a non-started scheduler — the job is registered in
  the in-memory jobstore but never fires. This keeps the test synchronous
  and avoids needing an event loop.
- The fixture MUST NOT call `QSScheduler.startup(app)` — that opens a
  PostgreSQL pool.
- Use `provider: None` (or omit `provider`) for non-multi rows so the
  loader routes to `scheduled_query_job`, not `scheduled_multiqs_job`.
- Use `caplog` to assert ERROR log emission for the invalid row in
  `test_invalid_shorthand_skips_job_but_loads_others`.

### References in Codebase

- `querysource/scheduler/scheduler.py:105-194` — `_load_scheduled_queries`
  body. Note the keys it reads from each row.
- `querysource/scheduler/scheduler.py:196-241` — `_load_cache_refresh_jobs`
  body. Skips `provider == "multi"` rows unconditionally.

---

## Acceptance Criteria

- [ ] `tests/scheduler/test_shorthand_loaders.py` exists with the four
      tests listed in Scope.
- [ ] `pytest tests/scheduler/test_shorthand_loaders.py -v` passes after
      TASK-689 is merged.
- [ ] Tests do NOT open a PostgreSQL pool, do NOT start the scheduler,
      and do NOT require an aiohttp app fixture.
- [ ] The invalid-row test asserts that a `Failed to parse trigger` ERROR
      is logged for the skipped row (via `caplog`).
- [ ] `ruff check tests/scheduler/test_shorthand_loaders.py` returns no
      new errors.

---

## Test Specification

See "Implementation Notes" for full test bodies. Four tests total:

| Test | Asserts |
|---|---|
| `test_load_scheduled_queries_with_hourly_shorthand` | `count == 1`; `get_job("query_test_hourly")` not None |
| `test_load_cache_refresh_with_daily_shorthand` | `count == 1`; `get_job("cache_cached_slug")` not None |
| `test_mixed_shorthand_and_legacy_load_together` | `count == 2`; both jobs registered |
| `test_invalid_shorthand_skips_job_but_loads_others` | `count == 2`; bad job NOT registered; ERROR logged |

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qsscheduler-add-variants.spec.md`.
2. **Check dependencies**: confirm TASK-689 is in `sdd/tasks/completed/`.
   If not, stop and wait.
3. **Verify the Codebase Contract** — open
   `querysource/scheduler/scheduler.py` and confirm `_create_scheduler`,
   `_load_scheduled_queries`, and `_load_cache_refresh_jobs` still match
   the signatures listed.
4. **Implement** the four tests.
5. **Run**: `source .venv/bin/activate && pytest tests/scheduler/test_shorthand_loaders.py -v`.
6. **Lint**: `ruff check tests/scheduler/`.
7. **Move this file** to `sdd/tasks/completed/TASK-690-shorthand-loader-integration-tests.md`.
8. **Update** `sdd/tasks/index/qsscheduler-add-variants.json` → set
   `status: "done"`, fill `completed_at`, mark feature `completed_at` if
   this was the last task.
9. **Fill in** the Completion Note below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
