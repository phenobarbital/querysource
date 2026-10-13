# TASK-888: Harden `_to_datetime_series` and add `_to_wall_clock`

**Feature**: FEAT-171 — pandas 3 mixed-UTC-offset handling for the remaining datetime transforms
**Spec**: `sdd/specs/pandas3-to-datetime-mixed-offsets.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none *(but PR #666 must be merged into `dev` first — see Precondition)*
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 1** (§2 Overview items 1–2). PR #666 added
`_to_datetime_series(series, infer_tz=False, field=None)`, which detects mixed
UTC offsets only through `pd.to_datetime` raising `ValueError`. On pandas 3.0.6
that misses three silent-loss cases (spec §1):

1. `object` input that already holds tz-aware values with mixed offsets (e.g. its own
   `infer_tz=True` output, or aware Python datetimes): `errors="coerce"` keeps the
   first offset and turns the others into `NaT`, without raising;
2. naive/aware mixtures in either order;
3. mixed-offset ISO strings with different fractional precision
   (`…23:30:00-07:00` and `…08:00:00.123-08:00`): the second value is lost.

It also misreads Workday `xs:date` values such as `2025-09-15-07:00` as the
naive time `2025-09-15 07:00:00` (every `Candidate_Start_Date` in
`artifacts/get_offers_and_hires_1.6.csv`).

**Precondition**: `grep -c 'def _to_datetime_series(' querysource/types/dt/transforms.py`
must print `1` in the feature worktree. If it prints `0`, PR #666 is not merged yet —
STOP and report; do not re-implement #666.

---

## Scope

- Add `_parse_offset_dates`, `_parse_datetime_value` and `_has_mixed_timezones`.
- Rewrite the body of `_to_datetime_series` to parse non-datetime input per value,
  then classify timezone state (signature unchanged).
- Add `_to_wall_clock`.
- Create `tests/unit/test_to_datetime_mixed_offsets.py` with the spec §4 **M1** tests.

**NOT in scope**: `to_excel_date`, `change_timezone` (TASK-889); `extract`,
`add_timestamp_to_time` (TASK-890); cross-cutting precision/regression tests and
`CHANGES.rst` (TASK-891); the real-data CSV test (TASK-892).
`_convert_datetime_column`, `convert_to_datetime` and `to_timestamp` are not edited —
they gain the hardening through `_to_datetime_series`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | new helpers + `_to_datetime_series` body |
| `tests/unit/test_to_datetime_mixed_offsets.py` | CREATE | M1 unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import datetime                                      # verified: transforms.py:7  (module import — use datetime.datetime)
from typing import Any, Dict, List, Optional, Union  # verified: transforms.py:14
import numpy as np                                   # verified: transforms.py:17
import pandas as pd                                  # verified: transforms.py:19
from navconfig.logging import logging                # verified: transforms.py:22
import re                                            # NOT imported yet — ADD it to the stdlib import block (after `import locale`, transforms.py:10)
```
Test file:
```python
import datetime
import pandas as pd
from querysource.types.dt.transforms import (
    _has_mixed_timezones, _parse_offset_dates, _to_datetime_series, _to_wall_clock,
    convert_to_datetime, to_timestamp,
)
```

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py (PR #666 head be9a3cbc)
def _to_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series  # line 30
def _convert_datetime_column(df: pd.DataFrame, field: str, infer_tz: bool = False) -> pd.DataFrame          # line 74
    # handles DatetimeTZDtype (as_unit ns), object (left as is), naive (astype datetime64[ns]) — do not edit
def to_timestamp(df, field, remove_nat=False, infer_tz=False)        # line 105
def convert_to_datetime(df, field, remove_nat=False, infer_tz=False) # line 1487
```

### Does NOT Exist
- ~~`_has_mixed_timezones`~~, ~~`_parse_offset_dates`~~, ~~`_parse_datetime_value`~~, ~~`_to_wall_clock`~~ — created by this task.
- ~~`import re` in transforms.py~~ — must be added.
- ~~`pd.to_datetime(..., errors="coerce")` raising on mixed aware `object` input~~ — it silently returns `NaT`; never rely on the exception.
- ~~`from datetime import datetime` in transforms.py~~ — the module is imported as `datetime`; the class is `datetime.datetime`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/dt/transforms.py", "action": "MODIFY"},
    {"path": "tests/unit/test_to_datetime_mixed_offsets.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#_to_datetime_series",
    "sym:querysource/types/dt/transforms.py#_convert_datetime_column",
    "sym:querysource/types/dt/transforms.py#convert_to_datetime",
    "sym:querysource/types/dt/transforms.py#to_timestamp"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Datetime-dtype input keeps the existing fast path (returned unchanged): no Python loop for already-parsed columns.
- Parse each non-null value once with `pd.Timestamp`; only individually unparseable values become `NaT`. Never let pandas infer one format from the first row — that is what loses the mixed-precision value.
- Classify by timezone identity: naive → key `None`, aware → `str(value.tz)`. More than one key is heterogeneous. A single named zone across DST (one `tz`, two offsets) is homogeneous and keeps its datetime dtype.
- Preserve index and name everywhere (`Series.map` does this).
- Logging uses module-level `logging.warning` — never `print`.

---

## Implementation Blueprint

### Steps (in order)
1. Verify the Precondition — *why*: every block below edits #666's helper.
2. Add `import re` after `import locale` — *why*: the xs:date regex needs it and it is not imported.
3. Insert the three new helpers directly above `def _to_datetime_series(` — *why*: `_to_datetime_series` calls them; keeping them adjacent keeps the parsing pipeline readable.
4. Replace the body of `_to_datetime_series` (keep its signature, update its docstring) — *why*: exception-based detection is the bug.
5. Insert `_to_wall_clock` directly below `_to_datetime_series` and above `def _convert_datetime_column(` — *why*: TASK-890 imports it.
6. Create the test file and fill in every test — *why*: AC-1..AC-5.

### `querysource/types/dt/transforms.py` (MODIFY) — new helpers
```python
# occurrences: 1 (verified: grep -c 'def _to_datetime_series(' querysource/types/dt/transforms.py)
# BEFORE — insert above `def _to_datetime_series(` (verified: transforms.py:30 @ be9a3cbc)
_OFFSET_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})$")


def _parse_offset_dates(series: pd.Series) -> pd.Series:
    """Rewrite date-only strings carrying a UTC offset to midnight at that offset.

    pandas reads xs:date values such as ``2025-09-15-07:00`` as the naive time
    ``2025-09-15 07:00``. This rewrites them to ``2025-09-15T00:00:00-07:00``
    (also ``+05:30`` and ``Z``). Every other value is returned unchanged.

    Args:
        series: Raw values.

    Returns:
        A Series with the same index and name.
    """
    return series.map(
        lambda v: _OFFSET_DATE_RE.sub(r"\1T00:00:00\2", v) if isinstance(v, str) else v
    )


def _parse_datetime_value(value: Any) -> Any:
    """Parse one value to ``pd.Timestamp``; nulls and unparseable values become ``NaT``."""
    try:
        if value is None or pd.isna(value):
            return pd.NaT
    except (TypeError, ValueError):
        pass  # list-like values: let pd.Timestamp reject them below
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def _has_mixed_timezones(series: pd.Series) -> bool:
    """Return True when parsed values do not share one timezone state.

    Naive values count as their own state, so naive/aware mixtures are
    heterogeneous. Aware values are keyed by ``str(value.tz)``: distinct fixed
    offsets are heterogeneous, one named zone across DST is not. Nulls are ignored.

    Args:
        series: Independently parsed ``pd.Timestamp``/``NaT`` values.

    Returns:
        True for multiple timezone states.
    """
    states: set = set()
    for value in series:
        if value is pd.NaT or pd.isna(value):
            continue
        states.add(None if value.tzinfo is None else str(value.tz))
        if len(states) > 1:
            return True
    return False
```
**Why this shape**: spec §2 item 1 and §3 M1 skeleton. `_has_mixed_timezones`
and `_parse_offset_dates` names/signatures are fixed by the spec; `_parse_datetime_value`
is a private extraction so the per-value loop has one testable unit.

### `querysource/types/dt/transforms.py` (MODIFY) — `_to_datetime_series` body
```python
# occurrences: 1 (verified: grep -c 'def _to_datetime_series(' querysource/types/dt/transforms.py)
# REPLACE — the body (docstring through the final `return`) of `_to_datetime_series` (transforms.py:30-71 @ be9a3cbc); keep the signature
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    parsed = _parse_offset_dates(series).map(_parse_datetime_value).astype(object)
    if _has_mixed_timezones(parsed):
        if infer_tz:
            return parsed
        logging.warning(
            f"Datetime column {field!r} has mixed timezones/offsets; "
            "normalizing to UTC (naive values read as UTC). Pass infer_tz=True "
            "to keep each value's own offset."
        )
        return parsed.map(
            lambda v: v if v is pd.NaT else (
                v.tz_localize("UTC") if v.tzinfo is None else v.tz_convert("UTC")
            )
        ).astype("datetime64[ns, UTC]")
    # FILL IN: assemble the homogeneous result with pd.to_datetime(parsed) — values are
    # already Timestamps, so no format inference happens; bounded by AC-4 (homogeneous
    # naive / single-timezone input keeps today's dtype semantics, index and name).
```
**Why**: per-value parsing is the only way not to lose the mixed-precision value,
and classifying *after* parsing catches already-aware `object` input. The
docstring must state: datetime dtypes pass through; heterogeneous input → `False`:
UTC + warning, `True`: `object` keeping offsets and naive values; no parseable value lost.

### `querysource/types/dt/transforms.py` (MODIFY) — `_to_wall_clock`
```python
# occurrences: 1 (verified: grep -c 'def _convert_datetime_column(' querysource/types/dt/transforms.py)
# BEFORE — insert above `def _convert_datetime_column(` (verified: transforms.py:74 @ be9a3cbc)
def _to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series:
    """Return naive ``datetime64[ns]`` local wall-clock values.

    Parses with ``_to_datetime_series(series, infer_tz=True)``: each value with
    an offset keeps its own local wall-clock; naive values are unchanged (read
    as UTC wall-clock); unparseable values become ``NaT``.

    Args:
        series: Values to convert.
        field: Column name, used only for logging.

    Returns:
        A naive ``datetime64[ns]`` Series with the same index and name.
    """
    parsed = _to_datetime_series(series, infer_tz=True, field=field)
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_localize(None).astype("datetime64[ns]")
    if parsed.dtype == object:
        parsed = pd.to_datetime(parsed.map(
            lambda v: v if v is pd.NaT or v.tzinfo is None else v.tz_localize(None)
        ))
    return parsed.astype("datetime64[ns]")
```
**Why**: spec §2 item 2; TASK-890 builds `extract` and `add_timestamp_to_time` on it.

### `tests/unit/test_to_datetime_mixed_offsets.py` (CREATE)
```python
"""FEAT-171: pandas 3 mixed-offset datetime parsing (helpers and callers)."""
import datetime

import pandas as pd

from querysource.types.dt.transforms import (
    _has_mixed_timezones,
    _parse_offset_dates,
    _to_datetime_series,
    _to_wall_clock,
    convert_to_datetime,
    to_timestamp,
)

MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
NAIVE_AWARE = [
    pd.Timestamp("2025-09-15T23:30:00-07:00"),
    pd.Timestamp("2025-09-16T01:00:00"),
    None,
]


def test_reconvert_infer_tz_output_keeps_all_values():
    """convert_to_datetime on its own infer_tz=True output introduces no NaT."""
    # FILL IN: default -> UTC (06:30Z, 16:00Z, NaT); infer_tz=True -> offsets -7/-8 kept


def test_aware_python_datetimes_mixed_offsets():
    """object Series of aware datetime.datetime values behaves like the string case."""
    # FILL IN: datetime.timezone(datetime.timedelta(hours=-7/-8)); both infer_tz modes


def test_has_mixed_timezones():
    """True: mixed aware, naive/aware (both orders), distinct zones. False: naive, one zone, all-null."""
    # FILL IN


def test_naive_aware_mixtures_keep_all_values(caplog):
    """False -> UTC with naive read as UTC and a warning naming the field; True -> values kept."""
    # FILL IN: Timestamp, Python datetime and string input, both orders; assert values,
    # null mask, index and name; caplog contains the field name


def test_to_wall_clock_modes():
    """naive passthrough; single offset -> local; mixed -> each value's own wall-clock."""
    # FILL IN: include NAIVE_AWARE; result dtype is datetime64[ns]


def test_offset_date_parsed_as_midnight():
    """'2025-09-15-07:00' -> 2025-09-15T00:00-07:00; '2025-09-15Z' -> midnight UTC; plain stays naive."""
    # FILL IN: cover _parse_offset_dates directly and through convert_to_datetime/to_timestamp
```
**Why**: TASK-889/890/891 append their tests to this file; keep the constants at module level so they can reuse them.

### FILL IN checklist
- [ ] `_to_datetime_series` homogeneous assembly — `pd.to_datetime(parsed)`; bounded by AC-4
- [ ] `_to_datetime_series` docstring — updated per the "Why" above
- [ ] all six test bodies — bounded by spec §4 M1 rows and AC-1..AC-5

---

## Acceptance Criteria

- [ ] AC-1: no parseable value becomes `NaT` for mixed-offset strings, mixed precision, aware Python datetimes, naive/aware mixtures (either order) or re-conversion of `infer_tz=True` output.
- [ ] AC-2: heterogeneous input — `infer_tz=False` → `datetime64[ns, UTC]` (naive read as UTC) plus one warning naming the field; `infer_tz=True` → `object` with each offset kept and naive values naive.
- [ ] AC-3: `YYYY-MM-DD±HH:MM` / `YYYY-MM-DDZ` parse as midnight at that offset; plain `YYYY-MM-DD` stays naive.
- [ ] AC-4: homogeneous naive and single-timezone input keep their current dtype; datetime-dtype input is returned unchanged; index and name are preserved.
- [ ] AC-5: `_to_wall_clock` returns naive `datetime64[ns]` local wall-clock values.
- [ ] `pytest tests/test_mixed_tz_datetime.py` still passes (PR #666's tests).
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors (baseline 20).

---

## Validation Commands

- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`
- `pytest tests/test_mixed_tz_datetime.py -q`

---

## Test Specification

See the CREATE block above — six tests, spec §4 rows tagged M1.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug pandas3-to-datetime-mixed-offsets --feature-id FEAT-171`).
   Run `python setup.py build_ext --inplace` there before pytest (the worktree has no compiled extensions).
2. **Check the Precondition** (PR #666 merged) before anything else.
3. **Verify the Codebase Contract**, re-running every `grep -c` above.
4. **Update status** in `sdd/tasks/index/pandas3-to-datetime-mixed-offsets.json` → `"in-progress"`.
5. **Implement** from the blueprint; complete every `# FILL IN:`.
6. **Verify** with the Validation Commands.
7. **Commit the code** — stage only the two files above.
8. **Close the task** with `scripts/sdd/close_task.sh TASK-888 pandas3-to-datetime-mixed-offsets verified`.
9. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*
