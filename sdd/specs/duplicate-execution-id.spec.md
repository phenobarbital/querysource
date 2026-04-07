# Feature Specification: Duplicate Task Execution ID Fix

**Feature ID**: FEAT-001
**Date**: 2026-03-02
**Author**: Antigravity
**Status**: approved
**Target version**: 5.8.23

---

## 1. Motivation & Business Requirements

### Problem Statement
Multiple executions of the same task are incorrectly using the same `_id` (UUID) in the stats payload. This occurs when tasks are dispatched automatically from the `flowtask` scheduler to the `qworker` service.

Both code repositories are in the same workspace: **flowtask** and **qworker**.

When a task is dispatched by the Scheduler, it is wrapped into a `TaskWrapper` object (lives in `qworker`) and sent to the queue. `TaskWrapper` inherits from `QueueWrapper`, which generates the `_id` in its `__init__`:

```python
# qw/wrappers/base.py — QueueWrapper.__init__
self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())
```

**Observed incidents:**
- `hisense.photos` → Repeated ID `e11a8bc4-...` across ≥4 executions (2025-07-23).
- `bose.tickets` → Repeated ID `30aca7c1-...` across 7 executions (2025-07-25).
- `networkninja.form_data` → Repeated ID `30aae231-...` across distinct executions (2025-07-25).

### Root Cause

The bug is in [`flowtask/scheduler/functions.py`](file:///home/jesuslara/proyectos/parallel/flowtask/flowtask/scheduler/functions.py#L50-L75):

```python
class TaskScheduler:
    def __init__(self, program, task, job_id, ...):
        # TaskWrapper is created ONCE at construction time
        self.wrapper = TaskWrapper(program=program, task=task, ...)
        self.task_id = self.wrapper.id  # UUID fixed here

    def __call__(self, *args, **kwargs):
        # Reuses self.wrapper on EVERY scheduler trigger
        self._schedule_task(self.wrapper, self.worker)
```

`TaskScheduler` is instantiated once per job in `get_function()` and registered with APScheduler. Every time the schedule fires, APScheduler calls `TaskScheduler.__call__()`, which reuses the **same** `self.wrapper` instance — and therefore the same `_id`.

**Why manual triggers work:** The manual API endpoint (`TaskService`) creates a **new** `TaskWrapper` per request, so `uuid.uuid4()` runs fresh each time.

### Goals
- Ensure every task execution has a unique `_id` per dispatch.
- Fix collision in InfluxDB/stats logs caused by repeated IDs.
- Improve auditability and error resolution by uniquely tracking every run.

### Non-Goals
- Refactoring the entire scheduling mechanism of `flowtask`.
- Changing the `QueueWrapper` / `TaskWrapper` interface in `qworker`.

---

## 2. Architectural Design

### Overview
The fix is to regenerate the `TaskWrapper` (or at minimum regenerate its `_id`) on every `TaskScheduler.__call__()` invocation, rather than reusing the one created in `__init__`.

### Component Diagram
```
APScheduler ──(fires)──→ TaskScheduler.__call__()
                              │
                              ├── Create NEW TaskWrapper (fresh uuid)
                              └── queue(wrapper) ──→ qworker ──→ InfluxDB
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| [`TaskScheduler`](file:///home/jesuslara/proyectos/parallel/flowtask/flowtask/scheduler/functions.py#L50-L75) | **modification** | Move `TaskWrapper` creation from `__init__` to `__call__`. |
| [`QueueWrapper`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/base.py#L10-L27) | **no change** | UUID generation logic is correct; the problem is in the caller. |
| [`TaskWrapper`](file:///home/jesuslara/proyectos/navigator/qworker/qw/wrappers/di_task.py#L23-L36) | **no change** | Correctly delegates to `QueueWrapper.__init__`. |

### Data Models
No data model changes. `QueueWrapper._id` remains `uuid.UUID`.

---

## 3. Module Breakdown

### Module 1: Fix TaskScheduler wrapper reuse
- **Path**: `flowtask/scheduler/functions.py`
- **Responsibility**: Move `TaskWrapper` instantiation from `TaskScheduler.__init__` into `TaskScheduler.__call__` so a fresh UUID is generated per dispatch.
- **Depends on**: none

### Module 2: Unit tests for UUID uniqueness
- **Path**: `tests/test_scheduler_uuid.py`
- **Responsibility**: Verify that calling `TaskScheduler.__call__()` multiple times produces distinct `wrapper.id` values.
- **Depends on**: Module 1

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_consecutive_calls_produce_unique_ids` | Module 1 | Call `TaskScheduler.__call__()` N times, assert all `wrapper.id` values are distinct. |
| `test_wrapper_recreated_per_call` | Module 1 | Assert `TaskScheduler.wrapper` is a different object on each `__call__`. |

### Integration Tests
| Test | Description |
|---|---|
| `test_scheduler_dispatch_unique_ids` | Simulate APScheduler firing a job multiple times, verify unique IDs in enqueued payloads. |

---

## 5. Acceptance Criteria
- [ ] Every automated task dispatch from `flowtask` generates a unique `_id`.
- [ ] No collisions detected in InfluxDB for repeated task runs.
- [ ] Manual triggers continue to generate unique IDs as before.
- [ ] `TaskScheduler.__call__()` creates a new `TaskWrapper` per invocation.
- [ ] Unit tests pass: `pytest tests/test_scheduler_uuid.py -v`

---

## 6. Implementation Notes & Constraints

### Proposed Fix (sketch)
```python
class TaskScheduler:
    def __init__(self, program, task, job_id, priority="low", worker=None, **kwargs):
        self.task = task
        self.program = program
        self.priority = priority
        self.worker = worker
        self._scheduled = False
        self._wrapper_kwargs = kwargs  # Store kwargs for later
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
        # ... use `wrapper` instead of `self.wrapper` ...
```

### Known Risks / Gotchas
- `self.task_id` is used in `save_task_id()` and `self.logger` name includes it — both need to be updated per call.
- `self.wrapper` is referenced in `_schedule_task`, `_send_task`, `_publish_task` — all usage must switch to local `wrapper`.

### External Dependencies
None. All changes are internal to `flowtask`.

---

## 7. Open Questions
- [x] Should the UUID be generated in `flowtask` or `qworker`? → **flowtask** (the caller is the source of the bug).
- [ ] Should `_wrapper_kwargs` be deepcopied to avoid mutation across calls?

---

## Revision History
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-02 | Antigravity | Initial draft from NAV-6365 |
| 0.2 | 2026-03-02 | Antigravity | Refined with precise root cause: `TaskWrapper` reuse in `TaskScheduler.__init__` |
