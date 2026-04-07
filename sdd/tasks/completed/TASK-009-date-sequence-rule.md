# TASK-009: Date Sequence Rule Handler

**Feature**: GenData Component
**Spec**: `sdd/specs/gendata-component.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements the foundational rule handler for the `date_sequence` rule type (Module 2 from the spec). It is the core logic that GenData delegates to — generating a pandas Series of dates from `firstdate` to `lastdate` with configurable `interval` and optional `dow` (day-of-week) alignment.

This must be completed before the GenData component (TASK-010) can assemble DataFrames.

---

## Scope

- Create the `flowtask/components/gendata/` subpackage with `__init__.py` and `date_sequence.py`.
- Implement the `DateSequenceRule` Pydantic model for validating rule configuration.
- Implement the `generate_date_sequence()` function that:
  - Accepts validated rule parameters.
  - Optionally aligns the start date to the specified `dow` (0=Monday..6=Sunday).
  - Generates dates from the (possibly aligned) start to `lastdate` stepping by `interval` days.
  - Supports a `date_format` parameter for output formatting (per open question resolution).
  - Returns a `pd.Series` of formatted date strings (or date objects if no format specified).
- Implement `align_to_dow(start, dow)` helper function.
- Create a base `AbstractRule` or protocol so future rule types follow the same interface.
- Write unit tests for the date sequence logic in `tests/test_gendata_rules.py`.

**NOT in scope**: The GenData FlowComponent itself (TASK-010), integration with FlowComponent pipeline variables.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/components/gendata/__init__.py` | CREATE | Package init, exports rule registry |
| `flowtask/components/gendata/date_sequence.py` | CREATE | DateSequenceRule model + generate function |
| `tests/test_gendata_rules.py` | CREATE | Unit tests for date_sequence rule |

---

## Implementation Notes

### Pattern to Follow
```python
from datetime import date, timedelta
from typing import Optional
import pandas as pd
from pydantic import BaseModel, Field


class DateSequenceRule(BaseModel):
    """Rule for generating a sequence of dates."""
    type: str = Field("date_sequence", description="Rule type identifier")
    column_name: str = Field(..., description="Name of the output column")
    firstdate: date = Field(..., description="Start of date range")
    lastdate: date = Field(..., description="End of date range")
    interval: int = Field(default=1, ge=1, description="Step in days")
    dow: Optional[int] = Field(default=None, ge=0, le=6)
    date_format: Optional[str] = Field(default=None, description="strftime format for output")


def align_to_dow(start: date, dow: int) -> date:
    """Shift start forward to the first occurrence of the target weekday."""
    days_ahead = dow - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


def generate_date_sequence(rule: DateSequenceRule) -> pd.Series:
    """Generate a Series of dates per the rule configuration."""
    start = rule.firstdate
    if rule.dow is not None:
        start = align_to_dow(start, rule.dow)
    dates = []
    current = start
    while current <= rule.lastdate:
        dates.append(current)
        current += timedelta(days=rule.interval)
    series = pd.Series(dates, name=rule.column_name)
    if rule.date_format:
        series = series.apply(lambda d: d.strftime(rule.date_format))
    return series
```

### Key Constraints
- `dow` uses Python convention: 0=Monday, 6=Sunday.
- If `dow` alignment pushes start past `lastdate`, return an empty Series.
- Accept both `str` and `date` objects for `firstdate`/`lastdate` via Pydantic validators.
- The `date_format` parameter controls output formatting (resolved open question).

### References in Codebase
- `flowtask/components/DateList.py` — similar date range logic, use as reference
- `flowtask/components/flow.py` — FlowComponent base class (for TASK-010)

---

## Acceptance Criteria

- [ ] `DateSequenceRule` model validates correct and rejects invalid configs
- [ ] `generate_date_sequence()` produces correct daily sequences
- [ ] `generate_date_sequence()` with `interval=7` produces weekly dates
- [ ] `dow` alignment shifts to correct weekday
- [ ] `dow` on matching day does not shift
- [ ] `date_format` parameter formats output correctly
- [ ] Empty result when range too short for dow alignment
- [ ] All tests pass: `pytest tests/test_gendata_rules.py -v`

---

## Test Specification

```python
# tests/test_gendata_rules.py
import pytest
from datetime import date
import pandas as pd
from flowtask.components.gendata.date_sequence import (
    DateSequenceRule,
    align_to_dow,
    generate_date_sequence,
)


class TestAlignToDow:
    def test_align_monday_from_wednesday(self):
        """2024-01-03 (Wed) -> 2024-01-08 (Mon)."""
        result = align_to_dow(date(2024, 1, 3), 0)
        assert result == date(2024, 1, 8)
        assert result.weekday() == 0

    def test_align_already_on_target(self):
        """2024-01-01 (Mon) stays 2024-01-01."""
        result = align_to_dow(date(2024, 1, 1), 0)
        assert result == date(2024, 1, 1)

    def test_align_all_days(self):
        """Each dow=0..6 produces correct weekday."""
        base = date(2024, 1, 1)  # Monday
        for dow in range(7):
            result = align_to_dow(base, dow)
            assert result.weekday() == dow


class TestDateSequenceRule:
    def test_basic_daily(self):
        rule = DateSequenceRule(
            column_name="daily", firstdate=date(2024, 1, 1),
            lastdate=date(2024, 1, 10), interval=1,
        )
        series = generate_date_sequence(rule)
        assert len(series) == 10
        assert series.name == "daily"

    def test_weekly_interval(self):
        rule = DateSequenceRule(
            column_name="weekly", firstdate=date(2024, 1, 1),
            lastdate=date(2024, 1, 31), interval=7,
        )
        series = generate_date_sequence(rule)
        assert len(series) == 5  # Jan 1, 8, 15, 22, 29

    def test_dow_alignment(self):
        rule = DateSequenceRule(
            column_name="mondays", firstdate=date(2024, 1, 1),
            lastdate=date(2024, 3, 31), interval=7, dow=0,
        )
        series = generate_date_sequence(rule)
        for d in series:
            assert d.weekday() == 0

    def test_empty_when_range_too_short(self):
        rule = DateSequenceRule(
            column_name="x", firstdate=date(2024, 1, 1),
            lastdate=date(2024, 1, 2), interval=7, dow=5,  # Friday
        )
        series = generate_date_sequence(rule)
        assert len(series) == 0

    def test_date_format(self):
        rule = DateSequenceRule(
            column_name="formatted", firstdate=date(2024, 1, 1),
            lastdate=date(2024, 1, 3), interval=1,
            date_format="%Y-%m-%d",
        )
        series = generate_date_sequence(rule)
        assert series.iloc[0] == "2024-01-01"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/gendata-component.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-009-date-sequence-rule.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
