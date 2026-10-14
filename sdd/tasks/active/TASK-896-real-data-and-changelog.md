# TASK-896: Real-data `drop_timezone` validation and changelog

**Feature**: FEAT-174 — Host Flowtask's timezone transforms in QuerySource (single datetime code base)
**Spec**: `sdd/specs/dt-timezone-transforms.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-894, TASK-895
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 4** (integration + changelog). FEAT-171 M5
(`tests/integration/test_offers_hires_datetime.py`) validates the production chain with a
**local mirror** of Flowtask's `drop_timezone`; now that QS hosts the real function
(TASK-894), the test must use it, and a new case must cover raw strings going straight
into `drop_timezone` (100 % of values were left as strings by Flowtask's version).

---

## Scope

- In `tests/integration/test_offers_hires_datetime.py`: import `drop_timezone`; make
  `test_offers_hires_drop_timezone_wall_clock` call the real function instead of
  `_drop_timezone_like_flowtask` (delete the mirror); add
  `test_offers_hires_drop_timezone_raw_strings`.
- `CHANGES.rst`: entry under *Unreleased* (to ship as 5.2.5 with FEAT-171).

**NOT in scope**: the transforms themselves (TASK-893..895); version bump (done by `/release`).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_offers_hires_datetime.py` | MODIFY | real `drop_timezone`; raw-string case |
| `CHANGES.rst` | MODIFY | Unreleased entry |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types.dt.transforms import convert_to_datetime, to_timestamp  # verified: test_offers_hires_datetime.py:13
# add: drop_timezone (TASK-894)
```

### Existing Signatures to Use
```python
# tests/integration/test_offers_hires_datetime.py  (dev @ ed6b50e)
COLUMNS = [...]                                                    # line 15 (5 Workday columns)
def offers_hires() -> pd.DataFrame                                 # line 26 — env QS_OFFERS_HIRES_CSV, pytest.skip when absent
def _reference(value: str) -> datetime                             # line 34
def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:  # line 49 — DELETE
def _source(offers_hires, column) -> pd.Series                      # line 62
def _run(offers_hires, func, column, infer_tz) -> pd.Series         # line 67
def test_offers_hires_drop_timezone_wall_clock(offers_hires, func, infer_tz, column) -> None  # line 113
    result = _drop_timezone_like_flowtask(_run(offers_hires, func, column, infer_tz))          # line 116
# querysource/types/dt/transforms.py
def drop_timezone(df, field, column=None) -> pd.DataFrame          # TASK-894
```
```rst
# CHANGES.rst:1-5
Unreleased
==========

pandas 3 compatibility for datetime and fill transforms
-------------------------------------------------------
```

### Does NOT Exist
- ~~`artifacts/get_offers_and_hires_1.6.csv` in git~~ — untracked, PII; never add, copy or print row values.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/integration/test_offers_hires_datetime.py", "action": "MODIFY"},
    {"path": "CHANGES.rst", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#drop_timezone",
    "sym:querysource/types/dt/transforms.py#change_timezone",
    "sym:querysource/types/dt/transforms.py#convert_timezone"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Expectations of the existing 20-case test are **unchanged** (infer_tz=True → local wall-clock; False → UTC wall-clock); only the function under test changes.
- Assertion messages report counts and column names only.

---

## Implementation Blueprint

### Steps (in order)
1. Re-run the greps for the two test anchors (expect 1 each).
2. Swap the mirror for the real function — *why*: the mirror documented Flowtask's behaviour; QS now owns it.
3. Add the raw-string case — *why*: the Flowtask failure mode (strings left behind).
4. Add the changelog entry.

### `tests/integration/test_offers_hires_datetime.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from querysource.types.dt.transforms import convert_to_datetime, to_timestamp' tests/integration/test_offers_hires_datetime.py)
# REPLACE line 13 with:
from querysource.types.dt.transforms import convert_to_datetime, drop_timezone, to_timestamp

# occurrences: 1 (verified: grep -c 'def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:' tests/integration/test_offers_hires_datetime.py)
# DELETE the function `_drop_timezone_like_flowtask` (lines 49-59 @ ed6b50e), then add:
def _drop_timezone(series: pd.Series) -> pd.Series:
    """Apply the real QuerySource drop_timezone to a one-column frame."""
    frame = series.to_frame(name="value")
    return drop_timezone(frame, "value")["value"]

# occurrences: 1 (verified: grep -c '_drop_timezone_like_flowtask(_run(' tests/integration/test_offers_hires_datetime.py)
# REPLACE `    result = _drop_timezone_like_flowtask(_run(offers_hires, func, column, infer_tz))` (line 116) with:
    result = _drop_timezone(_run(offers_hires, func, column, infer_tz))

# APPEND at end of file
@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_drop_timezone_raw_strings(offers_hires, column) -> None:
    """Raw source strings -> drop_timezone -> local wall-clock for every non-null value."""
    # FILL IN: src = _source(offers_hires, column); result = _drop_timezone(offers_hires[column]);
    # assert non-null count == len(src), no str values, and every value equals
    # _reference(v).replace(tzinfo=None) — counts-only messages (AC-2)
```

### `CHANGES.rst` (MODIFY)
```rst
# occurrences: 1 (verified: grep -c '^Unreleased$' CHANGES.rst)
# AFTER — insert below the `==========` underline of `Unreleased` (CHANGES.rst:2), before the FEAT-171 section:

Timezone transforms hosted in QuerySource (FEAT-174)
----------------------------------------------------

- New ``drop_timezone`` transform: drops timezone information keeping each
  value's own local wall-clock; mixed UTC offsets and date-only values with an
  offset (``2025-09-15-07:00`` → local midnight) are handled.
- ``change_timezone``: new keyword-only ``column``, ``tz_column`` (per-row
  target timezone) and ``default_timezone``; naive input without ``from_tz`` is
  now localized as UTC instead of being left unconverted; nonexistent DST times
  are shifted forward instead of failing the whole column.
- New ``convert_timezone`` transform: Flowtask-compatible alias of
  ``change_timezone``.
- New public helpers ``parse_datetime_series`` and ``to_wall_clock``.
```

### FILL IN checklist
- [ ] `test_offers_hires_drop_timezone_raw_strings` body — AC-2

---

## Acceptance Criteria

- [ ] AC-1: `test_offers_hires_drop_timezone_wall_clock` uses the real `drop_timezone`; the mirror is gone; all 20 cases pass with unchanged expectations.
- [ ] AC-2: the raw-string case passes for all 5 columns (local wall-clock, no `str`, non-null count unchanged).
- [ ] AC-3: `QS_OFFERS_HIRES_CSV=<path> pytest tests/integration/test_offers_hires_datetime.py` passes with **no skips**; `git ls-files artifacts/` is empty.
- [ ] AC-4: `CHANGES.rst` has the FEAT-174 entry under *Unreleased*.

---

## Validation Commands

- `pytest tests/integration/test_offers_hires_datetime.py -q`

---

## Test Specification

Spec §4 Integration Tests.

---

## Agent Instructions

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug dt-timezone-transforms --feature-id FEAT-174`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-894 and TASK-895 must be `"done"`.
3. **Verify the Codebase Contract** — re-run the greps.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands; for AC-3 point `QS_OFFERS_HIRES_CSV` at the local copy of the CSV (outside the repo).
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-896 dt-timezone-transforms verified`.
9. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none
