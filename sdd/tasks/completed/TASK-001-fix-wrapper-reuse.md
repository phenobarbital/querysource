# TASK-001: Fix TaskScheduler wrapper reuse

**Feature**: Duplicate Task Execution ID Fix (FEAT-001)
**Spec**: `sdd/specs/duplicate-execution-id.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: 29678b6f-5f02-4640-9927-694e7ff1cea2

---

## Context

> This task addresses the root cause of NAV-6365. `TaskScheduler.__init__` creates a
> `TaskWrapper` once, and `__call__` reuses it on every APScheduler trigger, causing
> all dispatches of the same job to share the same UUID.
> Implements Spec Module 1.

---

## Scope

- Refactor `TaskScheduler` in `flowtask/scheduler/functions.py` to create a **new
  `TaskWrapper`** on every `__call__()` invocation instead of reusing one from `__init__`.
- Store `TaskWrapper` constructor kwargs in `self._wrapper_kwargs` during `__init__`.
- Add a `_create_wrapper()` helper method.
- Update `__call__` to use the local `wrapper` and set `self.task_id` per invocation.
- Update logger name to not embed `task_id` at construction (it changes per call).

**NOT in scope**:
- Modifying `QueueWrapper` or `TaskWrapper` in `qworker`.
- Writing tests (that is TASK-002).
- Changing manual API dispatch (`TaskService`).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/scheduler/functions.py` | MODIFY | Refactor `TaskScheduler` class |

---

## Implementation Notes

### Pattern to Follow
```python
class TaskScheduler:
    def __init__(self, program, task, job_id, priority="low", worker=None, **kwargs):
        self.task = task
        self.program = program
        self.priority = priority
        self.worker = worker
        self._scheduled = False
        self._wrapper_kwargs = kwargs  # Store for per-call wrapper creation
        self.job_id = job_id
        self.logger = logging.getLogger(f"Job.{job_id}")

    def _create_wrapper(self) -> TaskWrapper:
        """Create a fresh TaskWrapper with a new UUID per dispatch."""
        return TaskWrapper(
            program=self.program,
            task=self.task,
            ignore_results=True,
            **self._wrapper_kwargs
        )

    def __call__(self, *args, **kwargs):
        wrapper = self._create_wrapper()
        self.task_id = wrapper.id
        # Use local `wrapper` for _schedule_task / _send_task / _publish_task
        ...
```

### Key Constraints
- `self.task_id` is used in `save_task_id()` — must be set per call, not per init.
- `self.wrapper` is referenced in `_schedule_task`, `_send_task`, `_publish_task` — replace `self.wrapper` with local `wrapper`.
- Do not break the `set_test_job()` flow in `scheduler.py` which also creates a `TaskScheduler`.

### References in Codebase
- [`flowtask/scheduler/functions.py:50-305`](file:///home/jesuslara/proyectos/parallel/flowtask/flowtask/scheduler/functions.py#L50-L305) — current `TaskScheduler` implementation
- [`qw/wrappers/base.py`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/base.py) — `QueueWrapper` UUID generation
- [`qw/wrappers/di_task.py`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/di_task.py) — `TaskWrapper` constructor

---

## Acceptance Criteria

- [x] `TaskScheduler.__call__()` creates a new `TaskWrapper` on every invocation
- [x] Each invocation produces a distinct `wrapper.id` (UUID)
- [x] `save_task_id()` uses the per-call `task_id`
- [x] No linting errors: `ruff check flowtask/scheduler/functions.py`
- [x] Existing scheduler job-adding flow (`add_job`, `set_test_job`) still works

---

## Test Specification

> See TASK-002 for full test coverage. Minimal smoke check:

```python
def test_wrapper_not_shared_across_calls():
    sched = TaskScheduler("test_program", "test_task", "job_1", worker=mock_worker)
    w1 = sched._create_wrapper()
    w2 = sched._create_wrapper()
    assert w1.id != w2.id
    assert w1 is not w2
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/duplicate-execution-id.spec.md`
2. **Check dependencies** — none for this task
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-001-fix-wrapper-reuse.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 29678b6f-5f02-4640-9927-694e7ff1cea2
**Date**: 2026-03-02
**Notes**: Refactored `TaskScheduler` to store kwargs during `__init__` and invoke `_create_wrapper()` per `__call__` execution. This ensures independent UUIDs are generated for each scheduled trigger of a job, resolving the InfluxDB collision (NAV-6365). The `self.task_id` attribute is now dynamically updated on each run. Created `smoke_test_taskscheduler.py` locally to verify behavior without `pytest`.

**Deviations from spec**: none
