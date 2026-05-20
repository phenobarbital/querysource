# TASK-663: Component Registry

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-661
**Assigned-to**: unassigned

---

## Context

> Implements Module 4 from the spec. Creates the ComponentRegistry class that
> discovers all MultiQuery component classes, builds the catalog using
> introspection classmethods, and provides pipeline validation. This is the
> core module that both the CLI (TASK-664) and HTTP handler (TASK-665) depend on.

---

## Scope

- Create `querysource/queries/multi/registry.py` with `ComponentRegistry` class
- Implement `discover_all()` — scans filesystem for operator/transform modules + reads SOURCE_REGISTRY/DESTINATION_REGISTRY
- Implement `get_catalog()` — calls `get_schema()`/`get_description()` on each discovered class, returns `list[ComponentInfo]`
- Implement `validate_pipeline(payload)` — syntactic + structural validation of MultiQuery pipeline JSON
- Create data model classes: `AttributeInfo`, `ComponentInfo`, `ValidationError`, `ValidationResult` (as dataclasses)
- Write comprehensive unit tests

**NOT in scope**: CLI command (TASK-664), HTTP handler (TASK-665), route registration (TASK-666)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/registry.py` | CREATE | ComponentRegistry + data models |
| `tests/test_component_registry.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Discovery functions
from querysource.queries.multi import get_operator_module  # verified: multi/__init__.py:22
from querysource.queries.multi import get_transform_module  # verified: multi/__init__.py:38

# Registries
from querysource.queries.multi.sources import SOURCE_REGISTRY  # verified: sources/__init__.py:22
from querysource.outputs.destinations import DESTINATION_REGISTRY  # verified: destinations/__init__.py:55

# Base classes (for isinstance/issubclass checks)
from querysource.queries.multi.abstract import AbstractMulti  # created by TASK-660
from querysource.queries.multi.transformations.abstract import AbstractTransform  # verified: line 12
from querysource.queries.multi.operators.abstract import AbstractOperator  # verified: line 15
from querysource.queries.multi.sources.base import ThreadSource  # verified: sources/base.py:11
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: destinations/abstract.py:17
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py
def get_operator_module(clsname: str):  # line 22 — returns class object
def get_transform_module(clsname: str):  # line 38 — returns class object

# querysource/queries/multi/sources/__init__.py:22
SOURCE_REGISTRY: dict = {
    "SourceSharepoint": SourceSharepoint,
    "SourceSmartSheet": SourceSmartSheet,
    "SourceS3": SourceS3,
    "SourceTable": SourceTable,
}

# querysource/outputs/destinations/__init__.py:55
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {
    "tableOutput": TableOutputAdapter,
    "TableOutput": TableOutputAdapter,
    "ToSharepoint": ToSharepoint,
    "ToS3": ToS3,
    "Table": TableDestination,
    "DWH": DWHDestination,
}

# AbstractMulti classmethods (created by TASK-660)
@classmethod def get_schema(cls) -> dict: ...  # returns {"json_schema": {...}, "attributes": [...]}
@classmethod def get_description(cls) -> dict: ...  # returns {"name", "description", "usage", "category", "example"}
@classmethod def get_attributes(cls) -> list[dict]: ...
```

### Discovery: Known Component Names

**Operators** (filesystem scan of `querysource/queries/multi/operators/`):
- Dynamically imported: `Join`, `Concat`, `Melt`, `Merge`, `Info`
- Statically imported: `Filter` (in `filter/flt.py`), `GroupBy`
- Module filename must match class name exactly (case-sensitive)

**Transforms** (filesystem scan of `querysource/queries/multi/transformations/`):
- Dynamically imported: `tPandas`, `tOrder`, `Map`, `correlation`, `crosstab`, `pivot`, `Forecast`
- Statically imported: `GoogleMaps` (in `google/maps.py`)
- Module filename must match class name exactly

**Sources**: Read from `SOURCE_REGISTRY` dict keys
**Destinations**: Read from `DESTINATION_REGISTRY` dict keys

### Does NOT Exist
- ~~`querysource/queries/multi/registry.py`~~ — this is the file YOU create
- ~~`ComponentRegistry`~~ — doesn't exist yet
- ~~`OPERATOR_REGISTRY`~~ — no operator registry exists; operators are discovered by filename
- ~~`TRANSFORM_REGISTRY`~~ — no transform registry exists; transforms are discovered by filename

---

## Implementation Notes

### discover_all() Strategy

```python
@classmethod
def discover_all(cls) -> dict[str, type]:
    """Discover all component classes by scanning the filesystem and registries."""
    components = {}

    # 1. Scan operators directory
    operators_dir = Path(__file__).parent / "operators"
    for py_file in operators_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "abstract.py":
            continue
        clsname = py_file.stem
        try:
            comp_cls = get_operator_module(clsname)
            components[clsname] = comp_cls
        except (ImportError, AttributeError):
            pass
    # Also get Filter (in subdirectory)
    from querysource.queries.multi.operators import Filter, GroupBy
    components["Filter"] = Filter
    components["GroupBy"] = GroupBy

    # 2. Scan transforms directory (similar pattern)
    # 3. Read SOURCE_REGISTRY
    # 4. Read DESTINATION_REGISTRY
    return components
```

### validate_pipeline() — Syntactic + Structural Checks

Validation rules (from spec):
1. All operator/transform step names must map to known component classes
2. Required attributes for each step must be present
3. Attribute types must match declared types (best-effort)
4. **Structural**: sources (queries/files) must be defined
5. **Structural**: Join/Merge need 2+ data sources referenced
6. **Structural**: steps can only reference data keys produced by prior steps or sources

Return format: `ValidationResult(valid=bool, errors=[ValidationError(step, field, message)])`

### Key Constraints
- `discover_all()` must handle `ImportError` gracefully (some components may have optional deps)
- Sources and destinations don't inherit `AbstractMulti` — catalog them via their registry entries, not via `get_schema()`
- For sources/destinations in the catalog, build `ComponentInfo` manually from class `__doc__` and `__init__` inspection

---

## Acceptance Criteria

- [ ] `ComponentRegistry.discover_all()` finds all 7 operators, 8 transforms, sources, and destinations
- [ ] `ComponentRegistry.get_catalog()` returns `list[ComponentInfo]` with schemas for all components
- [ ] `ComponentRegistry.validate_pipeline()` validates correct pipeline → `ValidationResult(valid=True)`
- [ ] Validation catches unknown operator name → error with step name
- [ ] Validation catches missing required attribute → error with field name
- [ ] Validation catches Join without 2+ inputs → structural error
- [ ] Validation catches pipeline with no sources → structural error
- [ ] Tests pass: `pytest tests/test_component_registry.py -v`
- [ ] Import works: `from querysource.queries.multi.registry import ComponentRegistry`

---

## Test Specification

```python
# tests/test_component_registry.py
import pytest
from querysource.queries.multi.registry import ComponentRegistry, ValidationResult


class TestDiscoverAll:
    def test_finds_operators(self):
        components = ComponentRegistry.discover_all()
        for op in ["Join", "Concat", "Melt", "Merge", "GroupBy", "Info", "Filter"]:
            assert op in components, f"Missing operator: {op}"

    def test_finds_transforms(self):
        components = ComponentRegistry.discover_all()
        for t in ["Map", "correlation", "crosstab", "pivot", "Forecast"]:
            assert t in components, f"Missing transform: {t}"


class TestGetCatalog:
    def test_returns_component_info_list(self):
        catalog = ComponentRegistry.get_catalog()
        assert len(catalog) > 0
        for item in catalog:
            assert item.name
            assert item.category in ("Operators", "Transformations", "Sources", "Destinations", "Components")


class TestValidatePipeline:
    def test_valid_pipeline(self):
        payload = {
            "queries": {"revenue": {"slug": "revenue_report"}},
            "Join": {"type": "inner", "left": "revenue", "right": "costs"},
        }
        result = ComponentRegistry.validate_pipeline(payload)
        # This may or may not be valid depending on "costs" not being defined
        assert isinstance(result, ValidationResult)

    def test_unknown_operator(self):
        payload = {
            "queries": {"a": {"slug": "test"}},
            "FakeOperator": {"foo": "bar"},
        }
        result = ComponentRegistry.validate_pipeline(payload)
        assert not result.valid
        assert any("FakeOperator" in e.step for e in result.errors)

    def test_no_sources(self):
        payload = {"Join": {"type": "inner"}}
        result = ComponentRegistry.validate_pipeline(payload)
        assert not result.valid
        assert any("source" in e.message.lower() for e in result.errors)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-661 is in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — confirm registries and discovery functions still work
4. **Implement** `querysource/queries/multi/registry.py`
5. **Write tests** in `tests/test_component_registry.py`
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_component_registry.py -v`
7. **Move this file** to `sdd/tasks/completed/TASK-663-component-registry.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
