# TASK-012: Implement tMerge cross_func join type

**Feature**: tMerge Cross-Function Join
**Feature ID**: FEAT-006
**Spec**: `sdd/specs/tmerge-cross-function.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements Module 1 from the FEAT-006 spec. The `tMerge` component currently supports `inner`, `outer`, `left`, `right`, `cross`, and `asof` join types. This task adds `cross_func` — a cross join followed by a vectorized date-range evaluation that produces a boolean result column.

The primary use case is joining a scaffold DataFrame (e.g. weekly dates) with an entity DataFrame (e.g. employees), then checking whether each entity was active on each date based on a `start_date`/`termination_date` range.

---

## Scope

- Add constructor parameters to `tMerge.__init__`: `eval_column`, `range_start`, `range_end`, `result_column` (default `"active"`).
- Add routing in `tMerge.run()` for `self.type == "cross_func"` to call `_run_cross_func()`.
- Implement `async def _run_cross_func(self)`:
  1. Validate required parameters (`eval_column`, `range_start`, `range_end`); raise `ConfigError` if missing.
  2. Perform `pd.merge(self.df1, self.df2, how='cross')`.
  3. Coerce `eval_column`, `range_start`, `range_end` to datetime via `pd.to_datetime`.
  4. Validate columns exist in the merged DataFrame; raise `ComponentError` if not.
  5. Vectorized range check: `eval >= start AND (end is NaT OR eval <= end)`.
  6. Assign boolean result to `result_column`.
  7. Emit metrics: `NUM_ROWS`, `NUM_COLUMNS`, `ACTIVE_COUNT`, `INACTIVE_COUNT`.
  8. Debug output via `_print_debug` when `self._debug` is set.
- Update the class docstring to document the new `cross_func` type and its YAML attributes.

**NOT in scope**:
- Unit tests (TASK-013)
- Custom evaluation functions / callables
- Exclusive boundary support

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/components/tMerge.py` | MODIFY | Add `cross_func` parameters, routing, and `_run_cross_func` method |

---

## Implementation Notes

### Pattern to Follow

Follow the existing `_run_asof` method pattern at line 144 of `tMerge.py`:

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

### Key Constraints
- Use vectorized pandas operations — no `apply()` or row-by-row iteration.
- All date coercion must use `errors='coerce'` to handle mixed types gracefully.
- Existing merge types must remain completely unchanged.
- Follow existing `print`-based debug pattern (not `self.logger`) for consistency with existing code.

### References in Codebase
- `flowtask/components/tMerge.py` — the file being modified
- `flowtask/components/flow.py` — base class `FlowComponent`
- `flowtask/exceptions.py` — `ComponentError`, `ConfigError`

---

## Acceptance Criteria

- [ ] `tMerge(type='cross_func', eval_column=..., range_start=..., range_end=..., result_column=...)` initializes without error.
- [ ] Cross join produces `len(DF1) * len(DF2)` rows.
- [ ] Boolean result column correctly evaluates date-range containment.
- [ ] Open-ended ranges (NaT/None in range_end) evaluate to `True`.
- [ ] Boundaries are inclusive on both sides.
- [ ] `ConfigError` raised when required params are missing.
- [ ] `ComponentError` raised when columns not found in merged DF.
- [ ] Metrics `NUM_ROWS`, `NUM_COLUMNS`, `ACTIVE_COUNT`, `INACTIVE_COUNT` are emitted.
- [ ] Existing merge types (`cross`, `inner`, `outer`, `left`, `right`, `asof`) are unaffected.
- [ ] Class docstring updated with `cross_func` documentation.

---

## Test Specification

*(Tests are in TASK-013 — this task focuses on implementation only)*

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/tmerge-cross-function.spec.md` for full context.
2. **Check dependencies** — this task has no dependencies.
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`.
4. **Read** `flowtask/components/tMerge.py` fully before modifying.
5. **Implement** following the scope and notes above.
6. **Verify** all acceptance criteria are met.
7. **Move this file** to `sdd/tasks/completed/TASK-012-tmerge-cross-func.md`.
8. **Update index** → `"done"`.
9. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: claude-session-2026-03-20
**Date**: 2026-03-20
**Notes**: Added `_run_cross_func()` method, 4 new constructor parameters (`eval_column`, `range_start`, `range_end`, `result_column`), routing in `run()`, updated docstring with cross_func documentation and YAML example. Version bumped to 1.2.0.

**Deviations from spec**: none
