# TASK-647: Smoke / integration test for multi-query routing

**Feature**: FEAT-092 — QSScheduler Multi-Query Support
**Spec**: `sdd/specs/qsscheduler-multi-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644, TASK-645, TASK-646
**Assigned-to**: unassigned

---

## Context

The unit tests in TASK-644/645/646 cover each seam individually. This
task adds an end-to-end smoke / integration test that confirms the
three changes work together: given a populated set of rows of all three
kinds (single-query schedule, multi-query schedule, cache-refresh
single-query), `QSScheduler` registers exactly one job of each kind
with the right id, callable, and kwargs — and the cache-refresh path
does NOT produce a `cache_<slug>` job for the multi-query row.

Implements **Open Question Q3 (resolved: yes)** and §4 Integration
Tests from the spec.

The project's existing scheduler test suite is **flat** under
`tests/test_scheduler_*.py` (no `tests/scheduler/` subdir), so this
task lives in `tests/test_scheduler_multi_routing.py`, not the
`tests/scheduler/...` path mentioned in the brainstorm/spec text.

---

## Scope

- Create `tests/test_scheduler_multi_routing.py` with two
  pytest-asyncio integration tests:
  - `test_qsscheduler_registers_multi_job_from_db_row`
  - `test_qsscheduler_mixed_row_population`
- Use the same fixture conventions as
  `tests/test_scheduler_integration.py` — read that file first to align
  with how DB seeding and `ENABLE_QS_SCHEDULER` toggling are done.
- If the existing scheduler integration suite uses a sqlite or mock
  PostgreSQL backend, use the same backend. Do NOT introduce a new
  live-DB dependency. If the existing suite mocks the DB query at
  `QSScheduler.startup`, mock it the same way here.

**NOT in scope**:
- Unit tests for individual functions → TASK-644 / 645 / 646.
- Modifying production source code → already done by TASK-644 / 645 /
  646.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_scheduler_multi_routing.py` | CREATE | Two integration tests + helper fixtures. |
| `tests/conftest.py` | MODIFY (only if needed) | Add a fixture for the three-row mixed-population dataset, IF integration tests in the existing suite use module-level fixtures. Default: keep fixtures local to the new file. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.scheduler.scheduler import QSScheduler
# verified: querysource/scheduler/scheduler.py:30

from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,   # introduced by TASK-644
)
# verified destination: querysource/scheduler/jobs.py (post-TASK-644)
```

### Existing Test File to Mirror

```text
# tests/test_scheduler_integration.py
# Read this file FIRST to:
#   - copy the existing DB-seeding / mocking pattern;
#   - reuse any conftest fixtures (look for autouse, scope='session');
#   - match the ENABLE_QS_SCHEDULER toggling approach.
```

### Existing Class Signatures Used

```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def __init__(self, loop=None): ...                # line 37
    def _load_scheduled_queries(self, rows): ...      # line 89
    def _load_cache_refresh_jobs(self, rows): ...     # line 131
    async def startup(self, app): ...                 # line 179
    async def shutdown(self, app): ...                # line 230
```

### Does NOT Exist

- ~~`tests/scheduler/` directory~~ — project convention is flat
  `tests/test_*.py`. Put the file at
  `tests/test_scheduler_multi_routing.py`.
- ~~A `QSScheduler.list_jobs()` helper~~ — does not exist. Use
  `self._scheduler.get_jobs()` (APScheduler API) for assertions.
- ~~`pytest-postgres` / live-DB fixture~~ — not assumed by the
  existing scheduler integration suite. Mock the DB query at
  `startup` if needed (the existing test suite likely does this
  already; verify before introducing anything new).

---

## Implementation Notes

### Pattern to Follow

```python
# tests/test_scheduler_multi_routing.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from querysource.scheduler.scheduler import QSScheduler
from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,
)


@pytest.fixture
def mixed_rows():
    return [
        # single-query schedule
        {
            "query_slug": "single_a",
            "attributes": {"scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            }},
            "cache_options": {},
            "provider": "db",
            "is_cached": False,
            "query_raw": "SELECT 1",
        },
        # multi-query schedule
        {
            "query_slug": "multi_b",
            "attributes": {"scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 60},
            }},
            "cache_options": {},
            "provider": "multi",
            "is_cached": False,
            "query_raw": '{"queries": {}}',
        },
        # cache-refresh single-query
        {
            "query_slug": "cached_c",
            "attributes": {},
            "cache_options": {
                "schedule_type": "interval",
                "schedule": {"minutes": 15},
            },
            "provider": "db",
            "is_cached": True,
            "query_raw": "SELECT 2",
        },
    ]


class TestQSSchedulerMixedPopulation:

    def test_registers_multi_job_from_db_row(self, mixed_rows):
        sched = QSScheduler()
        sched._scheduler = MagicMock()
        sched._load_scheduled_queries(mixed_rows)
        # one query_ and one multi_ job
        calls = sched._scheduler.add_job.call_args_list
        ids = [c.kwargs["id"] for c in calls]
        assert "query_single_a" in ids
        assert "multi_multi_b" in ids
        # callable assertions
        callables = [c.args[0] for c in calls]
        assert scheduled_multiqs_job in callables
        assert scheduled_query_job in callables

    def test_mixed_row_population(self, mixed_rows):
        sched = QSScheduler()
        sched._scheduler = MagicMock()
        sched._load_scheduled_queries(mixed_rows)
        sched._load_cache_refresh_jobs(mixed_rows)
        calls = sched._scheduler.add_job.call_args_list
        ids = [c.kwargs["id"] for c in calls]
        # exactly one of each kind, NO cache_multi_b
        assert ids.count("query_single_a") == 1
        assert ids.count("multi_multi_b") == 1
        assert ids.count("cache_cached_c") == 1
        assert "cache_multi_b" not in ids
```

If `tests/test_scheduler_integration.py` uses a higher-fidelity
fixture (e.g., a real `AsyncIOScheduler` + a mocked DB conn that
returns rows from `startup()`), promote the two tests above to use
that fixture instead of constructing rows by hand. **Read that file
first** and align.

### Key Constraints

- Tests must be hermetic — no live DB, no real network.
- No new pytest plugin dependencies.
- Use `pytest.mark.asyncio` only if the existing integration suite
  does (it likely does — verify).
- Assertions check identity (`callable is scheduled_multiqs_job`), not
  string-by-name — catches accidental rename/refactor.

### References in Codebase

- `tests/test_scheduler_integration.py` — pattern reference (read
  first).
- `tests/test_scheduler_core.py` — unit-test patterns for the loader
  methods.

---

## Acceptance Criteria

- [ ] `tests/test_scheduler_multi_routing.py` exists with two tests.
- [ ] `test_registers_multi_job_from_db_row` asserts the multi job is
      registered with id `multi_<slug>` and the callable IS
      `scheduled_multiqs_job`.
- [ ] `test_mixed_row_population` asserts exactly one of each kind
      (`query_`, `multi_`, `cache_`) is registered AND NO
      `cache_<multi-slug>` job exists.
- [ ] All tests pass:
      `pytest tests/test_scheduler_multi_routing.py -v`.
- [ ] Existing scheduler tests still pass:
      `pytest tests/test_scheduler_core.py
      tests/test_scheduler_jobs.py
      tests/test_scheduler_integration.py
      tests/test_scheduler_notifications.py -v`.
- [ ] No linting errors: `ruff check tests/test_scheduler_multi_routing.py`.

---

## Agent Instructions

1. **Read `tests/test_scheduler_integration.py` FIRST** — align with
   its fixture conventions (DB mocking, ENABLE_QS_SCHEDULER toggling).
2. **Verify TASK-644 / 645 / 646 are all completed** — check
   `sdd/tasks/completed/` for each.
3. **Verify the Codebase Contract** — confirm the three job callables
   are importable.
4. **Update task status** in
   `sdd/tasks/index/qsscheduler-multi-support.json` → `"in_progress"`.
5. **Implement** the test file.
6. **Run tests**:
   `pytest tests/test_scheduler_multi_routing.py
   tests/test_scheduler_core.py
   tests/test_scheduler_jobs.py
   tests/test_scheduler_integration.py -v`.
7. **Move this file** to
   `sdd/tasks/completed/TASK-647-multi-routing-smoke-test.md`.
8. **Update index** → `"completed"`.
9. **Fill in Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
