# TASK-929: Scheduler history and alert configuration keys

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Every FEAT-181 module reads its tunables from `querysource/conf.py`: the history
store, claim timeout, TTL, alert provider and interval anchor. This task adds those
keys only (spec §6 "Configuration References"), so later tasks can import them.

---

## Scope

- Add the `QS_SCHEDULER_*` history/alert keys and `EVENT_CHAT_BOT` / `EVENT_CHAT_ID`
  to `querysource/conf.py`, immediately after `QS_SCHEDULER_COALESCE`.
- Write a test asserting every key exists with the documented default type.

**NOT in scope**: using the keys (other tasks), `pyproject.toml` changes (TASK-940).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | New config keys after `QS_SCHEDULER_COALESCE` |
| `tests/scheduler/test_history_config.py` | CREATE | Defaults/types of the new keys |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navconfig import BASE_DIR, config        # verified: querysource/conf.py:6
```

### Existing Signatures to Use
```python
# querysource/conf.py
DOCUMENTDB_DATABASE = config.get('DOCUMENTDB_DATABASE', fallback='navigator')   # line 253
ENVIRONMENT = config.get('ENVIRONMENT', fallback='development')                 # line 214
ENABLE_QS_SCHEDULER = config.getboolean('ENABLE_QS_SCHEDULER', fallback=False)  # line 388
QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)  # line 391
# navconfig `config` exposes get / getboolean / getint (all used in this file).
```

### Does NOT Exist
- ~~`EVENT_CHAT_BOT`, `EVENT_CHAT_ID`, `DEFAULT_RECIPIENT` in `querysource/conf.py`~~: they exist only in flowtask (`flowtask/conf.py:304-314`); add the two Telegram keys here.
- ~~`config.getfloat`~~: not used in this file. Use `float(config.get(..., fallback=...))`, the pattern at `conf.py:385` (`QS_DESCRIBE_COLUMNS_TIMEOUT`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/conf.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_history_config.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Defaults must match spec §6 exactly; later tasks rely on them.
- `QS_SCHEDULER_HISTORY_DATABASE` falls back to `DOCUMENTDB_DATABASE` (defined
  earlier in the file, line 253), so place the block after it. The insertion point
  (line 391) is already after it.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the block below after the `QS_SCHEDULER_COALESCE` line. *Why:* it groups
   all scheduler keys, and every name the block references is already defined
   above that line.
2. Create the test file and fill in the assertions. *Why:* it pins the defaults the
   other tasks depend on.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)" querysource/conf.py)
# AFTER — insert below `QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)` (verified: querysource/conf.py:391)
## QSScheduler execution history (FEAT-181): DocumentDB-backed, TTL-retained.
QS_SCHEDULER_HISTORY = config.getboolean('QS_SCHEDULER_HISTORY', fallback=True)
QS_SCHEDULER_HISTORY_DATABASE = config.get('QS_SCHEDULER_HISTORY_DATABASE', fallback=DOCUMENTDB_DATABASE)
QS_SCHEDULER_HISTORY_COLLECTION = config.get('QS_SCHEDULER_HISTORY_COLLECTION', fallback='qs_scheduler_runs')
QS_SCHEDULER_HISTORY_TTL_DAYS = config.getint('QS_SCHEDULER_HISTORY_TTL_DAYS', fallback=90)
QS_SCHEDULER_HISTORY_QUEUE = config.getint('QS_SCHEDULER_HISTORY_QUEUE', fallback=1000)
QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT = float(config.get('QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT', fallback=5))
QS_SCHEDULER_CLAIM_TIMEOUT = float(config.get('QS_SCHEDULER_CLAIM_TIMEOUT', fallback=2.0))
QS_SCHEDULER_HISTORY_READ_TIMEOUT = float(config.get('QS_SCHEDULER_HISTORY_READ_TIMEOUT', fallback=3.0))
QS_SCHEDULER_RUN_TIMEOUT = config.getint('QS_SCHEDULER_RUN_TIMEOUT', fallback=21600)
QS_SCHEDULER_SLOT_LOOKBACK = config.getint('QS_SCHEDULER_SLOT_LOOKBACK', fallback=86400)
QS_SCHEDULER_INTERVAL_ANCHOR = config.get('QS_SCHEDULER_INTERVAL_ANCHOR', fallback='2000-01-01T00:00:00')
QS_SCHEDULER_ERROR_MAX = config.getint('QS_SCHEDULER_ERROR_MAX', fallback=4000)
## Failure alerts (async-notify): telegram | email | slack | none
QS_SCHEDULER_NOTIFY = config.get('QS_SCHEDULER_NOTIFY', fallback='telegram')
QS_SCHEDULER_ALERT_COOLDOWN = config.getint('QS_SCHEDULER_ALERT_COOLDOWN', fallback=600)
QS_SCHEDULER_ALERT_URL = config.get('QS_SCHEDULER_ALERT_URL', fallback=None)
EVENT_CHAT_BOT = config.get('EVENT_CHAT_BOT', fallback=None)
EVENT_CHAT_ID = config.get('EVENT_CHAT_ID', fallback=None)
```
**Why**: these are the spec §6 keys verbatim. `EVENT_CHAT_*` reuse flowtask's names, so
one vault entry serves both schedulers.

### `tests/scheduler/test_history_config.py` (CREATE)
```python
"""FEAT-181: scheduler history/alert config keys exist with documented defaults."""
from querysource import conf


def test_history_keys_defaults() -> None:
    """Every FEAT-181 key exists with the type the spec documents."""
    assert isinstance(conf.QS_SCHEDULER_HISTORY, bool)
    assert conf.QS_SCHEDULER_HISTORY_COLLECTION
    assert isinstance(conf.QS_SCHEDULER_HISTORY_TTL_DAYS, int)
    assert isinstance(conf.QS_SCHEDULER_CLAIM_TIMEOUT, float)
    # FILL IN: assert the remaining keys (queue, drain, read timeout, run timeout,
    # lookback, anchor, error max, notify, cooldown) — bounded by spec §6 table
```

### FILL IN checklist
- [ ] `test_history_keys_defaults`: cover every key in the block.

---

## Acceptance Criteria

- [ ] All keys from spec §6 "Configuration References" exist in `querysource/conf.py`.
- [ ] `ruff check querysource/conf.py tests/scheduler/test_history_config.py` clean.
- [ ] Test passes.

---

## Validation Commands

- `pytest tests/scheduler/test_history_config.py -q`

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug scheduler-execution-history --feature-id FEAT-181`).
2. Read the spec; check `Depends-on` is done in `sdd/tasks/index/scheduler-execution-history.json`.
3. Verify the Codebase Contract before writing code.
4. Set this task `in-progress` in the index (commit only the index).
5. Implement from the blueprint; complete every `# FILL IN:`.
6. Run the Validation Commands; commit only this task's files.
7. Close with `scripts/sdd/close_task.sh TASK-929 scheduler-execution-history verified`, fill the Completion Note, commit SDD state.

---

## Completion Note

*(Agent fills this in when done)*
