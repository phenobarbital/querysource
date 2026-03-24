# TASK-009: Planogram Type Routing in PlanogramCompliance Component

**Feature**: planogram-composable-type-support
**Feature ID**: FEAT-003
**Spec**: `sdd/specs/planogram-composable-type-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> The parrot library redesigned `PlanogramCompliance` to use a composable type pattern
> where type-specific logic (ROI detection, compliance) is delegated to subclasses of
> `AbstractPlanogramType` selected via `PlanogramConfig.planogram_type`.
>
> The flowtask component never reads `planogram_type` from the DB record or YAML config,
> so it always silently falls back to `"product_on_shelves"`. This task closes that gap.
>
> Implements spec Section 2 (Architectural Design) and Section 3 (Module Breakdown).

---

## Scope

- Add `planogram_type` optional kwarg to `PlanogramCompliance.__init__()`, defaulting to `"product_on_shelves"`.
- In `get_planogram_config()`, read `planogram_type` from DB result via `result.get('planogram_type', self._planogram_type)`.
- Pass the resolved `planogram_type` to `PlanogramConfig(planogram_type=resolved_type, ...)`.
- Replace the `print('PLANOGRAM CONFIG:', planogram_config)` on line 156 with `self._logger.debug(...)`.

**NOT in scope**: The `print()` fix in `AIPipeline` (that is TASK-010). Unit tests (also TASK-010).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/components/PlanogramCompliance.py` | MODIFY | Add `planogram_type` kwarg + read from DB + replace print |

---

## Implementation Notes

### Fallback chain (priority order)
```
DB result['planogram_type']          ← highest priority
    → self._planogram_type (YAML kwarg)  ← fallback
        → "product_on_shelves"           ← default
```

YAML acts as emergency override over DB. DB is the primary source of truth.

### Key Constraints
- Use `result.get('planogram_type', self._planogram_type)` — safe fallback when DB column doesn't exist yet.
- The `planogram_type` string is validated implicitly by parrot (`KeyError` on unknown type) — wrap in the existing `ConfigError` try/except in `get_planogram_config()`.
- Must remain backward compatible: existing YAML tasks without `planogram_type` must work identically.
- async throughout, Google-style docstrings, strict type hints.

### References in Codebase
- `flowtask/components/PlanogramCompliance.py:66–102` — `__init__()` kwarg extraction pattern to follow.
- `flowtask/components/PlanogramCompliance.py:104–168` — `get_planogram_config()` where the DB read and `PlanogramConfig()` construction happens.
- `flowtask/components/PlanogramCompliance.py:144–155` — `PlanogramConfig(...)` constructor call to extend.
- `parrot/pipelines/models.py` — `PlanogramConfig.planogram_type` field (already exists, defaults to `"product_on_shelves"`).

---

## Acceptance Criteria

- [ ] Existing YAML tasks without `planogram_type` field work identically (backward compatible).
- [ ] YAML task with `planogram_type: product_on_shelves` produces identical behavior to omitting the field.
- [ ] When DB record has `planogram_type`, that value is used.
- [ ] When DB record lacks `planogram_type` column, no exception is raised.
- [ ] When an unknown `planogram_type` string is provided, `ConfigError` is raised.
- [ ] No `print()` on line 156 of `PlanogramCompliance.py` — uses `self._logger.debug()`.
- [ ] Google-style docstrings and type hints on modified code.

---

## Test Specification

```python
# tests/test_planogram_compliance_routing.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from flowtask.components.PlanogramCompliance import PlanogramCompliance


class TestPlanogramTypeRouting:
    def test_default_planogram_type(self):
        """planogram_type defaults to product_on_shelves when not provided."""
        comp = PlanogramCompliance.__new__(PlanogramCompliance)
        comp._planogram_type = None
        # Instantiate with no planogram_type kwarg
        # assert self._planogram_type == "product_on_shelves"

    def test_yaml_planogram_type_kwarg(self):
        """planogram_type kwarg is stored from YAML config."""
        # Build component with planogram_type="ink_wall"
        # assert self._planogram_type == "ink_wall"

    @pytest.mark.asyncio
    async def test_db_value_takes_precedence(self):
        """DB planogram_type overrides YAML kwarg."""
        # Mock DB result with planogram_type = "product_on_shelves"
        # Component initialized with planogram_type = "ink_wall" (YAML)
        # After get_planogram_config(), resolved type should be "product_on_shelves" (DB wins)

    @pytest.mark.asyncio
    async def test_missing_db_column_no_exception(self):
        """No exception when DB record lacks planogram_type column."""
        # Mock DB result WITHOUT planogram_type key
        # get_planogram_config() should succeed using YAML fallback

    @pytest.mark.asyncio
    async def test_unknown_type_raises_config_error(self):
        """Unknown planogram_type raises ConfigError."""
        from flowtask.exceptions import ConfigError
        # Mock DB result with planogram_type = "nonexistent_type"
        # assert raises ConfigError
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/planogram-composable-type-support.spec.md` for full context.
2. **Read the current component** at `flowtask/components/PlanogramCompliance.py` before editing.
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID.
4. **Implement** following the scope and notes above.
5. **Verify** all acceptance criteria are met.
6. **Move this file** to `sdd/tasks/completed/TASK-009-planogram-type-routing.md`.
7. **Update index** → `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
