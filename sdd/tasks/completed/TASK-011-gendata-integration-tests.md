# TASK-011: GenData Integration Tests

**Feature**: GenData Component
**Spec**: `sdd/specs/gendata-component.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-009, TASK-010
**Assigned-to**: unassigned

---

## Context

This task adds integration tests verifying that GenData works end-to-end as a FlowComponent within the pipeline — loadable via `getComponent()`, producing DataFrames compatible with downstream `t*` components, and handling the full YAML configuration lifecycle.

---

## Scope

- Write integration tests in `tests/test_gendata_integration.py`.
- Test that `getComponent("GenData")` successfully loads the component.
- Test a realistic YAML-like configuration with multiple rules and verify output.
- Test the full `start()` → `run()` lifecycle.
- Test cross-join behavior with rules of different row counts.
- Verify output DataFrame is compatible with tFilter / tJoin patterns.

**NOT in scope**: Rule handler unit tests (TASK-009), component unit tests (TASK-010).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_gendata_integration.py` | CREATE | Integration tests |

---

## Implementation Notes

### Key Test Scenarios

1. **Component loading**: `getComponent("GenData")` returns the GenData class.
2. **Full lifecycle**: Configure → `start()` → `run()` → verify `_result` is a DataFrame.
3. **Real-world scenario**: Weekly Mondays from 2024-01-01 to 2026-03-20 with `dow=0`, `interval=7`.
   - Verify first date is 2024-01-01 (a Monday).
   - Verify last date is <= 2026-03-20.
   - Verify all dates are Mondays.
   - Verify interval between consecutive dates is 7 days.
4. **Cross-join**: Two rules with different row counts produce `len(r1) * len(r2)` rows.
5. **Date format**: `date_format: "%Y-%m-%d"` produces string columns.

### References in Codebase
- `flowtask/components/__init__.py` — `getComponent()` function
- `flowtask/components/GenData.py` — component under test (TASK-010)
- `flowtask/components/gendata/` — rule handlers (TASK-009)

---

## Acceptance Criteria

- [ ] `getComponent("GenData")` loads successfully
- [ ] Full lifecycle test passes with realistic config
- [ ] Cross-join produces correct row count
- [ ] All generated dates satisfy dow constraint
- [ ] All tests pass: `pytest tests/test_gendata_integration.py -v`

---

## Test Specification

```python
# tests/test_gendata_integration.py
import pytest
from datetime import date, timedelta
import pandas as pd


class TestGenDataIntegration:
    def test_component_loadable(self):
        """GenData is discoverable via getComponent."""
        from flowtask.components import getComponent
        cls = getComponent("GenData")
        assert cls is not None
        assert cls.__name__ == "GenData"

    @pytest.mark.asyncio
    async def test_weekly_mondays_full_lifecycle(self):
        """Realistic: every Monday from 2024-01-01 to 2026-03-20."""
        # Configure, start, run
        # Verify all dates are Mondays
        # Verify first >= 2024-01-01, last <= 2026-03-20

    @pytest.mark.asyncio
    async def test_cross_join_two_rules(self):
        """Two rules with 4 and 3 dates produce 12 rows."""

    @pytest.mark.asyncio
    async def test_date_format_output(self):
        """date_format produces string column."""
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/gendata-component.spec.md` for full context
2. **Check dependencies** — verify TASK-009 and TASK-010 are in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-011-gendata-integration-tests.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
