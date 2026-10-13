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

**Date-only values with an offset are misread (found in real data).**
Workday exports carry `xs:date` values such as `2025-09-15-07:00` (a date plus
a UTC offset). pandas 3.0.6, and so `_to_datetime_series`, parses these as the
**naive time** `Timestamp('2025-09-15 07:00:00')`: the offset becomes the time
of day, with no error and no `NaT`. In
`artifacts/get_offers_and_hires_1.6.csv` (pipe-delimited, 31,605 rows) all
1,459 non-null `Candidate_Start_Date` values are corrupted this way by
`convert_to_datetime` / `to_timestamp`, in both `infer_tz` modes.

Review reproduced two further silent-loss cases on pandas 3.0.6:

* An `object` Series containing one naive and one aware Timestamp loses the
  second value under `errors="coerce"`, in either ordering. Counting only
  distinct aware offsets cannot detect this mixture.
* Mixed-offset ISO strings with differing fractional-second precision, such as
  `2025-09-15T23:30:00-07:00` and `2025-12-01T08:00:00.123-08:00`, lose the
  second value without raising, even with `utc=True`.

The helper must therefore preserve individually parseable values before
classifying their timezone state; exception-only fallback is insufficient.

### Goals
- Make `_to_datetime_series` preserve mixed-offset and naive/aware input,
  including already-parsed objects and ISO strings with differing precision,
  without relying on `ValueError` to detect loss.
- Route `add_timestamp_to_time`, `to_excel_date`, `extract` and
  `change_timezone` through the shared helper. They must never silently drop a
  timezone or a value on mixed-offset input.
- Make the transforms whose output depends on which offset is used (`extract`,
  `add_timestamp_to_time`) **preserve each value's own offset**: they use each
  value's local wall-clock and fall back to UTC only for values that carry no
  offset (§8 Q2).
- Keep the current output for naive input and for single-offset tz-aware input,
  including timezone-bearing `extract(value="ldom")` results. The intentional
  `change_timezone` correction described in §8 Q3 is the exception.

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

1. **`_to_datetime_series`** (existing, from #666, hardened here): keep the
   existing fast path for datetime dtypes. For other input, including pandas
   string dtypes and `object`, parse each non-null value independently with
   `pd.Timestamp`; only individually unparseable values become `NaT`. Preserve
   the index and name. Classify the resulting values before assembling a
   datetime Series; never infer one string format from the first row.
   * Multiple aware offsets **or a mixture of naive and aware values** use
     the heterogeneous branch. `infer_tz=False` normalizes the parsed values
     to UTC, treating naive values as UTC, and logs a warning naming the
     field. `infer_tz=True` returns an `object` Series retaining each aware
     value's offset and leaving naive values naive.
   * Homogeneous naive or single-timezone input retains its existing datetime
     dtype semantics; nulls do not affect classification. Do not coerce
     successfully parsed values when assembling the result. Values that
     cannot share a timezone dtype follow the heterogeneous branch as well.
2. **`_to_wall_clock`** (new): returns a **naive** `datetime64[ns]` Series for
   transforms that need `.dt` accessors or naive output. It preserves local
   wall-clock components, then removes timezone metadata (it parses with
   `_to_datetime_series(..., infer_tz=True)`):
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
| `extract` | No new parameter. Parse non-datetime input with `_to_datetime_series(..., infer_tz=True)` and retain those parsed values in the source column. Numeric components and period output use a temporary `_to_wall_clock` view when needed. For `value="ldom"`, apply `MonthEnd(0)` to the timezone-preserving parsed values, per value for heterogeneous objects; retain each value's timezone/offset, local time, and nulls. Never route `ldom` output through the naive view. |
| `add_timestamp_to_time` | No new parameter. Takes the date and time parts from `_to_wall_clock` of each input column (each value's own offset preserved), so `object`/mixed columns no longer fail on `.dt`. |

### Component Diagram
```
convert_to_datetime / to_timestamp ──→ _convert_datetime_column ──→ _to_datetime_series (hardened, M1)
to_excel_date, change_timezone  ─────────────────────────────────→ _to_datetime_series
extract, add_timestamp_to_time  ──→ _to_wall_clock (new, M1) ────→ _to_datetime_series
extract (source parsing / ldom) ────────────────────────────────→ _to_datetime_series(infer_tz=True)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `_to_datetime_series` (PR #666) | modifies | parses non-datetime values independently and classifies mixed offsets and naive/aware mixtures before dtype assembly |
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
| M5: real-data validation (final) | yes | §3 Module 5; column list and expectations fixed below | — |

### Module 1: Helper hardening
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: Make `_to_datetime_series` detect mixed offsets for
  every input shape, and add `_to_wall_clock`.
- **Depends on**: PR #666 (`_to_datetime_series` must exist on `dev`)
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/dt/transforms.py:30 (PR #666 head be9a3cbc)
  def _has_mixed_timezones(series: pd.Series) -> bool:
      """Inspect independently parsed Timestamp/NaT values. Return True for
      multiple aware offsets, naive/aware mixtures, or incompatible timezones.
      Ignore nulls, but track naive values separately from aware offsets."""

  def _to_datetime_series(  # verified: transforms.py:30 on be9a3cbc
      series: pd.Series, infer_tz: bool = False, field: Optional[str] = None
  ) -> pd.Series:
      """Parse non-datetime input per value, then classify timezone state.
      Heterogeneous input: False -> UTC (naive interpreted as UTC) with warning;
      True -> object preserving offsets and naive values. No valid value lost."""

  def _parse_offset_dates(series: pd.Series) -> pd.Series:
      """Rewrite date-only strings carrying a UTC offset (xs:date, e.g.
      '2025-09-15-07:00', '2025-09-15+05:30', '2025-09-15Z') to midnight at
      that offset ('2025-09-15T00:00:00-07:00') so they parse as tz-aware.
      Called by _to_datetime_series before parsing; every other value
      (including full timestamps and plain 'YYYY-MM-DD') is returned unchanged."""

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
      """Preserve parsed source timezones; use a wall-clock view for components.
      ldom applies MonthEnd(0) to timezone-preserving values, not the naive view."""
  def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str):  # verified: transforms.py:529
      """Date/time parts are taken from _to_wall_clock of each column."""
  ```

### Module 4: Tests
- **Path**: `tests/unit/test_to_datetime_mixed_offsets.py` (new)
- **Responsibility**: The §4 tests.
- **Depends on**: Modules 1–3

---

### Module 5: Real-data validation on `get_offers_and_hires_1.6.csv` (final task)
- **Path**: `tests/integration/test_offers_hires_datetime.py` (new)
- **Responsibility**: Run `convert_to_datetime` and `to_timestamp`, in both
  `infer_tz` modes, over every date/time column of the Workday export and
  assert that no value is lost, misread or left as a string.
- **Depends on**: Modules 1–4 (runs last)
- **Data**: `artifacts/get_offers_and_hires_1.6.csv`. It is untracked, 79 MB
  and contains candidate PII. It is **never committed or copied into the repo**:
  - the path comes from env `QS_OFFERS_HIRES_CSV`, defaulting to
    `<repo root>/artifacts/get_offers_and_hires_1.6.csv`;
  - the module calls `pytest.skip` when the file is missing (always the case in
    a fresh worktree; point the env var at the main checkout's copy);
  - assertion messages report counts and column names only, never row values.
- **Load contract**: `pd.read_csv(path, sep="|", dtype=str, usecols=COLUMNS)`
  (the file is pipe-delimited; a default comma read fails at line 6).
- **Columns** (profiled on 2026-10-13; all mix `-07:00`/`-08:00`):

  | Column | Non-null | Format |
  |---|---|---|
  | `Candidate_Start_Date` | 1,459 | `YYYY-MM-DD±HH:MM` (date-only with offset) |
  | `Job_Requisition_Date_Job_Posted` | 31,605 | `YYYY-MM-DDTHH:MM:SS.fff±HH:MM` |
  | `Offer_Event_Offer_Sent` | 1,713 | same |
  | `Offer_Event_Offer_Completed` | 1,680 | same |
  | `Job_Application_Ready_for_Hire_Completed_Date` | 1,105 | same |

- **Expectations**, for each column × {`convert_to_datetime`, `to_timestamp`} × `infer_tz` ∈ {False, True}:
  - the non-null count is unchanged and no `str` value remains;
  - every parsed value is the same instant as the source string (compare with
    an independent per-value reference: `datetime.fromisoformat`, with the
    date-only form expanded to `T00:00:00` first);
  - `infer_tz=False` → dtype `datetime64[ns, UTC]`;
  - `infer_tz=True` → each value's `utcoffset()` equals the source string's offset;
  - `Candidate_Start_Date` values are midnight at their offset, never a naive
    `07:00`/`08:00` time;
  - applying the same function again to its own output changes nothing (M1 re-conversion hardening).
- **Interface Skeleton**:
  ```python
  # tests/integration/test_offers_hires_datetime.py  (new)
  COLUMNS: list[str]  # the five columns above

  @pytest.fixture(scope="module")
  def offers_hires() -> pd.DataFrame:
      """Load the CSV per the load contract; pytest.skip when the file is absent."""

  @pytest.mark.parametrize("func", [convert_to_datetime, to_timestamp])
  @pytest.mark.parametrize("infer_tz", [False, True])
  @pytest.mark.parametrize("column", COLUMNS)
  def test_offers_hires_datetime_columns(offers_hires, func, infer_tz, column) -> None:
      """Per-column expectations above."""
  ```

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_reconvert_infer_tz_output_keeps_all_values` | M1 | `convert_to_datetime` on its own `infer_tz=True` output: no `NaT` introduced; default → UTC, `infer_tz=True` → offsets kept |
| `test_aware_python_datetimes_mixed_offsets` | M1 | `object` Series of aware `datetime.datetime` values with `-07:00`/`-08:00` behaves like the string case |
| `test_has_mixed_timezones` | M1 | true for mixed aware, naive/aware mixtures in both orders, and incompatible timezones; false for homogeneous naive, single timezone/offset, all-null |
| `test_naive_aware_mixtures_keep_all_values` | M1 | Timestamp, Python datetime, and string input in both orders: False → UTC with naive interpreted as UTC and field-naming warning; True → original aware offsets and naive values; assert values, null mask, index and name |
| `test_mixed_iso_precision_keeps_all_values` | M1–M3 | seconds-only and fractional-second ISO strings with mixed offsets, in both orders and with object/string dtypes; both infer_tz modes and all four callers produce the expected non-null results, preserving fractional precision where the output retains time |
| `test_to_wall_clock_modes` | M1 | naive passthrough; single offset → local wall-clock; mixed → each value's own wall-clock; naive + aware mix |
| `test_to_excel_date_mixed_offsets` | M2 | mixed strings and `infer_tz` object → UTC-naive values, no `NaT` |
| `test_change_timezone_mixed_and_aware` | M2 | mixed strings, single-offset aware and `infer_tz` object input all convert to `to_tz` with no `NaT`; naive + `from_tz` unchanged |
| `test_extract_mixed_offsets` | M3 | `value="day"` on `2025-09-15T23:30-07:00` mixed with a `-08:00` value → 15 (own offset preserved, not 16 as UTC would give) |
| `test_extract_ldom_preserves_timezone` | M3 | single-offset string `2025-09-15T23:30:00-07:00` → `2025-09-30T23:30:00-07:00`; cover parsed input, mixed offsets, naive/aware mixtures and nulls, asserting timezone-bearing output and source preservation when field differs from column |
| `test_add_timestamp_to_time_mixed` | M3 | mixed/object inputs no longer raise; assert combined local date/time values, fractional seconds and null propagation |
| `test_naive_and_single_offset_regressions` | M2/M3 | preserve working naive and single-offset output, including extract ldom and source-column timezone; change_timezone aware-input expectations follow the intentional Q3 correction |
| `test_offset_date_parsed_as_midnight` | M1 | `"2025-09-15-07:00"` → `2025-09-15T00:00:00-07:00` (not naive `07:00`); `"2025-09-15Z"` → midnight UTC; plain `"2025-09-15"` stays naive |

### Integration Tests
| Test | Description |
|---|---|
| `test_offers_hires_datetime_columns` (M5) | 5 Workday columns × 2 functions × 2 `infer_tz` modes on `artifacts/get_offers_and_hires_1.6.csv`; skipped when the file is absent |

### Test Data / Fixtures
```python
MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
MIXED_PRECISION = [
    "2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00.123-08:00", None
]
NAIVE_AWARE = [
    pd.Timestamp("2025-09-15T23:30:00-07:00"),
    pd.Timestamp("2025-09-16T01:00:00"),
    None,
]
```

---

## 5. Acceptance Criteria

- [ ] No `NaT` is introduced for a parseable value in any of the four functions, or in `convert_to_datetime`/`to_timestamp`, for mixed-offset strings (including differing fractional precision), `infer_tz=True` output, Python datetimes, or naive/aware mixtures in either order. For add_timestamp_to_time, both source values must be valid; a null source propagates to null output.
- [ ] `infer_tz` keeps #666's meaning wherever it exists (`convert_to_datetime`/`to_timestamp`): heterogeneous input with `False` → UTC normalization plus a logged warning naming the field (naive values interpreted as UTC); `True` → each aware value's own offset and naive values left naive. Homogeneous input retains existing semantics.
- [ ] `extract` and `add_timestamp_to_time` preserve each value's own offset (local wall-clock); only values without an offset are read as UTC.
- [ ] `change_timezone` with tz-aware input and an explicit `from_tz` logs a warning and ignores `from_tz`.
- [ ] `to_excel_date`, `extract`, `change_timezone` and `add_timestamp_to_time` no longer raise on mixed-offset input.
- [ ] Output is unchanged for working naive and single-offset input, except the intentional change_timezone aware-input correction in Q3. In particular, extract(value="ldom") preserves timezone-bearing results, and extracting into a separate field does not strip the parsed source column's timezone.
- [ ] `to_time` is untouched.
- [ ] `pytest tests/unit/test_to_datetime_mixed_offsets.py tests/unit/test_pandas3_transforms.py tests/test_mixed_tz_datetime.py` passes.
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors (baseline: 20 pre-existing).
- [ ] `CHANGES.rst` entry under *Unreleased*.
- [ ] Date-only values with an offset (`YYYY-MM-DD±HH:MM`, `YYYY-MM-DDZ`) parse as midnight at that offset, never as a naive time of day.
- [ ] Final task (M5): `QS_OFFERS_HIRES_CSV=<main checkout>/artifacts/get_offers_and_hires_1.6.csv pytest tests/integration/test_offers_hires_datetime.py` passes all 20 cases with **no skips**, and the CSV is not tracked by git (`git ls-files artifacts/` is empty).

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
| `_has_mixed_timezones` | `_to_datetime_series` | classify independently parsed values before dtype assembly | replace parsing flow at `transforms.py:53` |
| `_to_wall_clock` | `_to_datetime_series` | call | `transforms.py:30` |

### Does NOT Exist (Anti-Hallucination)
- ~~`_to_wall_clock`~~, ~~`_has_mixed_timezones`~~ — introduced by this spec.
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
| `tests/integration/test_offers_hires_datetime.py` | CREATE | — | — | — |
| `CHANGES.rst` | MODIFY | `Unreleased` | `CHANGES.rst:1` | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Reuse the #666 helpers; never call `pd.to_datetime` directly in the four touched functions.
- Use `logging.warning` / `logging.error` (module-level `navconfig.logging`), not `print`, in touched functions.
- Parse non-datetime input independently before classifying timezone state. Track naive values as well as aware offsets/timezones; ignore nulls. Do not rely on `pd.to_datetime` raising or on utc=True repairing format-inference losses.
- For extract, keep timezone-preserving parsed values separate from the temporary naive component view; ldom must use the former.

### Known Risks / Gotchas
- **`extract` on mixed input**: components come from each value's own offset,
  so a `23:30-07:00` value yields day 15 (UTC would give 16). This only affects
  inputs that currently raise, so no working task changes output.
- **`change_timezone` with tz-aware input and `from_tz`**: today this fails in
  `tz_localize`, prints, and returns the column unconverted. After the change
  it converts. That is a fix, but it is visible in the output.
- **Performance**: non-datetime parsing requires a Python pass for both object
  and string dtypes. Reuse parsed values rather than reparsing raw strings;
  retain vectorized paths for datetime dtypes. Classification may stop once
  heterogeneity is established, but parsing must still process every value.
- **`extract(value="ldom")`**: MonthEnd returns a datetime, unlike numeric
  components. Stripping its timezone changes working single-offset output;
  heterogeneous values require per-value calendar arithmetic, preserving
  timezone semantics and local wall-clock time.
- **Cython builds**: tests in a worktree need `python setup.py build_ext --inplace` first. `transforms.py` itself is pure Python.
- **PII in the M5 data file**: `artifacts/` is not git-ignored, and the CSV holds candidate names and IDs. Never `git add` it, never copy it into the worktree or `tests/`, and never print row values in assertions or logs.

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
- **Module graph**: M2 → M1, M3 → M1 (both call `_to_datetime_series` / `_to_wall_clock`); M4 → M1–M3; M5 → M1–M4 (final task; it validates the finished helpers on real data).
- **Shared files**: `querysource/types/dt/transforms.py` (M1, M2, M3). Their tasks are serialized.
- **Exclusive resources**: none (pure Python; a worktree only needs the in-place Cython build to import the package).
- **Cross-feature dependencies**: PR #666 (`fix/datetime-infer-tz`) must be merged into `dev` first.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-13 | Jesus Lara | Initial draft |
| 0.2 | 2026-10-13 | Jesus Lara | §8 Q1–Q4 resolved: M1 stays here; extract/add_timestamp_to_time preserve per-value offsets (no infer_tz); from_tz warning; format/unit audit filed |
| 0.3 | 2026-10-13 | Jesus Lara | M5 final task: real-data validation on artifacts/get_offers_and_hires_1.6.csv; M1 adds `_parse_offset_dates` for xs:date values misread as naive times |
| 0.3 | 2026-10-13 | Codex | Address review issues 3d6c989fd3b8, c5d2828575b5 and d4ebf933e643: independent parsing and naive/aware classification, differing ISO precision, timezone-preserving ldom, expanded regression criteria |
