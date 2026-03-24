# TASK-017: Notification Callbacks

**Feature**: QuerySource Scheduler (QSScheduler)
**Spec**: `sdd/specs/querysource-scheduler.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> This task implements the pluggable notification callback system for job errors.
> v1 ships with only a logging callback, but the architecture supports adding
> more callbacks (Telegram, Slack, webhook) in the future.
> Implements Spec Module 4 (Notification Callbacks).

---

## Scope

- Create `querysource/scheduler/notifications.py` with:
  - `NotificationManager` class:
    - `__init__()` — initialize empty callback list, add default logging callback
    - `add_callback(callback: Callable)` — append to callback list
    - `notify(job_id: str, slug: str, error: Exception)` — invoke all registered callbacks
  - `logging_callback(job_id: str, slug: str, error: Exception)` — default callback
    that logs at WARNING level via `navconfig.logging`
- Write unit tests for callback registration and invocation

**NOT in scope**: Actual notification integrations (Telegram, Slack), scheduler core (TASK-015), job definitions (TASK-016).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/notifications.py` | CREATE | NotificationManager + logging callback |
| `tests/test_scheduler_notifications.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
# querysource/scheduler/notifications.py
from collections.abc import Callable
from navconfig.logging import logging

logger = logging.getLogger("QSScheduler.Notifications")


def logging_callback(job_id: str, slug: str, error: Exception) -> None:
    """Default notification callback — logs at WARNING level."""
    logger.warning(
        f"Scheduler job {job_id} (slug={slug}) failed: {error}"
    )


class NotificationManager:
    """Pluggable callback registry for scheduler job notifications."""

    def __init__(self):
        self._callbacks: list[Callable] = []
        # Register default logging callback
        self.add_callback(logging_callback)

    def add_callback(self, callback: Callable) -> None:
        """Register a notification callback."""
        self._callbacks.append(callback)

    def notify(self, job_id: str, slug: str, error: Exception) -> None:
        """Invoke all registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(job_id=job_id, slug=slug, error=error)
            except Exception as exc:
                logger.error(f"Notification callback failed: {exc}")
```

### Key Constraints
- Callbacks must be synchronous callables with signature `(job_id, slug, error) -> None`
- `notify()` must catch exceptions from individual callbacks (one failing callback must NOT prevent others)
- Default `logging_callback` is always registered on init
- Use `navconfig.logging` for the logger

### References in Codebase
- `flowtask/scheduler/notifications.py` — reference (if exists) for notification pattern
- `navconfig.logging` — logging framework

---

## Acceptance Criteria

- [ ] `NotificationManager` initializes with `logging_callback` as default
- [ ] `add_callback()` appends to callback list
- [ ] `notify()` invokes all registered callbacks
- [ ] Failing callback does not prevent other callbacks from running
- [ ] `logging_callback` logs at WARNING level
- [ ] Importable: `from querysource.scheduler.notifications import NotificationManager`
- [ ] All unit tests pass: `pytest tests/test_scheduler_notifications.py -v`

---

## Test Specification

```python
# tests/test_scheduler_notifications.py
import pytest
from unittest.mock import MagicMock


class TestNotificationManager:
    def test_default_callback_registered(self):
        """NotificationManager has logging_callback on init."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        assert len(mgr._callbacks) == 1

    def test_add_callback(self):
        """add_callback appends to list."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.add_callback(lambda **kw: None)
        assert len(mgr._callbacks) == 2

    def test_notify_calls_all_callbacks(self):
        """notify() invokes every registered callback."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        mock_cb = MagicMock()
        mgr.add_callback(mock_cb)
        mgr.notify(job_id="test", slug="test_slug", error=RuntimeError("fail"))
        mock_cb.assert_called_once()

    def test_failing_callback_does_not_block_others(self):
        """One failing callback does not prevent others."""
        from querysource.scheduler.notifications import NotificationManager
        mgr = NotificationManager()
        failing_cb = MagicMock(side_effect=RuntimeError("boom"))
        passing_cb = MagicMock()
        mgr.add_callback(failing_cb)
        mgr.add_callback(passing_cb)
        mgr.notify(job_id="test", slug="s", error=RuntimeError("x"))
        passing_cb.assert_called_once()


class TestLoggingCallback:
    def test_logs_at_warning(self, caplog):
        """logging_callback logs at WARNING level."""
        import logging as stdlib_logging
        from querysource.scheduler.notifications import logging_callback
        with caplog.at_level(stdlib_logging.WARNING):
            logging_callback(job_id="j1", slug="s1", error=RuntimeError("err"))
        assert "j1" in caplog.text
        assert "s1" in caplog.text
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/querysource-scheduler.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` -> `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-017-notification-callbacks.md`
7. **Update index** -> `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
