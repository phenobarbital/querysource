# TASK-932: scheduled_slot, occurrence_id and the interval anchor

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-931
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2. Single-execution needs every worker to compute the **same**
occurrence key for one fire. APScheduler 3.11 does not pass `scheduled_run_time` to
job callables, so the slot is derived from the trigger: the latest fire time ≤ now.
Interval triggers anchor at each worker's registration time unless they have a
`start_date`, so `_parse_trigger` must add a fixed anchor (flowtask precedent).

---

## Scope

- Add `scheduled_slot(trigger, now, *, lookback)` and `occurrence_id(job_id, slot)` to
  `querysource/scheduler/history.py`.
- In `QSScheduler._parse_trigger`, interval branch: if `schedule` has no
  `start_date`, add `QS_SCHEDULER_INTERVAL_ANCHOR` localized to the trigger TZ.
- Property-style tests across cron, crontab, interval, hourly, daily, weekly, monthly
  and biweekly.

**NOT in scope**: using the slot in `admit` (TASK-936).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/history.py` | MODIFY | Append `scheduled_slot`, `occurrence_id` |
| `querysource/scheduler/scheduler.py` | MODIFY | Interval anchor in `_parse_trigger` |
| `tests/scheduler/test_scheduled_slot.py` | CREATE | Slot + anchor tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from apscheduler.triggers.base import BaseTrigger                      # verified: querysource/scheduler/scheduler.py:30
from apscheduler.triggers.cron import CronTrigger                      # scheduler.py:31
from apscheduler.triggers.interval import IntervalTrigger              # scheduler.py:32
from querysource.conf import QS_SCHEDULER_INTERVAL_ANCHOR, QS_SCHEDULER_SLOT_LOOKBACK   # TASK-929
```

### Existing Signatures to Use
```python
# querysource/scheduler/scheduler.py
class QSScheduler:                                                     # line 128
    def _parse_trigger(self, schedule_type: str, schedule: dict)       # line 205
        # line 219: tz = schedule.get("timezone", self._timezone)
        # line 220-221: if schedule_type == "interval": return IntervalTrigger(**schedule)
# apscheduler 3.11.2: BaseTrigger.get_next_fire_time(previous_fire_time, now) -> datetime | None
```

### Does NOT Exist
- ~~`job.scheduled_run_time` / `scheduled_run_time` kwarg in job callables~~: only on APScheduler events.
- ~~`BaseTrigger.get_prev_fire_time`~~: not in APScheduler 3.x. Walk forward from `now - lookback`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/history.py", "action": "MODIFY"},
    {"path": "querysource/scheduler/scheduler.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_scheduled_slot.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/scheduler/scheduler.py#QSScheduler"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Append the helpers to `history.py`. *Why:* `admit` (TASK-936) imports them from there.
2. Walk forward:
   1. `t = trigger.get_next_fire_time(None, now - lookback)`;
   2. while `t` exists and `t <= now`, keep the last one and step with
      `trigger.get_next_fire_time(t, t + 1µs)`;
   3. cap the number of iterations, and if the cap is hit restart the walk from a
      shorter window.

   *Why:* starting from `now - lookback` would otherwise return the **earliest** fire
   in the window for frequent crons.
3. If no fire time is found, return `now` truncated to the second and log DEBUG.
4. Patch `_parse_trigger`. *Why:* deterministic interval slots across workers.

### `querysource/scheduler/history.py` (MODIFY)
```python
# AFTER — append at end of file (after TASK-931's tracked_run)
def scheduled_slot(trigger: Any, now: datetime, *, lookback: timedelta) -> datetime:
    """Latest trigger fire time <= now (tz-aware); identical across workers for the same trigger config."""
    # FILL IN: forward walk described in Steps; bounded iterations (e.g. 10_000) — bounded by spec §3 M2
    raise NotImplementedError


def occurrence_id(job_id: str, slot: datetime) -> str:
    """Stable occurrence key ``"<job_id>|<slot UTC ISO-8601>"``."""
    return f"{job_id}|{slot.astimezone(timezone.utc).isoformat()}"
```

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '                return IntervalTrigger(**schedule)' querysource/scheduler/scheduler.py)
# REPLACE `                return IntervalTrigger(**schedule)` (verified: querysource/scheduler/scheduler.py:221) with:
                if "start_date" not in schedule:
                    # FEAT-181: deterministic anchor so every worker computes the same slots.
                    schedule = {**schedule, "start_date": QS_SCHEDULER_INTERVAL_ANCHOR}
                return IntervalTrigger(**schedule)
```
Also add `QS_SCHEDULER_INTERVAL_ANCHOR` to the existing `from querysource.conf import (...)`
block (`scheduler.py:35`).
**Why**: `IntervalTrigger` parses an ISO string `start_date` in its own `timezone` kwarg.
`schedule` already carries `timezone` when set, which keeps the anchor in the job's TZ.

### FILL IN checklist
- [ ] `scheduled_slot` forward walk + iteration cap + fallback.
- [ ] Tests for all 8 schedule types (build triggers through `QSScheduler._parse_trigger`), including `*/1` cron and ±3 s worker skew.

---

## Acceptance Criteria

- [ ] For daily 06:00 America/New_York, `scheduled_slot` at 06:00:00.4 and at 06:14 both return today 06:00.
- [ ] Two "workers" calling at `now` and `now + 3 s` get the same slot for every type.
- [ ] `*/1` cron with 1-day lookback returns the latest minute ≤ now.
- [ ] Interval without `start_date` gets the anchor; an explicit `start_date` is untouched.
- [ ] Existing `tests/scheduler/test_parse_trigger_shorthands.py` still passes.

---

## Validation Commands

- `pytest tests/scheduler/test_scheduled_slot.py -q`
- `pytest tests/scheduler/test_parse_trigger_shorthands.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-931 is done.
2. Verify the anchor line count is still 1 before editing.
3. Set `in-progress`; implement; complete every `# FILL IN:`; run the Validation Commands.
4. Close with `scripts/sdd/close_task.sh TASK-932 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
