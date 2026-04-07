# FEAT-003: Planogram Composable Type Support

**Status**: approved
**Author**: juanfran
**Created**: 2026-03-19
**Updated**: 2026-03-19

---

## 1. Motivation & Business Requirements

### Problem Statement

The parrot library (`parrot.pipelines.planogram`) was redesigned to use a **composable type
pattern**: a single `PlanogramCompliance` orchestrator delegates type-specific logic (ROI
detection, object detection, compliance) to `AbstractPlanogramType` subclasses (e.g.,
`ProductOnShelves`). The type is selected via `PlanogramConfig.planogram_type`.

The flowtask component `flowtask/components/PlanogramCompliance.py` never reads `planogram_type`
from the DB record or YAML config, so it always silently falls back to `"product_on_shelves"`.
As new types land in parrot (`InkWall`, `BoxesOnFloor`, `TVWall`, backlits, etc.) the component
cannot route to them without code changes.

### Business Value

The goal is a clear two-tier configuration model:

| Tier | Where | When to change |
|------|-------|---------------|
| **Planogram instance** (new endcap, new store, new prompts) | DB table `troc.planograms_configurations` | No deploy needed |
| **Planogram type** (new visual structure, new ROI logic) | parrot code (`AbstractPlanogramType` subclass) | Requires deploy |

This means: adding a new planogram for a brand/store that uses an existing type (e.g., a new
`ProductOnShelves` endcap for Staples) only requires a DB record — no code changes in flowtask
or parrot. New types (e.g., `InkWall`) require a parrot code change but no flowtask changes.

### Success Metrics

- Existing YAML tasks with no `planogram_type` field behave identically (backward compatible).
- A task can select `planogram_type: ink_wall` (once parrot supports it) via YAML or DB with no flowtask deploy.
- All planogram configuration (prompts, thresholds, geometry) is DB-driven; YAML only needs `name` and optionally `planogram_type` as an emergency override.

---

## 2. Architectural Design

### Overview

Two focused changes to the flowtask component:

1. **`__init__()`**: Accept `planogram_type` as an optional YAML kwarg (defaults to `"product_on_shelves"`).
2. **`get_planogram_config()`**: Read `planogram_type` from the DB result (with fallback to the YAML kwarg, then to the default). Pass it to `PlanogramConfig(planogram_type=...)`.

Everything else — composable selection, type-specific ROI/detection/compliance logic — stays
inside parrot where it belongs. Flowtask's role is purely to assemble the `PlanogramConfig`
with all DB-sourced values and hand it to `parrot.pipelines.planogram.PlanogramCompliance`.

One minor cleanup: replace the `print('PLANOGRAM CONFIG:', ...)` on line 156 with
`self._logger.debug(...)`.

### Integration Points

| Component | Interaction | Impact |
|-----------|-------------|--------|
| `flowtask/components/PlanogramCompliance.py` | modified | Read + forward `planogram_type`; replace print with logger |
| `flowtask/interfaces/pipelines/parrot.py` | modified (minor) | Replace `print()` on line 131 with `self.logger.debug()` |
| `parrot.pipelines.models.PlanogramConfig` | consumed (no change) | `planogram_type` field already exists, defaults to `"product_on_shelves"` |
| `parrot.pipelines.planogram.plan.PlanogramCompliance` | consumed (no change) | Already routes to `_PLANOGRAM_TYPES[planogram_type]` internally |
| DB: `troc.planograms_configurations` | consumed (no schema change required) | `planogram_type` column read via `.get()` with safe fallback |

### Data Flow

```
YAML task
  └─ planogram_type (optional override)
       ↓
PlanogramCompliance.__init__()
  └─ self._planogram_type = kwargs.get('planogram_type', 'product_on_shelves')
       ↓
get_planogram_config()
  └─ DB result.get('planogram_type', self._planogram_type)  ← DB wins, YAML as fallback
       └─ PlanogramConfig(planogram_type=resolved_type, prompts=..., geometry=..., ...)
            ↓
parrot.PlanogramCompliance(planogram_config=config, llm=llm)
  └─ _type_handler = _PLANOGRAM_TYPES[planogram_type](pipeline=self, config=config)
       └─ compute_roi() / detect_objects() / check_planogram_compliance()  ← type-specific
```

---

## 3. Module Breakdown

### New Modules

None.

### Modified Modules

| Module | Path | Changes |
|--------|------|---------|
| `PlanogramCompliance` | `flowtask/components/PlanogramCompliance.py` | Add `planogram_type` kwarg in `__init__`; read from DB result in `get_planogram_config()`; replace `print()` with `self._logger.debug()` |
| `AIPipeline` | `flowtask/interfaces/pipelines/parrot.py` | Replace `print()` on line 131 with `self.logger.debug()` |

---

## 4. External Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `parrot` | installed | `PlanogramConfig.planogram_type` field already present |

### AI-Parrot Dependencies

No changes required to parrot. This feature only adapts the flowtask component to correctly
forward configuration that parrot already supports.

When new types (e.g., `InkWall`) are added to parrot, flowtask will route to them automatically
via the `planogram_type` string — no additional flowtask changes needed.

---

## 5. Acceptance Criteria

- [ ] Existing YAML tasks without `planogram_type` continue to work unchanged (defaults to `"product_on_shelves"`).
- [ ] A YAML task with `planogram_type: product_on_shelves` produces identical behavior to omitting the field.
- [ ] When DB record has `planogram_type` column, that value is used; YAML value overrides DB if present.
- [ ] When DB record lacks `planogram_type` column, no exception is raised (safe `.get()` fallback).
- [ ] When an unknown `planogram_type` is provided, a `ConfigError` is raised with a clear message.
- [ ] No `print()` statements remain in `PlanogramCompliance.py` or `AIPipeline`; all use logger.
- [ ] All new code has Google-style docstrings and type hints.
- [ ] All existing tests pass: `pytest tests/ -v`

---

## 6. Test Plan

### Unit Tests

- `tests/test_planogram_compliance.py` — test that `planogram_type` is read from DB result and forwarded to `PlanogramConfig`; test fallback chain (DB → YAML kwarg → default).

### Integration Tests

- Manual: run an existing YAML planogram task against a real DB record without `planogram_type` column — verify no regression.
- Manual: add `planogram_type: product_on_shelves` to a YAML task — verify same results.

### Manual Validation

1. Run an existing planogram task (no changes to YAML) — confirm identical output.
2. Add `planogram_type: product_on_shelves` explicitly to YAML — confirm identical output.
3. Set `planogram_type` to an invalid string — confirm `ConfigError` is raised.

---

## 7. Open Questions

| # | Question | Owner | Status |
|---|----------|-------|--------|
| 1 | Does `troc.planograms_configurations` already have a `planogram_type` column in prod? | Jesus Lara / DB team | open | No, we need to add it.
| 2 | Priority order when both YAML and DB define `planogram_type`: current proposal is YAML overrides DB (YAML = emergency override). Confirm? | Jesus Lara | open | Yes.
| 3 | Should the `print()` fix in `AIPipeline` (line 131) be in scope or a separate cleanup PR? | juanfran | open | Yes, it is in scope.

---

## 8. Worktree Strategy

- **Isolation**: `per-spec`
- **Parallel tasks**: none — both changes are in tightly coupled methods of the same component; sequential is cleaner.
- **Cross-feature deps**: none — no other in-flight specs.
- **Rationale**: The diff is small (≈10 lines across 2 files). A single worktree keeps things simple and avoids merge overhead for a change of this size.
