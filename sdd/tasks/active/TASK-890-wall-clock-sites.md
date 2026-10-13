# TASK-890: Preserve per-value offsets in `extract` and `add_timestamp_to_time`

**Feature**: FEAT-171 — pandas 3 mixed-UTC-offset handling for the remaining datetime transforms
**Spec**: `sdd/specs/pandas3-to-datetime-mixed-offsets.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-889
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 3** (§2 per-site table rows `extract`, `add_timestamp_to_time`).
On pandas 3.0.6 with mixed-offset input, `extract` raises `ValueError` (it only catches
`TypeError`), and `add_timestamp_to_time` raises `AttributeError` (`.dt` on an object column).

Decision (spec §8 Q2): these two functions **preserve each value's own offset**. Date
parts come from each value's local wall-clock; only values with no offset are read as
UTC. They get **no** `infer_tz` parameter.
So `2025-09-15T23:30:00-07:00` → day **15** (UTC would give 16).

`extract(value="ldom")` must keep timezone-bearing output: apply `MonthEnd(0)` to the
timezone-preserving parsed values, never to the naive wall-clock view.

---

## Scope

- `extract`: parse non-datetime, non-timedelta input with `_to_datetime_series(..., infer_tz=True)` and store it back in the source column; take numeric components and period output from a wall-clock view; `ldom` uses the timezone-preserving values (per value for `object` columns).
- `add_timestamp_to_time`: combine `_to_wall_clock(date)`'s date with `_to_wall_clock(time)`'s time of day.
- Append the spec §4 **M3** tests.

**NOT in scope**: the helpers (TASK-888); `to_excel_date`/`change_timezone` (TASK-889); cross-cutting precision/regression tests (TASK-891).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | `extract`, `add_timestamp_to_time` |
| `tests/unit/test_to_datetime_mixed_offsets.py` | MODIFY | M3 tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pandas as pd                          # verified: transforms.py:19
from navconfig.logging import logging        # verified: transforms.py:22
from pandas.tseries.offsets import MonthEnd  # verified: transforms.py:23
```
Test file — extend the import block with `add_timestamp_to_time, extract`.

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py (line numbers @ be9a3cbc; TASK-888/889 shift them — re-grep)
def _to_datetime_series(series, infer_tz=False, field=None) -> pd.Series            # TASK-888
def _to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series     # TASK-888
def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str)       # line 529
def is_datetime_column(column) -> bool                                              # line 2003
def is_time_column(column) -> bool                                                  # line 2013
def extract(df: pd.DataFrame, field: str, column="", to_date=None, value="day")     # line 2023
    # body: parse guard lines 2041-2047; component chain lines 2049-2075 ("day", "dow"/"dayofweek",
    # "doy"/"dayofyear", "month"→Int64, "year"→Int64, "quarter", "hour" with .dt.components fallback,
    # "minute", "second", "ldom" → `df[column] + MonthEnd(0)`, else `.dt.to_period(value)`)
```

### Does NOT Exist
- ~~`infer_tz` on `extract` / `add_timestamp_to_time`~~ — not added (spec §8 Q2).
- ~~`Series + MonthEnd(0)` on an `object` column of mixed-offset Timestamps~~ — not a supported vectorized op; apply per value.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/dt/transforms.py", "action": "MODIFY"},
    {"path": "tests/unit/test_to_datetime_mixed_offsets.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#_to_datetime_series",
    "sym:querysource/types/dt/transforms.py#add_timestamp_to_time",
    "sym:querysource/types/dt/transforms.py#extract",
    "sym:querysource/types/dt/transforms.py#is_datetime_column",
    "sym:querysource/types/dt/transforms.py#is_time_column"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Leave timedelta columns untouched** — `extract(value="hour")` supports them through `.dt.components.hours`; per-value `pd.Timestamp` parsing would turn them into `NaT`.
- Datetime-dtype columns (naive or single zone) keep today's behaviour exactly: components on a tz-aware dtype are already local.
- When `field != column`, the source column keeps its parsed, timezone-preserving values (spec AC on source-column timezone).
- `add_timestamp_to_time`: a null in either source gives a null result; fractional seconds are kept.

---

## Implementation Blueprint

### Steps (in order)
1. Re-run the anchor greps — *why*: earlier tasks shifted line numbers.
2. Replace the parse guard in `extract` and introduce `source`/`view` — *why*: mixed input must parse without raising and without losing offsets.
3. Point the component chain at `view`, and `ldom` at `source` — *why*: components need local wall-clock; `ldom` must keep timezones.
4. Rewrite the body of `add_timestamp_to_time` — *why*: `.dt` fails on `object` columns.
5. Append the M3 tests.

### `querysource/types/dt/transforms.py` (MODIFY) — `extract` parse guard
```python
# occurrences: 1 (verified: grep -c '                df\[column\] = pd.to_datetime(df\[column\])' querysource/types/dt/transforms.py)
# REPLACE the block from `    if not is_datetime_column(df[column]):` through `                pass`
# (transforms.py:2041-2047 @ be9a3cbc) with:
    if not is_datetime_column(df[column]) and not pd.api.types.is_timedelta64_dtype(df[column]):
        try:
            df[column] = _to_datetime_series(df[column], infer_tz=True, field=column)
        except (TypeError, ValueError) as err:
            logging.error(f"extract: cannot parse {column!r} as datetime: {err}")
    source = df[column]
    view = source if source.dtype != object else _to_wall_clock(source, field=column)
```
**Why**: `infer_tz=True` keeps each offset in the source column (spec §2 `extract`
row); `view` is a naive local wall-clock copy used only for components, so the source
is never stripped. The dropped `is_time_column` check was identical to `is_datetime_column`.

### `querysource/types/dt/transforms.py` (MODIFY) — `extract` component chain
```python
# In the if/elif chain that follows (transforms.py:2049-2075 @ be9a3cbc):
# FILL IN: replace every `df[column].dt` with `view.dt` (day, dow, doy, month, year,
#   quarter, hour incl. its `.dt.components.hours` fallback, minute, second, to_period)
#   — bounded by AC-2 and AC-4 (naive/single-offset results unchanged).
# REPLACE the ldom branch body `        df[field] = df[column] + MonthEnd(0)` with:
        if source.dtype == object:
            df[field] = source.map(lambda v: v if pd.isna(v) else v + MonthEnd(0))
        else:
            df[field] = source + MonthEnd(0)
```
**Why**: spec §2 — `ldom` applies `MonthEnd(0)` to timezone-preserving values,
per value for heterogeneous `object` columns, keeping each value's offset, local
time and nulls.

### `querysource/types/dt/transforms.py` (MODIFY) — `add_timestamp_to_time` body
```python
# occurrences: 1 (verified: grep -c 'def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str):' querysource/types/dt/transforms.py)
# REPLACE the three-line `df[field] = pd.to_datetime(...)` statement after the docstring
# (transforms.py:540-542 @ be9a3cbc) with:
    date_part = _to_wall_clock(df[date], field=date)
    time_part = _to_wall_clock(df[time], field=time)
    df[field] = date_part.dt.normalize() + (time_part - time_part.dt.normalize())
```
**Why**: both parts come from local wall-clock (spec §8 Q2). Adding the time-of-day
offset keeps fractional seconds, and `NaT` propagates, so a null in either source gives
a null result. This replaces a string round-trip that dropped sub-second precision.

### `tests/unit/test_to_datetime_mixed_offsets.py` (MODIFY)
```python
# APPEND at end of file
def test_extract_mixed_offsets():
    """value='day' on 2025-09-15T23:30-07:00 mixed with a -08:00 value -> 15, not 16."""
    # FILL IN: also month/hour; source column keeps its offsets when field != column


def test_extract_ldom_preserves_timezone():
    """'2025-09-15T23:30:00-07:00' -> 2025-09-30T23:30:00-07:00."""
    # FILL IN: parsed input, mixed offsets, NAIVE_AWARE, nulls; timezone-bearing output


def test_add_timestamp_to_time_mixed():
    """Mixed/object inputs no longer raise; local date/time combined; fractional seconds; nulls propagate."""
    # FILL IN
```

### FILL IN checklist
- [ ] `extract` component chain → `view.dt` — AC-2, AC-4
- [ ] three test bodies — spec §4 M3 rows

---

## Acceptance Criteria

- [ ] AC-1: `extract` and `add_timestamp_to_time` no longer raise on mixed-offset, `infer_tz=True` object or naive/aware input.
- [ ] AC-2: components use each value's own local wall-clock; naive values are read as UTC (`23:30-07:00` → day 15).
- [ ] AC-3: `extract(value="ldom")` keeps timezone-bearing results (per value for `object`), and the source column keeps its parsed timezones when `field != column`.
- [ ] AC-4: naive, single-offset and timedelta columns give today's results.
- [ ] AC-5: `add_timestamp_to_time` keeps fractional seconds and propagates nulls.

---

## Validation Commands

- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`
- `pytest tests/unit/test_pandas3_transforms.py -q`

---

## Test Specification

See the MODIFY block above — spec §4 rows tagged M3.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug pandas3-to-datetime-mixed-offsets --feature-id FEAT-171`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-889 must be `"done"`.
3. **Verify the Codebase Contract** — re-run the greps.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands.
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-890 pandas3-to-datetime-mixed-offsets verified`.
9. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*
