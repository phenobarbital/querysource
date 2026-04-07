# TASK-013: Unit tests for tMerge cross_func

**Feature**: tMerge Cross-Function Join
**Feature ID**: FEAT-006
**Spec**: `sdd/specs/tmerge-cross-function.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-012
**Assigned-to**: unassigned

---

## Context

This task implements Module 2 from the FEAT-006 spec. After TASK-012 adds the `cross_func` join type to `tMerge`, this task validates all edge cases and expected behaviors with comprehensive unit tests.

---

## Scope

- Create `tests/test_tmerge_cross_func.py` with the following test cases:
  - `test_cross_func_basic` — cross join + range check produces correct boolean column
  - `test_cross_func_open_range` — `NaT`/`None` in `range_end` treated as open (returns `True`)
  - `test_cross_func_before_start` — date before `range_start` returns `False`
  - `test_cross_func_after_end` — date after `range_end` returns `False`
  - `test_cross_func_on_boundary` — date exactly on `range_start` or `range_end` returns `True` (inclusive)
  - `test_cross_func_custom_result_column` — `result_column: "is_employed"` creates that column name
  - `test_cross_func_missing_config` — missing `eval_column` or `range_start` raises `ConfigError`
  - `test_cross_func_missing_column` — column not found in DataFrame raises `ComponentError`
  - `test_cross_func_row_count` — result has `len(DF1) * len(DF2)` rows
  - `test_cross_func_metrics` — validates `ACTIVE_COUNT` and `INACTIVE_COUNT` metrics

**NOT in scope**:
- Integration tests with a full pipeline
- Modifying the `tMerge` implementation (that's TASK-012)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_tmerge_cross_func.py` | CREATE | Comprehensive unit tests for cross_func join type |

---

## Implementation Notes

### Pattern to Follow

The tests must instantiate `tMerge` directly and mock the `previous` attribute to provide test DataFrames. Look at existing test patterns in the project for how `FlowComponent` subclasses are tested.

Since `tMerge` requires `self.previous[0].output()` and `self.previous[1].output()` for its DataFrames, tests need mock objects that return the fixture DataFrames.

### Test Data / Fixtures

```python
import pandas as pd
import pytest

@pytest.fixture
def weekly_dates_df():
    """3 weeks of Mondays."""
    return pd.DataFrame({
        "weekly_date": pd.to_datetime(["2025-01-06", "2025-01-13", "2025-01-20"])
    })

@pytest.fixture
def employees_df():
    """Two employees: one active, one terminated mid-range."""
    return pd.DataFrame({
        "employee_id": [1, 2],
        "name": ["Alice", "Bob"],
        "start_date": pd.to_datetime(["2025-01-01", "2025-01-01"]),
        "termination_date": [pd.NaT, pd.Timestamp("2025-01-15")],
    })
```

Expected result for fixtures above (3 weeks x 2 employees = 6 rows):

| weekly_date | employee_id | name | start_date | termination_date | active |
|---|---|---|---|---|---|
| 2025-01-06 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-06 | 2 | Bob | 2025-01-01 | 2025-01-15 | True |
| 2025-01-13 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-13 | 2 | Bob | 2025-01-01 | 2025-01-15 | True |
| 2025-01-20 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-20 | 2 | Bob | 2025-01-01 | 2025-01-15 | False |

### Key Constraints
- Use `pytest` and `pytest-asyncio` for async test methods.
- Do not depend on external services or databases.
- Tests must be fully self-contained with fixture data.

### References in Codebase
- `flowtask/components/tMerge.py` — the component under test
- `flowtask/exceptions.py` — `ComponentError`, `ConfigError`
- `tests/` — existing test directory for pattern reference

---

## Acceptance Criteria

- [ ] All 10 test cases created and passing.
- [ ] Tests cover: basic behavior, open ranges, boundary conditions, custom column names, error handling, row counts, metrics.
- [ ] Tests run with: `pytest tests/test_tmerge_cross_func.py -v`
- [ ] No test depends on external services or databases.
- [ ] Tests properly mock the `previous` attribute for DataFrame injection.

---

## Test Specification

```python
# tests/test_tmerge_cross_func.py
import pytest
import pandas as pd
from unittest.mock import MagicMock
from flowtask.components.tMerge import tMerge
from flowtask.exceptions import ComponentError, ConfigError


@pytest.fixture
def weekly_dates_df():
    return pd.DataFrame({
        "weekly_date": pd.to_datetime(["2025-01-06", "2025-01-13", "2025-01-20"])
    })


@pytest.fixture
def employees_df():
    return pd.DataFrame({
        "employee_id": [1, 2],
        "name": ["Alice", "Bob"],
        "start_date": pd.to_datetime(["2025-01-01", "2025-01-01"]),
        "termination_date": [pd.NaT, pd.Timestamp("2025-01-15")],
    })


class TestTMergeCrossFunc:
    @pytest.mark.asyncio
    async def test_cross_func_basic(self, weekly_dates_df, employees_df):
        """Cross join + range check produces correct boolean column."""
        # Setup tMerge with cross_func, inject DFs, call run, verify 'active' column
        ...

    @pytest.mark.asyncio
    async def test_cross_func_open_range(self, weekly_dates_df, employees_df):
        """NaT in range_end treated as open (returns True)."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_before_start(self):
        """Date before range_start returns False."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_after_end(self):
        """Date after range_end returns False."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_on_boundary(self):
        """Date exactly on boundary returns True (inclusive)."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_custom_result_column(self, weekly_dates_df, employees_df):
        """Custom result_column name is used."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_missing_config(self):
        """Missing required params raises ConfigError."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_missing_column(self):
        """Column not in DataFrame raises ComponentError."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_row_count(self, weekly_dates_df, employees_df):
        """Result has len(DF1) * len(DF2) rows."""
        ...

    @pytest.mark.asyncio
    async def test_cross_func_metrics(self, weekly_dates_df, employees_df):
        """ACTIVE_COUNT and INACTIVE_COUNT metrics are correct."""
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/tmerge-cross-function.spec.md` for full context.
2. **Check dependencies** — verify TASK-012 is in `sdd/tasks/completed/`.
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`.
4. **Read** `flowtask/components/tMerge.py` to understand the implementation from TASK-012.
5. **Check existing tests** in `tests/` for patterns on how components are tested.
6. **Implement** all test cases following the scope above.
7. **Run** `pytest tests/test_tmerge_cross_func.py -v` and ensure all pass.
8. **Verify** all acceptance criteria are met.
9. **Move this file** to `sdd/tasks/completed/TASK-013-tmerge-cross-func-tests.md`.
10. **Update index** → `"done"`.
11. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: claude-session-2026-03-20
**Date**: 2026-03-20
**Notes**: All 10 test cases implemented and passing. Tests use a MockComponent helper to inject DataFrames directly, bypassing the full pipeline. Metrics validation uses unittest.mock.MagicMock for the stat object.

**Deviations from spec**: none
