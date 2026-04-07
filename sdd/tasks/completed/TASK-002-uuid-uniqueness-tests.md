# TASK-002: Unit tests for UUID uniqueness

**Feature**: Duplicate Task Execution ID Fix (FEAT-001)
**Spec**: `sdd/specs/duplicate-execution-id.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-001
**Assigned-to**: 29678b6f-5f02-4640-9927-694e7ff1cea2

---

## Context

> After TASK-001 refactors `TaskScheduler` to create a new `TaskWrapper` per dispatch,
> this task adds unit and integration tests to verify UUID uniqueness and prevent
> regression. Implements Spec Module 2.

---

## Scope

- Write unit tests verifying that `TaskScheduler._create_wrapper()` produces distinct
  UUIDs on every call.
- Write a test verifying that `TaskScheduler.__call__()` uses a different `wrapper`
  each invocation.
- Write an integration-style test simulating multiple APScheduler triggers and asserting
  unique IDs in the enqueued payloads.

**NOT in scope**:
- Modifying `TaskScheduler` (done in TASK-001).
- End-to-end tests against a live InfluxDB.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_scheduler_uuid.py` | CREATE | Unit & integration tests for UUID uniqueness |

---

## Implementation Notes

### Key Constraints
- Mock `QClient` (worker) to avoid needing a real queue connection.
- Mock `TaskWrapper.__call__` / `Task` to avoid needing a real DB.
- Use `unittest.mock.patch` or `pytest-mock` for isolating dependencies.
- Tests should be fast (no I/O, no network).

### References in Codebase
- [`flowtask/scheduler/functions.py`](file:///home/jesuslara/proyectos/parallel/flowtask/flowtask/scheduler/functions.py) — `TaskScheduler` (after TASK-001 fix)
- [`qw/wrappers/di_task.py`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/di_task.py) — `TaskWrapper`
- [`qw/wrappers/base.py`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/base.py) — `QueueWrapper._id` generation

---

## Acceptance Criteria

- [x] `test_consecutive_calls_produce_unique_ids` passes
- [x] `test_wrapper_recreated_per_call` passes
- [x] `test_scheduler_dispatch_unique_ids` passes
- [x] All tests pass: `pytest tests/test_scheduler_uuid.py -v`
- [x] No linting errors: `ruff check tests/test_scheduler_uuid.py`

---

## Test Specification

```python
# tests/test_scheduler_uuid.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from flowtask.scheduler.functions import TaskScheduler


@pytest.fixture
def mock_worker():
    worker = MagicMock()
    worker.queue = AsyncMock(return_value={"message": "ok"})
    return worker


@pytest.fixture
def scheduler(mock_worker):
    return TaskScheduler(
        program="test_program",
        task="test_task",
        job_id="test_job_001",
        priority="low",
        worker=mock_worker,
    )


class TestTaskSchedulerUUID:
    def test_consecutive_calls_produce_unique_ids(self, scheduler):
        """Each _create_wrapper() call must generate a distinct UUID."""
        ids = {scheduler._create_wrapper().id for _ in range(10)}
        assert len(ids) == 10

    def test_wrapper_recreated_per_call(self, scheduler):
        """Each _create_wrapper() returns a NEW TaskWrapper instance."""
        w1 = scheduler._create_wrapper()
        w2 = scheduler._create_wrapper()
        assert w1 is not w2
        assert w1.id != w2.id

    @patch("flowtask.scheduler.functions.TaskScheduler.save_task_id", new_callable=AsyncMock)
    def test_scheduler_dispatch_unique_ids(self, mock_save, scheduler):
        """Simulates multiple scheduler triggers and verifies unique IDs."""
        collected_ids = []
        original_call = scheduler.__call__

        # Patch _schedule_task to capture wrapper.id
        async def capture_schedule(wrapper, worker):
            collected_ids.append(wrapper.id)
            return {"message": "ok"}

        scheduler._schedule_task = capture_schedule
        # Trigger 5 times
        for _ in range(5):
            scheduler()

        assert len(set(collected_ids)) == 5
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/duplicate-execution-id.spec.md`
2. **Check dependencies** — TASK-001 must be in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and test spec above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-002-uuid-uniqueness-tests.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 29678b6f-5f02-4640-9927-694e7ff1cea2
**Date**: 2026-03-02
**Notes**: Implemented unit and integration test coverage for `TaskScheduler._create_wrapper()` and `TaskScheduler.__call__()`. Verified the `ruff` linter and `pytest` suite ran successfully in the isolated environment. The ID uniqueness bug fix from TASK-001 is now covered by regression tests.

**Deviations from spec**: none
