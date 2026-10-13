# TASK-892: Real-data validation on `get_offers_and_hires_1.6.csv` (final task)

**Feature**: FEAT-171 — pandas 3 mixed-UTC-offset handling for the remaining datetime transforms
**Spec**: `sdd/specs/pandas3-to-datetime-mixed-offsets.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-891
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 5**: the feature's final gate. It runs `convert_to_datetime`
and `to_timestamp` in both `infer_tz` modes over the five date/time columns of a real
Workday export. It then repeats the run through a mirror of flowtask's `drop_timezone`,
which the production task `programs/workday/tasks/get_offers_and_hires.yaml` applies
after `tMap` → `convert_to_datetime`.

Profiled 2026-10-13 (pipe-delimited, 31,605 rows, every column mixes `-07:00`/`-08:00`):

| Column | Non-null | Format |
|---|---|---|
| `Candidate_Start_Date` | 1,459 | `YYYY-MM-DD±HH:MM` (date-only with offset) |
| `Job_Requisition_Date_Job_Posted` | 31,605 | `YYYY-MM-DDTHH:MM:SS.fff±HH:MM` |
| `Offer_Event_Offer_Sent` | 1,713 | same |
| `Offer_Event_Offer_Completed` | 1,680 | same |
| `Job_Application_Ready_for_Hire_Completed_Date` | 1,105 | same |

Pre-feature baseline (PR #666 only): the four timestamp columns already pass the
`drop_timezone` check (16/16 verified). `Candidate_Start_Date` is misread as naive 07:00/08:00,
and only TASK-888 fixes it.

---

## Scope

- Create `tests/integration/test_offers_hires_datetime.py` with the two parametrized tests of spec §3 M5 (40 cases).
- Run it against the real file with **no skips**.

**NOT in scope**: changing transforms (a failure here is a defect to report against TASK-888..890, not to patch around in the test); importing flowtask.

### Data handling — PII (mandatory)
`artifacts/get_offers_and_hires_1.6.csv` is untracked, 79 MB, and holds candidate names and IDs.
- Never `git add` it, never copy it into the worktree or `tests/`, never create fixtures from it.
- Read it from env `QS_OFFERS_HIRES_CSV`, defaulting to `<repo root>/artifacts/get_offers_and_hires_1.6.csv`; `pytest.skip` when absent.
- Assertion messages report counts and column names only — never row values.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_offers_hires_datetime.py` | CREATE | 40-case real-data test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from querysource.types.dt.transforms import convert_to_datetime, to_timestamp
# convert_to_datetime(df, field, remove_nat=False, infer_tz=False) — transforms.py:1487 @ be9a3cbc
# to_timestamp(df, field, remove_nat=False, infer_tz=False)        — transforms.py:105  @ be9a3cbc
```

### Existing Signatures to Use
```python
# flowtask/components/TransformRows/functions.py:408 @ flowtask c0406e71 — MIRRORED, not imported
def drop_timezone(df, field, column=None):
    # DatetimeTZDtype -> series.dt.tz_localize(None)
    # object          -> per value x.replace(tzinfo=None) for aware Timestamp/datetime, then .astype('datetime64[ns]')
    # otherwise       -> unchanged
```
`tests/integration/__init__.py` exists; there is no `tests/integration/conftest.py`.

### Does NOT Exist
- ~~`flowtask` as a querysource test dependency~~ — never import it; use the local mirror.
- ~~a tracked copy of the CSV or a fixture derived from it~~ — must not be created.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/integration/test_offers_hires_datetime.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#convert_to_datetime",
    "sym:querysource/types/dt/transforms.py#to_timestamp"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Load contract: `pd.read_csv(path, sep="|", dtype=str, usecols=COLUMNS)` — a default comma read fails at line 6.
- Independent reference: `datetime.fromisoformat`, after expanding the date-only form `YYYY-MM-DD±HH:MM` to `YYYY-MM-DDT00:00:00±HH:MM`. Never use the code under test to build expectations.
- Pass a **copy** of the one-column frame to each function (both mutate in place).
- Module-scoped fixture: the CSV is read once.

---

## Implementation Blueprint

### Steps (in order)
1. Create the test file from the block below — *why*: spec §3 M5 skeleton.
2. Fill in the two test bodies — *why*: spec §3 M5 Expectations and `drop_timezone` check.
3. Run with `QS_OFFERS_HIRES_CSV` pointing at the **main checkout's** copy; confirm 40 passed, 0 skipped — *why*: a skipped run proves nothing (spec §5).
4. Confirm `git ls-files artifacts/` is empty and `git status` shows no CSV staged.

### `tests/integration/test_offers_hires_datetime.py` (CREATE)
```python
"""FEAT-171 M5: real-data validation of datetime transforms on a Workday export.

The CSV holds candidate PII: it is never committed, and assertion messages
report counts and column names only.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from querysource.types.dt.transforms import convert_to_datetime, to_timestamp

COLUMNS = [
    "Candidate_Start_Date",
    "Job_Requisition_Date_Job_Posted",
    "Offer_Event_Offer_Sent",
    "Offer_Event_Offer_Completed",
    "Job_Application_Ready_for_Hire_Completed_Date",
]
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "artifacts" / "get_offers_and_hires_1.6.csv"


@pytest.fixture(scope="module")
def offers_hires() -> pd.DataFrame:
    """Load the CSV per the load contract; skip when the file is absent."""
    path = Path(os.environ.get("QS_OFFERS_HIRES_CSV", DEFAULT_CSV))
    if not path.is_file():
        pytest.skip(f"real-data CSV not found: set QS_OFFERS_HIRES_CSV (looked at {path})")
    return pd.read_csv(path, sep="|", dtype=str, usecols=COLUMNS)


def _reference(value: str) -> datetime:
    """Parse one source string independently (date-only form expanded to midnight)."""
    # FILL IN: expand 'YYYY-MM-DD±HH:MM' (len 16) to 'YYYY-MM-DDT00:00:00±HH:MM', then fromisoformat


def _drop_timezone_like_flowtask(series: pd.Series) -> pd.Series:
    """Local mirror of flowtask TransformRows drop_timezone (functions.py:408 @ c0406e71)."""
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_localize(None)
    if series.dtype == "object":
        return series.apply(
            lambda x: x.replace(tzinfo=None)
            if isinstance(x, (pd.Timestamp, datetime)) and x.tzinfo is not None
            else x
        ).astype("datetime64[ns]")
    return series


@pytest.mark.parametrize("func", [convert_to_datetime, to_timestamp])
@pytest.mark.parametrize("infer_tz", [False, True])
@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_datetime_columns(offers_hires, func, infer_tz, column) -> None:
    """No value lost, misread or left as str; same instant as the source."""
    # FILL IN: spec §3 M5 Expectations — non-null count unchanged; no str left;
    # instants equal _reference; False -> datetime64[ns, UTC]; True -> utcoffset() equals
    # the source offset; Candidate_Start_Date is midnight at its offset; applying func
    # again to its own output changes nothing


@pytest.mark.parametrize("func", [convert_to_datetime, to_timestamp])
@pytest.mark.parametrize("infer_tz", [False, True])
@pytest.mark.parametrize("column", COLUMNS)
def test_offers_hires_drop_timezone_wall_clock(offers_hires, func, infer_tz, column) -> None:
    """infer_tz=True -> local wall-clock (pandas<3 result); False -> UTC wall-clock."""
    # FILL IN: result is naive datetime64[ns], non-null count unchanged, no str;
    # True  -> equals _reference(v).replace(tzinfo=None)
    # False -> equals _reference(v).astimezone(timezone.utc).replace(tzinfo=None)
```
**Why this shape**: the mirror's algorithm is fixed by spec §3 M5. It is copied, not
imported, so querysource tests do not depend on flowtask. The default path resolves
from the test file to the repo root, and the skip message names the env var so a
worktree run knows how to point at the real file.

### FILL IN checklist
- [ ] `_reference` — date-only expansion; bounded by spec §3 M5 "independent per-value reference"
- [ ] `test_offers_hires_datetime_columns` body — AC-1
- [ ] `test_offers_hires_drop_timezone_wall_clock` body — AC-2

---

## Acceptance Criteria

- [ ] AC-1: all 20 `test_offers_hires_datetime_columns` cases pass.
- [ ] AC-2: all 20 `test_offers_hires_drop_timezone_wall_clock` cases pass.
- [ ] AC-3: run as `QS_OFFERS_HIRES_CSV=<main checkout>/artifacts/get_offers_and_hires_1.6.csv pytest tests/integration/test_offers_hires_datetime.py` → **40 passed, 0 skipped**; record the pytest summary line in the Completion Note.
- [ ] AC-4: `git ls-files artifacts/` is empty; no assertion message contains a row value.

---

## Validation Commands

- `pytest tests/integration/test_offers_hires_datetime.py -q`

(Set `QS_OFFERS_HIRES_CSV` to the main checkout's `artifacts/get_offers_and_hires_1.6.csv` first. Without it, every case skips and AC-3 fails.)

---

## Test Specification

See the CREATE block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug pandas3-to-datetime-mixed-offsets --feature-id FEAT-171`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-891 must be `"done"` (this is the final task).
3. **Update status** → `"in-progress"`.
4. **Implement** from the blueprint; complete every `# FILL IN:`.
5. **Verify** per AC-3 — no skips.
6. **Commit the code** — stage only the test file. Never the CSV.
7. **Close the task** with `scripts/sdd/close_task.sh TASK-892 pandas3-to-datetime-mixed-offsets verified`.
8. **Fill in the Completion Note** (include the pytest summary line), then commit the staged SDD state.

---

## Completion Note


- Task: TASK-892
- Feature: pandas3-to-datetime-mixed-offsets
- Implementation SHA: 8b357ffa242ba6351d4646e22ec8f736bf3c7004
- Closed at (UTC): 2026-10-13T21:58:52+00:00
- Fix commits: none

| Metric | Value |
|---|---|
| validation_refs | 1 |
| fix_commits | 0 |
| seat_summary | Seat: sonnet · Backend: native · Model: sonnet · Attempts: 1 · Duration: 165s · Tokens: n/a |
| validation_note | Engine merge-tier run skipped all 40 cases (real CSV not available in the sandbox). Direct run with QS_OFFERS_HIRES_CSV=artifacts/get_offers_and_hires_1.6.csv: 53 passed (40 integration + 13 unit). |
