# FEAT-004: Graphic Panel Display Planogram Type

**Feature ID**: FEAT-004
**Date**: 2026-03-20
**Author**: juanfran
**Status**: approved
**Target version**: TBD

---

## 1. Motivation & Business Requirements

### Problem Statement

Several planogram configurations (EcoTank endcaps, projector displays, Bose audio displays)
do not contain physical products on shelves. Instead, they consist entirely of **graphic panels /
signage zones** fixed to a display fixture. The current `product_on_shelves` composable type
is designed around detecting physical demo units, fact tags, and shelf boundaries — none of
which apply to these displays.

Running graphic-panel planograms through `product_on_shelves` is semantically incorrect and
produces meaningless shelf/fact-tag logic. These planograms need a dedicated composable type
that understands zone-based compliance: verify that the correct graphic is present in the
correct zone, with the correct text content, and (where applicable) the correct illumination
state.

**Affected configs (estimated 8–10 of 19):**
- `epson_depot_ecotank_backlit_planogram_config` (id 23)
- `epson_depot_ecotank_not_backlit_planogram_config` (id 24)
- `epson_ecotank_planogram_config` (id 5)
- `epson_depot_ecotank_planogram_config` (id 12)
- `epson_projector_backlit_planogram_config` (id 13)
- `epson_projector_not_backlit_planogram_config` (id 14)
- Bose audio displays (ids 17, 18, 20, 21, 22) — pending confirmation

### Goals

- Implement `GraphicPanelDisplay` as a new `AbstractPlanogramType` subclass in parrot.
- Register it as `"graphic_panel_display"` in `_PLANOGRAM_TYPES`.
- Zone-based compliance: each shelf level maps to a named graphic zone (header, middle, bottom).
- Text requirement verification per zone (same mechanism as `product_on_shelves`).
- **Illumination check**: detect whether a graphic is backlit (ON) or not backlit (OFF) and
  enforce it as a compliance criterion when specified in the planogram config.
- No fact-tag logic, no physical product counting.

### Non-Goals

- Physical product detection — that remains in `product_on_shelves`.
- Changes to the `PlanogramCompliance` orchestrator (`plan.py`) — it already supports
  any `AbstractPlanogramType` subclass without modification.
- Changes to flowtask `PlanogramCompliance.py` — the `planogram_type` routing already
  works via FEAT-003.
- Migration of existing YAML tasks — operator responsibility.

---

## 2. Architectural Design

### Overview

Add `GraphicPanelDisplay` as a new composable type in `parrot/pipelines/planogram/types/`.
The class implements the four abstract methods from `AbstractPlanogramType`:

1. **`compute_roi`** — uses the existing LLM ROI prompt (same as `product_on_shelves`) to
   locate the endcap boundary.
2. **`detect_objects_roi`** — detects the named graphic zones defined in the planogram config
   using the `roi_detection_prompt` (already customised per planogram in DB).
3. **`detect_objects`** — for each detected zone, runs OCR + visual feature verification.
   Applies illumination check when `illumination_status` is in `visual_features`.
4. **`check_planogram_compliance`** — compares detected zones against expected zones per shelf
   level; evaluates text requirements and illumination compliance.

### Component Diagram

```
PlanogramCompliance (plan.py)
        │
        ▼
_PLANOGRAM_TYPES["graphic_panel_display"]
        │
        ▼
GraphicPanelDisplay(AbstractPlanogramType)
   ├── compute_roi()            ← LLM ROI detection (reuse existing)
   ├── detect_objects_roi()     ← LLM zone detection via roi_detection_prompt
   ├── detect_objects()         ← OCR + visual verification + illumination check
   └── check_planogram_compliance() ← zone vs. expected, text reqs, illumination
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractPlanogramType` | extends | Implement all 4 abstract methods |
| `PlanogramCompliance` (plan.py) | used by | No changes needed — routing via `_PLANOGRAM_TYPES` |
| `PlanogramConfig` | reads | `shelves`, `roi_detection_prompt`, `object_identification_prompt` |
| `ComplianceResult` | produces | One per shelf/zone |
| `Detection` / `IdentifiedProduct` | produces | One per detected graphic panel |

### Data Models

No new Pydantic models required. Reuses existing:

```python
# Already defined in parrot.models
Detection          # bbox, label, confidence, ocr_text
IdentifiedProduct  # product_model, product_type, shelf_location, visual_features
ShelfRegion        # level, y_start, y_end, detections
ComplianceResult   # shelf_level, compliance_status, compliance_score, ...
```

Illumination check uses the existing `visual_features` list convention:
- `"illumination_status: ON"` → backlit expected
- `"illumination_status: OFF"` → not backlit expected

### New Public Interfaces

```python
# parrot/pipelines/planogram/types/graphic_panel_display.py

class GraphicPanelDisplay(AbstractPlanogramType):
    """Composable type for graphic-panel / signage endcap compliance.

    Handles displays where compliance is based on the presence, text
    content, and illumination state of named graphic zones — not on
    physical product counting or fact-tag detection.
    """

    async def compute_roi(self, img: Image.Image) -> ...: ...
    async def detect_objects_roi(self, img, roi) -> List[Detection]: ...
    async def detect_objects(self, img, roi, macro_objects) -> Tuple[List[IdentifiedProduct], List[ShelfRegion]]: ...
    def check_planogram_compliance(self, identified_products, planogram_description) -> List[ComplianceResult]: ...
```

---

## 3. Module Breakdown

### Module 1: GraphicPanelDisplay composable

- **Path**: `parrot/pipelines/planogram/types/graphic_panel_display.py`
- **Responsibility**: Full implementation of the `AbstractPlanogramType` interface for
  zone-based graphic compliance. Includes illumination detection logic.
- **Depends on**: `AbstractPlanogramType`, existing `Detection`/`IdentifiedProduct`/`ComplianceResult` models

### Module 2: Type registration

- **Path**: `parrot/pipelines/planogram/types/__init__.py`
- **Responsibility**: Export `GraphicPanelDisplay` and register it in `_PLANOGRAM_TYPES`
  dict in `plan.py` under the key `"graphic_panel_display"`.
- **Depends on**: Module 1

### Module 3: Unit tests

- **Path**: `tests/test_graphic_panel_display.py`
- **Responsibility**: Test zone detection, illumination check logic, text requirement
  verification, and compliance scoring without hitting live LLM.
- **Depends on**: Modules 1 & 2

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_zone_detection_all_present` | Module 1 | All expected zones detected → 100% compliance |
| `test_zone_detection_missing_zone` | Module 1 | Missing mandatory zone → non-compliant |
| `test_illumination_check_off_pass` | Module 1 | Zone with `illumination_status: OFF` detected as OFF → compliant |
| `test_illumination_check_off_fail` | Module 1 | Zone with `illumination_status: OFF` detected as ON → non-compliant, score=0 |
| `test_illumination_check_on_pass` | Module 1 | Backlit zone detected as ON → compliant |
| `test_text_requirement_pass` | Module 1 | Required text found in OCR → compliant |
| `test_text_requirement_fail` | Module 1 | Required text missing → compliance score reduced |
| `test_no_fact_tag_logic` | Module 1 | No fact-tag processing occurs |
| `test_type_registered` | Module 2 | `"graphic_panel_display"` key exists in `_PLANOGRAM_TYPES` |

### Integration Tests

| Test | Description |
|---|---|
| `test_ecotank_not_backlit_end_to_end` | Full pipeline run with mocked LLM returning EcoTank zones |

### Test Data / Fixtures

```python
@pytest.fixture
def ecotank_not_backlit_config():
    """PlanogramConfig for epson_depot_ecotank_not_backlit."""
    return PlanogramConfig(
        planogram_type="graphic_panel_display",
        shelves=[
            {"level": "header", "products": [{"name": "Epson_Top_Not_Backlit",
              "visual_features": ["illumination_status: OFF"], ...}]},
            {"level": "middle",  "products": [{"name": "Epson_Comparison_Table", ...}]},
            {"level": "bottom",  "products": [{"name": "Epson_Base_Special_Offer", ...}]},
        ],
        ...
    )
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `GraphicPanelDisplay` class exists and passes all unit tests
- [ ] `"graphic_panel_display"` is registered in `_PLANOGRAM_TYPES`
- [ ] Illumination check: zone detected as ON when config expects OFF → `compliance_score = 0`
- [ ] All text requirements evaluated per zone (same behaviour as `product_on_shelves`)
- [ ] No fact-tag or physical-product logic runs for this type
- [ ] Running `epson_depot_ecotank_not_backlit_planogram_config` through the pipeline
  produces 100% compliance on a known-good image
- [ ] All existing `product_on_shelves` tests continue to pass (no regression)
- [ ] `pytest tests/test_graphic_panel_display.py -v` → all green

---

## 6. Implementation Notes & Constraints

### Patterns to Follow

- Inherit `AbstractPlanogramType` — implement all 4 abstract methods.
- Follow async-first: `compute_roi` and `detect_objects_roi` must be `async`.
- Use `self.logger` (inherited from `AbstractPlanogramType.__init__`), not `print`.
- Pydantic models for any new structured data.
- Reuse existing LLM call helpers from `self.pipeline` (e.g. `_call_llm_vision`).

### Illumination Check Convention

The illumination check is driven entirely by `visual_features` in the planogram config:

```json
"visual_features": ["illumination_status: OFF"]
```

During `detect_objects`, the OCR/visual enrichment prompt must confirm the illumination
state. If the confirmed state contradicts the expected state, `compliance_score` for
that zone must be forced to `0` and `compliance_status` to `non_compliant`.

### Known Risks / Gotchas

- **LLM coordinate format**: Some LLMs return absolute pixel coordinates instead of
  normalized [0,1]. The existing `product_on_shelves` normalizer handles this — reuse
  the same recovery path (`Recovered Step 1 detections after normalizing pixel coordinates`).
- **Zone labels vs product labels**: The `roi_detection_prompt` uses zone labels
  (`top_zone`, `middle_zone`) while the planogram config uses product names
  (`Epson_Top_Not_Backlit`). The mapping must align labels from the LLM response to
  product names in the config. Document the convention clearly.
- **Backlit vs not-backlit configs are separate DB records** — do not try to infer
  illumination from the config name. Always rely on `visual_features`.

### External Dependencies

No new packages required. Uses existing parrot dependencies (PIL, pydantic, aiohttp).

---

## 7. Open Questions

- [ ] Should `GraphicPanelDisplay` reuse the same `roi_detection_prompt` field from
  `PlanogramConfig`, or does it need a dedicated prompt field? — *Owner: Jesus Lara*
- [ ] For Bose audio displays (ids 17–22): confirm whether they are graphic-panel type
  or a hybrid with physical products. — *Owner: juanfran*
- [ ] Should the illumination check be a hard fail (score=0) or a configurable penalty?
  Current proposal: hard fail. — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks in one worktree)
- All tasks run sequentially — Module 2 depends on Module 1, tests depend on both.
- **Cross-feature dependencies**: FEAT-003 must be merged to `main` first (provides
  `planogram_type` routing in flowtask and the `AbstractPlanogramType` contract).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-20 | juanfran | Initial draft |
