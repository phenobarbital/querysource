---
type: feature
base_branch: dev
---
# Feature Specification: QSScheduler Shorthand Variants (hourly / daily / weekly / monthly / biweekly)

**Feature ID**: FEAT-098
**Date**: 2026-05-23
**Author**: Jesus Lara
**Status**: approved
**Target version**: 5.x

---

## 1. Motivation & Business Requirements

### Problem Statement

QSScheduler today (FEAT-008) only accepts three `schedule_type` values: `cron`,
`crontab`, and `interval`. All three require the author of the query to know
APScheduler trigger semantics — for example, "every Monday at 09:00" must be
encoded as either:

```jsonc
{"schedule_type": "cron",    "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0}}
{"schedule_type": "crontab", "schedule": {"crontab": "0 9 * * 1"}}
```

This is verbose, error-prone, and noisy for the dominant scheduling cases.
The majority of scheduled queries fall into five intuitive cadences — hourly,
daily, weekly, monthly, and every-other-week — and should be expressible with a
small, named vocabulary that asks only for the variable parts of each cadence.

### Goals
- Add five new `schedule_type` values to `_parse_trigger`:
  `hourly`, `daily`, `weekly`, `monthly`, `biweekly`.
- Each shorthand exposes only the parameters that vary for its cadence
  (e.g. `daily` asks for `hour` + `minute`, not for `day_of_week`).
- Existing `cron` / `crontab` / `interval` continue to work unchanged —
  shorthands are additive.
- Invalid / missing required params for a shorthand follow the existing
  error path: log at ERROR and skip the job (preserving load-order tolerance
  for all other jobs).

### Non-Goals (explicitly out of scope)
- Runtime add/remove/reschedule API (still v2 of FEAT-008).
- New persistent jobstore — still `MemoryJobStore`.
- Migration of existing rows: this spec does not rewrite any stored
  `attributes.scheduler` or `cache_options` to the new shorthands.
- A separate `triggers.py` module: the shorthand expansion lives inside
  `_parse_trigger` per §3 of the clarifying questions.
- New shorthand for sub-minute cadences (use `interval` for those).

---

## 2. Architectural Design

### Overview

All five shorthands are translated into APScheduler triggers inside the
existing `QSScheduler._parse_trigger` method (`querysource/scheduler/scheduler.py:73`).
Four of the five (`hourly`, `daily`, `weekly`, `monthly`) compile to a single
`CronTrigger(**kwargs)`. The fifth (`biweekly`) has two compilation modes:

- If the user supplies a `start_date`, it compiles to
  `IntervalTrigger(weeks=2, start_date=..., ...)` for precise every-14-days
  cadence anchored to that date.
- If no `start_date` is supplied, it compiles to `CronTrigger(week='*/2', ...)`
  which fires on alternating ISO weeks based on calendar parity.

The router stays in the same `if/elif` chain so the diff is local and small.
The `schedule` dict passed in is **not** treated as a raw kwarg bag for
shorthands — it is validated and re-mapped to APScheduler trigger kwargs.

### Component Diagram

```
QSScheduler._parse_trigger(schedule_type, schedule)
    │
    ├── "interval"   ──→ IntervalTrigger(**schedule)                   [existing]
    ├── "crontab"    ──→ CronTrigger.from_crontab(schedule["crontab"]) [existing]
    ├── "cron"       ──→ CronTrigger(**schedule)                       [existing]
    │
    ├── "hourly"     ──→ CronTrigger(minute=M, timezone=tz)            [NEW]
    ├── "daily"      ──→ CronTrigger(hour=H, minute=M, timezone=tz)    [NEW]
    ├── "weekly"     ──→ CronTrigger(day_of_week=D, hour=H, minute=M)  [NEW]
    ├── "monthly"    ──→ CronTrigger(day=D, hour=H, minute=M)          [NEW]
    └── "biweekly"   ──→ branch:                                       [NEW]
                          ├── start_date present:
                          │     IntervalTrigger(weeks=2, start_date=...,
                          │                     start_date carries hour/min via
                          │                     a datetime built from the inputs)
                          └── start_date absent:
                                CronTrigger(week='*/2',
                                            day_of_week=D, hour=H, minute=M)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/scheduler/scheduler.py:QSScheduler._parse_trigger` | extends | Adds 5 new `elif` branches and a helper to validate shorthand kwargs |
| `querysource/scheduler/scheduler.py:QSScheduler._load_scheduled_queries` | depends on (no changes) | Calls `_parse_trigger` — receives the trigger as before |
| `querysource/scheduler/scheduler.py:QSScheduler._load_cache_refresh_jobs` | depends on (no changes) | Same router → same benefit for `cache_options.schedule_type` |
| `querysource/conf.py:QS_SCHEDULER_TIMEZONE` | uses | Default timezone applied when shorthand omits an explicit `timezone` |
| `APScheduler.triggers.cron.CronTrigger` | uses | Already imported at scheduler.py:24 |
| `APScheduler.triggers.interval.IntervalTrigger` | uses | Already imported at scheduler.py:25 |

### Data Models

Shorthand schedule definitions live in the same JSON columns as the existing
schedules (`public.queries.attributes.scheduler` for query jobs;
`public.queries.cache_options` for cache-refresh jobs). The new shapes are:

```jsonc
// hourly — required: minute (0-59)
{"schedule_type": "hourly",  "schedule": {"minute": 15}}
// timezone is optional in all shorthands; falls back to QS_SCHEDULER_TIMEZONE.

// daily — required: hour, minute
{"schedule_type": "daily",   "schedule": {"hour": 9, "minute": 30}}

// weekly — required: day_of_week, hour, minute
// day_of_week: APScheduler string ("mon".."sun") OR int 0-6 (mon=0)
{"schedule_type": "weekly",  "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0}}

// monthly — required: day (day of month, 1-31), hour, minute
{"schedule_type": "monthly", "schedule": {"day": 1, "hour": 0, "minute": 0}}

// biweekly — required: day_of_week, hour, minute; optional: start_date
// start_date format: "YYYY-MM-DD" (or full ISO 8601 with hour/min)
{"schedule_type": "biweekly", "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0}}
{"schedule_type": "biweekly", "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0, "start_date": "2026-06-01"}}
```

**Parameter map (shorthand → APScheduler trigger):**

| `schedule_type` | Required fields | Optional | APScheduler call |
|---|---|---|---|
| `hourly` | `minute` (int 0-59) | `timezone` | `CronTrigger(minute=M, timezone=tz)` |
| `daily` | `hour`, `minute` | `timezone` | `CronTrigger(hour=H, minute=M, timezone=tz)` |
| `weekly` | `day_of_week`, `hour`, `minute` | `timezone` | `CronTrigger(day_of_week=D, hour=H, minute=M, timezone=tz)` |
| `monthly` | `day`, `hour`, `minute` | `timezone` | `CronTrigger(day=D, hour=H, minute=M, timezone=tz)` |
| `biweekly` | `day_of_week`, `hour`, `minute` | `start_date`, `timezone` | If `start_date`: `IntervalTrigger(weeks=2, start_date=anchor_dt, timezone=tz)`. Else: `CronTrigger(week='*/2', day_of_week=D, hour=H, minute=M, timezone=tz)` |

### New Public Interfaces

No new public class is exposed. The contract change is purely in the accepted
values for the existing `schedule_type` JSON field consumed by
`QSScheduler._parse_trigger`. The method signature is unchanged:

```python
def _parse_trigger(self, schedule_type: str, schedule: dict):
    """Parse a schedule definition into an APScheduler trigger.

    Args:
        schedule_type: One of 'cron', 'crontab', 'interval',
                       'hourly', 'daily', 'weekly', 'monthly', 'biweekly'.
        schedule: Trigger-specific kwargs (shape per §2 Data Models).

    Returns:
        An APScheduler trigger instance, or None if parsing fails.
    """
```

---

## 3. Module Breakdown

### Module 1: Shorthand Parsing
- **Path**: `querysource/scheduler/scheduler.py` (modify existing)
- **Responsibility**:
  - Extend the `if/elif` chain inside `_parse_trigger` (currently lines 86-98)
    with five new branches: `hourly`, `daily`, `weekly`, `monthly`, `biweekly`.
  - For each shorthand, validate that required fields are present and within
    range; on any failure, fall through to the existing
    `except Exception` block, which logs an ERROR and returns `None`.
  - Resolve `timezone`: use `schedule.get("timezone", self._timezone)` (same
    pattern as the existing `crontab` branch).
  - For `biweekly`, branch on `start_date` presence:
    - With `start_date`: parse to `datetime` with `hour`/`minute`/`day_of_week`
      respected (the anchor date is moved forward to the next matching
      `day_of_week` at `hour:minute` if it does not already land there), then
      build `IntervalTrigger(weeks=2, start_date=anchor_dt, timezone=tz)`.
    - Without `start_date`: build
      `CronTrigger(week='*/2', day_of_week=D, hour=H, minute=M, timezone=tz)`.
- **Depends on**: `apscheduler.triggers.cron.CronTrigger`,
  `apscheduler.triggers.interval.IntervalTrigger` (both already imported).

### Module 2: Tests
- **Path**: `tests/scheduler/test_parse_trigger_shorthands.py` (new file).
- **Responsibility**: Cover the parser table per §4. Tests must not require
  a running scheduler or DB — they instantiate `QSScheduler` and call
  `_parse_trigger` directly, asserting on the returned trigger type and its
  computed `next_fire_time` / `fields`.
- **Depends on**: pytest, Module 1.

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_hourly_minute_only` | Module 2 | `{"schedule_type":"hourly","schedule":{"minute":15}}` returns `CronTrigger` whose `minute` field expands to `15` |
| `test_hourly_missing_minute_returns_none` | Module 2 | `{"schedule_type":"hourly","schedule":{}}` logs ERROR, returns `None` |
| `test_hourly_out_of_range_minute_returns_none` | Module 2 | `minute=60` triggers APScheduler validation error → returns `None` |
| `test_daily_hour_minute` | Module 2 | `{"schedule_type":"daily","schedule":{"hour":9,"minute":30}}` returns `CronTrigger` firing daily at 09:30 |
| `test_daily_missing_hour_returns_none` | Module 2 | Missing `hour` → logged ERROR, `None` |
| `test_weekly_mon_9am` | Module 2 | `{"day_of_week":"mon","hour":9,"minute":0}` returns CronTrigger; next fire time is a Monday at 09:00 |
| `test_weekly_int_day_of_week` | Module 2 | `day_of_week=0` (int form) is accepted and behaves identically to `"mon"` |
| `test_weekly_invalid_day_of_week` | Module 2 | `day_of_week="funday"` → APScheduler raises → returns `None` |
| `test_monthly_day_1_midnight` | Module 2 | `{"day":1,"hour":0,"minute":0}` returns CronTrigger; next fire time is the 1st of next month at 00:00 |
| `test_monthly_day_out_of_range_returns_none` | Module 2 | `day=32` returns `None` |
| `test_biweekly_with_start_date_uses_interval` | Module 2 | `{"day_of_week":"mon","hour":9,"minute":0,"start_date":"2026-06-01"}` returns an `IntervalTrigger` with `interval == timedelta(weeks=2)` |
| `test_biweekly_without_start_date_uses_cron_week_step` | Module 2 | Without `start_date` returns a `CronTrigger` whose `week` field expression contains `*/2` |
| `test_biweekly_anchor_date_aligns_to_day_of_week` | Module 2 | If `start_date` is a Wednesday but `day_of_week="mon"`, the anchor used for IntervalTrigger is the next Monday at the requested hour:minute |
| `test_timezone_fallback` | Module 2 | Omitting `timezone` causes the trigger to be built with `self._timezone` |
| `test_timezone_override` | Module 2 | Providing `timezone="America/New_York"` overrides the default |
| `test_existing_cron_path_unchanged` | Module 2 | Regression: `schedule_type="cron"` still routes to `CronTrigger(**schedule)` and is unaffected by new code |
| `test_existing_interval_path_unchanged` | Module 2 | Regression: `schedule_type="interval"` still routes to `IntervalTrigger(**schedule)` |
| `test_existing_crontab_path_unchanged` | Module 2 | Regression: `schedule_type="crontab"` still routes to `CronTrigger.from_crontab` |
| `test_unknown_schedule_type_returns_none` | Module 2 | An unknown value (e.g. `"yearly"`) logs ERROR and returns `None` |

### Integration Tests

| Test | Description |
|---|---|
| `test_load_scheduled_queries_with_hourly_shorthand` | A query row whose `attributes.scheduler` uses `hourly` is registered as a job with the correct slug-derived id |
| `test_load_cache_refresh_with_daily_shorthand` | A row with `cache_options.schedule_type=="daily"`, `is_cached=True` registers a `cache_<slug>` job |
| `test_mixed_shorthand_and_legacy_load_together` | A startup with rows using both legacy `cron` and new `biweekly` succeeds; both jobs are registered |
| `test_invalid_shorthand_skips_job_but_loads_others` | One row has `monthly` with missing `day`; loader logs ERROR for that row and still registers other valid rows (count assertion) |

### Test Data / Fixtures

```python
@pytest.fixture
def scheduler():
    """Minimal QSScheduler instance — does NOT start the scheduler or DB pool."""
    from querysource.scheduler.scheduler import QSScheduler
    return QSScheduler(loop=None)


@pytest.fixture
def hourly_def():
    return {"schedule_type": "hourly", "schedule": {"minute": 15}}


@pytest.fixture
def daily_def():
    return {"schedule_type": "daily", "schedule": {"hour": 9, "minute": 30}}


@pytest.fixture
def weekly_def():
    return {
        "schedule_type": "weekly",
        "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
    }


@pytest.fixture
def monthly_def():
    return {
        "schedule_type": "monthly",
        "schedule": {"day": 1, "hour": 0, "minute": 0},
    }


@pytest.fixture
def biweekly_anchored_def():
    return {
        "schedule_type": "biweekly",
        "schedule": {
            "day_of_week": "mon",
            "hour": 9,
            "minute": 0,
            "start_date": "2026-06-01",
        },
    }


@pytest.fixture
def biweekly_unanchored_def():
    return {
        "schedule_type": "biweekly",
        "schedule": {"day_of_week": "mon", "hour": 9, "minute": 0},
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `_parse_trigger` accepts `schedule_type` values `hourly`, `daily`,
      `weekly`, `monthly`, `biweekly` and returns a valid APScheduler trigger
      for each well-formed payload.
- [ ] `hourly` requires `minute` and rejects payloads missing it (returns
      `None`, logs ERROR).
- [ ] `daily` requires `hour` and `minute`; `weekly` requires
      `day_of_week`, `hour`, `minute`; `monthly` requires `day`, `hour`,
      `minute`. Missing required params on any of these returns `None` and
      logs ERROR.
- [ ] `biweekly` with a `start_date` field returns an `IntervalTrigger` with
      a 2-week interval whose anchor matches the requested `day_of_week` and
      `hour`/`minute` (the anchor is rolled forward from the given
      `start_date` if needed).
- [ ] `biweekly` without `start_date` returns a `CronTrigger` whose `week`
      field contains the step expression `*/2`.
- [ ] When `timezone` is omitted from any shorthand, the trigger uses
      `self._timezone` (the QSScheduler default — see
      `QS_SCHEDULER_TIMEZONE` in `querysource/conf.py:344`).
- [ ] Existing `cron`, `crontab`, and `interval` paths produce
      byte-identical triggers to the pre-change behavior (regression tests
      pass unchanged).
- [ ] All new unit tests pass (`pytest tests/scheduler/test_parse_trigger_shorthands.py -v`).
- [ ] All existing scheduler tests still pass.
- [ ] No new external dependencies are added to `pyproject.toml`.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# Already imported at querysource/scheduler/scheduler.py:24-25 — DO NOT re-import.
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Already imported at querysource/scheduler/scheduler.py:30-35.
from querysource.conf import (
    QS_SCHEDULER_TIMEZONE,   # str — querysource/conf.py:344
    QS_SCHEDULER_MAX_INSTANCES,
    QS_SCHEDULER_COALESCE,
    default_dsn,
)

# Stdlib — required for the biweekly anchor calculation. NOT yet imported in
# scheduler.py; the implementer must add the import.
from datetime import datetime, timedelta
```

### Existing Class Signatures

```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def __init__(self, loop: asyncio.AbstractEventLoop = None):  # line 53
        self._timezone = QS_SCHEDULER_TIMEZONE                   # line 56

    def _parse_trigger(self, schedule_type: str, schedule: dict):  # line 73
        # Current chain (lines 86-103):
        #   if schedule_type == "interval":  return IntervalTrigger(**schedule)
        #   elif schedule_type == "crontab": return CronTrigger.from_crontab(...)
        #   elif schedule_type == "cron":    return CronTrigger(**schedule)
        #   else: logger.error("Unknown schedule_type ..."); return None
        # The try/except at line 99-103 catches everything and logs.
        ...

    def _load_scheduled_queries(self, rows: list) -> int:  # line 105
        # Calls self._parse_trigger(schedule_type, schedule) at line 132.
        # No changes needed here.
        ...

    def _load_cache_refresh_jobs(self, rows: list) -> int:  # line 196
        # Calls self._parse_trigger(schedule_type, schedule) at line 224.
        # No changes needed here.
        ...
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| Shorthand branches | `CronTrigger(**kwargs)` | direct construction | `querysource/scheduler/scheduler.py:93` (existing cron branch shows the pattern) |
| `biweekly` (anchored) | `IntervalTrigger(weeks=2, start_date=...)` | direct construction | `querysource/scheduler/scheduler.py:87` (existing interval branch shows pattern) |
| Timezone fallback | `self._timezone` | attribute read | `querysource/scheduler/scheduler.py:90` (existing crontab branch uses `schedule.get("timezone", self._timezone)`) |
| Tests | `QSScheduler._parse_trigger` | direct call without startup | `QSScheduler.__init__` requires only an optional loop arg (line 53) |

### APScheduler API references

```python
# CronTrigger accepts these kwargs (year, month, day, week, day_of_week,
# hour, minute, second, start_date, end_date, timezone, jitter).
#   - day_of_week: "mon"-"sun" OR 0-6 (mon=0) OR "*"
#   - week step expression "*/2" fires on every other ISO week.
#   - day: day of month, 1-31.
# Reference: apscheduler/triggers/cron/__init__.py

# IntervalTrigger accepts (weeks, days, hours, minutes, seconds, start_date,
# end_date, timezone, jitter).
#   - weeks=2 + start_date gives precise every-14-days cadence.
# Reference: apscheduler/triggers/interval.py
```

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.scheduler.triggers`~~ — module does not exist; this spec
  keeps shorthand logic inside `scheduler.py`. Do not create a `triggers.py`.
- ~~`QSScheduler.add_shorthand_trigger()`~~ — no such method; shorthands are
  routed inside `_parse_trigger`.
- ~~`CronTrigger.biweekly()`~~ — no classmethod by that name in APScheduler.
  `biweekly` is composed from `CronTrigger(week='*/2', ...)` or
  `IntervalTrigger(weeks=2, ...)`.
- ~~`CronTrigger.from_shorthand()`~~ — does not exist; only
  `CronTrigger.from_crontab(expr, timezone=...)` is provided by APScheduler.
- ~~An `Enum` for `schedule_type`~~ — none exists; the field is a plain
  string compared with `==` inside `_parse_trigger`.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Mirror the existing branches in `_parse_trigger` (scheduler.py:86-98):
  same `elif` chain, same outer `try/except Exception` that logs and returns
  `None`. No new logging logic.
- Resolve `timezone` exactly like the existing `crontab` branch:
  `tz = schedule.get("timezone", self._timezone)`.
- For each new branch, pull required keys with `schedule["key"]` (raises
  `KeyError` → caught by the outer `except` → logged as ERROR → returns
  `None`). This keeps validation behavior aligned with the §3 decision:
  "log error + skip job" with no defaulting.
- For `biweekly` anchored mode, compute the anchor as follows:
  1. Parse `start_date` into a `datetime` (accept both `"YYYY-MM-DD"` and
     ISO 8601 forms via `datetime.fromisoformat`).
  2. Apply `hour=H, minute=M, second=0, microsecond=0`.
  3. While `anchor.weekday() != target_dow`, add `timedelta(days=1)`.
     (Use the numeric mapping `{"mon":0,"tue":1,...,"sun":6}` for the
     string form of `day_of_week`.)
  4. Pass the resulting `datetime` as `start_date` to `IntervalTrigger`.
- Keep `day_of_week` accepting both `"mon".."sun"` and `0-6`. APScheduler
  accepts the string form natively in CronTrigger; for the biweekly
  anchored helper, convert the string form to an int via the mapping
  above before computing the weekday rollover.
- Do not introduce a Pydantic model — JSON validation is deliberately
  loose here, mirroring the existing `_parse_trigger` contract.

### Known Risks / Gotchas

- **Biweekly cron parity is calendar-bound**: `CronTrigger(week='*/2')` fires
  on alternating ISO weeks, so the actual cadence depends on which ISO week
  the scheduler starts in. Two users who deploy the same `biweekly` (no
  `start_date`) job a week apart will see opposite phases. Mitigation:
  document the `start_date` form as the recommended way to anchor cadence,
  and note this in the row's `attributes.scheduler.comment` if precision is
  required.
- **`day` (monthly) vs `day_of_week` (weekly/biweekly) confusion**: We use
  `day` (day-of-month, 1-31) for `monthly` and `day_of_week` (string or 0-6)
  for `weekly`/`biweekly`, matching APScheduler's own kwarg naming.
  Tests must include a regression that ensures we do NOT silently accept
  `day_of_month` or `dow` as aliases.
- **Timezone-naive `start_date` for biweekly**: If the user supplies a
  naive `YYYY-MM-DD`, APScheduler interprets it in the trigger's timezone.
  We must pass `timezone=tz` to `IntervalTrigger` so the anchor is unambiguous.
- **Validation policy is "fail loud, then skip"**: The §3 decision is to NOT
  apply defaults. A `daily` payload with only `{"hour": 9}` is treated as an
  error and the job is skipped — it is NOT silently promoted to
  `{"hour": 9, "minute": 0}`. Implementers must keep this behavior even when
  it feels tempting to be lenient.
- **Existing cron/crontab/interval rows are not migrated**: this spec is
  additive only. Anyone who wants the new shorthands must edit the row
  manually.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `APScheduler` | `>=3.11.0,<3.12.0` | Already present (`querysource/scheduler/scheduler.py:21`) — no version bump |

No new dependencies. The `datetime`/`timedelta` import for the biweekly
anchor is stdlib.

---

## 8. Open Questions

- [x] What parameters should `hourly` accept? — *Resolved in clarifying
  questions*: only `minute` (int 0-59) is required; trigger is
  `CronTrigger(minute=M, timezone=tz)`. "Every N hours" remains expressible
  via the existing `cron` shorthand (`{"hour": "*/N", "minute": 0}`).
- [x] How is `biweekly` implemented? — *Resolved in clarifying questions*:
  hybrid mode — `IntervalTrigger(weeks=2, start_date=anchor)` when
  `start_date` is provided, otherwise `CronTrigger(week='*/2', ...)`.
- [x] What happens when a shorthand is missing a required parameter? —
  *Resolved in clarifying questions*: log at ERROR and return `None`,
  matching the existing `except Exception` behavior at scheduler.py:99-103.
  No defaults are applied.
- [x] Where does the shorthand expansion live? — *Resolved in clarifying
  questions*: inline `elif` branches inside `_parse_trigger` in
  `querysource/scheduler/scheduler.py`. Do not create a new module.
- [ ] Should we add a brief operator-facing doc page in `docs/` listing the
  five shorthands with examples, or is the spec itself sufficient? —
  *Owner: Jesus Lara* (deferrable to implementation).

---

## Worktree Strategy

- **Default isolation**: `per-spec` — all tasks run sequentially in one
  worktree.
- **Rationale**: The change is localized to a single method
  (`QSScheduler._parse_trigger`) plus a new test file. There is no
  parallelizable surface — adding a separate task worktree per shorthand
  would create more coordination overhead than it saves.
- **Cross-feature dependencies**: Depends on the merged state of FEAT-008
  (`querysource-scheduler.spec.md`) — confirmed merged, code is in
  `querysource/scheduler/scheduler.py` at HEAD.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-23 | Jesus Lara / Claude | Initial draft — five shorthand schedule_type values added to QSScheduler._parse_trigger. |
