# TASK-889: Route `to_excel_date` and `change_timezone` through `_to_datetime_series`

**Feature**: FEAT-171 — pandas 3 mixed-UTC-offset handling for the remaining datetime transforms
**Spec**: `sdd/specs/pandas3-to-datetime-mixed-offsets.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-888
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 2** (§2 per-site table rows `to_excel_date`, `change_timezone`).
On pandas 3.0.6 with mixed-offset input:

- `to_excel_date` raises `ValueError` (mixed strings), or silently turns the `-08:00`
  value into `NaT` when the column already holds aware objects (`infer_tz=True` output).
- `change_timezone` calls `pd.to_datetime` **outside** its `try`, so mixed strings raise;
  aware input then fails in `tz_localize(from_tz)` and is only `print`ed.

Decisions (spec §8): Q3 → with tz-aware input and an explicit `from_tz`, **log a
warning and ignore `from_tz`**. `to_excel_date` keeps its documented UTC-naive output
and gets no `infer_tz` parameter.

---

## Scope

- `to_excel_date`: parse with `_to_datetime_series(..., infer_tz=False)`; replace `print` with `logging.error`; keep the re-raise.
- `change_timezone`: parse inside the `try`; aware values → `tz_convert(to_tz)` directly (warning when `from_tz` was given); naive values keep `tz_localize(from_tz, ambiguous=…).tz_convert(to_tz)`; `print` → `logging.error`; remove `return` from `finally` (ruff B012).
- Append the spec §4 **M2** tests to `tests/unit/test_to_datetime_mixed_offsets.py`.

**NOT in scope**: the helpers (TASK-888); `extract`, `add_timestamp_to_time` (TASK-890); no new parameters on either function.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | `to_excel_date`, `change_timezone` |
| `tests/unit/test_to_datetime_mixed_offsets.py` | MODIFY | M2 tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import numpy as np                     # verified: transforms.py:17
import pandas as pd                    # verified: transforms.py:19
from navconfig.logging import logging  # verified: transforms.py:22
```
Test file — extend the existing import block (created by TASK-888) with `change_timezone, to_excel_date`.

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py (line numbers @ be9a3cbc; TASK-888 shifts them — re-grep)
def _to_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series  # TASK-888
def to_excel_date(df: pd.DataFrame, field: str, column: str = None)                         # line 1917
    df[column] = pd.to_datetime(df[column], errors='coerce')                                 # line 1932
def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC")  # line 2258
    df[field] = pd.to_datetime(df[field], errors="coerce")                                   # line 2268
```

### Does NOT Exist
- ~~`infer_tz` on `to_excel_date` / `change_timezone`~~ — not added (spec §2, §6).
- ~~`self.logger`~~ — these are module functions; use module-level `logging`.

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
    "sym:querysource/types/dt/transforms.py#to_excel_date",
    "sym:querysource/types/dt/transforms.py#change_timezone"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- With `infer_tz=False`, `_to_datetime_series` returns a datetime dtype for every input (mixed → `datetime64[ns, UTC]`), so `.dt` is safe afterwards.
- Naive input to `change_timezone` keeps its exact current path, including `ambiguous=np.array([False] * n)`.
- Signatures unchanged.

---

## Implementation Blueprint

### Steps (in order)
1. Re-run the anchor greps — *why*: TASK-888 inserted ~90 lines above these functions.
2. Edit `to_excel_date` — *why*: mixed offsets must normalize to UTC (its contract) instead of raising or losing values.
3. Rewrite the body of `change_timezone` — *why*: parsing outside `try` raises, and aware input cannot be `tz_localize`d.
4. Append the M2 tests — *why*: AC-1..AC-4.

### `querysource/types/dt/transforms.py` (MODIFY) — `to_excel_date`
```python
# occurrences: 1 (verified: grep -c "        df\[column\] = pd.to_datetime(df\[column\], errors='coerce')" querysource/types/dt/transforms.py)
# REPLACE `        df[column] = pd.to_datetime(df[column], errors='coerce')` (verified: transforms.py:1932 @ be9a3cbc) with:
        df[column] = _to_datetime_series(df[column], infer_tz=False, field=column)
# and REPLACE `        print('Error on to_excel_date: ', e)` in the same function with:
        logging.error(f"Error on to_excel_date for {column!r}: {e}")
```
**Why**: `infer_tz=False` yields UTC for mixed input, which the existing
`tz_convert('UTC').tz_localize(None)` branch then turns into UTC-naive — exactly the
function's documented Excel contract. Keep `raise e`.

### `querysource/types/dt/transforms.py` (MODIFY) — `change_timezone` body
```python
# occurrences: 1 (verified: grep -c 'def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):' querysource/types/dt/transforms.py)
# REPLACE everything after the docstring of `change_timezone` (transforms.py:2268-2277 @ be9a3cbc) with:
    try:
        values = _to_datetime_series(df[field], infer_tz=False, field=field)
        if isinstance(values.dtype, pd.DatetimeTZDtype):
            if from_tz:
                logging.warning(
                    f"change_timezone: {field!r} is already timezone-aware; "
                    f"ignoring from_tz={from_tz!r}"
                )
            df[field] = values.dt.tz_convert(to_tz)
        else:
            infer_dst = np.array([False] * df.shape[0])
            df[field] = (
                values.dt.tz_localize(from_tz, ambiguous=infer_dst).dt.tz_convert(to_tz)
            )
    except Exception as err:
        logging.error(f"Error changing timezone of {field!r}: {err}")
    return df
```
**Why**: spec §8 Q3 (warning, ignore `from_tz`). Moving the parse inside `try` and
dropping `return` from `finally` stops exceptions from escaping or being masked.
The docstring gains one line: tz-aware input is converted directly and `from_tz` is ignored.

### `tests/unit/test_to_datetime_mixed_offsets.py` (MODIFY)
```python
# APPEND at end of file
def test_to_excel_date_mixed_offsets():
    """Mixed strings and infer_tz=True object input -> UTC-naive values, no NaT."""
    # FILL IN: MIXED -> 2025-09-16 06:30, 2025-12-01 16:00, NaT; dtype naive


def test_change_timezone_mixed_and_aware(caplog):
    """Mixed, single-offset aware and infer_tz object input convert to to_tz; naive + from_tz unchanged."""
    # FILL IN: to_tz="America/New_York"; no NaT; warning logged only when from_tz is given
    # for aware input; naive input with from_tz="UTC" matches today's result
```

### FILL IN checklist
- [ ] `test_to_excel_date_mixed_offsets` body — AC-1
- [ ] `test_change_timezone_mixed_and_aware` body — AC-2, AC-3

---

## Acceptance Criteria

- [ ] AC-1: `to_excel_date` returns UTC-naive values with no `NaT` for mixed strings and for `infer_tz=True` object input; errors are logged via `logging.error` and still re-raised.
- [ ] AC-2: `change_timezone` converts mixed-offset strings, single-offset aware input and `infer_tz=True` object input to `to_tz` with no `NaT`.
- [ ] AC-3: aware input with an explicit `from_tz` logs a warning naming the field and ignores `from_tz`; naive input with `from_tz` gives today's result.
- [ ] AC-4: no `print` and no `return` inside `finally` remain in the two functions.
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors (baseline 20; this task may remove one B012).

---

## Validation Commands

- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`
- `pytest tests/unit/test_pandas3_transforms.py -q`

---

## Test Specification

See the MODIFY block above — spec §4 rows tagged M2.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug pandas3-to-datetime-mixed-offsets --feature-id FEAT-171`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-888 must be `"done"` in `sdd/tasks/index/pandas3-to-datetime-mixed-offsets.json`.
3. **Verify the Codebase Contract** — re-run the greps; line numbers moved after TASK-888.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands.
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-889 pandas3-to-datetime-mixed-offsets verified`.
9. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note


- Task: TASK-889
- Feature: pandas3-to-datetime-mixed-offsets
- Implementation SHA: 40322e5401d25069018ff5b40b7893b98640af5f
- Closed at (UTC): 2026-10-13T21:42:48+00:00
- Fix commits: none

| Metric | Value |
|---|---|
| validation_refs | 1 |
| fix_commits | 0 |
| seat_summary | Seat: gpt-5.6-terra · Backend: codex · Model: gpt-5.6-terra · Attempts: 1 · Duration: 352.7s · Tokens: n/a |
