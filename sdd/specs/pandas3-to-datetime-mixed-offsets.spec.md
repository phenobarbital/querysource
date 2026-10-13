---
type: feature
base_branch: dev
projects: [querysource]
tags: [pandas3, datetime, timezone, transforms]
---

# Feature Specification: pandas 3 mixed-UTC-offset handling for the remaining datetime transforms

**Feature ID**: FEAT-171
**Date**: 2026-10-13
**Author**: Jesus Lara
**Status**: draft
**Target version**: 5.2.x

---

## 1. Motivation & Business Requirements

### Problem Statement

Under pandas>=3 (pinned `pandas>=3.0,<4`, FEAT-161), `pd.to_datetime` raises
`ValueError: Mixed timezones detected` when the values carry different UTC
offsets (e.g. DST `-07:00`/`-08:00`), **even with `errors="coerce"`**.
PR #666 (on top of #664) fixed this for `convert_to_datetime` / `to_timestamp`
with a shared helper, `_to_datetime_series(series, infer_tz=False, field=None)`,
and an explicit contract:

* `infer_tz=False` (default): normalize mixed offsets to UTC, log a warning.
* `infer_tz=True`: keep each value's own offset (`object` dtype).

Five other direct `pd.to_datetime` calls in
`querysource/types/dt/transforms.py` were left out. On pandas 3.0.6, with input
`["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]`, they
behave as follows:

| Function | Line on #666 head (`be9a3cbc`) | Mixed-offset strings | `infer_tz=True` output (object of aware Timestamps) |
|---|---|---|---|
| `to_time` | 474 | not affected: it builds naive 1900-01-01 Timestamps | n/a |
| `add_timestamp_to_time` | 540 | `AttributeError` (`.dt` on object) | `AttributeError` |
| `to_excel_date` | 1932 | `ValueError` (re-raised) | **silent data loss**: the `-08:00` value becomes `NaT` |
| `extract` | 2045 | `ValueError` (only `TypeError` is caught) | `ValueError` (tz-aware datetime cannot be cast) |
| `change_timezone` | 2268 | `ValueError` (the call is outside the `try`) | **silent data loss** (`NaT`), then the convert fails and only a `print` reports it |

**pandas 3 gotcha that also affects PR #666's helper:**
`pd.to_datetime(errors="coerce")` raises **nothing** when an `object` Series
already holds tz-aware `Timestamp`/`datetime` values with mixed offsets. It
keeps the first offset and silently coerces every value with a different
offset to `NaT`. As a result, `_to_datetime_series` never reaches its fallback
for that input. Reproduced:

```
convert_to_datetime(convert_to_datetime(df, "d", infer_tz=True), "d")
  -> [Timestamp('2025-09-15 23:30:00-0700'), NaT, NaT]   # -08:00 value lost
```

Any task that converts a column twice, or whose source yields tz-aware Python
datetimes with mixed offsets, loses rows of data without an error. During the
pandas 2 → 3 migration, this is exactly the "silently dropping the timezone, or
the datetime itself" behaviour that #666 set out to eliminate.

### Goals
- Make `_to_datetime_series` detect mixed offsets in already-parsed tz-aware
  `object` input, not only through `ValueError`, so the `infer_tz` contract
  holds for every input shape.
- Route `add_timestamp_to_time`, `to_excel_date`, `extract` and
  `change_timezone` through the shared helper. They must never silently drop a
  timezone or a value on mixed-offset input.
- Make the transforms whose output depends on which offset is used (`extract`,
  `add_timestamp_to_time`) **preserve each value's own offset**: they use each
  value's local wall-clock and fall back to UTC only for values that carry no
  offset (§8 Q2).
- Keep the current output for naive input and for single-offset tz-aware input.

### Non-Goals (explicitly out of scope)
- `to_time` (line 474 on #666): it builds naive `pd.Timestamp(year=1900, …)`
  values, so no offset can be present. It is verified unaffected and must not
  be changed.
- Calls with an explicit `format=`/`unit=` (`to_datetime`, `convert_to_date`,
  `datetime_format`, `from_unix` …, #666 lines 1513–1666) and the Excel serial
  conversion (line 1983). If these show the same issue, it is tracked
  separately in `issue:581d7f51d6f3` (§8 Q4).
- Rewriting the other `print`/`except Exception` patterns in `transforms.py`
  beyond the four functions touched here.

---

## 2. Architectural Design

### Overview

All datetime parsing in the four affected transforms goes through two private
helpers in `transforms.py`:

1. **`_to_datetime_series`** (existing, from #666, hardened here): before
   calling `pd.to_datetime`, check whether an `object` Series holds tz-aware
   values with more than one distinct `utcoffset()`. If it does, resolve it the
   same way as the `ValueError` path: `infer_tz=False` → `utc=True` plus a
   warning; `infer_tz=True` → per-value Timestamps (`object`).
2. **`_to_wall_clock`** (new): returns a **naive** `datetime64[ns]` Series for
   transforms that need `.dt` accessors or naive output. It always preserves
   offsets (it parses with `_to_datetime_series(..., infer_tz=True)`):
   * a value that carries an offset → its own local wall-clock (single-offset
     and single-tz input give exactly today's result);
   * a value without an offset (naive) → used as is, i.e. read as UTC wall-clock
     (no conversion, so both readings give the same components);
   * unparseable → `NaT`.

Per-site behaviour:

| Function | Change |
|---|---|
| `to_excel_date` | Parse with `_to_datetime_series`. Mixed offsets go to UTC, matching its documented "convert to UTC and remove the timezone" contract. No `infer_tz` parameter: its output is UTC-naive by definition. |
| `change_timezone` | Parse with `_to_datetime_series` inside the existing `try`. If the parsed values are tz-aware, skip `tz_localize(from_tz)` and `tz_convert(to_tz)` directly, because the values carry their own offsets (§8 Q3). Naive input keeps the current `tz_localize(from_tz).tz_convert(to_tz)` path. Uses `logging` instead of `print`. |
| `extract` | No new parameter. When `df[column]` is not already a datetime column, components are taken from `_to_wall_clock(df[column])`, i.e. each value's own offset is preserved. Catches the mixed-offset path instead of letting the `ValueError` escape. |
| `add_timestamp_to_time` | No new parameter. Takes the date and time parts from `_to_wall_clock` of each input column (each value's own offset preserved), so `object`/mixed columns no longer fail on `.dt`. |

### Component Diagram
```
convert_to_datetime / to_timestamp ──→ _convert_datetime_column ──→ _to_datetime_series (hardened, M1)
to_excel_date, change_timezone  ─────────────────────────────────→ _to_datetime_series
extract, add_timestamp_to_time  ──→ _to_wall_clock (new, M1) ────→ _to_datetime_series
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `_to_datetime_series` (PR #666) | modifies | adds detection of mixed offsets in tz-aware `object` input |
| `_convert_datetime_column` (PR #666) | uses (unchanged) | gains the hardening for free |
| `to_excel_date`, `change_timezone`, `extract`, `add_timestamp_to_time` | modifies | route through the helpers |
| `is_datetime_column` / `is_time_column` | uses (unchanged) | `extract` keeps its existing guards |

### Data Models
None.

### New Public Interfaces
None. All public signatures are unchanged. `extract` and
`add_timestamp_to_time` get no `infer_tz` parameter: they always preserve each
value's offset (§8 Q2), so a flag defaulting to UTC would contradict that
decision.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: helper hardening + `_to_wall_clock` | yes | skeletons below; `infer_tz` semantics fixed by #666 | — |
| M2: UTC-semantics sites (`to_excel_date`, `change_timezone`) | yes | per-site table in §2; `from_tz` ignored with a warning on aware input (§8 Q3) | — |
| M3: wall-clock sites (`extract`, `add_timestamp_to_time`) | yes | per-site table in §2; per-value offset preserved, no new parameter (§8 Q2) | — |
| M4: tests | yes | §4 table | — |

### Module 1: Helper hardening
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: Make `_to_datetime_series` detect mixed offsets for
  every input shape, and add `_to_wall_clock`.
- **Depends on**: PR #666 (`_to_datetime_series` must exist on `dev`)
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/dt/transforms.py:30 (PR #666 head be9a3cbc)
  def _has_mixed_offsets(series: pd.Series) -> bool:
      """Return True when an object Series holds tz-aware datetime values
      with more than one distinct utcoffset(). Nulls and naive values are ignored."""

  def _to_datetime_series(  # verified: transforms.py:30 on be9a3cbc
      series: pd.Series, infer_tz: bool = False, field: Optional[str] = None
  ) -> pd.Series:
      """(unchanged contract) Also routes to the mixed-offset branch when
      _has_mixed_offsets(series) is True, before calling pd.to_datetime."""

  def _to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series:
      """Parse via _to_datetime_series(series, infer_tz=True, field=field) and
      return naive datetime64[ns]: each value with an offset -> its own local
      wall-clock; naive values -> unchanged (UTC wall-clock); unparseable -> NaT."""
  ```

### Module 2: UTC-semantics sites
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: `to_excel_date` and `change_timezone` per the §2 table.
- **Depends on**: Module 1
- **Interface Skeleton** (signatures unchanged):
  ```python
  def to_excel_date(df: pd.DataFrame, field: str, column: str = None):  # verified: transforms.py:1917
      """Mixed offsets are normalized to UTC, then made naive. Errors are still re-raised."""
  def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):  # verified: transforms.py:2258
      """tz-aware input -> tz_convert(to_tz) (from_tz ignored, with a logged warning when given);
      naive input -> tz_localize(from_tz).tz_convert(to_tz) as today."""
  ```

### Module 3: Wall-clock sites
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: `extract` and `add_timestamp_to_time` per the §2 table.
- **Depends on**: Module 1
- **Interface Skeleton**:
  ```python
  def extract(df: pd.DataFrame, field: str, column="", to_date=None, value="day"):  # verified: transforms.py:2023
      """Non-datetime columns are parsed with _to_wall_clock (offsets preserved)."""
  def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str):  # verified: transforms.py:529
      """Date/time parts are taken from _to_wall_clock of each column."""
  ```

### Module 4: Tests
- **Path**: `tests/unit/test_to_datetime_mixed_offsets.py` (new)
- **Responsibility**: The §4 tests.
- **Depends on**: Modules 1–3

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_reconvert_infer_tz_output_keeps_all_values` | M1 | `convert_to_datetime` on its own `infer_tz=True` output: no `NaT` introduced; default → UTC, `infer_tz=True` → offsets kept |
| `test_aware_python_datetimes_mixed_offsets` | M1 | `object` Series of aware `datetime.datetime` values with `-07:00`/`-08:00` behaves like the string case |
| `test_has_mixed_offsets` | M1 | true for mixed aware; false for naive, single offset, all-null |
| `test_to_wall_clock_modes` | M1 | naive passthrough; single offset → local wall-clock; mixed → each value's own wall-clock; naive + aware mix |
| `test_to_excel_date_mixed_offsets` | M2 | mixed strings and `infer_tz` object → UTC-naive values, no `NaT` |
| `test_change_timezone_mixed_and_aware` | M2 | mixed strings, single-offset aware and `infer_tz` object input all convert to `to_tz` with no `NaT`; naive + `from_tz` unchanged |
| `test_extract_mixed_offsets` | M3 | `value="day"` on `2025-09-15T23:30-07:00` mixed with a `-08:00` value → 15 (own offset preserved, not 16 as UTC would give) |
| `test_add_timestamp_to_time_mixed` | M3 | mixed/object inputs no longer raise `AttributeError` |
| `test_naive_and_single_offset_regressions` | M2/M3 | all four functions match their current output for naive and single-offset input |

### Integration Tests
None (pure DataFrame transforms).

### Test Data / Fixtures
```python
MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
```

---

## 5. Acceptance Criteria

- [ ] No `NaT` is introduced for a parseable value in any of the four functions, or in `convert_to_datetime`/`to_timestamp`, for mixed-offset strings, `infer_tz=True` output, or aware Python datetimes.
- [ ] `infer_tz` keeps #666's meaning wherever it exists (`convert_to_datetime`/`to_timestamp`): `False` → UTC normalization plus a logged warning naming the field; `True` → each value's own offset.
- [ ] `extract` and `add_timestamp_to_time` preserve each value's own offset (local wall-clock); only values without an offset are read as UTC.
- [ ] `change_timezone` with tz-aware input and an explicit `from_tz` logs a warning and ignores `from_tz`.
- [ ] `to_excel_date`, `extract`, `change_timezone` and `add_timestamp_to_time` no longer raise on mixed-offset input.
- [ ] Output is unchanged for naive and single-offset input (regression test).
- [ ] `to_time` is untouched.
- [ ] `pytest tests/unit/test_to_datetime_mixed_offsets.py tests/unit/test_pandas3_transforms.py tests/test_mixed_tz_datetime.py` passes.
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors (baseline: 20 pre-existing).
- [ ] `CHANGES.rst` entry under *Unreleased*.

---

## 6. Codebase Contract

### Verified Imports
```python
import numpy as np                              # verified: transforms.py:17
import pandas as pd                             # verified: transforms.py:19
from typing import Any, Dict, List, Optional, Union  # verified: transforms.py:14
from navconfig.logging import logging           # verified: transforms.py:22
```

### Existing Class Signatures
```python
# querysource/types/dt/transforms.py  (PR #666 head be9a3cbc)
def _to_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series  # line 30
def _convert_datetime_column(df: pd.DataFrame, field: str, infer_tz: bool = False) -> pd.DataFrame          # line 74
def to_time(df, field, replace_nulls=False, value="00:00:00", format="%I:%M %p", fallback_format="%I:%M%p", as_timedelta=False)  # line 410
def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str)                                  # line 529
def to_excel_date(df: pd.DataFrame, field: str, column: str = None)                                            # line 1917
def is_datetime_column(column) -> bool                                                                         # line 2003
def is_time_column(column) -> bool                                                                             # line 2013
def extract(df: pd.DataFrame, field: str, column="", to_date=None, value="day")                                # line 2023
def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC")                     # line 2258
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `_has_mixed_offsets` | `_to_datetime_series` | call before `pd.to_datetime` | `transforms.py:53` |
| `_to_wall_clock` | `_to_datetime_series` | call | `transforms.py:30` |

### Does NOT Exist (Anti-Hallucination)
- ~~`_to_wall_clock`~~, ~~`_has_mixed_offsets`~~ — introduced by this spec.
- ~~`_to_datetime_series` / `_convert_datetime_column` on `dev`~~ — they exist only on PR #666 (`fix/datetime-infer-tz`) until it merges.
- ~~`infer_tz` parameter on `extract`, `add_timestamp_to_time`, `to_excel_date`, `change_timezone`~~ — does not exist and is NOT added by this spec.
- ~~`pd.to_datetime(..., errors="coerce")` raising on mixed aware `object` input~~ — it does not raise; it coerces to `NaT` (pandas 3.0.6, verified).

### Edit Sites (Blueprint Anchors)

Verified against: `be9a3cbc` (PR #666 head, branch `fix/datetime-infer-tz`). `dev` line numbers differ until #666 merges: 403, 469, 1863, 1976, 2193.

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | `def _to_datetime_series(` | `transforms.py:30` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str):` | `transforms.py:529` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `        df[column] = pd.to_datetime(df[column], errors='coerce')` (in `to_excel_date`) | `transforms.py:1932` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `def extract(df: pd.DataFrame, field: str, column="", to_date=None, value="day"):` | `transforms.py:2023` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `                df[column] = pd.to_datetime(df[column])` (in `extract`) | `transforms.py:2045` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):` | `transforms.py:2258` | 1 |
| `tests/unit/test_to_datetime_mixed_offsets.py` | CREATE | — | — | — |
| `CHANGES.rst` | MODIFY | `Unreleased` | `CHANGES.rst:1` | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Reuse the #666 helpers; never call `pd.to_datetime` directly in the four touched functions.
- Use `logging.warning` / `logging.error` (module-level `navconfig.logging`), not `print`, in touched functions.
- Detect mixed offsets with `utcoffset()` on non-null tz-aware values. Do not rely on `pd.to_datetime` raising.

### Known Risks / Gotchas
- **`extract` on mixed input**: components come from each value's own offset,
  so a `23:30-07:00` value yields day 15 (UTC would give 16). This only affects
  inputs that currently raise, so no working task changes output.
- **`change_timezone` with tz-aware input and `from_tz`**: today this fails in
  `tz_localize`, prints, and returns the column unconverted. After the change
  it converts. That is a fix, but it is visible in the output.
- **Performance**: `_has_mixed_offsets` scans `object` columns in Python. Only
  run it when `series.dtype == object`, and stop at the second distinct offset.
- **Cython builds**: tests in a worktree need `python setup.py build_ext --inplace` first. `transforms.py` itself is pure Python.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=3.0,<4` (already pinned) | behaviour targeted |

---

## 8. Open Questions

- [x] Q1: Should M1 (the `_to_datetime_series` hardening) be folded into PR #666 before it merges? — *Owner: Jesus Lara*: on this spec. M1 stays in FEAT-171; #666 merges as is.
- [x] Q2: Default wall-clock for mixed offsets in `extract` / `add_timestamp_to_time`? — *Owner: Jesus Lara*: "if we can preserve the offset, is preferable to preserve the offset before default to UTC, only UTC if date doesn't have offset". Each value's own offset is preserved; UTC applies only to values without an offset. No `infer_tz` parameter on these two functions.
- [x] Q3: `change_timezone` with tz-aware input and an explicit `from_tz`? — *Owner: Jesus Lara*: warning (ignore `from_tz`, log a warning).
- [x] Q4: Audit the `format=`/`unit=` call sites in a follow-up ledger issue? — *Owner: Jesus Lara*: yes, filed as `issue:581d7f51d6f3` (confirmed: `format=` with `%z` raises on mixed offsets).

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted brainstorm/proposal exploration doc)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-171, branched from `dev` **after PR #666 merges**.
- **Module graph**: M2 → M1, M3 → M1 (both call `_to_datetime_series` / `_to_wall_clock`); M4 → M1–M3.
- **Shared files**: `querysource/types/dt/transforms.py` (M1, M2, M3). Their tasks are serialized.
- **Exclusive resources**: none (pure Python; a worktree only needs the in-place Cython build to import the package).
- **Cross-feature dependencies**: PR #666 (`fix/datetime-infer-tz`) must be merged into `dev` first.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-13 | Jesus Lara | Initial draft |
| 0.2 | 2026-10-13 | Jesus Lara | §8 Q1–Q4 resolved: M1 stays here; extract/add_timestamp_to_time preserve per-value offsets (no infer_tz); from_tz warning; format/unit audit filed |
