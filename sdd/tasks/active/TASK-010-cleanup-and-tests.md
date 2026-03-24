# TASK-010: Replace print() with Logger in AIPipeline + Unit Tests

**Feature**: planogram-composable-type-support
**Feature ID**: FEAT-003
**Spec**: `sdd/specs/planogram-composable-type-support.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-009
**Assigned-to**: unassigned

---

## Context

> With TASK-009 complete, the `planogram_type` routing is functional. This task handles
> two remaining acceptance criteria from the spec:
> 1. Replace the leftover `print()` on line 131 of `AIPipeline` with `self.logger.debug()`.
> 2. Write the unit tests that validate the full fallback chain implemented in TASK-009.
>
> Implements spec Section 5 (Acceptance Criteria items 6-8) and Section 6 (Test Plan).

---

## Scope

- Replace `print(f'Processing row {idx} with data: {row_dict}')` on line 131 of `flowtask/interfaces/pipelines/parrot.py` with `self.logger.debug(...)`.
- Write unit tests in `tests/test_planogram_compliance_routing.py` covering the full fallback chain from TASK-009.

**NOT in scope**: Any further refactoring of `AIPipeline`. Only the one print() on line 131.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/interfaces/pipelines/parrot.py` | MODIFY | Replace print() on line 131 with self.logger.debug() |
| `tests/test_planogram_compliance_routing.py` | CREATE | Unit tests for planogram_type fallback chain |

---

## Implementation Notes

### The print() to replace
```python
# Current (line 131 of flowtask/interfaces/pipelines/parrot.py):
print(f'Processing row {idx} with data: {row_dict}')

# Replace with:
self.logger.debug(f'Processing row {idx}')
# Note: do NOT log row_dict — it may contain image bytes (huge output)
```

### Key Constraints
- Tests must use `pytest` and `pytest-asyncio` for async tests.
- Mock the DB connection (`AsyncDB`) to avoid needing a real DB in tests.
- Mock parrot's `PlanogramCompliance` to avoid needing GPU/LLM in tests.
- Tests must pass with `pytest tests/test_planogram_compliance_routing.py -v`.

### References in Codebase
- `flowtask/interfaces/pipelines/parrot.py:131` — the print() to replace.
- `tests/` — existing test patterns to follow for mocking style.
- `flowtask/components/PlanogramCompliance.py` — the component under test (post TASK-009).

---

## Acceptance Criteria

- [ ] No `print()` remains in `flowtask/interfaces/pipelines/parrot.py`.
- [ ] `self.logger.debug()` used instead (row_dict NOT logged to avoid binary data in logs).
- [ ] All unit tests pass: `pytest tests/test_planogram_compliance_routing.py -v`.
- [ ] Tests cover: default type, YAML kwarg, DB overrides YAML, missing DB column, unknown type raises ConfigError.
- [ ] Google-style docstrings and type hints on new test file.

---

## Test Specification

```python
# tests/test_planogram_compliance_routing.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from flowtask.exceptions import ConfigError


class TestPlanogramTypeDefault:
    def test_defaults_to_product_on_shelves(self):
        """planogram_type defaults to 'product_on_shelves' when not provided in kwargs."""
        # Initialize PlanogramCompliance without planogram_type kwarg
        # Assert self._planogram_type == "product_on_shelves"


class TestPlanogramTypeYAMLKwarg:
    def test_yaml_kwarg_is_stored(self):
        """planogram_type kwarg from YAML is stored on the instance."""
        # Initialize with planogram_type="ink_wall"
        # Assert self._planogram_type == "ink_wall"


class TestPlanogramTypeDBResolution:
    @pytest.mark.asyncio
    async def test_db_value_wins_over_yaml(self):
        """DB planogram_type takes precedence over YAML kwarg."""
        # YAML: planogram_type="ink_wall"
        # DB result: planogram_type="product_on_shelves"
        # Expected: PlanogramConfig receives planogram_type="product_on_shelves"

    @pytest.mark.asyncio
    async def test_yaml_fallback_when_db_column_missing(self):
        """YAML planogram_type used when DB record lacks the column."""
        # YAML: planogram_type="ink_wall"
        # DB result: no planogram_type key
        # Expected: PlanogramConfig receives planogram_type="ink_wall"

    @pytest.mark.asyncio
    async def test_default_fallback_when_both_missing(self):
        """Default 'product_on_shelves' used when neither YAML nor DB provide type."""
        # No YAML kwarg, no DB column
        # Expected: PlanogramConfig receives planogram_type="product_on_shelves"

    @pytest.mark.asyncio
    async def test_unknown_type_raises_config_error(self):
        """Unknown planogram_type string raises ConfigError."""
        # DB result: planogram_type="nonexistent_type"
        # Expected: raises ConfigError with descriptive message


class TestAIPipelineNoprint:
    def test_no_print_in_process_rows(self):
        """AIPipeline._process_dataframe_rows uses logger.debug, not print."""
        import flowtask.interfaces.pipelines.parrot as m
        import inspect
        source = inspect.getsource(m.AIPipeline._process_dataframe_rows)
        assert 'print(' not in source
```

---

## Agent Instructions

When you pick up this task:

1. **Verify TASK-009 is in `sdd/tasks/completed/`** before starting.
2. **Read the spec** at `sdd/specs/planogram-composable-type-support.spec.md`.
3. **Read `flowtask/interfaces/pipelines/parrot.py`** to locate the print() on line 131.
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID.
5. **Implement** following the scope and notes above.
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_planogram_compliance_routing.py -v`.
7. **Verify** all acceptance criteria are met.
8. **Move this file** to `sdd/tasks/completed/TASK-010-cleanup-and-tests.md`.
9. **Update index** → `"done"`.
10. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
