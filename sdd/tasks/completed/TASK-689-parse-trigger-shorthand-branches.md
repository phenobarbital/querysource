# TASK-689: Implement five shorthand `schedule_type` branches in `_parse_trigger`

**Feature**: FEAT-098 — QSScheduler Shorthand Variants (hourly / daily / weekly / monthly / biweekly)
**Spec**: `sdd/specs/qsscheduler-add-variants.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`QSScheduler._parse_trigger` at `querysource/scheduler/scheduler.py:73` today
accepts only three `schedule_type` values: `interval`, `crontab`, `cron`. This
task adds five additional values — `hourly`, `daily`, `weekly`, `monthly`,
`biweekly` — by extending the existing `if/elif` chain. The shorthands are
additive: existing `cron`/`crontab`/`interval` payloads continue to compile to
exactly the same APScheduler triggers.

Both consumers of `_parse_trigger` (`_load_scheduled_queries` at
`scheduler.py:105` and `_load_cache_refresh_jobs` at `scheduler.py:196`)
already route any returned trigger transparently — no changes are required in
the loaders. This task is the only place new code lands inside `scheduler.py`.

Implements **Module 1** and the **unit tests** subset of Module 2 from the
spec (§3 and §4 Unit Tests). Integration tests at the loader level belong to
TASK-690.

---

## Scope

- Extend `QSScheduler._parse_trigger` (`querysource/scheduler/scheduler.py:73`)
  with five new `elif` branches inside the existing `try:` block, placed
  **after** the existing `cron` branch and **before** the `else:` that logs
  "Unknown schedule_type":
    1. `hourly` → `CronTrigger(minute=schedule["minute"], timezone=tz)`
    2. `daily` → `CronTrigger(hour=schedule["hour"], minute=schedule["minute"], timezone=tz)`
    3. `weekly` → `CronTrigger(day_of_week=schedule["day_of_week"], hour=schedule["hour"], minute=schedule["minute"], timezone=tz)`
    4. `monthly` → `CronTrigger(day=schedule["day"], hour=schedule["hour"], minute=schedule["minute"], timezone=tz)`
    5. `biweekly` → two compilation modes (see Implementation Notes).
- For every shorthand, resolve `tz` exactly as the existing `crontab` branch
  does: `tz = schedule.get("timezone", self._timezone)`.
- Require fields are pulled via `schedule["key"]` (NOT `schedule.get(...)`),
  so missing keys raise `KeyError`, which is caught by the existing outer
  `except Exception` block at `scheduler.py:99-103` → logged at ERROR →
  returns `None`. **Do NOT add separate try/except blocks per shorthand**;
  reuse the outer one already present.
- Add a stdlib import at the top of `scheduler.py`:
  `from datetime import datetime, timedelta` (NOT yet present).
- Add a module-private helper `_biweekly_anchor(start_date, day_of_week, hour, minute)`
  inside `scheduler.py` (file-scoped function, not a class method) that:
    - Accepts `start_date` as either a `str` (`"YYYY-MM-DD"` or full ISO
      8601) or a `datetime`.
    - Maps a string `day_of_week` (`"mon".."sun"`) to int 0-6 via the table
      `{"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}`. Accepts
      int 0-6 unchanged.
    - Parses `start_date` (via `datetime.fromisoformat` for str inputs) and
      replaces hour/minute/second/microsecond with the requested
      `hour`/`minute`/0/0.
    - Rolls the anchor forward by `timedelta(days=1)` until
      `anchor.weekday() == target_dow`.
    - Returns the resulting `datetime`.
- Write the new unit test module `tests/scheduler/test_parse_trigger_shorthands.py`
  covering every test listed in the spec §4 Unit Tests table (19 tests).
- Tests must NOT start the scheduler or open a DB connection; they
  instantiate `QSScheduler(loop=None)` and call `_parse_trigger` directly.

**NOT in scope** (covered by TASK-690):
- Integration tests via `_load_scheduled_queries` / `_load_cache_refresh_jobs`.
- Any change to the loader methods themselves.
- Any change to `attributes.scheduler` schema documentation in `docs/`.
- Migration of existing rows from `cron`/`crontab` to the new shorthands.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | Add `from datetime import datetime, timedelta`; add `_biweekly_anchor` helper near top of file (after imports, before class); extend `_parse_trigger` with five new `elif` branches |
| `tests/scheduler/test_parse_trigger_shorthands.py` | CREATE | Unit tests per spec §4 Unit Tests table |
| `tests/scheduler/__init__.py` | CREATE (if missing) | Empty marker file so pytest discovers the new subpackage |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: Use these exact imports and signatures. Do not invent.

### Verified Imports

```python
# Already imported at querysource/scheduler/scheduler.py:24-25 — DO NOT re-import:
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Already imported at querysource/scheduler/scheduler.py:30-35:
from querysource.conf import (
    QS_SCHEDULER_TIMEZONE,        # str — querysource/conf.py:344
    QS_SCHEDULER_MAX_INSTANCES,
    QS_SCHEDULER_COALESCE,
    default_dsn,
)

# NEW import required by this task — must be added at the top of scheduler.py:
from datetime import datetime, timedelta
```

### Existing Signatures to Use

```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def __init__(self, loop: asyncio.AbstractEventLoop = None):  # line 53
        self.logger = logger              # line 54
        self._timezone = QS_SCHEDULER_TIMEZONE  # line 56

    def _parse_trigger(self, schedule_type: str, schedule: dict):  # line 73
        # Current body shape (lines 85-103) you are extending:
        try:
            if schedule_type == "interval":
                return IntervalTrigger(**schedule)
            elif schedule_type == "crontab":
                crontab_expr = schedule["crontab"]
                tz = schedule.get("timezone", self._timezone)
                return CronTrigger.from_crontab(crontab_expr, timezone=tz)
            elif schedule_type == "cron":
                return CronTrigger(**schedule)
            # ← NEW elif branches go HERE, before the else.
            else:
                self.logger.error(
                    f"Unknown schedule_type '{schedule_type}' — skipping"
                )
                return None
        except Exception as exc:
            self.logger.error(
                f"Failed to parse trigger (type={schedule_type}): {exc}"
            )
            return None
```

### APScheduler API contract

```python
# CronTrigger(year=None, month=None, day=None, week=None, day_of_week=None,
#             hour=None, minute=None, second=None, start_date=None,
#             end_date=None, timezone=None, jitter=None)
#   - day_of_week accepts "mon".."sun" OR 0-6 (mon=0) OR "*"
#   - week="*/2" fires on every other ISO week
#   - day = day of month (1-31)

# IntervalTrigger(weeks=0, days=0, hours=0, minutes=0, seconds=0,
#                 start_date=None, end_date=None, timezone=None, jitter=None)
#   - weeks=2 + start_date=<datetime> gives precise every-14-days cadence
```

### Does NOT Exist

- ~~`querysource.scheduler.triggers`~~ — module does not exist; this task
  does NOT create one. All shorthand logic stays inline inside
  `_parse_trigger`. The only new helper is `_biweekly_anchor` and it lives
  inside `scheduler.py`.
- ~~`CronTrigger.biweekly(...)`~~ — no such classmethod. Compose biweekly
  from `CronTrigger(week="*/2", ...)` or `IntervalTrigger(weeks=2, ...)`.
- ~~`CronTrigger.from_shorthand(...)`~~ — does not exist. Only
  `CronTrigger.from_crontab(expr, timezone=...)` is provided.
- ~~`QSScheduler.add_shorthand_trigger(...)`~~ — no such method; do NOT
  invent a public API for shorthand registration.
- ~~A Pydantic model named `ScheduleConfig` / `ShorthandSchedule` in
  `querysource/scheduler/`~~ — none exists; do NOT introduce one.
  Validation is deliberately loose ("fail loud via KeyError, log, skip").

---

## Implementation Notes

### Pattern to Follow

Replicate the existing `crontab` branch's timezone resolution and one-liner
construction. Example for `daily`:

```python
elif schedule_type == "daily":
    tz = schedule.get("timezone", self._timezone)
    return CronTrigger(
        hour=schedule["hour"],
        minute=schedule["minute"],
        timezone=tz,
    )
```

`monthly` uses APScheduler's `day` kwarg (day-of-month), not
`day_of_month`:

```python
elif schedule_type == "monthly":
    tz = schedule.get("timezone", self._timezone)
    return CronTrigger(
        day=schedule["day"],
        hour=schedule["hour"],
        minute=schedule["minute"],
        timezone=tz,
    )
```

### Biweekly branch

```python
elif schedule_type == "biweekly":
    tz = schedule.get("timezone", self._timezone)
    day_of_week = schedule["day_of_week"]
    hour = schedule["hour"]
    minute = schedule["minute"]
    start_date = schedule.get("start_date")
    if start_date is not None:
        anchor = _biweekly_anchor(start_date, day_of_week, hour, minute)
        return IntervalTrigger(
            weeks=2,
            start_date=anchor,
            timezone=tz,
        )
    return CronTrigger(
        week="*/2",
        day_of_week=day_of_week,
        hour=hour,
        minute=minute,
        timezone=tz,
    )
```

### `_biweekly_anchor` helper (module-level, not a method)

```python
_DOW_TO_INT = {"mon": 0, "tue": 1, "wed": 2, "thu": 3,
               "fri": 4, "sat": 5, "sun": 6}


def _biweekly_anchor(start_date, day_of_week, hour, minute):
    """Return a datetime anchored on the requested day-of-week at hour:minute,
    rolling forward from start_date until that weekday is reached.
    """
    if isinstance(start_date, str):
        anchor = datetime.fromisoformat(start_date)
    elif isinstance(start_date, datetime):
        anchor = start_date
    else:
        raise TypeError(
            f"biweekly start_date must be str or datetime, got {type(start_date).__name__}"
        )
    anchor = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if isinstance(day_of_week, str):
        target_dow = _DOW_TO_INT[day_of_week.lower()]
    else:
        target_dow = int(day_of_week)
    while anchor.weekday() != target_dow:
        anchor = anchor + timedelta(days=1)
    return anchor
```

### Key Constraints

- Do NOT add a separate try/except per shorthand. The outer
  `except Exception` at scheduler.py:99-103 already does the right thing.
  Missing keys (`KeyError`), out-of-range values (`ValueError` from
  APScheduler), and bad day-of-week strings (`KeyError` from
  `_DOW_TO_INT`) all funnel through that block.
- Do NOT call `.lower()` on `day_of_week` when passing it to
  `CronTrigger` — APScheduler accepts both `"mon"` and `"MON"`; only the
  `_DOW_TO_INT` lookup in `_biweekly_anchor` lowercases (to be permissive
  for the anchor calculation).
- Preserve method signature of `_parse_trigger` exactly. Only the body
  changes.
- Keep order of `elif` branches stable: `interval`, `crontab`, `cron`,
  `hourly`, `daily`, `weekly`, `monthly`, `biweekly`, then `else`.

### References in Codebase

- `querysource/scheduler/scheduler.py:85-103` — `_parse_trigger` body to
  extend.
- `querysource/scheduler/scheduler.py:24-25` — APScheduler trigger imports
  (already present, do not duplicate).
- `querysource/conf.py:344` — `QS_SCHEDULER_TIMEZONE` default that
  `self._timezone` resolves to.

---

## Acceptance Criteria

- [ ] `_parse_trigger("hourly", {"minute": 15})` returns a `CronTrigger`
      whose `minute` field expression equals `"15"`.
- [ ] `_parse_trigger("daily", {"hour": 9, "minute": 30})` returns a
      `CronTrigger` that fires daily at 09:30 in `self._timezone`.
- [ ] `_parse_trigger("weekly", {"day_of_week": "mon", "hour": 9, "minute": 0})`
      returns a `CronTrigger` whose next fire time is a Monday at 09:00.
- [ ] `_parse_trigger("weekly", {"day_of_week": 0, "hour": 9, "minute": 0})`
      (int form) returns an equivalent CronTrigger.
- [ ] `_parse_trigger("monthly", {"day": 1, "hour": 0, "minute": 0})`
      returns a `CronTrigger` that fires on day 1 at 00:00.
- [ ] `_parse_trigger("biweekly", {"day_of_week": "mon", "hour": 9, "minute": 0, "start_date": "2026-06-01"})`
      returns an `IntervalTrigger` whose `interval == timedelta(weeks=2)`.
- [ ] The biweekly anchor returned by `_biweekly_anchor("2026-06-03", "mon", 9, 0)`
      (a Wednesday) equals `datetime(2026, 6, 8, 9, 0)` (next Monday at 09:00).
- [ ] `_parse_trigger("biweekly", {"day_of_week": "mon", "hour": 9, "minute": 0})`
      (no `start_date`) returns a `CronTrigger` whose `week` field expression
      contains `*/2`.
- [ ] Missing required keys (`hourly` without `minute`, `daily` without
      `hour`, `weekly` without `day_of_week`, `monthly` without `day`,
      `biweekly` without any of `day_of_week`/`hour`/`minute`) all return
      `None` and log an ERROR via `self.logger.error`.
- [ ] Out-of-range values (`minute=60`, `day=32`,
      `day_of_week="funday"`) return `None`.
- [ ] `timezone` defaulting works: omitting `timezone` yields a trigger
      built with `self._timezone`; providing `timezone="America/New_York"`
      overrides it.
- [ ] **Regression**: `_parse_trigger("cron", {...})`,
      `_parse_trigger("interval", {...})`, and
      `_parse_trigger("crontab", {"crontab": "*/5 * * * *"})` are unchanged
      and pass byte-equivalent triggers compared to pre-task behavior.
- [ ] Unknown `schedule_type` (e.g. `"yearly"`) still hits the existing
      `else` branch, logs the "Unknown schedule_type" error, returns `None`.
- [ ] All 19 unit tests pass: `pytest tests/scheduler/test_parse_trigger_shorthands.py -v`.
- [ ] `ruff check querysource/scheduler/scheduler.py` returns no new errors.
- [ ] `mypy querysource/scheduler/scheduler.py` returns no new errors (if
      mypy is part of the project's lint suite — otherwise skip).

---

## Test Specification

```python
# tests/scheduler/test_parse_trigger_shorthands.py
from datetime import datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from querysource.scheduler.scheduler import QSScheduler, _biweekly_anchor


@pytest.fixture
def scheduler():
    """Minimal QSScheduler — does NOT start the scheduler or DB pool."""
    return QSScheduler(loop=None)


# ----- hourly ---------------------------------------------------------------

def test_hourly_minute_only(scheduler):
    trig = scheduler._parse_trigger("hourly", {"minute": 15})
    assert isinstance(trig, CronTrigger)
    minute_field = next(f for f in trig.fields if f.name == "minute")
    assert str(minute_field) == "15"


def test_hourly_missing_minute_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("hourly", {}) is None
    assert any("Failed to parse trigger" in r.message for r in caplog.records)


def test_hourly_out_of_range_minute_returns_none(scheduler):
    assert scheduler._parse_trigger("hourly", {"minute": 60}) is None


# ----- daily ----------------------------------------------------------------

def test_daily_hour_minute(scheduler):
    trig = scheduler._parse_trigger("daily", {"hour": 9, "minute": 30})
    assert isinstance(trig, CronTrigger)


def test_daily_missing_hour_returns_none(scheduler):
    assert scheduler._parse_trigger("daily", {"minute": 0}) is None


# ----- weekly ---------------------------------------------------------------

def test_weekly_mon_9am(scheduler):
    trig = scheduler._parse_trigger(
        "weekly", {"day_of_week": "mon", "hour": 9, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_weekly_int_day_of_week(scheduler):
    trig = scheduler._parse_trigger(
        "weekly", {"day_of_week": 0, "hour": 9, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_weekly_invalid_day_of_week(scheduler):
    assert (
        scheduler._parse_trigger(
            "weekly", {"day_of_week": "funday", "hour": 9, "minute": 0}
        )
        is None
    )


# ----- monthly --------------------------------------------------------------

def test_monthly_day_1_midnight(scheduler):
    trig = scheduler._parse_trigger(
        "monthly", {"day": 1, "hour": 0, "minute": 0}
    )
    assert isinstance(trig, CronTrigger)


def test_monthly_day_out_of_range_returns_none(scheduler):
    assert (
        scheduler._parse_trigger(
            "monthly", {"day": 32, "hour": 0, "minute": 0}
        )
        is None
    )


# ----- biweekly -------------------------------------------------------------

def test_biweekly_with_start_date_uses_interval(scheduler):
    trig = scheduler._parse_trigger(
        "biweekly",
        {
            "day_of_week": "mon",
            "hour": 9,
            "minute": 0,
            "start_date": "2026-06-01",
        },
    )
    assert isinstance(trig, IntervalTrigger)
    assert trig.interval == timedelta(weeks=2)


def test_biweekly_without_start_date_uses_cron_week_step(scheduler):
    trig = scheduler._parse_trigger(
        "biweekly",
        {"day_of_week": "mon", "hour": 9, "minute": 0},
    )
    assert isinstance(trig, CronTrigger)
    week_field = next(f for f in trig.fields if f.name == "week")
    assert "*/2" in str(week_field)


def test_biweekly_anchor_date_aligns_to_day_of_week():
    # 2026-06-03 is a Wednesday; the next Monday is 2026-06-08.
    anchor = _biweekly_anchor("2026-06-03", "mon", 9, 0)
    assert anchor == datetime(2026, 6, 8, 9, 0)


# ----- timezone -------------------------------------------------------------

def test_timezone_fallback(scheduler):
    trig = scheduler._parse_trigger("daily", {"hour": 9, "minute": 0})
    # We only assert the trigger was built; deep tz introspection is brittle.
    assert isinstance(trig, CronTrigger)


def test_timezone_override(scheduler):
    trig = scheduler._parse_trigger(
        "daily", {"hour": 9, "minute": 0, "timezone": "America/New_York"}
    )
    assert isinstance(trig, CronTrigger)


# ----- regressions on the legacy paths --------------------------------------

def test_existing_cron_path_unchanged(scheduler):
    trig = scheduler._parse_trigger(
        "cron", {"hour": "*/2", "minute": 27}
    )
    assert isinstance(trig, CronTrigger)


def test_existing_interval_path_unchanged(scheduler):
    trig = scheduler._parse_trigger("interval", {"minutes": 30})
    assert isinstance(trig, IntervalTrigger)


def test_existing_crontab_path_unchanged(scheduler):
    trig = scheduler._parse_trigger("crontab", {"crontab": "*/5 * * * *"})
    assert isinstance(trig, CronTrigger)


# ----- unknown --------------------------------------------------------------

def test_unknown_schedule_type_returns_none(scheduler, caplog):
    assert scheduler._parse_trigger("yearly", {}) is None
    assert any("Unknown schedule_type" in r.message for r in caplog.records)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qsscheduler-add-variants.spec.md`.
2. **Dependencies**: none — this task can start immediately.
3. **Verify the Codebase Contract** (before writing code):
   - Open `querysource/scheduler/scheduler.py` and confirm the `_parse_trigger`
     body still matches the shape shown in "Existing Signatures to Use".
   - Confirm `CronTrigger`/`IntervalTrigger` are still imported at lines 24-25.
   - If `_parse_trigger`'s body has changed, update this contract first, then
     adapt the implementation.
4. **Implement** following the scope and pattern.
5. **Run the tests** locally: `source .venv/bin/activate && pytest tests/scheduler/test_parse_trigger_shorthands.py -v`.
6. **Lint**: `ruff check querysource/scheduler/scheduler.py`.
7. **Verify** all acceptance criteria are met.
8. **Move this file** to `sdd/tasks/completed/TASK-689-parse-trigger-shorthand-branches.md`.
9. **Update** `sdd/tasks/index/qsscheduler-add-variants.json` → set
   `status: "done"`, fill `completed_at`.
10. **Fill in** the Completion Note below.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-05-23
**Notes**: All 19 unit tests pass. Added `from datetime import datetime, timedelta` import,
`_DOW_TO_INT` dict and `_biweekly_anchor` module-level helper, and five new elif branches
(hourly, daily, weekly, monthly, biweekly) in `_parse_trigger`. ruff returns no errors.

**Deviations from spec**: none
