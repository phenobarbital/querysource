# TASK-018: QuerySource Integration

**Feature**: QuerySource Scheduler (QSScheduler)
**Spec**: `sdd/specs/querysource-scheduler.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-014, TASK-015
**Assigned-to**: unassigned

---

## Context

> This task wires QSScheduler into the QuerySource application lifecycle.
> When `ENABLE_QS_SCHEDULER` is True, the scheduler is created during `setup()`
> and its startup/shutdown hooks are registered on aiohttp signals.
> Implements Spec Module 5 (QuerySource Integration).

---

## Scope

- Modify `querysource/services.py`:
  - Import `ENABLE_QS_SCHEDULER` from `querysource.conf`
  - In `setup()`, after existing startup registrations (after line ~217):
    - If `ENABLE_QS_SCHEDULER` is True:
      - Import `QSScheduler` from `querysource.scheduler`
      - Create `self._scheduler = QSScheduler(loop=self._loop)`
      - Call `self._scheduler.setup(app)` (which registers startup/shutdown hooks)
      - Store reference: `app["qs_scheduler"] = self._scheduler`
  - Ensure scheduler startup runs AFTER `QueryConnection.start()` (ordering via
    append order in `on_startup` — scheduler hook must be appended after connection hook)
- Write integration test verifying conditional initialization

**NOT in scope**: QSScheduler internals (TASK-015), job definitions (TASK-016), notifications (TASK-017).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Conditionally init QSScheduler in setup() |
| `tests/test_scheduler_integration.py` | CREATE | Integration test for conditional setup |

---

## Implementation Notes

### Pattern to Follow
```python
# In QuerySource.setup(), after the existing on_startup/on_shutdown registration:
from .conf import ENABLE_QS_SCHEDULER

# ... existing setup code ...

# QSScheduler (conditional)
if ENABLE_QS_SCHEDULER:
    from .scheduler import QSScheduler
    self._scheduler = QSScheduler(loop=self._loop)
    self._scheduler.setup(app)
    app["qs_scheduler"] = self._scheduler
```

### Key Constraints
- Import `QSScheduler` lazily (inside the `if` block) to avoid loading APScheduler
  when the scheduler is disabled
- Scheduler startup must come AFTER `QueryConnection.start()` in the aiohttp
  startup sequence — this is guaranteed by appending `scheduler.setup(app)` after
  `self.connection.setup(app)` (aiohttp runs `on_startup` handlers in append order)
- When disabled, no scheduler-related imports should execute (zero overhead)
- Do NOT modify `QueryConnection` or `QS` classes

### References in Codebase
- `querysource/services.py` — `QuerySource.setup()` method (lines 79-230)
- `flowtask/scheduler/scheduler.py` — `NavScheduler.setup()` for aiohttp integration pattern (lines 163-179)

---

## Acceptance Criteria

- [ ] When `ENABLE_QS_SCHEDULER=True`, `QSScheduler` is created and registered on app
- [ ] When `ENABLE_QS_SCHEDULER=False` (default), no scheduler code is imported or executed
- [ ] Scheduler is accessible via `app["qs_scheduler"]` when enabled
- [ ] Scheduler startup hook runs after QueryConnection startup
- [ ] No breaking changes to existing QuerySource behavior
- [ ] Integration test passes: `pytest tests/test_scheduler_integration.py -v`

---

## Test Specification

```python
# tests/test_scheduler_integration.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestQuerySourceSchedulerIntegration:
    @patch("querysource.services.ENABLE_QS_SCHEDULER", True)
    @patch("querysource.services.QSScheduler")
    def test_scheduler_created_when_enabled(self, mock_sched_cls):
        """QSScheduler is created when flag is True."""
        # This test verifies the conditional import and creation
        pass

    @patch("querysource.services.ENABLE_QS_SCHEDULER", False)
    def test_scheduler_not_created_when_disabled(self):
        """No scheduler created when flag is False."""
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/querysource-scheduler.spec.md` for full context
2. **Check dependencies** — TASK-014 (config) and TASK-015 (core scheduler) must be done
3. **Read** `querysource/services.py` to understand the current setup() method
4. **Update status** in `sdd/tasks/.index.json` -> `"in-progress"`
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-018-querysource-integration.md`
8. **Update index** -> `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: claude-session-2026-03-25
**Date**: 2026-03-25
**Notes**: Added `ENABLE_QS_SCHEDULER` import and conditional QSScheduler creation in `QuerySource.setup()`. Scheduler hooks are appended after connection setup hooks, ensuring correct startup ordering. 5 integration tests pass.

**Deviations from spec**: none
