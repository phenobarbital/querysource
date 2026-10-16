# TASK-893: Public `parse_datetime_series` / `to_wall_clock` wrappers

**Feature**: FEAT-174 — Host Flowtask's timezone transforms in QuerySource (single datetime code base)
**Spec**: `sdd/specs/dt-timezone-transforms.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 1**. FEAT-171's parser (`_to_datetime_series`) and wall-clock
view (`_to_wall_clock`) are private. Flowtask (FEAT-567) must call them without importing
`_`-prefixed names, and TASK-894/895 build on them. Decision (spec §8 Q3): public
wrappers live in `transforms.py` (precedent: `is_datetime_column`, `transforms.py:2125`);
the private names stay because `tests/unit/test_to_datetime_mixed_offsets.py` imports them.

---

## Scope

- Add `parse_datetime_series(series, infer_tz=False, field=None)` and
  `to_wall_clock(series, field=None)` to `querysource/types/dt/transforms.py`, right
  after `_to_wall_clock`, as pure delegations.
- Create `tests/unit/test_dt_timezone_transforms.py` with the shared fixtures and the M1 test.

**NOT in scope**: `drop_timezone` (TASK-894); `change_timezone` / `convert_timezone`
(TASK-895); any change to the private helpers' behaviour.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | two public wrappers |
| `tests/unit/test_dt_timezone_transforms.py` | CREATE | fixtures + `test_public_helpers_delegate` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from typing import Any, Dict, List, Optional, Union  # verified: querysource/types/dt/transforms.py:15
import pandas as pd                                  # verified: transforms.py:20
```
Test file:
```python
import pandas as pd
from querysource.types.dt.transforms import (
    _to_datetime_series,   # verified: transforms.py:103
    _to_wall_clock,        # verified: transforms.py:152
    parse_datetime_series, # created by this task
    to_wall_clock,         # created by this task
)
```

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py  (dev @ ed6b50e)
def _to_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series  # line 103
def _to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series                             # line 152
    return _prefer_ns(parsed)                                                                              # line 176 (last line)
def _convert_datetime_column(                                                                              # line 179
```

### Does NOT Exist
- ~~`parse_datetime_series`, `to_wall_clock`~~ — created here.
- ~~exports in `querysource/types/dt/__init__.py`~~ — docstring only; do not add exports there.
- ~~`_vectorised_datetime_series`, `_warn_unparsed`~~ — only on the FEAT-171 branch (PR #673), not on `dev`; do not reference them.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/dt/transforms.py", "action": "MODIFY"},
    {"path": "tests/unit/test_dt_timezone_transforms.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#_to_datetime_series",
    "sym:querysource/types/dt/transforms.py#_to_wall_clock"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Wrappers contain **no logic** — a single `return` delegating with the same arguments (AC-1).
- If PR #673 has landed on `dev` by the time this runs, line numbers shift (~+43); anchors are verbatim — re-grep.

---

## Implementation Blueprint

### Steps (in order)
1. Re-run `grep -c 'def _convert_datetime_column(' querysource/types/dt/transforms.py` (expect 1) — *why*: insertion point.
2. Insert the two wrappers before `_convert_datetime_column` — *why*: next to the helpers they wrap.
3. Create the test file — *why*: TASK-894/895 append to it.

### `querysource/types/dt/transforms.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'def _convert_datetime_column(' querysource/types/dt/transforms.py)
# BEFORE — insert above `def _convert_datetime_column(` (verified: transforms.py:179), keeping two blank lines
def parse_datetime_series(
    series: pd.Series, infer_tz: bool = False, field: Optional[str] = None
) -> pd.Series:
    """Parse a Series to datetimes, preserving every parseable timezone-bearing value.

    Public entry point for the FEAT-171 parser (same contract as ``_to_datetime_series``).

    Args:
        series: Raw or datetime-like values.
        infer_tz: When false, heterogeneous offsets are normalized to UTC (a warning is
            logged); when true, an ``object`` Series keeps each value's own offset.
        field: Column name, used only for logging.

    Returns:
        The parsed Series with the same index and name; unparseable values become ``NaT``.
    """
    return _to_datetime_series(series, infer_tz=infer_tz, field=field)


def to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series:
    """Return naive datetime64 values holding each value's own local wall-clock.

    Public entry point for ``_to_wall_clock``: values with an offset keep their local
    time, values without an offset are unchanged, date-only values with an offset
    become local midnight, unparseable values become ``NaT``.

    Args:
        series: Values to convert.
        field: Column name, used only for logging.

    Returns:
        A naive datetime64 Series with the same index and name.
    """
    return _to_wall_clock(series, field=field)
```
**Why**: spec §2 item 1 / §8 Q3 — public names for Flowtask, zero behaviour change.

### `tests/unit/test_dt_timezone_transforms.py` (CREATE)
```python
"""FEAT-174 — timezone transforms hosted in QuerySource."""
import pandas as pd

from querysource.types.dt.transforms import (
    _to_datetime_series,
    _to_wall_clock,
    parse_datetime_series,
    to_wall_clock,
)

MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
NAIVE_AWARE = [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-09-16T01:00:00"), None]
OFFSET_DATES = ["2025-09-15-07:00", "2025-12-01-08:00", "2025-09-15Z", "2025-09-15", None]
DST_AMBIGUOUS = ["2025-11-03 09:00:00", "2025-11-02 01:30:00"]     # America/Chicago, unsorted
DST_NONEXISTENT = ["2025-03-09 02:30:00", "2025-03-10 09:00:00"]  # America/Chicago


def test_public_helpers_delegate():
    """Public wrappers equal the private helpers on MIXED, NAIVE_AWARE, naive and all-null input."""
    # FILL IN: for each input (as pd.Series(..., name="d")) and infer_tz in (False, True):
    #   pd.testing.assert_series_equal(parse_datetime_series(s, infer_tz=...), _to_datetime_series(s, infer_tz=...))
    #   pd.testing.assert_series_equal(to_wall_clock(s), _to_wall_clock(s))
    # inputs: MIXED, NAIVE_AWARE (dtype=object), ["2025-09-15 10:00", None], [None, None]
```
**Why**: AC-1 — the wrappers must not change results.

### FILL IN checklist
- [ ] `test_public_helpers_delegate` body — AC-1

---

## Acceptance Criteria

- [ ] AC-1: `parse_datetime_series` / `to_wall_clock` return exactly what `_to_datetime_series` / `_to_wall_clock` return for the same arguments.
- [ ] AC-2: the private helpers are unchanged and still importable; `pytest tests/unit/test_to_datetime_mixed_offsets.py` passes unmodified.
- [ ] AC-3: `ruff check querysource/types/dt/transforms.py` reports no new errors.

---

## Validation Commands

- `pytest tests/unit/test_dt_timezone_transforms.py -q`
- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`

---

## Test Specification

Spec §4 row `test_public_helpers_delegate` (M1).

---

## Agent Instructions

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug dt-timezone-transforms --feature-id FEAT-174`); run `python setup.py build_ext --inplace` before pytest.
2. **Verify the Codebase Contract** — re-run the greps.
3. **Update status** → `"in-progress"`.
4. **Implement** from the blueprint; complete every `# FILL IN:`.
5. **Verify** with the Validation Commands.
6. **Commit the code** — stage only the two files above.
7. **Close the task** with `scripts/sdd/close_task.sh TASK-893 dt-timezone-transforms verified`.
8. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-10-13
**Notes**: Wrappers added as pure delegations; test_public_helpers_delegate passes; FEAT-171 regression 21 passed; ruff clean. Env: Cython 0.29 in shared venv cannot build .pyx, so prebuilt (gitignored) .so files were copied from the main checkout into the worktree. Code commit 252bfb3.

**Deviations from spec**: none
