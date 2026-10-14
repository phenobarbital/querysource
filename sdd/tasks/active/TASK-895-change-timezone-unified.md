# TASK-895: Extend `change_timezone` and add the `convert_timezone` alias

**Feature**: FEAT-174 — Host Flowtask's timezone transforms in QuerySource (single datetime code base)
**Spec**: `sdd/specs/dt-timezone-transforms.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-894
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 3** with decisions §8 Q1 (unify) and Q2 (DST policy):

- **One implementation**: `change_timezone` gains keyword-only `column`, `tz_column`,
  `default_timezone` (Flowtask's feature set); `convert_timezone` is a thin alias with
  Flowtask's signature (8 Flowtask task files call it by name, networkninja with
  `tz_column: store_timezone`).
- **Naive default**: naive input with no `from_tz` is localized as UTC. Today
  `change_timezone` logs an error and returns the column unchanged in that case
  (verified on `dev` @ `0fd0a56`).
- **DST**: ambiguous → `ambiguous=np.array([False] * n)` (QS policy, kept); nonexistent →
  `nonexistent="shift_forward"` (verified: `2025-03-09 02:30` America/Chicago →
  `03:00-05:00`; ambiguous `2025-11-02 01:30` → `-06:00`). Flowtask's `ambiguous="infer"`
  raised on a single ambiguous value in unsorted data and left the whole column unconverted.

---

## Scope

- Replace the signature and body of `change_timezone` (docstring updated).
- Add `convert_timezone` right after `drop_timezone` (TASK-894), delegating to `change_timezone`.
- Append the M3 tests to `tests/unit/test_dt_timezone_transforms.py`.

**NOT in scope**: `drop_timezone` (TASK-894); integration test and changelog (TASK-896).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | `change_timezone` extended; new `convert_timezone` |
| `tests/unit/test_dt_timezone_transforms.py` | MODIFY | M3 tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from typing import Any, Dict, List, Optional, Union  # verified: transforms.py:15
from zoneinfo import ZoneInfo                        # verified: transforms.py:16
import numpy as np                                   # verified: transforms.py:18
import pandas as pd                                  # verified: transforms.py:20
from navconfig.logging import logging                # verified: transforms.py:23
```
Test file — extend the import block with `change_timezone`, `convert_timezone`.

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py  (dev @ ed6b50e; TASK-893/894 shift lines — re-grep)
def parse_datetime_series(series, infer_tz=False, field=None) -> pd.Series            # TASK-893
def drop_timezone(df, field, column=None) -> pd.DataFrame                             # TASK-894
def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):  # line 2382
    # current body: lines 2394-2410 (try: _to_datetime_series(df[field], infer_tz=False, field=field) ...
    #   aware -> warning if from_tz, tz_convert(to_tz); naive -> infer_dst = np.array([False] * df.shape[0]),
    #   tz_localize(from_tz, ambiguous=infer_dst).tz_convert(to_tz); except -> logging.error; return df)
```

Per-row `tz_column` logic to port (Flowtask `flowtask/components/TransformRows/functions.py:495-517` @ `0f872c57`):
```python
timezones = df[tz_column].fillna(default_timezone).astype("string")
utc_times = out.dt.tz_convert('UTC')
converted_times = []
for idx in df.index:
    try:
        converted_times.append(utc_times.loc[idx].tz_convert(ZoneInfo(timezones.loc[idx])))
    except Exception:
        converted_times.append(utc_times.loc[idx].tz_convert(ZoneInfo(default_timezone)))
out = pandas.Series(converted_times, index=df.index)
```

### Does NOT Exist
- ~~`convert_timezone` in QuerySource~~ — created here.
- ~~`column` / `tz_column` / `default_timezone` on `change_timezone`~~ — added here, keyword-only.
- ~~`ambiguous="infer"`~~ — not used (spec §8 Q2).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/dt/transforms.py", "action": "MODIFY"},
    {"path": "tests/unit/test_dt_timezone_transforms.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#change_timezone",
    "sym:querysource/types/dt/transforms.py#parse_datetime_series",
    "sym:querysource/types/dt/transforms.py#drop_timezone"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Positional parameters of `change_timezone` (`df, field, from_tz, to_tz`) are unchanged; new ones are keyword-only (AC-1).
- `convert_timezone` contains no conversion logic (AC-2).
- With `column=None`, `change_timezone` reads and writes `field` exactly as today.
- `parse_datetime_series(..., infer_tz=False)` never returns `object`: mixed input becomes `datetime64[ns, UTC]`, so `.dt` is safe.
- A `tz_column` row whose `utc_times` value is `NaT` must stay `NaT` (guard before `tz_convert`).

---

## Implementation Blueprint

### Steps (in order)
1. Re-run the greps below (TASK-893/894 inserted code).
2. Replace `change_timezone` (signature + docstring + body) — *why*: single implementation.
3. Insert `convert_timezone` right after `drop_timezone` — *why*: spec §3 M3 placement.
4. Append the M3 tests.

### `querysource/types/dt/transforms.py` (MODIFY) — `change_timezone`
```python
# occurrences: 1 (verified: grep -c 'def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):' querysource/types/dt/transforms.py)
# REPLACE the whole function (verified: transforms.py:2382-2410 @ ed6b50e) with:
def change_timezone(
    df: pd.DataFrame,
    field: str,
    from_tz: Optional[str] = None,
    to_tz: Optional[str] = "UTC",
    *,
    column: Optional[str] = None,
    tz_column: Optional[str] = None,
    default_timezone: str = "UTC",
) -> pd.DataFrame:
    """Convert a datetime column to a target timezone.

    Timezone-aware values are converted directly (``from_tz`` is ignored, with a
    warning when given). Naive values are localized to ``from_tz`` (UTC when not
    given): ambiguous wall-clock times are read as standard time and nonexistent
    times are shifted forward.

    Args:
        df: DataFrame to modify.
        field: Output column.
        from_tz: Timezone of naive values (defaults to UTC).
        to_tz: Target timezone (``default_timezone`` when ``None``).
        column: Source column (defaults to ``field``).
        tz_column: Column holding a target timezone per row; overrides ``to_tz``.
        default_timezone: Fallback for null/invalid ``tz_column`` values and for ``to_tz=None``.

    Returns:
        The DataFrame with ``field`` set.
    """
    if column is None:
        column = field
    try:
        values = parse_datetime_series(df[column], infer_tz=False, field=column)
        if isinstance(values.dtype, pd.DatetimeTZDtype):
            if from_tz:
                logging.warning(
                    f"change_timezone: {column!r} is already timezone-aware; "
                    f"ignoring from_tz={from_tz!r}"
                )
        else:
            infer_dst = np.array([False] * df.shape[0])
            values = values.dt.tz_localize(
                from_tz or "UTC", ambiguous=infer_dst, nonexistent="shift_forward"
            )
        if tz_column is None:
            df[field] = values.dt.tz_convert(to_tz or default_timezone)
        else:
            # FILL IN: port the per-row block from the Codebase Contract — zones from
            # df[tz_column].fillna(default_timezone).astype("string"); convert each row of
            # values.dt.tz_convert("UTC") with ZoneInfo(zone); on any exception fall back to
            # ZoneInfo(default_timezone); keep NaT rows as NaT; assign pd.Series(..., index=df.index)
            # to df[field] — bounded by AC-4
            pass
    except Exception as err:
        logging.error(f"Error changing timezone of {column!r}: {err}")
    return df
```
**Why**: spec §2 item 3, §8 Q1/Q2. `from_tz or "UTC"` fixes the silent no-op; `shift_forward`
keeps one nonexistent value from failing the whole column.

### `querysource/types/dt/transforms.py` (MODIFY) — `convert_timezone`
```python
# AFTER — insert below the end of `drop_timezone` (TASK-894), before `def to_numeric(df: pd.DataFrame, field: str`
# occurrences: 1 (verified: grep -c 'def to_numeric(df: pd.DataFrame, field: str' querysource/types/dt/transforms.py)
def convert_timezone(
    df: pd.DataFrame,
    field: str,
    *,
    column: Optional[str] = None,
    from_tz: Optional[str] = None,
    to_tz: Optional[str] = None,
    tz_column: Optional[str] = None,
    default_timezone: str = "UTC",
) -> pd.DataFrame:
    """Flowtask-compatible alias of :func:`change_timezone`.

    Args:
        df: DataFrame to modify.
        field: Output column.
        column: Source column (defaults to ``field``).
        from_tz: Timezone of naive values (defaults to UTC).
        to_tz: Target timezone (defaults to ``default_timezone``).
        tz_column: Column holding a target timezone per row.
        default_timezone: Fallback timezone.

    Returns:
        The DataFrame with ``field`` set.
    """
    return change_timezone(
        df,
        field,
        from_tz=from_tz,
        to_tz=to_tz or default_timezone,
        column=column,
        tz_column=tz_column,
        default_timezone=default_timezone,
    )
```
**Why**: spec §2 item 4 — name and keyword-only signature match Flowtask's
`convert_timezone`, so YAML resolves unchanged; no second implementation.

### `tests/unit/test_dt_timezone_transforms.py` (MODIFY)
```python
# APPEND at end of file
def test_change_timezone_existing_callers():
    """Positional change_timezone(df, field, from_tz, to_tz) calls that work today give identical output."""
    # FILL IN: naive + from_tz="America/Chicago" -> to_tz="UTC"; aware single offset -> to_tz — AC-1


def test_change_timezone_naive_without_from_tz():
    """Naive input, no from_tz -> localized as UTC and converted (today: column unchanged)."""
    # FILL IN: AC-3


def test_timezone_mixed_strings():
    """MIXED -> America/New_York, same instants, no NaT, for both names."""
    # FILL IN: AC-5


def test_timezone_aware_object_no_nat():
    """object of aware Timestamps -07:00/-08:00 -> all converted (Flowtask's silent NaT regression)."""
    # FILL IN: AC-5


def test_timezone_from_tz_warning(caplog):
    """Aware input + explicit from_tz -> warning logged; no warning when omitted."""
    # FILL IN: AC-3


def test_timezone_dst_policy():
    """America/Chicago: DST_AMBIGUOUS -> 01:30-06:00 (standard), whole column converted;
    DST_NONEXISTENT -> 2025-03-09 03:00-05:00, whole column converted."""
    # FILL IN: AC-6


def test_convert_timezone_tz_column():
    """Per-row zones incl. a null and an invalid zone -> default_timezone fallback;
    networkninja config (from_tz="UTC", tz_column="store_timezone") matches Flowtask on working input."""
    # FILL IN: AC-4


def test_convert_timezone_is_alias():
    """convert_timezone(...) == change_timezone(..., to_tz=to_tz or default_timezone, ...)."""
    # FILL IN: AC-2


def test_timezone_column_output():
    """column is the source, field the output; source column unchanged."""
    # FILL IN: AC-1


def test_transforms_never_raise(caplog):
    """A missing column is logged with logging.error and the frame is returned (all three transforms)."""
    # FILL IN: AC-7
```

### FILL IN checklist
- [ ] `tz_column` per-row block in `change_timezone` — AC-4
- [ ] ten test bodies — AC-1..AC-7

---

## Acceptance Criteria

- [ ] AC-1: `change_timezone` keeps its positional signature; `column` / `tz_column` / `default_timezone` are keyword-only; working calls give identical output; `column` is the source and `field` the output.
- [ ] AC-2: `convert_timezone` only delegates to `change_timezone` (to_tz → `to_tz or default_timezone`).
- [ ] AC-3: aware input ignores `from_tz` (warning only when given); naive input uses `from_tz or "UTC"`.
- [ ] AC-4: `tz_column` per-row conversion with `default_timezone` fallback for null/invalid zones; `NaT` rows stay `NaT`.
- [ ] AC-5: mixed-offset strings and aware `object` input convert with no `NaT`.
- [ ] AC-6: ambiguous naive → standard time; nonexistent → shifted forward; the rest of the column is converted.
- [ ] AC-7: errors logged with `logging.error`, never raised.
- [ ] `pytest tests/unit/test_to_datetime_mixed_offsets.py` passes unmodified (FEAT-171 `change_timezone` tests).
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors.

---

## Validation Commands

- `pytest tests/unit/test_dt_timezone_transforms.py -q`
- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`
- `pytest tests/test_mixed_tz_datetime.py -q`

---

## Test Specification

Spec §4 rows tagged M3.

---

## Agent Instructions

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug dt-timezone-transforms --feature-id FEAT-174`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-894 must be `"done"`.
3. **Verify the Codebase Contract** — re-run the greps.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands.
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-895 dt-timezone-transforms verified`.
9. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none
