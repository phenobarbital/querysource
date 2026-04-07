# Feature Specification: tMerge Cross-Function Join

**Feature ID**: FEAT-006
**Date**: 2026-03-20
**Author**: Jesus Lara
**Status**: approved
**Target version**: 1.x

---

## 1. Motivation & Business Requirements

### Problem Statement

When building analytical pipelines, a common pattern is to generate a scaffold DataFrame (e.g. every Monday for 3 years) and cross-join it with an entity DataFrame (e.g. employees), then evaluate whether each entity was "active" on each date based on a date range in the entity data (e.g. `start_date` to `termination_date`).

Today, `tMerge` supports `type: cross` which produces the cartesian product, but users must then add a separate `TransformRows` step to evaluate the range condition and create the boolean column. This is verbose, error-prone, and requires writing Python expressions inline in YAML.

A new merge type — **`cross_func`** — would perform the cross join *and* apply a configurable range-check function in a single declarative step, producing a boolean result column.

### Goals
- Add a `cross_func` join type to `tMerge` that performs a cross join followed by a per-row evaluation function.
- The built-in evaluation checks whether a single date column from DF1 falls within a range defined by two columns in DF2.
- Support open-ended ranges: if the right boundary column is `NaT`/`None`/`null`, treat the range as open (entity is still active).
- Assign the boolean result to a configurable output column (default: `"active"`).
- Keep the component backwards-compatible — existing `cross`, `inner`, `outer`, `left`, `right`, `asof` types are unchanged.

### Non-Goals (explicitly out of scope)
- Generic arbitrary Python callables as the evaluation function (v1 only supports date-range containment).
- Multi-column evaluation (e.g. checking two date columns from DF1 against two ranges).
- Performance optimization via vectorized interval trees — standard pandas boolean indexing is sufficient for expected data sizes.

---

## 2. Architectural Design

### Overview

Extend `tMerge` with a new `cross_func` join type. When `type: cross_func` is specified, the component:

1. Performs a standard `pd.merge(..., how='cross')` to produce the cartesian product.
2. Applies a vectorized date-range check: `df[eval_column] >= df[range_start]` AND (`df[range_end].isna()` OR `df[eval_column] <= df[range_end]`).
3. Assigns the boolean result to `df[result_column]`.

```
DF1 (weekly_dates)  ──→  cross join  ──→  Cartesian Product  ──→  range eval  ──→  Result DF
DF2 (employees)     ──┘                                                              (with "active" column)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `tMerge` | extends | New join type added to existing component |
| `FlowComponent` | inherits | No changes to base class |
| `GenData` (FEAT-005) | upstream | Natural producer of the weekly_date scaffold DF |

### Data Models

No new Pydantic models required. Configuration is passed via YAML kwargs to the existing `tMerge.__init__`.

### New Public Interfaces

New YAML configuration attributes for `tMerge` when `type: cross_func`:

```yaml
tMerge:
  depends:
    - GenData_weekly     # DF1: scaffold with weekly_date column
    - QueryToPandas_emp  # DF2: employee data with start_date, termination_date
  type: cross_func
  eval_column: weekly_date        # column from DF1 to evaluate
  range_start: start_date         # column from DF2: left boundary (inclusive)
  range_end: termination_date     # column from DF2: right boundary (inclusive, NaT = open)
  result_column: active           # output boolean column name (default: "active")
```

### New Constructor Parameters

```python
# Added to tMerge.__init__
self._eval_column: Optional[str] = kwargs.pop('eval_column', None)
self._range_start: Optional[str] = kwargs.pop('range_start', None)
self._range_end: Optional[str] = kwargs.pop('range_end', None)
self._result_column: str = kwargs.pop('result_column', 'active')
```

---

## 3. Module Breakdown

### Module 1: tMerge cross_func join type
- **Path**: `flowtask/components/tMerge.py`
- **Responsibility**: Implement the `cross_func` merge path — cross join + vectorized date-range evaluation + boolean result column.
- **Depends on**: Existing `tMerge` class, `pd.merge`

### Module 2: Unit tests
- **Path**: `tests/test_tmerge_cross_func.py`
- **Responsibility**: Validate the `cross_func` join type with various edge cases.
- **Depends on**: Module 1

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_cross_func_basic` | Module 1 | Cross join + range check produces correct boolean column |
| `test_cross_func_open_range` | Module 1 | `NaT`/`None` in `range_end` treated as open (returns `True`) |
| `test_cross_func_before_start` | Module 1 | Date before `range_start` returns `False` |
| `test_cross_func_after_end` | Module 1 | Date after `range_end` returns `False` |
| `test_cross_func_on_boundary` | Module 1 | Date exactly on `range_start` or `range_end` returns `True` (inclusive) |
| `test_cross_func_custom_result_column` | Module 1 | `result_column: "is_employed"` creates that column name |
| `test_cross_func_missing_config` | Module 1 | Missing `eval_column` or `range_start` raises `ConfigError` |
| `test_cross_func_missing_column` | Module 1 | Column not found in DataFrame raises `ComponentError` |
| `test_cross_func_row_count` | Module 1 | Result has `len(DF1) * len(DF2)` rows |

### Integration Tests

| Test | Description |
|---|---|
| `test_cross_func_with_gendata` | Pipeline: `GenData` (weekly Mondays) → `tMerge cross_func` with employee DF |

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

Expected result for the above (3 weeks × 2 employees = 6 rows):

| weekly_date | employee_id | name | start_date | termination_date | active |
|---|---|---|---|---|---|
| 2025-01-06 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-06 | 2 | Bob | 2025-01-01 | 2025-01-15 | True |
| 2025-01-13 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-13 | 2 | Bob | 2025-01-01 | 2025-01-15 | True |
| 2025-01-20 | 1 | Alice | 2025-01-01 | NaT | True |
| 2025-01-20 | 2 | Bob | 2025-01-01 | 2025-01-15 | False |

---

## 5. Acceptance Criteria

- [ ] `tMerge` accepts `type: cross_func` and produces correct cartesian product with boolean range column.
- [ ] Open-ended ranges (`NaT`/`None` in `range_end`) correctly evaluate to `True`.
- [ ] Boundary dates are inclusive on both sides.
- [ ] `result_column` is configurable (default `"active"`).
- [ ] `ConfigError` raised when required parameters (`eval_column`, `range_start`, `range_end`) are missing.
- [ ] All date columns auto-coerced to datetime via `pd.to_datetime`.
- [ ] All unit tests pass.
- [ ] No breaking changes to existing `tMerge` join types (`cross`, `inner`, `outer`, `left`, `right`, `asof`).
- [ ] Metrics emitted: `NUM_ROWS`, `NUM_COLUMNS`, `ACTIVE_COUNT`, `INACTIVE_COUNT`.

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Follow the existing `_run_asof` / `_run_merge` pattern — add a `_run_cross_func` method.
- Route to it from `run()` when `self.type == "cross_func"`.
- Use vectorized pandas operations (no row-by-row `apply`).
- Use `self.logger` for debug output instead of `print` (though existing code uses `print` in debug — follow existing pattern for consistency).

### Implementation Sketch

```python
async def _run_cross_func(self):
    """Cross join + date-range evaluation."""
    if not self._eval_column or not self._range_start or not self._range_end:
        raise ConfigError(
            "tMerge cross_func requires 'eval_column', 'range_start', and 'range_end'"
        )

    # 1. Cross join
    df = pd.merge(self.df1, self.df2, how='cross')

    # 2. Coerce to datetime
    for col in (self._eval_column, self._range_start, self._range_end):
        if col not in df.columns:
            raise ComponentError(f"Column '{col}' not found after cross join")
        df[col] = pd.to_datetime(df[col], errors='coerce')

    # 3. Vectorized range check (inclusive, open right if NaT)
    after_start = df[self._eval_column] >= df[self._range_start]
    end_is_open = df[self._range_end].isna()
    before_end = df[self._eval_column] <= df[self._range_end]
    df[self._result_column] = after_start & (end_is_open | before_end)

    # 4. Metrics
    self._result = df
    self.add_metric("NUM_ROWS", df.shape[0])
    self.add_metric("NUM_COLUMNS", df.shape[1])
    active_count = int(df[self._result_column].sum())
    self.add_metric("ACTIVE_COUNT", active_count)
    self.add_metric("INACTIVE_COUNT", df.shape[0] - active_count)

    if self._debug:
        self._print_debug(df)

    return self._result
```

### Known Risks / Gotchas
- **Memory**: Cross join produces `N × M` rows. For 156 weeks × 10,000 employees = 1.56M rows — manageable, but large employee sets with long date ranges could grow. Document the memory implication.
- **Column name collisions**: If DF1 and DF2 share column names (other than the join-related ones), `pd.merge(how='cross')` appends `_x` / `_y` suffixes. The `eval_column`, `range_start`, `range_end` must refer to the post-merge column names.

### External Dependencies

No new dependencies. Uses `pandas` (already required).

---

## 7. Open Questions

- [ ] Should we support a `filter_active` option that automatically drops rows where `active == False` to reduce downstream data size? — *Owner: Jesus*: No
- [ ] Should the range check support exclusive boundaries (e.g. `range_end_inclusive: false`)? — *Owner: Jesus*: No
- [ ] Should we allow a custom evaluation function (callable) in a future version for non-date-range checks? — *Owner: Jesus*: Yes

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks)
- All tasks run in a single worktree since Module 2 depends on Module 1.
- **Cross-feature dependencies**: None required to be merged first. Optional synergy with FEAT-005 (`GenData`) for scaffold DataFrames but not a hard dependency.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-20 | Jesus Lara | Initial draft |
