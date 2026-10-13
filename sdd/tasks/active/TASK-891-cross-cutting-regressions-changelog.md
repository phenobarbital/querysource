# TASK-891: Cross-cutting precision/regression tests and changelog

**Feature**: FEAT-171 — pandas 3 mixed-UTC-offset handling for the remaining datetime transforms
**Spec**: `sdd/specs/pandas3-to-datetime-mixed-offsets.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-890
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 4**: the §4 tests that span modules 1–3 and cannot live in
one module's task. These are mixed-precision behaviour through all callers, and the
naive/single-offset regression guard. It also adds the `CHANGES.rst` entry and runs the
full spec §5 gate (all three test files plus ruff).

---

## Scope

- Append `test_mixed_iso_precision_keeps_all_values` and `test_naive_and_single_offset_regressions` to `tests/unit/test_to_datetime_mixed_offsets.py`.
- Add a FEAT-171 bullet list to the existing *pandas 3 compatibility for datetime and fill transforms* section of `CHANGES.rst` (added by PR #666).
- Run the spec §5 test and ruff gate; fix any test this feature broke, inside the files of TASK-888..890 only if the failure is a defect of this feature.

**NOT in scope**: new behaviour; the real-data CSV test (TASK-892).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/unit/test_to_datetime_mixed_offsets.py` | MODIFY | cross-module tests |
| `CHANGES.rst` | MODIFY | Unreleased entry |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
Test file — the import block created by TASK-888 and extended by TASK-889/890 already holds
`_to_datetime_series, _to_wall_clock, add_timestamp_to_time, change_timezone, convert_to_datetime,
extract, to_excel_date, to_timestamp` (all in `querysource/types/dt/transforms.py`). Add nothing else.

### Existing Signatures to Use
```text
# CHANGES.rst (PR #666 head be9a3cbc)
line 1:  Unreleased
line 4:  pandas 3 compatibility for datetime and fill transforms
line 5:  -------------------------------------------------------
lines 7-17: three bullets (convert_to_datetime x2, fill_nulls)
```

### Does NOT Exist
- ~~a separate FEAT-171 section in `CHANGES.rst`~~ — extend the #666 section; do not duplicate its heading.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/unit/test_to_datetime_mixed_offsets.py", "action": "MODIFY"},
    {"path": "CHANGES.rst", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/types/dt/transforms.py#_to_datetime_series",
    "sym:querysource/types/dt/transforms.py#extract",
    "sym:querysource/types/dt/transforms.py#add_timestamp_to_time",
    "sym:querysource/types/dt/transforms.py#to_excel_date",
    "sym:querysource/types/dt/transforms.py#change_timezone"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Regression expectations must be computed from the **pre-feature** behaviour: for naive and single-offset input, compare with the values the PR #666 code produced (record them as literals in the test, not by calling the old code).
- `change_timezone` with aware input is the one intentional change (spec §8 Q3); assert the new behaviour there.

---

## Implementation Blueprint

### Steps (in order)
1. Append the two tests — *why*: spec §4 rows tagged M1–M3 and M2/M3.
2. Edit `CHANGES.rst` — *why*: spec §5 AC "CHANGES.rst entry under Unreleased".
3. Run the Validation Commands and `ruff check querysource/types/dt/transforms.py` — *why*: spec §5 gate.

### `tests/unit/test_to_datetime_mixed_offsets.py` (MODIFY)
```python
# APPEND at end of file
MIXED_PRECISION = [
    "2025-09-15T23:30:00-07:00", "2025-12-01T08:00:00.123-08:00", None
]


def test_mixed_iso_precision_keeps_all_values():
    """Seconds-only and fractional ISO strings with mixed offsets: no value lost anywhere."""
    # FILL IN: both orders, object and "str" dtypes; both infer_tz modes of
    # convert_to_datetime/to_timestamp and all four callers (to_excel_date, change_timezone,
    # extract, add_timestamp_to_time); keep the .123 fraction where the output has time


def test_naive_and_single_offset_regressions():
    """Working naive and single-offset input gives today's output."""
    # FILL IN: all four callers; include extract(value="ldom") on single-offset input
    # (-> 2025-09-30T23:30:00-07:00) and a source column that keeps its timezone when
    # field != column; change_timezone aware input follows the Q3 correction
```

### `CHANGES.rst` (MODIFY)
```rst
# occurrences: 1 (verified: grep -c '^pandas 3 compatibility for datetime and fill transforms$' CHANGES.rst — on #666 head)
# AFTER — insert below the last bullet of that section (the ``fill_nulls`` bullet ending
# "no longer raises ``TypeError`` under pandas>=3.") and before the blank line + next heading:
- Datetime parsing (FEAT-171) no longer loses values silently under pandas>=3:
  already-parsed tz-aware values with mixed offsets, naive/aware mixtures and
  mixed fractional precision are all kept, and date-only values with an offset
  (``2025-09-15-07:00``) parse as midnight at that offset instead of 07:00.
- ``to_excel_date`` and ``change_timezone`` accept mixed-offset input;
  ``change_timezone`` converts tz-aware input directly and ignores ``from_tz``
  with a warning.
- ``extract`` and ``add_timestamp_to_time`` accept mixed-offset input and use
  each value's own local time (``23:30-07:00`` is day 15, not 16 as in UTC).
```
**Why**: user-visible behaviour changes must be documented (spec §5); the `extract`
day-15 example states the semantic choice explicitly for task authors.

### FILL IN checklist
- [ ] two test bodies — spec §4 rows; AC-1, AC-2

---

## Acceptance Criteria

- [ ] AC-1: `test_mixed_iso_precision_keeps_all_values` passes for every caller and both `infer_tz` modes.
- [ ] AC-2: `test_naive_and_single_offset_regressions` passes with literal pre-feature expectations.
- [ ] AC-3: `CHANGES.rst` has the FEAT-171 bullets in the existing pandas 3 section.
- [ ] AC-4: spec §5 gate — `pytest tests/unit/test_to_datetime_mixed_offsets.py tests/unit/test_pandas3_transforms.py tests/test_mixed_tz_datetime.py` passes; `ruff check querysource/types/dt/transforms.py` shows no new errors (baseline 20).

---

## Validation Commands

- `pytest tests/unit/test_to_datetime_mixed_offsets.py -q`
- `pytest tests/unit/test_pandas3_transforms.py -q`
- `pytest tests/test_mixed_tz_datetime.py -q`

---

## Test Specification

See the MODIFY block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** (`python -m scripts.sdd.ensure_worktree --slug pandas3-to-datetime-mixed-offsets --feature-id FEAT-171`); run `python setup.py build_ext --inplace` before pytest.
2. **Check dependencies** — TASK-890 must be `"done"`.
3. **Update status** → `"in-progress"`.
4. **Implement** from the blueprint; complete every `# FILL IN:`.
5. **Verify** with the Validation Commands and ruff.
6. **Commit the code** — stage only the two files above.
7. **Close the task** with `scripts/sdd/close_task.sh TASK-891 pandas3-to-datetime-mixed-offsets verified`.
8. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*
