---
type: feature
base_branch: dev
projects: [querysource]
tags: [pandas3, datetime, timezone, transforms, flowtask]
---

# Feature Specification: Host Flowtask's timezone transforms in QuerySource (single datetime code base)

**Feature ID**: FEAT-174
**Date**: 2026-10-13
**Author**: Juan Francisco Ruffato
**Status**: approved
**Target version**: 5.2.5

---

## 1. Motivation & Business Requirements

### Problem Statement

Flowtask's `TransformRows` component resolves a transform name in Flowtask's own
`flowtask/components/TransformRows/functions.py` first and falls back to
`querysource.types.dt.transforms` (`TransformRows.py:240-244`, Flowtask). Three
datetime transforms live in Flowtask and have the same pandas 3 mixed-UTC-offset
defects that FEAT-171 fixed in QuerySource:

| Flowtask function | Task files using it | Defect under pandas 3.0.6 |
|---|---|---|
| `drop_timezone` | 20 | mixed-offset strings: raises internally, `print`s, returns the **strings unchanged** (downstream `CopyToPg` fails); date-only with offset (`2025-09-15-07:00`) misread as naive `07:00` |
| `convert_timezone` | 8 (networkninja, uses `tz_column`) | mixed-offset strings: column left as strings; `object` of aware Timestamps with mixed offsets: **silent `NaT`** for every non-first offset |
| `add_timestamp_to_time` | 229 | shadows QS's function of the same name (already fixed by FEAT-171) |

Measured on the real Workday export `get_offers_and_hires_1.6.csv` (31,605 rows, 5
datetime columns mixing `-07:00`/`-08:00`): Flowtask `drop_timezone` on the raw string
columns leaves **100 % of values as strings**. A `drop_timezone` built on FEAT-171's
`_to_wall_clock` returns the correct local wall-clock for **100 %** of values in all five
columns, including the date-only `Candidate_Start_Date` (1,459/1,459), verified value by
value against `datetime.fromisoformat`.

`add_timestamp_to_time` parity (Flowtask vs QS `dev` @ `0fd0a56`, 10 synthetic cases):
identical output on the 7 currently-working inputs (str date + `HH:MM:SS` / `HH:MM` /
`10:30 AM`; `date` + `time` objects as returned by Postgres; `datetime64` + `time`;
`datetime64` with time + str; null time). QS is strictly better on the 3 failing ones
(date + `timedelta`, a garbage time value, mixed-offset aware dates), where Flowtask
swallows the error and does not create the output column.

Decision (Flowtask brainstorm `sdd/proposals/pandas3-datetime-mixed-offsets.brainstorm.md`
in the Flowtask repo, Option C hybrid, 2026-10-13): **QuerySource is the single code base
for datetime parsing**. The two Flowtask-only timezone transforms move to QS, Flowtask
deletes its three datetime transforms and pins the QS release, and the FEAT-171 parser
becomes public so Flowtask's non-datetime helpers (`extract_from_*(as_timestamp=True)`)
and components can delegate to it without importing `_`-prefixed names.

### Goals
- Add `drop_timezone` and `convert_timezone` to `querysource/types/dt/transforms.py`,
  with Flowtask-compatible names and signatures so existing task YAML keeps working
  through the TransformRows fallback, and FEAT-171 mixed-offset semantics.
- **One timezone-conversion implementation**: extend `change_timezone` with Flowtask's
  `column` / `tz_column` / `default_timezone` (keyword-only) and make `convert_timezone`
  a thin alias with Flowtask's signature (§8 Q1).
- One DST policy for both names: ambiguous wall-clock times → standard time (QS's
  existing `ambiguous=[False…]`), nonexistent times → `nonexistent="shift_forward"`, so a
  single odd value never leaves a whole column unconverted (§8 Q2).
- Expose public `parse_datetime_series` and `to_wall_clock` wrappers over FEAT-171's
  `_to_datetime_series` / `_to_wall_clock` (§8 Q3).
- No value lost, no string left behind, on mixed-offset input.
- Output identical to today's output for working input (naive and single-offset),
  except the documented fixes (§7).
- Ship in **5.2.5 together with FEAT-171** (§8 Q4).

### Non-Goals (explicitly out of scope)
- Changing `add_timestamp_to_time`, `convert_to_datetime`, `to_timestamp` or any FEAT-171
  helper behaviour.
- Flowtask-side changes (deleting the three functions, pinning QS, migrating
  `extract_from_*` and the components audited in the brainstorm). Tracked in Flowtask
  FEAT-567 (`pandas3-datetime-mixed-offsets`), which depends on this release.
- Duplicating the parser in Flowtask (brainstorm Option A — rejected: two code bases).
- The open FEAT-171 ledger items `issue:581d7f51d6f3` (`format=` + `%z`),
  `issue:c5d2828575b5` (varying ISO precision — note: verified working on `dev` @
  `0fd0a56` through `_to_wall_clock`) and `issue:d4ebf933e643` (`extract` ldom).

---

## 2. Architectural Design

### Overview

Two new DataFrame transform names (`drop_timezone`, `convert_timezone`), an extended
`change_timezone`, and two public series helpers, all in
`querysource/types/dt/transforms.py`, built only on FEAT-171 helpers:

1. **`parse_datetime_series(series, infer_tz=False, field=None)`** and
   **`to_wall_clock(series, field=None)`** — public, documented wrappers that delegate to
   `_to_datetime_series` / `_to_wall_clock` unchanged. The private names stay (the
   FEAT-171 tests import them). Precedent for series-level public helpers in this module:
   `is_datetime_column` / `is_time_column` (`transforms.py:2125`).
2. **`drop_timezone(df, field, column=None)`** — writes a naive column to `field` from
   `df[column or field]`:
   - `DatetimeTZDtype` → `.dt.tz_localize(None)` (today's Flowtask behaviour; UTC input
     yields UTC wall-clock, as pinned by FEAT-171 M5);
   - naive `datetime64` → unchanged;
   - `object` or pandas string dtype → `to_wall_clock` (each value's **own local
     wall-clock**; values without offset unchanged; date-only with offset → local
     midnight; unparseable → `NaT`);
   - any other dtype (numeric, bool, …) → copied unchanged (today's contract).
3. **`change_timezone(df, field, from_tz=None, to_tz="UTC", *, column=None,
   tz_column=None, default_timezone="UTC")`** — the single implementation (extended;
   existing positional parameters unchanged):
   - source `df[column or field]`, output `df[field]` — the same `field`/`column`
     convention as `to_excel_date` and `extract`;
   - parse with `parse_datetime_series(..., infer_tz=False)` (mixed offsets → UTC
     instants, nothing lost) — already the case since FEAT-171;
   - tz-aware result → no localization; warn when `from_tz` is passed (FEAT-171 Q3,
     unchanged);
   - naive result → `tz_localize(from_tz or "UTC", ambiguous=<all False>,
     nonexistent="shift_forward")`. `from_tz or "UTC"` **fixes** today's silent no-op:
     naive input with no `from_tz` currently logs an error and returns the column
     unchanged (verified on `dev` @ `0fd0a56`);
   - `tz_column is None` → `tz_convert(to_tz or default_timezone)`;
   - `tz_column` given → per-row conversion to that row's zone, falling back to
     `default_timezone` for null/invalid zones (Flowtask behaviour, ported);
   - errors → `logging.error`, frame returned (unchanged contract).
4. **`convert_timezone(df, field, *, column=None, from_tz=None, to_tz=None,
   tz_column=None, default_timezone="UTC")`** — Flowtask-compatible alias, no logic of
   its own: delegates to `change_timezone(df, field, from_tz=from_tz,
   to_tz=to_tz or default_timezone, column=column, tz_column=tz_column,
   default_timezone=default_timezone)`. Flowtask's `from_tz="UTC"` default becomes
   `None`, behaviour-neutral for naive input.
5. `drop_timezone` errors are logged with `logging.error` and the frame is returned
   (Flowtask's "never abort the task" contract, minus `print`).

**DST policy evidence** (pandas 3.0.6, `America/Chicago`, §8 Q2):

| Naive input | Flowtask `ambiguous="infer"` | QS `ambiguous=[False…]` (adopted) |
|---|---|---|
| one `2025-11-02 01:30` among unsorted rows (typical) | ❌ raises → **whole column left unconverted** | ✅ converts (standard time) |
| sorted series repeating `01:30` across the fall-back | ✅ disambiguates | ⚠️ both read as standard time |
| `2025-03-09 02:30` (nonexistent) | ❌ raises → whole column | ❌ raises today → **`shift_forward` adopted** |

### Component Diagram
```
Flowtask TransformRows ──getattr fallback──→ querysource.types.dt.transforms
                                               ├─ drop_timezone ──→ to_wall_clock ──→ _to_wall_clock ──→ _to_datetime_series
                                               ├─ convert_timezone (alias) ──→ change_timezone (extended) ──→ parse_datetime_series ──→ _to_datetime_series
                                               └─ add_timestamp_to_time (FEAT-171, unchanged)
Flowtask extract_from_* / components ──(Flowtask FEAT-567 / follow-up)──→ parse_datetime_series / to_wall_clock
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `_to_datetime_series` (`transforms.py:103`) | uses (unchanged) | via `parse_datetime_series` |
| `_to_wall_clock` (`transforms.py:152`) | uses (unchanged) | via `to_wall_clock` |
| `change_timezone` (`transforms.py:2382`) | modifies (compatible) | keyword-only `column` / `tz_column` / `default_timezone`; naive default `"UTC"`; `nonexistent="shift_forward"` |
| Flowtask `TransformRows._get_df_function` | consumer (other repo) | name-based fallback; names must match Flowtask's exactly |
| `tests/integration/test_offers_hires_datetime.py` (FEAT-171 M5) | modifies | replace the local Flowtask mirror with the real `drop_timezone` |

### Data Models
None.

### New Public Interfaces
```python
def parse_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series: ...
def to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series: ...
def drop_timezone(df: pd.DataFrame, field: str, column: Optional[str] = None) -> pd.DataFrame: ...
def change_timezone(  # extended — existing positional parameters unchanged
    df: pd.DataFrame, field: str, from_tz: Optional[str] = None, to_tz: Optional[str] = "UTC", *,
    column: Optional[str] = None, tz_column: Optional[str] = None, default_timezone: str = "UTC",
) -> pd.DataFrame: ...
def convert_timezone(  # alias of change_timezone with Flowtask's signature
    df: pd.DataFrame, field: str, *, column: Optional[str] = None, from_tz: Optional[str] = None,
    to_tz: Optional[str] = None, tz_column: Optional[str] = None, default_timezone: str = "UTC",
) -> pd.DataFrame: ...
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: public parsing helpers | yes | skeleton below; pure delegation | — |
| M2: `drop_timezone` | yes | dtype dispatch table in §2 | — |
| M3: `change_timezone` extension + `convert_timezone` alias | yes | §2 items 3–4; `tz_column` per-row logic ported from Flowtask `functions.py:495-517`; DST policy fixed (§8 Q2) | — |
| M4: tests + real-data + changelog | yes | §4 tables; M5 mirror replaced by the real function | — |

### Module 1: Public parsing helpers
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: public, documented entry points to the FEAT-171 parser.
- **Depends on**: FEAT-171 (merged)
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/dt/transforms.py — insert after _to_wall_clock (verified: transforms.py:152-177),
  # before `def _convert_datetime_column(` (verified: transforms.py:179)
  def parse_datetime_series(
      series: pd.Series, infer_tz: bool = False, field: Optional[str] = None
  ) -> pd.Series:
      """Public wrapper of _to_datetime_series (FEAT-171 contract, unchanged).

      Args:
          series: Raw or datetime-like values.
          infer_tz: False -> heterogeneous offsets normalized to UTC (warning);
              True -> object Series keeping each value's offset.
          field: Column name, used only for logging.

      Returns:
          Parsed Series, same index/name; unparseable values become NaT.
      """

  def to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series:
      """Public wrapper of _to_wall_clock: naive datetime64, each value's own local wall-clock."""
  ```

### Module 2: `drop_timezone`
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: Flowtask-compatible `drop_timezone` with mixed-offset support.
- **Depends on**: Module 1
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/dt/transforms.py — insert after change_timezone (verified: transforms.py:2382),
  # before `def to_numeric(` (verified: transforms.py:2413)
  def drop_timezone(df: pd.DataFrame, field: str, column: Optional[str] = None) -> pd.DataFrame:
      """Drop timezone information, keeping each value's local wall-clock.

      Source is df[column or field]; result is written to df[field].
      DatetimeTZDtype -> tz_localize(None); naive datetime64 -> unchanged;
      object/string -> to_wall_clock; other dtypes -> copied unchanged.
      Errors are logged (logging.error) and df is returned.
      """
  ```

### Module 3: `change_timezone` extension + `convert_timezone` alias
- **Path**: `querysource/types/dt/transforms.py`
- **Responsibility**: one timezone-conversion implementation serving both names.
- **Depends on**: Module 1
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/dt/transforms.py:2382 (verified anchor:
  # `def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):`)
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
      """Convert df[column or field] to a target timezone, writing df[field].

      Parsing: parse_datetime_series(infer_tz=False) (mixed offsets -> UTC instants).
      Aware values: from_tz ignored (warning when passed). Naive values:
      tz_localize(from_tz or "UTC", ambiguous=<all False>, nonexistent="shift_forward").
      Target: to_tz or default_timezone; with tz_column, per-row zone with
      default_timezone fallback for null/invalid zones.
      Errors are logged (logging.error) and df is returned.
      """

  # inserted right after drop_timezone (M2)
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
      """Flowtask-compatible alias: change_timezone(df, field, from_tz=from_tz,
      to_tz=to_tz or default_timezone, column=column, tz_column=tz_column,
      default_timezone=default_timezone)."""
  ```

### Module 4: Tests, real-data validation, changelog
- **Paths**: `tests/unit/test_dt_timezone_transforms.py` (new),
  `tests/integration/test_offers_hires_datetime.py` (modify), `CHANGES.rst` (modify)
- **Responsibility**: §4 tests; replace `_drop_timezone_like_flowtask` with the real
  `drop_timezone`; add a raw-string `drop_timezone` real-data case; changelog entry.
- **Depends on**: Modules 1–3

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_public_helpers_delegate` | M1 | `parse_datetime_series` / `to_wall_clock` equal the private helpers on `MIXED`, `NAIVE_AWARE`, naive and all-null input |
| `test_drop_timezone_mixed_strings` | M2 | `MIXED` strings → naive `[2025-09-15 23:30, 2025-12-01 08:00, NaT]`; no `str` left |
| `test_drop_timezone_offset_date` | M2 | `"2025-09-15-07:00"` → `2025-09-15 00:00:00`; `"2025-09-15Z"` → midnight; plain `"2025-09-15"` stays midnight |
| `test_drop_timezone_dtypes` | M2 | `DatetimeTZDtype` (incl. UTC) → `tz_localize(None)`; naive datetime64 unchanged; `object` of aware Timestamps (mixed) → local wall-clock; numeric column copied unchanged; `column` ≠ `field` leaves source untouched |
| `test_drop_timezone_flowtask_parity` | M2 | on Flowtask's currently-working inputs (tz dtype, aware object, naive) output equals the FEAT-171 M5 mirror `_drop_timezone_like_flowtask` |
| `test_change_timezone_existing_callers` | M3 | positional `change_timezone(df, field, from_tz, to_tz)` calls that work today give identical output |
| `test_change_timezone_naive_without_from_tz` | M3 | naive input, no `from_tz` → localized as UTC and converted (today: logged error, column unchanged) |
| `test_timezone_mixed_strings` | M3 | `MIXED` → `to_tz="America/New_York"`, same instants, no `NaT` (both names) |
| `test_timezone_aware_object_no_nat` | M3 | `object` of aware Timestamps `-07:00`/`-08:00` → all converted (regression for Flowtask's silent `NaT`) |
| `test_timezone_from_tz_warning` | M3 | aware input + explicit `from_tz` → warning logged, `from_tz` ignored; no warning when omitted |
| `test_timezone_dst_policy` | M3 | `America/Chicago`: single ambiguous `2025-11-02 01:30` in unsorted rows → standard time, whole column converted; nonexistent `2025-03-09 02:30` → shifted forward to `03:00`, whole column converted |
| `test_convert_timezone_tz_column` | M3 | per-row zones incl. a null and an invalid zone → `default_timezone` fallback; output equals Flowtask's for working input (networkninja config: `from_tz: UTC`, `tz_column: store_timezone`) |
| `test_convert_timezone_is_alias` | M3 | `convert_timezone(...)` equals `change_timezone(..., to_tz=to_tz or default_timezone, ...)` on the fixtures above |
| `test_timezone_column_output` | M3 | `column` is the source, `field` the output; source column unchanged |
| `test_transforms_never_raise` | M2/M3 | an unexpected error is logged with `logging.error` and the frame is returned |

### Integration Tests
| Test | Description |
|---|---|
| `test_offers_hires_drop_timezone_wall_clock` (FEAT-171 M5, modified) | same 20 combinations, now using the real `drop_timezone` instead of the local mirror; expectations unchanged |
| `test_offers_hires_drop_timezone_raw_strings` (new) | 5 columns: raw strings → `drop_timezone` → local wall-clock for every non-null value, no `str`, non-null count unchanged; skipped when the CSV is absent |

### Test Data / Fixtures
```python
MIXED = ["2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00-08:00", None]
NAIVE_AWARE = [pd.Timestamp("2025-09-15T23:30:00-07:00"), pd.Timestamp("2025-09-16T01:00:00"), None]
OFFSET_DATES = ["2025-09-15-07:00", "2025-12-01-08:00", "2025-09-15Z", "2025-09-15", None]
DST_AMBIGUOUS = ["2025-11-03 09:00:00", "2025-11-02 01:30:00"]     # America/Chicago, unsorted
DST_NONEXISTENT = ["2025-03-09 02:30:00", "2025-03-10 09:00:00"]  # America/Chicago
```

---

## 5. Acceptance Criteria

- [ ] `drop_timezone` and `convert_timezone` exist in `querysource.types.dt.transforms` with exactly the §2 signatures (names and parameter names match Flowtask's, so YAML resolves unchanged via the TransformRows fallback).
- [ ] `convert_timezone` contains no conversion logic of its own: it delegates to `change_timezone`.
- [ ] `change_timezone` keeps its positional signature; `column`, `tz_column`, `default_timezone` are keyword-only additions; working calls produce identical output.
- [ ] `parse_datetime_series` and `to_wall_clock` are public and delegate to the FEAT-171 helpers without changing their behaviour; the private names still exist.
- [ ] No parseable value becomes `NaT` and no `str` value remains for mixed-offset strings, aware `object` input, naive/aware mixtures or date-only-with-offset values.
- [ ] `drop_timezone` returns each value's local wall-clock for `object`/string input; tz-dtype input keeps `tz_localize(None)` semantics; non-datetime dtypes are copied unchanged.
- [ ] Aware input ignores `from_tz` (warning only when passed explicitly); naive input uses `from_tz or "UTC"`.
- [ ] DST: ambiguous naive times → standard time (`ambiguous=<all False>`), nonexistent → `nonexistent="shift_forward"`; a single DST-edge value never leaves the whole column unconverted.
- [ ] `tz_column` per-row behaviour and `default_timezone` fallback match Flowtask's for working input.
- [ ] All three transforms log instead of printing and never raise.
- [ ] No change to `add_timestamp_to_time` or any FEAT-171 helper (`pytest tests/unit/test_to_datetime_mixed_offsets.py tests/test_mixed_tz_datetime.py` still passes unmodified).
- [ ] `pytest tests/unit/test_dt_timezone_transforms.py` passes.
- [ ] `QS_OFFERS_HIRES_CSV=<path> pytest tests/integration/test_offers_hires_datetime.py` passes with **no skips**; the CSV is never committed (`git ls-files artifacts/` empty).
- [ ] `ruff check querysource/types/dt/transforms.py` reports no new errors.
- [ ] `CHANGES.rst` entry under *Unreleased* (to be released as **5.2.5** with FEAT-171): new `drop_timezone` / `convert_timezone`, extended `change_timezone` (keyword args, naive default UTC, DST `shift_forward`), public helpers.

---

## 6. Codebase Contract

### Verified Imports
```python
from typing import Any, Dict, List, Optional, Union  # verified: querysource/types/dt/transforms.py:15
from zoneinfo import ZoneInfo                        # verified: transforms.py:16
import numpy as np                                   # verified: transforms.py:18
import pandas as pd                                  # verified: transforms.py:20
from navconfig.logging import logging                # verified: transforms.py:23
```

### Existing Class Signatures
```python
# querysource/types/dt/transforms.py  (dev @ 67419a9)
_OFFSET_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})$")              # line 30
def _parse_offset_dates(series: pd.Series) -> pd.Series                              # line 33
def _has_mixed_timezones(series: pd.Series) -> bool                                  # line 67
def _to_datetime_series(series: pd.Series, infer_tz: bool = False, field: Optional[str] = None) -> pd.Series  # line 103
def _to_wall_clock(series: pd.Series, field: Optional[str] = None) -> pd.Series     # line 152
def _convert_datetime_column(df: pd.DataFrame, field: str, infer_tz: bool = False) -> pd.DataFrame  # line 179
def to_timestamp(...)                                                                # line 210
def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str)       # line 635
def convert_to_datetime(df, field, remove_nat=False, infer_tz: bool = False)        # line 1605
def is_datetime_column(column) -> bool                                               # line 2125
def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC")  # line 2382
def to_numeric(df: pd.DataFrame, field: str, remove_alpha: bool = True, to_integer: bool = False)  # line 2413
```

Reference implementation being ported (Flowtask repo, `dev` @ `27bb116c`):
```python
# flowtask/components/TransformRows/functions.py
def drop_timezone(df: pandas.DataFrame, field: str, column: Optional[str] = None) -> pandas.DataFrame  # line 408
def convert_timezone(df, field, *, column: str | None = None, from_tz: str = "UTC", to_tz: str | None = None,
                     tz_column: str | None = None, default_timezone: str = "UTC") -> pandas.DataFrame   # line 451
    # 483: out = pandas.to_datetime(out, errors="coerce")
    # 487: out.dt.tz_localize(from_tz, ambiguous="infer", nonexistent="raise")
    # 493: out.dt.tz_convert(target)
    # 503-517: tz_column per-row: utc_times.loc[idx].tz_convert(ZoneInfo(tz_name)), fallback ZoneInfo(default_timezone)
# flowtask/components/TransformRows/TransformRows.py
def _get_df_function(self, fn: str):  # line 240 — getattr(dffunctions, fn) then getattr(qsdfunctions, fn)
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `parse_datetime_series` | `_to_datetime_series` | call | `transforms.py:103` |
| `to_wall_clock` | `_to_wall_clock` | call | `transforms.py:152` |
| `drop_timezone` | `to_wall_clock` | call | M1 |
| `change_timezone` (extended) | `parse_datetime_series` | call | M1 |
| `convert_timezone` | `change_timezone` | call (alias) | `transforms.py:2382` |
| Flowtask TransformRows | `drop_timezone`, `convert_timezone` | `getattr(qsdfunctions, fn)` | Flowtask `TransformRows.py:243` |

### Does NOT Exist (Anti-Hallucination)
- ~~`drop_timezone`, `convert_timezone` in QuerySource~~ — introduced by this spec (today Flowtask-only).
- ~~`parse_datetime_series`, `to_wall_clock`~~ — introduced by this spec.
- ~~exports in `querysource/types/dt/__init__.py`~~ — that file holds only a docstring; consumers import from `querysource.types.dt.transforms`.
- ~~`tz_column` / `column` / `default_timezone` on `change_timezone`~~ — do not exist yet; added by M3 as keyword-only.
- ~~`_vectorised_datetime_series`, `_warn_unparsed` on `dev`~~ — exist only on `origin/feat-FEAT-171-pandas3-to-datetime-mixed-offsets` (PR #673, merged there, not yet into `dev`). When #673 lands, every `transforms.py` line number in this section shifts by ~+43; anchors are verbatim, re-run `grep -n`.

### Edit Sites (Blueprint Anchors)

Verified against: `67419a9`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | `def _convert_datetime_column(` (insert M1 before) | `transforms.py:179` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):` (M3 replaces signature + body) | `transforms.py:2382` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `def to_numeric(df: pd.DataFrame, field: str` (insert M2 `drop_timezone` + M3 `convert_timezone` before) | `transforms.py:2413` | 1 |
| `tests/unit/test_dt_timezone_transforms.py` | CREATE | — | — | — |
| `tests/integration/test_offers_hires_datetime.py` | MODIFY | `def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:` | `test_offers_hires_datetime.py:49` | 1 |
| `tests/integration/test_offers_hires_datetime.py` | MODIFY | `_drop_timezone_like_flowtask(_run(` | `test_offers_hires_datetime.py:116` | 1 |
| `CHANGES.rst` | MODIFY | `Unreleased` | `CHANGES.rst:1` | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Build only on FEAT-171 helpers; never call `pd.to_datetime` directly on raw input in the new transforms.
- `logging.warning` / `logging.error` (module-level `navconfig.logging`), never `print`.
- Google-style docstrings; keep the public wrappers free of logic.
- Keep the `test_drop_timezone_flowtask_parity` mirror in the unit test (it documents the Flowtask behaviour being replaced) even after M5 switches to the real function.

### Known Risks / Gotchas
- **Name-based coupling**: Flowtask resolves these by name; renaming them later breaks 28 task files (20 `drop_timezone`, 8 `convert_timezone`; 1 tasks-repo file uses `change_timezone`). Treat the names and parameter names as public API.
- **`field` / `column`**: `field` = output, `column` = source (defaults to `field`) — the same convention as `to_excel_date` and `extract`, and Flowtask's. `change_timezone` today reads and writes `field` only; with `column=None` that stays true.
- **UTC-dtype input to `drop_timezone`** yields UTC wall-clock (original offsets already lost). Tasks wanting pandas<3 local output must pass `infer_tz: true` to `convert_to_datetime` upstream (FEAT-171 M5).
- **DST (§8 Q2)**: ambiguous → `ambiguous=np.array([False] * n)` (QS's policy since before FEAT-171 — `infer_dst` in `change_timezone`, kept by TASK-889). Visible change for Flowtask's 8 `convert_timezone` tasks only on a **sorted** series repeating a fall-back hour (today disambiguated by `infer`, now both read as standard time); in exchange a single ambiguous value in unsorted data no longer leaves the whole column unconverted. Nonexistent → `shift_forward` (today both implementations fail the whole column).
- **`change_timezone` behaviour changes are fixes only**: naive input without `from_tz` (today: logged error, column unchanged) → UTC; nonexistent times (today: whole column unconverted) → shifted forward.
- **PR #673** (vectorised fast path) is not yet in `dev`; branch FEAT-174 after it lands or expect line shifts. The fast path is transparent to these transforms (same `_to_datetime_series` contract).
- **Flowtask still shadows** these names until the Flowtask spec deletes them; until then QS's versions are reachable only from QS-native callers and tests.
- **PII**: the real-data CSV contains candidate data — env var path, skip if absent, counts-only assertions, never committed.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=3.0,<4` (already pinned) | behaviour targeted |

---

## 8. Open Questions

- [x] Single code base for datetime parsing? — *Resolved in Flowtask brainstorm (Juan Francisco Ruffato)*: yes, QuerySource; Flowtask functions move here.
- [x] Wall-clock for mixed offsets — *Resolved in FEAT-171 Q2 (Jesus Lara)*: each value's own offset; UTC only for values without an offset.
- [x] `from_tz` on tz-aware input — *Resolved in FEAT-171 Q3 / Flowtask brainstorm*: ignored with a warning, only when passed explicitly; `from_tz` default becomes `None`, naive input then treated as UTC.
- [x] Unparseable values — *Resolved in Flowtask brainstorm*: per-value `NaT` + logged warning; never raise.
- [x] Q1: Separate `convert_timezone` vs. extending `change_timezone`? — *Resolved 2026-10-13 (Juan Francisco Ruffato)*: unify — extend `change_timezone` (keyword-only `column` / `tz_column` / `default_timezone`) and make `convert_timezone` a thin alias; also fixes `change_timezone`'s silent no-op on naive input without `from_tz`.
- [x] Q2: DST policy? — *Resolved 2026-10-13 (Juan Francisco Ruffato)*: QS's existing ambiguous policy (all `False` → standard time) + `nonexistent="shift_forward"`; evidence table in §2 (`infer` fails the whole column on a single ambiguous value in unsorted data).
- [x] Q3: Public helper location/names? — *Resolved 2026-10-13 (Juan Francisco Ruffato)*: `parse_datetime_series` / `to_wall_clock` in `transforms.py` (precedent: `is_datetime_column`), wrapping the private helpers, which stay.
- [x] Q4: Release version? — *Resolved 2026-10-13 (Juan Francisco Ruffato)*: **5.2.5**, together with FEAT-171 and PR #673 (FEAT-171 is unreleased: latest tag `5.2.4` does not contain merge `37830150`). Flowtask FEAT-567 pins `querysource>=5.2.5`.
- [x] Confirm Q1–Q4 decisions — *Resolved 2026-10-13 (Juan Francisco Ruffato)*: approved without a separate sign-off; Jesus Lara reviews the implementation PR.

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted brainstorm/proposal exploration doc in this repo; source brainstorm lives in Flowtask with status `exploration`)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-174, branched from `dev` (FEAT-171 merged; preferably after PR #673 reaches `dev`).
- **Module graph**: M2 → M1 (`to_wall_clock`), M3 → M1 (`parse_datetime_series`), M4 → M1–M3.
- **Shared files**: `querysource/types/dt/transforms.py` (M1, M2, M3) — their tasks are serialized.
- **Exclusive resources**: none (pure Python; the worktree needs `python setup.py build_ext --inplace` only to import the package).
- **Cross-feature dependencies**: FEAT-171 (merged), PR #673 (on the FEAT-171 branch). Both ship with this in 5.2.5. Downstream: Flowtask **FEAT-567** (`sdd/specs/pandas3-datetime-mixed-offsets.spec.md` in the Flowtask repo) deletes Flowtask's `drop_timezone` / `convert_timezone` / `add_timestamp_to_time`, moves `extract_from_*` to `parse_datetime_series` and pins this release.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-13 | Juan Francisco Ruffato | Initial draft from the Flowtask brainstorm `pandas3-datetime-mixed-offsets` (Option C hybrid) |
| 0.3 | 2026-10-13 | Juan Francisco Ruffato | Approved; review happens on the implementation PR |
| 0.2 | 2026-10-13 | Juan Francisco Ruffato | §8 Q1–Q4 resolved after reviewing FEAT-171 (code, ledger, PR #673, release tags): unify on `change_timezone` + alias; QS DST policy + `shift_forward`; helpers in `transforms.py`; release 5.2.5 with FEAT-171. Fixed wrong `field`/`column` claim; noted #673 not yet on `dev` |
