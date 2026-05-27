# TASK-697: QWorker Configuration Settings

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> Adds QWORKER_HOST, QWORKER_PORT, and QWORKER_TIMEOUT configuration settings to
> querysource's conf.py. These provide the central default for remote query dispatch.
> Implements Spec §3 (Module 5).

---

## Scope

- Add three new settings to `querysource/conf.py` using the existing `navconfig.config` pattern:
  - `QWORKER_HOST`: `config.get('QWORKER_HOST', fallback=None)` — None means not configured
  - `QWORKER_PORT`: `config.getint('QWORKER_PORT', fallback=8888)` — matches qworker default
  - `QWORKER_TIMEOUT`: `config.getint('QWORKER_TIMEOUT', fallback=60)` — seconds for query execution
- Write minimal tests verifying defaults

**NOT in scope**: Using these settings in MultiQS (TASK-696), executor implementation
(TASK-693/694), any qworker-side changes

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | Add QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT |
| `tests/test_qworker_config.py` | CREATE | Verify config defaults |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# querysource/conf.py:5 — existing config import:
from navconfig import BASE_DIR, config
```

### Existing Signatures to Use
```python
# querysource/conf.py — existing config pattern (line 24):
DBHOST = config.get('DBHOST', fallback='localhost')

# querysource/conf.py — integer config pattern (line 50):
POSTGRES_MIN_CONNECTIONS = config.getint('POSTGRES_MIN_CONNECTIONS', fallback=2)
```

### Does NOT Exist
- ~~`querysource.conf.QWORKER_HOST`~~ — does not exist yet; this task creates it
- ~~`querysource.conf.QWORKER_PORT`~~ — does not exist yet; this task creates it
- ~~`querysource.conf.QWORKER_TIMEOUT`~~ — does not exist yet; this task creates it

---

## Implementation Notes

### Pattern to Follow
```python
# Add after the DB settings block in conf.py (around line 60):

### QWorker Remote Execution
QWORKER_HOST = config.get('QWORKER_HOST', fallback=None)
QWORKER_PORT = config.getint('QWORKER_PORT', fallback=8888)
QWORKER_TIMEOUT = config.getint('QWORKER_TIMEOUT', fallback=60)
```

### Key Constraints
- **QWORKER_HOST defaults to None**: This is intentional — if not configured, remote
  execution is not available by default. TASK-696 checks for None to raise DriverError.
- **QWORKER_PORT defaults to 8888**: Matches qworker's `WORKER_DEFAULT_PORT` (qw/conf.py:16).
- **QWORKER_TIMEOUT defaults to 60**: Seconds. This is the execution timeout, not
  the TCP connection timeout (which is QClient's own `timeout` attribute, default 5s).
- Place the settings in a clearly labeled section, following the existing code's commenting style.

### References in Codebase
- `querysource/conf.py:24-60` — existing config patterns to follow

---

## Acceptance Criteria

- [ ] `QWORKER_HOST`, `QWORKER_PORT`, `QWORKER_TIMEOUT` added to `querysource/conf.py`
- [ ] `QWORKER_HOST` defaults to `None`
- [ ] `QWORKER_PORT` defaults to `8888` (int)
- [ ] `QWORKER_TIMEOUT` defaults to `60` (int)
- [ ] Settings follow existing `navconfig.config.get()` / `.getint()` pattern
- [ ] Import works: `from querysource.conf import QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT`
- [ ] No linting errors: `ruff check querysource/conf.py`

---

## Test Specification

```python
# tests/test_qworker_config.py
from querysource.conf import QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT


class TestQWorkerConfig:
    def test_host_default_is_none(self):
        """QWORKER_HOST defaults to None when not configured."""
        # When env var is not set, should be None
        assert QWORKER_HOST is None or isinstance(QWORKER_HOST, str)

    def test_port_is_int(self):
        """QWORKER_PORT is an integer."""
        assert isinstance(QWORKER_PORT, int)

    def test_timeout_is_int(self):
        """QWORKER_TIMEOUT is an integer."""
        assert isinstance(QWORKER_TIMEOUT, int)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — this task has none
3. **Verify the Codebase Contract** — read `querysource/conf.py` to see current patterns
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-697-qworker-config-settings.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
