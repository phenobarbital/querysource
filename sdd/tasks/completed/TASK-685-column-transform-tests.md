# TASK-685: Unit & Integration Tests for Column Transforms

**Feature**: FEAT-098 — MultiQS New Transformations
**Spec**: `sdd/specs/multiqs-new-transformations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-682, TASK-683, TASK-684
**Assigned-to**: unassigned

---

## Context

This task creates comprehensive unit and integration tests for the three new
column-selection transforms: PluckCols, DropCols, and FilterCols (spec §4).
It depends on all three implementation tasks being complete first.

---

## Scope

- Create test file with all unit tests specified in the spec §4.
- Cover all five matching modes for PluckCols and DropCols.
- Cover all three expressions for FilterCols.
- Test dict-of-DataFrames input for all three transforms.
- Test error conditions (missing columns, invalid regex, no selectors, unknown expression).
- Test empty DataFrame raises `DataNotFound`.
- Integration test: chain PluckCols → DropCols in sequence.
- Integration test: `get_transform_module()` discovery for all three.
- Integration test: `ComponentRegistry.discover_all()` includes all three.

**NOT in scope**:
- Modifying any implementation code
- End-to-end MultiQS pipeline tests with real data sources

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_multiqs_column_transforms.py` | CREATE | All unit + integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pytest                                                                      # test framework
import pandas as pd                                                                # verified: used throughout
from querysource.queries.multi.transformations.PluckCols import PluckCols          # TASK-682 creates this
from querysource.queries.multi.transformations.DropCols import DropCols            # TASK-683 creates this
from querysource.queries.multi.transformations.FilterCols import FilterCols        # TASK-684 creates this
from querysource.queries.multi import get_transform_module                         # verified: querysource/queries/multi/__init__.py:37
from querysource.queries.multi.registry import ComponentRegistry                   # verified: querysource/queries/multi/registry.py:64
from querysource.exceptions import DriverError, DataNotFound                       # verified: querysource/exceptions.py
```

### Existing Test Patterns
```python
# Tests in this codebase use pytest + pytest-asyncio
# Async tests use: @pytest.mark.asyncio / async def test_...
# Fixtures use standard @pytest.fixture decorator
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:37
def get_transform_module(clsname: str):
    """Dynamic import: .transformations.<clsname> → getattr(module, clsname)"""

# querysource/queries/multi/registry.py:84
class ComponentRegistry:
    @classmethod
    @functools.lru_cache(maxsize=1)
    def discover_all(cls) -> dict[str, type]:  # line 84
        """Discover all component classes."""
```

### Does NOT Exist
- ~~`querysource.queries.multi.transformations.PluckCols`~~ — TASK-682 creates this
- ~~`querysource.queries.multi.transformations.DropCols`~~ — TASK-683 creates this
- ~~`querysource.queries.multi.transformations.FilterCols`~~ — TASK-684 creates this
- ~~`ComponentRegistry.discover_all.cache_clear()`~~ — use `ComponentRegistry.discover_all.cache_clear()` (lru_cache provides this)

---

## Implementation Notes

### Test Fixtures
```python
@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie"],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "phone": ["111", "222", "333"],
        "internal_id": [1, 2, 3],
        "revenue_q1": [100, 200, 300],
        "revenue_q2": [110, 210, 310],
        "debug_flag": [True, True, True],
        "debug_trace": ["x", "y", "z"],
        "tmp_scratch": ["a", "b", "c"],
        "all_null_col": [None, None, None],
        "empty_col": [None, "", None],
        "constant_col": ["X", "X", "X"],
    })

@pytest.fixture
def sample_dict(sample_df):
    return {"df1": sample_df.copy(), "df2": sample_df.copy()}
```

### Key Constraints
- All transform tests must use `async with obj as o: result = await o.run()`
  pattern (matching MultiQS dispatch at `multi/__init__.py:342-343`).
- `ComponentRegistry.discover_all` is LRU-cached — call `cache_clear()` before
  testing discovery if other tests may have populated the cache.
- Tests must be independent and not depend on execution order.
- Use `pytest.raises(DriverError)` for error condition tests.
- Use `pytest.raises(DataNotFound)` for empty DataFrame tests.

### References in Codebase
- `querysource/queries/multi/__init__.py:338-343` — how transforms are invoked
- `querysource/queries/multi/registry.py:119-131` — how transforms are discovered

---

## Acceptance Criteria

- [ ] All PluckCols unit tests pass (exact, glob, regex, startswith, endswith, combined, dict, errors)
- [ ] All DropCols unit tests pass (exact, glob, regex, startswith, endswith, combined, dict, errors)
- [ ] All FilterCols unit tests pass (all_null, all_empty, constant, invalid, dict)
- [ ] Empty DataFrame test passes for all three transforms
- [ ] Integration test: transform chain PluckCols → DropCols works
- [ ] Integration test: `get_transform_module()` discovers all three
- [ ] Integration test: `ComponentRegistry.discover_all()` includes all three
- [ ] All tests pass: `pytest tests/test_multiqs_column_transforms.py -v`
- [ ] No linting errors: `ruff check tests/test_multiqs_column_transforms.py`

---

## Test Specification

> Full test matrix from spec §4:

| Test | Module | Description |
|---|---|---|
| `test_pluck_cols_exact` | PluckCols | Keep 2 of N columns by exact name |
| `test_pluck_cols_glob_pattern` | PluckCols | `pattern: "revenue_*"` keeps matching columns |
| `test_pluck_cols_regex` | PluckCols | `regex: "^(name\|email)$"` keeps matching |
| `test_pluck_cols_startswith` | PluckCols | `startswith: ["rev"]` keeps matching |
| `test_pluck_cols_endswith` | PluckCols | `endswith: ["_id"]` keeps matching |
| `test_pluck_cols_combined` | PluckCols | Multiple modes unioned |
| `test_pluck_cols_missing_exact` | PluckCols | Exact name not present → DriverError |
| `test_pluck_cols_no_selector` | PluckCols | No mode provided → DriverError |
| `test_pluck_cols_dict_input` | PluckCols | Dict of DataFrames |
| `test_drop_cols_exact` | DropCols | Drop 2 of N columns by exact name |
| `test_drop_cols_glob_pattern` | DropCols | `pattern: "debug_*"` drops matching |
| `test_drop_cols_regex` | DropCols | `regex: "^tmp_"` drops matching |
| `test_drop_cols_startswith` | DropCols | `startswith: ["debug_"]` drops matching |
| `test_drop_cols_endswith` | DropCols | `endswith: ["_flag"]` drops matching |
| `test_drop_cols_combined` | DropCols | Multiple modes unioned |
| `test_drop_cols_missing_exact` | DropCols | Non-existent column silently ignored |
| `test_drop_cols_dict_input` | DropCols | Dict of DataFrames |
| `test_filter_cols_all_null` | FilterCols | Column with all NaN removed |
| `test_filter_cols_all_empty` | FilterCols | Column with NaN + empty strings removed |
| `test_filter_cols_constant` | FilterCols | Column with single unique value removed |
| `test_filter_cols_invalid_expression` | FilterCols | Unknown expression → DriverError |
| `test_filter_cols_dict_input` | FilterCols | Dict of DataFrames |
| `test_empty_dataframe` | All | Empty DataFrame → DataNotFound |
| `test_transform_chain_pluck_then_drop` | Integration | Chain PluckCols + DropCols |
| `test_get_transform_module_discovery` | Integration | `get_transform_module` finds all three |
| `test_component_registry_discovery` | Integration | `ComponentRegistry` includes all three |

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiqs-new-transformations.spec.md` for full context
2. **Check dependencies** — verify TASK-682, TASK-683, TASK-684 are complete
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm PluckCols, DropCols, FilterCols modules exist (created by prior tasks)
   - Confirm `get_transform_module` and `ComponentRegistry` imports still work
   - Read the actual class implementations to understand exact attribute names
4. **Implement** all tests per the test matrix above
5. **Run tests**: `pytest tests/test_multiqs_column_transforms.py -v`
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-685-column-transform-tests.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
