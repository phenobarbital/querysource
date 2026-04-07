# Brainstorm: planogram-composable-type-support

**Status**: exploration
**Date**: 2026-03-19
**Author**: juanfran

---

## Problem Statement

The parrot library (`parrot.pipelines.planogram`) was redesigned by the team lead to support a
**composable type pattern**: `PlanogramCompliance` (parrot) is now the single orchestrator, and
type-specific logic (ROI detection, object detection, compliance checking) lives in subclasses of
`AbstractPlanogramType` (e.g., `ProductOnShelves`). The type is selected via
`PlanogramConfig.planogram_type`.

The flowtask component `flowtask/components/PlanogramCompliance.py` was written against the old
single-class design and does not pass `planogram_type` through its pipeline, which means it will
always silently fall back to `"product_on_shelves"` regardless of what is configured in the DB or
YAML. As new types are added (e.g., `InkWall`, `BoxesOnFloor`, `TVWall`), the component will be
unable to route to them without code changes.

## User / Stakeholder

- **Flowtask task developers**: need to configure the correct planogram type via YAML without touching Python.
- **Ops / data team**: runs planogram compliance pipelines for different fixture types (shelves, ink walls, floor boxes) — need correct routing.
- **Parrot library maintainers (Jesus Lara)**: designed the composable API and expects consumers to forward `planogram_type`.

## Constraints

- Must remain backward-compatible: existing YAML tasks that omit `planogram_type` must default to `"product_on_shelves"`.
- `PlanogramConfig` is a Pydantic model owned by parrot — the flowtask component must not redefine it.
- DB schema (`troc.planograms_configurations`) may or may not have a `planogram_type` column yet; the component must handle the absence gracefully.
- Async/await throughout; no blocking I/O.
- `uv` + `pyproject.toml` for deps — no new dependencies needed (parrot already owns all pipeline logic).

---

## Options

### Option A: Minimal — Forward `planogram_type` from DB + YAML

**Description**: Add two changes: (1) read `planogram_type` from the DB result in
`get_planogram_config()` and pass it to `PlanogramConfig`; (2) accept an optional `planogram_type`
kwarg in `PlanogramCompliance.__init__()` so it can also be overridden from the YAML task definition.
Everything else stays the same — the composable selection is handled entirely inside parrot.

**Pros**:
- Minimal diff, low risk of regression.
- The composable logic stays inside parrot where it belongs.
- Immediately enables `InkWall`, `BoxesOnFloor`, etc. as they are added to parrot with zero flowtask changes.
- YAML tasks gain a `planogram_type` field they can set.

**Cons**:
- DB column `planogram_type` may not exist yet — needs a safe `.get()` fallback.
- `print('PLANOGRAM CONFIG:', ...)` left in `get_planogram_config()` is not addressed (minor).

**Effort**: Low

**Libraries / Tools**:
| Package | Purpose | Notes |
|---------|---------|-------|
| `parrot` (installed) | `PlanogramConfig.planogram_type` field | Already defined in models.py |

**Existing Code to Reuse**:
- `flowtask/components/PlanogramCompliance.py:104` — `get_planogram_config()`, only needs one new line.
- `flowtask/components/PlanogramCompliance.py:66` — `__init__()`, add `planogram_type` kwarg.

---

### Option B: Surface Full Composable Config in YAML

**Description**: Extend the flowtask component to fully expose the new parrot composable pattern
in YAML. Beyond `planogram_type`, allow the YAML task to override `roi_detection_prompt`,
`object_identification_prompt`, and `model_normalization_patterns` inline (instead of relying solely
on DB values). This means the flowtask component becomes the "config assembler" that merges DB
defaults with YAML overrides before constructing `PlanogramConfig`.

**Pros**:
- Maximum flexibility for task authors: they can tweak prompts per deployment without DB changes.
- Enables rapid iteration on prompt engineering without schema migrations.
- Naturally documents all config knobs in the YAML interface.

**Cons**:
- Larger surface area in the component — more kwargs to maintain.
- Risk of YAML and DB configs diverging (two sources of truth for prompts).
- More complex merging logic in `get_planogram_config()`.

**Effort**: Medium

**Libraries / Tools**:
| Package | Purpose | Notes |
|---------|---------|-------|
| `parrot` (installed) | All model fields | `PlanogramConfig`, `EndcapGeometry` |
| `pydantic` (installed) | Model merging via `model_validate` | Already a dep |

**Existing Code to Reuse**:
- `flowtask/components/PlanogramCompliance.py:66–102` — `__init__()` kwarg extraction pattern.
- `flowtask/components/PlanogramCompliance.py:225–233` — `_initialize_llm()`, shows existing merge pattern.

---

### Option C: Refactor `AIPipeline` Interface into a Type-Aware Factory

**Description**: Redesign `flowtask/interfaces/pipelines/parrot.py` (`AIPipeline`) to understand
the composable type pattern. Instead of a static `pipeline_mapping` dict that maps name→class,
use a factory that accepts `planogram_type` and wires up the correct parrot `PlanogramCompliance`
with the right config. This would allow future pipeline types beyond planogram (e.g., `InkWall`
could be a top-level pipeline name, not just a type string).

**Pros**:
- Clean separation: `AIPipeline` becomes the routing layer for all parrot pipeline variants.
- Extensible to non-planogram pipelines without touching individual components.
- Removes the `print()` in `AIPipeline._process_dataframe_rows()` (line 131).

**Cons**:
- Highest effort and risk — touches a shared interface used by all future pipeline components.
- The composable routing already exists inside parrot; duplicating it in flowtask adds coupling.
- Premature generalization if planogram is the only pipeline type for now.

**Effort**: High

**Libraries / Tools**:
| Package | Purpose | Notes |
|---------|---------|-------|
| `parrot` (installed) | `AbstractPlanogramType`, `_PLANOGRAM_TYPES` | Internal to parrot |

**Existing Code to Reuse**:
- `flowtask/interfaces/pipelines/parrot.py:57–86` — `_load_pipeline()`, would be extended.
- `flowtask/interfaces/pipelines/parrot.py:88–102` — `_initialize_pipeline()`.

---

## Recommendation

**Selected**: Option A

**Rationale**: The composable type routing is already fully implemented in parrot. The flowtask
component's only gap is that it never reads `planogram_type` from the DB result or YAML config and
therefore cannot forward it to `PlanogramConfig`. Option A closes this gap with a surgical change:
two lines in `__init__` (new kwarg) and two lines in `get_planogram_config()` (read from DB with
fallback + pass to constructor). Option B adds value but introduces dual-source-of-truth complexity
before it is needed. Option C is premature.

**What we trade off**: We do not expose individual prompt overrides via YAML (Option B). If that
becomes necessary it can be layered on top of Option A with another small PR.

---

## Feature Description

### User-Facing Behavior

A YAML task can now declare a `planogram_type` field:

```yaml
PlanogramCompliance:
  name: ink_wall_bestbuy
  planogram_type: ink_wall      # new field — optional, defaults to product_on_shelves
  image_column: image_data
  ...
```

If omitted, behavior is identical to today (defaults to `"product_on_shelves"`). When the DB record
also carries a `planogram_type` column, the DB value is used; the YAML value acts as an override
with the DB value as fallback, and `"product_on_shelves"` as the final default.

### Internal Behavior

1. `PlanogramCompliance.__init__()` reads `planogram_type` from kwargs; stored as `self._planogram_type`.
2. `get_planogram_config()` reads `planogram_type` from the DB result via `result.get('planogram_type', self._planogram_type)`.
3. The resolved type string is passed as `planogram_type=...` to `PlanogramConfig(...)`.
4. Parrot's `PlanogramCompliance.__init__()` uses `planogram_config.planogram_type` to select and
   instantiate the right `AbstractPlanogramType` subclass (`_PLANOGRAM_TYPES[ptype]`).
5. All four pipeline steps (ROI detection, object detection, compliance, rendering) are executed by
   the type-specific handler — no changes needed in the flowtask component's row-processing logic.
6. The `print('PLANOGRAM CONFIG:', ...)` on line 156 is replaced with `self._logger.debug()`.

### Edge Cases & Error Handling

- **DB column absent**: `result.get('planogram_type', self._planogram_type)` — safe fallback to kwarg or `"product_on_shelves"`.
- **Unknown type string**: parrot raises `KeyError` on `_PLANOGRAM_TYPES[ptype]` — this bubbles up as `ConfigError` in `get_planogram_config()` try/except.
- **Existing YAML tasks with no `planogram_type`**: resolve to `"product_on_shelves"` — zero behavior change.

---

## Capabilities

### New Capabilities

- `planogram-type-routing` — flowtask component forwards `planogram_type` to parrot, enabling composable type selection.

### Modified Capabilities

- `planogram-config-loading` — `get_planogram_config()` now reads `planogram_type` from DB result and merges with YAML kwarg.
- `planogram-component-init` — `__init__()` accepts `planogram_type` kwarg.

---

## Impact & Integration

| Component | Impact | Notes |
|-----------|--------|-------|
| `flowtask/components/PlanogramCompliance.py` | modified | Add `planogram_type` kwarg + forward in `get_planogram_config()` |
| `flowtask/interfaces/pipelines/parrot.py` | modified (minor) | Replace `print()` on line 131 with `self.logger.debug()` |
| `parrot.pipelines.planogram.plan.PlanogramCompliance` | no change | Already handles type routing internally |
| `parrot.pipelines.models.PlanogramConfig` | no change | `planogram_type` field already exists |
| DB: `troc.planograms_configurations` | no change required | Column optional; component uses `.get()` fallback |

---

## Parallelism Assessment

- **Internal parallelism**: No — both changes are in tightly coupled methods of the same class; sequential implementation makes sense.
- **Cross-feature independence**: No conflicts with in-flight specs. `PlanogramCompliance.py` is not touched by any other active branch.
- **Recommended isolation**: `per-spec`
- **Rationale**: The change is a small, focused diff on a single component. A single worktree is sufficient; parallel worktrees would add overhead for no benefit.

---

## Open Questions

| # | Question | Owner | Status |
|---|----------|-------|--------|
| 1 | Does `troc.planograms_configurations` already have a `planogram_type` column in prod? | Jesus Lara / DB team | open |
| 2 | When will `InkWall` and `BoxesOnFloor` types land in parrot? (defines urgency of this ticket) | Jesus Lara | open |
| 3 | Should YAML `planogram_type` override DB value, or vice versa? (current proposal: YAML overrides DB) | Jesus Lara | open |
| 4 | Should `AIPipeline._process_dataframe_rows()` print on line 131 be fixed in this PR or a separate cleanup? | juanfran | open |
