# TASK-894: `drop_timezone` transform (Flowtask-compatible, mixed-offset safe)

**Feature**: FEAT-174 — Host Flowtask's timezone transforms in QuerySource (single datetime code base)
**Spec**: `sdd/specs/dt-timezone-transforms.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-893
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 2**. Flowtask's TransformRows calls `drop_timezone` by name
(20 task files) and falls back to `querysource.types.dt.transforms` once Flowtask deletes
its own copy (FEAT-567). Flowtask's version leaves mixed-offset strings unchanged and
misreads `2025-09-15-07:00` as `07:00`. Measured on the Workday offers/hires export, a
`drop_timezone` built on `to_wall_clock` returns the correct local wall-clock for 100 %
of values in all five datetime columns.

---

## Scope

- Add `drop_timezone(df, field, column=None)` to `querysource/types/dt/transforms.py`,
  immediately before `def to_numeric(`.
- Append the M2 tests to `tests/unit/test_dt_timezone_transforms.py`.

**NOT in scope**: `change_timezone` / `convert_timezone` (TASK-895); the integration test (TASK-896).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | new `drop_timezone` |
| `tests/unit/test_dt_timezone_transforms.py` | MODIFY | M2 tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from typing import Any, Dict, List, Optional, Union  # verified: transforms.py:15
import pandas as pd                                  # verified: transforms.py:20
from navconfig.logging import logging                # verified: transforms.py:23
```
Test file — extend the import block created by TASK-893 with `drop_timezone`.

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py  (dev @ ed6b50e; TASK-893 adds ~35 lines above — re-grep)
def to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series          # TASK-893
def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC")  # line 2382
def to_numeric(df: pd.DataFrame, field: str, remove_alpha: bool = True, to_integer: bool = False)  # line 2413
```

Reference behaviour being replaced (Flowtask `flowtask/components/TransformRows/functions.py:408` @ `0f872c57`):
```python
# DatetimeTZDtype -> series.dt.tz_localize(None)
# object          -> per value x.replace(tzinfo=None) for aware Timestamp/datetime, then .astype("datetime64[ns]")
# anything else   -> df[field] = series (unchanged)
# errors          -> print(...) and return df
```

### Does NOT Exist
- ~~`drop_timezone` in QuerySource~~ — created here.
- ~~`self.logger`~~ — module function; use module-level `logging`.

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
    "sym:querysource/types/dt/transforms.py#to_wall_clock",
    "sym:querysource/types/dt/transforms.py#to_numeric"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Name and parameters must match Flowtask exactly: `drop_timezone(df, field, column=None)`; `column` is the source (defaults to `field`), `field` the output.
- Non-datetime, non-string dtypes (numeric, bool, …) are copied unchanged — Flowtask's contract.
- pandas 3 string columns are `str`/`string` dtype, **not** `object` — dispatch with `pd.api.types.is_object_dtype(...) or pd.api.types.is_string_dtype(...)`.
- Never raise: log with `logging.error` and return `df`.

---

## Implementation Blueprint

### Steps (in order)
1. Re-run `grep -c 'def to_numeric(df: pd.DataFrame, field: str' querysource/types/dt/transforms.py` (expect 1).
2. Insert `drop_timezone` above it — *why*: spec §3 M2 places it after `change_timezone`.
3. Append the M2 tests.

### `querysource/types/dt/transforms.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'def to_numeric(df: pd.DataFrame, field: str' querysource/types/dt/transforms.py)
# BEFORE — insert above `def to_numeric(df: pd.DataFrame, field: str` (verified: transforms.py:2413 @ ed6b50e), two blank lines
def drop_timezone(df: pd.DataFrame, field: str, column: Optional[str] = None) -> pd.DataFrame:
    """Drop timezone information, keeping each value's local wall-clock.

    Datetime-aware columns lose their timezone (``tz_localize(None)``); object or
    string columns are parsed and each value keeps its own local wall-clock
    (date-only values with an offset become local midnight, unparseable values
    become ``NaT``); naive datetimes and non-datetime columns are copied unchanged.

    Args:
        df: DataFrame to modify.
        field: Output column.
        column: Source column (defaults to ``field``).

    Returns:
        The DataFrame with ``field`` set.
    """
    if column is None:
        column = field
    try:
        series = df[column]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            df[field] = series.dt.tz_localize(None)
        elif pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            df[field] = to_wall_clock(series, field=field)
        else:
            df[field] = series
    except Exception as err:
        logging.error(f"Error on drop_timezone {field!r}: {err}")
    return df
```
**Why**: spec §2 item 2. UTC-dtype input keeps `tz_localize(None)` (UTC wall-clock), as
pinned by FEAT-171 M5; only object/string input goes through `to_wall_clock`.

### `tests/unit/test_dt_timezone_transforms.py` (MODIFY)
```python
# APPEND at end of file
def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:
    """Mirror of Flowtask TransformRows drop_timezone (functions.py:408) — the behaviour being replaced."""
    # FILL IN: copy the 3-branch algorithm from the Codebase Contract reference (tz dtype / object / else)


def test_drop_timezone_mixed_strings():
    """MIXED strings -> naive [2025-09-15 23:30, 2025-12-01 08:00, NaT]; no str left."""
    # FILL IN: AC-1


def test_drop_timezone_offset_date():
    """'2025-09-15-07:00' -> 2025-09-15 00:00; '2025-09-15Z' -> midnight; plain date stays midnight."""
    # FILL IN: AC-2, using OFFSET_DATES


def test_drop_timezone_dtypes():
    """tz dtype (incl. UTC) -> tz_localize(None); naive unchanged; aware object mixed -> local wall-clock;
    numeric column copied unchanged; column != field leaves the source untouched."""
    # FILL IN: AC-3, AC-4


def test_drop_timezone_flowtask_parity():
    """On Flowtask's working inputs (tz dtype, aware object, naive) output equals the mirror."""
    # FILL IN: AC-5 — compare values (pd.testing.assert_series_equal with check_dtype=False if resolution differs)
```

### FILL IN checklist
- [ ] `_drop_timezone_like_flowtask` body — AC-5
- [ ] four test bodies — AC-1..AC-5

---

## Acceptance Criteria

- [ ] AC-1: mixed-offset strings → naive local wall-clock, no `str`, no parseable value lost.
- [ ] AC-2: date-only with offset → local midnight; `Z` → midnight; plain date unchanged.
- [ ] AC-3: `DatetimeTZDtype` → `tz_localize(None)`; naive datetime and numeric columns copied unchanged.
- [ ] AC-4: `column` ≠ `field` writes `field` and leaves `column` untouched.
- [ ] AC-5: identical output to Flowtask's algorithm on its currently-working inputs.
- [ ] AC-6: errors are logged with `logging.error`, never raised, no `print`.
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors.

---

## Validation Commands

- `pytest tests/unit/test_dt_timezone_transforms.py -q`
- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`

---

## Test Specification

Spec §4 rows tagged M2.

---

## Agent Instructions

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug dt-timezone-transforms --feature-id FEAT-174`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-893 must be `"done"`.
3. **Verify the Codebase Contract** — re-run the greps (TASK-893 shifted line numbers).
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands.
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-894 dt-timezone-transforms verified`.
9. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-10-13
**Notes**: drop_timezone added before to_numeric; 5 new-file tests (4 M2 + mirror) pass (26 total with FEAT-171 regression); ruff clean. Code commit bcbf9c8.

**Deviations from spec**: none
