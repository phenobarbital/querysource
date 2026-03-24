# TASK-014: Scheduler Configuration

**Feature**: QuerySource Scheduler (QSScheduler)
**Spec**: `sdd/specs/querysource-scheduler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> This task adds the configuration flag that gates the entire QSScheduler feature.
> Without this flag, no scheduler code should execute.
> Implements Spec Module 1 (Configuration).

---

## Scope

- Add `ENABLE_QS_SCHEDULER = config.getboolean('ENABLE_QS_SCHEDULER', fallback=False)` to `querysource/conf.py`
- Add `QS_SCHEDULER_TIMEZONE` config value (fallback to existing `TIMEZONE` or `'UTC'`)
- Add `QS_SCHEDULER_MAX_INSTANCES = config.getint('QS_SCHEDULER_MAX_INSTANCES', fallback=1)` for per-job max instances
- Add `QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)` for job coalescing

**NOT in scope**: The scheduler class itself (TASK-015), job definitions (TASK-016), notifications (TASK-017), or integration with services.py (TASK-018).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | Add scheduler configuration flags |

---

## Implementation Notes

### Pattern to Follow
```python
# Follow existing pattern in querysource/conf.py:
# e.g. USE_VECTORS = config.getboolean('USE_VECTORS', fallback=False)
ENABLE_QS_SCHEDULER = config.getboolean('ENABLE_QS_SCHEDULER', fallback=False)
QS_SCHEDULER_TIMEZONE = config.get('QS_SCHEDULER_TIMEZONE', fallback=TIMEZONE if 'TIMEZONE' in dir() else 'UTC')
QS_SCHEDULER_MAX_INSTANCES = config.getint('QS_SCHEDULER_MAX_INSTANCES', fallback=1)
QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)
```

### Key Constraints
- Use `navconfig.config` (already imported as `config` in conf.py)
- Defaults must be safe: `ENABLE_QS_SCHEDULER=False` means zero overhead
- Group the new config values together with a comment block

### References in Codebase
- `querysource/conf.py` — existing configuration loading patterns
- `flowtask/conf.py` — reference for similar scheduler config values (`SCHEDULER_MAX_INSTANCES`, `SCHEDULER_GRACE_TIME`)

---

## Acceptance Criteria

- [ ] `ENABLE_QS_SCHEDULER` defaults to `False`
- [ ] `QS_SCHEDULER_TIMEZONE` defaults to existing `TIMEZONE` value
- [ ] Config values are importable: `from querysource.conf import ENABLE_QS_SCHEDULER`
- [ ] No side effects when scheduler is disabled

---

## Test Specification

```python
# tests/test_scheduler_config.py
import pytest


def test_scheduler_disabled_by_default():
    """ENABLE_QS_SCHEDULER defaults to False."""
    from querysource.conf import ENABLE_QS_SCHEDULER
    assert ENABLE_QS_SCHEDULER is False or isinstance(ENABLE_QS_SCHEDULER, bool)


def test_scheduler_config_importable():
    """All scheduler config values are importable."""
    from querysource.conf import (
        ENABLE_QS_SCHEDULER,
        QS_SCHEDULER_TIMEZONE,
        QS_SCHEDULER_MAX_INSTANCES,
        QS_SCHEDULER_COALESCE,
    )
    assert isinstance(QS_SCHEDULER_MAX_INSTANCES, int)
    assert isinstance(QS_SCHEDULER_COALESCE, bool)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/querysource-scheduler.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` -> `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-014-scheduler-config.md`
7. **Update index** -> `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
