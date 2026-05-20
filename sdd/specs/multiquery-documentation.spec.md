---
type: feature
base_branch: dev
---

# Feature Specification: MultiQuery Documentation System

**Feature ID**: FEAT-095
**Date**: 2026-05-20
**Author**: Jesus Lara
**Status**: draft
**Target version**: 5.10.0

---

## 1. Motivation & Business Requirements

### Problem Statement

Three abstract base classes (`AbstractTransform`, `AbstractOperator`, `AbstractComponent`) share 90%+ identical boilerplate code (async context manager, kwargs-based init, start/run/close lifecycle) but have no common parent. There are zero introspection methods, zero documentation-generation tools, and zero HTTP endpoints for component discovery or pipeline validation. Most concrete components (7 operators, 8 transforms) lack docstrings. Users building MultiQuery pipelines have no programmatic way to discover available components, their attributes, or validate pipeline definitions.

### Goals
- Normalize `AbstractTransform`, `AbstractOperator`, and `AbstractComponent` under a shared `AbstractMulti` base class with common methods
- Provide a `get_schema()` classmethod producing both JSON Schema (draft-2020-12) and a simplified attribute dict for documentation
- Add comprehensive docstrings to every concrete Operator and Transform
- Create a CLI command to generate per-component JSON documentation files
- Expose an HTTP GET endpoint listing all supported MultiQuery components
- Expose an HTTP POST endpoint for syntactic+structural validation of pipeline definitions

### Non-Goals (explicitly out of scope)
- Modifying `ThreadSource` or `AbstractDestination` to inherit from `AbstractMulti` — deferred per user request; they already have their own well-defined hierarchies (FEAT-093, FEAT-094). Integration is a follow-up.
- Semantic/data-type-flow validation (column existence checks, type propagation across pipeline steps)
- MkDocs or static HTML documentation site generation
- Pydantic model conversion of existing component classes

---

## 2. Architectural Design

### Overview

Create `AbstractMulti(ABC)` as a unified base class providing shared boilerplate and introspection classmethods. Refactor the three existing abstract classes to inherit from it. Build documentation tooling on top of the introspection layer: a CLI generator writing JSON files to `generated/`, an HTTP GET endpoint returning the component catalog, and an HTTP POST endpoint validating pipeline definitions with syntactic + structural checks.

Schema output uses **both** JSON Schema (draft-2020-12) for machine validation and a simplified attribute dict for display, as resolved in proposal U1.

Validation performs **syntactic + structural** checks (valid operator/transform names, required attributes present, correct types, pipeline structure constraints like "Join needs 2+ inputs") but no semantic analysis, as resolved in proposal U2.

### Component Diagram
```
AbstractMulti (ABC)  ← NEW
├── get_schema() → {json_schema: {...}, attributes: [...]}
├── get_description() → {name, description, usage, category, example}
├── get_attributes() → [{name, type, default, required}, ...]
├── __init__(data, **kwargs)
├── async __aenter__/__aexit__
├── async start()/run()/close()
└── _print_info(df)

AbstractTransform(AbstractMulti)    ← REFACTORED (removes duplicated boilerplate)
AbstractOperator(AbstractMulti)     ← REFACTORED
AbstractComponent(AbstractMulti)    ← REFACTORED

ComponentRegistry                   ← NEW
├── discover_all() → dict[str, type]
├── get_catalog() → list[ComponentInfo]
└── validate_pipeline(payload) → ValidationResult

CLI: generate-docs command          ← NEW
├── uses ComponentRegistry.discover_all()
├── calls get_schema()/get_description() per class
└── writes JSON to generated/

HTTP: /api/v3/components (GET)      ← NEW
└── uses ComponentRegistry.get_catalog()

HTTP: /api/v3/validate (POST)       ← NEW
└── uses ComponentRegistry.validate_pipeline()
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractTransform` | refactored | Inherits `AbstractMulti`, removes boilerplate |
| `AbstractOperator` | refactored | Inherits `AbstractMulti`, removes boilerplate |
| `AbstractComponent` | refactored | Inherits `AbstractMulti`, removes boilerplate |
| `get_operator_module()` | wrapped | `ComponentRegistry` uses this for operator discovery |
| `get_transform_module()` | wrapped | `ComponentRegistry` uses this for transform discovery |
| `SOURCE_REGISTRY` | read | `ComponentRegistry` reads for source listing |
| `DESTINATION_REGISTRY` | read | `ComponentRegistry` reads for destination listing |
| `QuerySource.setup()` | extended | New routes registered alongside existing v3 routes |
| `AbstractHandler` | extended | New handler inherits from it |

### Data Models
```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AttributeInfo:
    """Single attribute definition."""
    name: str
    type: str  # Python type as string, e.g. "str", "int", "list", "Any"
    default: Any = None
    required: bool = False
    description: str = ""

@dataclass
class ComponentInfo:
    """Documentation for a single MultiQuery component."""
    name: str
    category: str  # "Operators" | "Transformations" | "Sources" | "Destinations" | "Components"
    description: str
    usage: str
    attributes: list[AttributeInfo] = field(default_factory=list)
    json_schema: dict = field(default_factory=dict)
    example: dict = field(default_factory=dict)

@dataclass
class ValidationError:
    """Single validation error."""
    step: str
    field: str
    message: str

@dataclass
class ValidationResult:
    """Pipeline validation result."""
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
```

### New Public Interfaces
```python
# querysource/queries/multi/abstract.py
class AbstractMulti(ABC):
    """Unified base for all MultiQuery processing steps."""

    def __init__(self, data, **kwargs) -> None: ...
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc_value, traceback): ...
    async def start(self): ...

    @abstractmethod
    async def run(self): ...

    async def close(self): ...
    def _print_info(self, df: pd.DataFrame) -> None: ...

    @classmethod
    def get_schema(cls) -> dict:
        """Return both JSON Schema and simplified attribute list."""
        ...

    @classmethod
    def get_description(cls) -> dict:
        """Return {name, description, usage, category, example}."""
        ...

    @classmethod
    def get_attributes(cls) -> list[dict]:
        """Return [{name, type, default, required}, ...]."""
        ...

# querysource/queries/multi/registry.py
class ComponentRegistry:
    """Discovers and catalogs all MultiQuery components."""

    @classmethod
    def discover_all(cls) -> dict[str, type]: ...

    @classmethod
    def get_catalog(cls) -> list[ComponentInfo]: ...

    @classmethod
    def validate_pipeline(cls, payload: dict) -> ValidationResult: ...

# querysource/handlers/components.py
class ComponentHandler(AbstractHandler):
    """HTTP handler for component documentation and validation."""

    async def list_components(self, request: web.Request) -> web.Response: ...
    async def validate_pipeline(self, request: web.Request) -> web.Response: ...

# querysource/cli/generate_docs.py
def generate_docs(output_dir: str = "generated") -> None: ...
def main() -> None: ...  # argparse entry point
```

---

## 3. Module Breakdown

### Module 1: AbstractMulti Base Class
- **Path**: `querysource/queries/multi/abstract.py`
- **Responsibility**: Unified ABC for all MultiQuery processing steps. Extracts common boilerplate (init, async context manager, lifecycle methods) and adds introspection classmethods (`get_schema`, `get_description`, `get_attributes`).
- **Depends on**: none (new file)

### Module 2: Refactor Existing Abstract Classes
- **Paths**:
  - `querysource/queries/multi/transformations/abstract.py`
  - `querysource/queries/multi/operators/abstract.py`
  - `querysource/queries/multi/components/abstract.py`
- **Responsibility**: Make each inherit from `AbstractMulti`, remove duplicated boilerplate, keep subclass-specific logic (e.g., modin backend in `AbstractOperator`, data validation in `AbstractTransform`).
- **Depends on**: Module 1

### Module 3: Component Docstrings
- **Paths**: All files in `querysource/queries/multi/operators/` and `querysource/queries/multi/transformations/`
- **Responsibility**: Add comprehensive docstrings to all 7 operators (Join, Concat, Melt, Merge, GroupBy, Info, Filter) and 8 transforms (tPandas, tOrder, Map, correlation, crosstab, pivot, Forecast, GoogleMaps). Each docstring includes: description, usage, attribute list, JSON pipeline example.
- **Depends on**: Module 2 (docstrings reference the unified `category` attribute)

### Module 4: Component Registry
- **Path**: `querysource/queries/multi/registry.py`
- **Responsibility**: Discovers all component classes (operators, transforms, sources, destinations) using existing `get_operator_module`/`get_transform_module` functions and `SOURCE_REGISTRY`/`DESTINATION_REGISTRY` dicts. Provides `discover_all()`, `get_catalog()`, and `validate_pipeline()`.
- **Depends on**: Module 1 (uses `get_schema()`/`get_description()` classmethods)

### Module 5: CLI Documentation Generator
- **Path**: `querysource/cli/generate_docs.py`
- **Responsibility**: argparse-based CLI command. Discovers components via `ComponentRegistry`, calls introspection methods, writes per-component JSON files to `generated/` directory. Entry point registered in `pyproject.toml`.
- **Depends on**: Module 4

### Module 6: HTTP Component Handler
- **Path**: `querysource/handlers/components.py`
- **Responsibility**: Two endpoints: `GET /api/v3/components` returns JSON catalog of all components; `POST /api/v3/validate` validates a pipeline definition payload.
- **Depends on**: Module 4

### Module 7: Route Registration
- **Path**: `querysource/services.py` (modify existing)
- **Responsibility**: Register the new `/api/v3/components` and `/api/v3/validate` routes in `QuerySource.setup()`.
- **Depends on**: Module 6

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_abstract_multi_init` | Module 1 | `AbstractMulti.__init__` sets data and kwargs as attrs |
| `test_abstract_multi_context_manager` | Module 1 | `__aenter__`/`__aexit__` call `start()`/`close()` |
| `test_get_attributes_returns_typed_list` | Module 1 | `get_attributes()` extracts type hints correctly |
| `test_get_attributes_fallback_any` | Module 1 | Untyped attrs reported as `Any` |
| `test_get_schema_json_schema_format` | Module 1 | Output includes valid JSON Schema draft-2020-12 |
| `test_get_schema_simplified_format` | Module 1 | Output includes simplified attribute list |
| `test_get_description_from_docstring` | Module 1 | Extracts name, description, usage, category from docstring |
| `test_transform_inherits_abstract_multi` | Module 2 | `AbstractTransform` is a subclass of `AbstractMulti` |
| `test_operator_inherits_abstract_multi` | Module 2 | `AbstractOperator` is a subclass of `AbstractMulti` |
| `test_component_inherits_abstract_multi` | Module 2 | `AbstractComponent` is a subclass of `AbstractMulti` |
| `test_transform_backward_compat` | Module 2 | Existing Transform subclasses still instantiate correctly |
| `test_operator_backward_compat` | Module 2 | Existing Operator subclasses still instantiate correctly |
| `test_registry_discover_all` | Module 4 | Discovers all operators, transforms, sources, destinations |
| `test_registry_get_catalog` | Module 4 | Returns `ComponentInfo` list with schemas |
| `test_validate_pipeline_valid` | Module 4 | Valid pipeline returns `{valid: true}` |
| `test_validate_pipeline_unknown_operator` | Module 4 | Unknown operator name → error |
| `test_validate_pipeline_missing_required` | Module 4 | Missing required attribute → error |
| `test_validate_pipeline_join_needs_inputs` | Module 4 | Join without 2+ inputs → structural error |

### Integration Tests
| Test | Description |
|---|---|
| `test_cli_generate_docs` | Run CLI, verify `generated/` directory contains JSON files |
| `test_api_list_components` | `GET /api/v3/components` returns JSON with all components |
| `test_api_validate_valid_pipeline` | `POST /api/v3/validate` with valid payload returns 200 |
| `test_api_validate_invalid_pipeline` | `POST /api/v3/validate` with bad payload returns errors |
| `test_existing_multiquery_unaffected` | `POST /api/v3/queries` still works after refactor |

### Test Data / Fixtures
```python
@pytest.fixture
def sample_pipeline():
    return {
        "queries": {"revenue": {"slug": "revenue_report"}},
        "Join": {"type": "inner", "left": "revenue", "right": "costs"},
        "Transform": {"Map": {"fields": {"total": "revenue + costs"}}},
    }

@pytest.fixture
def invalid_pipeline():
    return {
        "queries": {"revenue": {"slug": "revenue_report"}},
        "UnknownOp": {"foo": "bar"},
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `AbstractMulti` base class exists at `querysource/queries/multi/abstract.py` with `get_schema()`, `get_description()`, `get_attributes()` classmethods
- [ ] `AbstractTransform`, `AbstractOperator`, `AbstractComponent` all inherit from `AbstractMulti`
- [ ] All existing operator/transform subclasses remain backward-compatible (existing pipeline execution unaffected)
- [ ] `get_schema()` returns both JSON Schema (draft-2020-12) and simplified attribute list
- [ ] `get_attributes()` uses `typing.get_type_hints()` with `Any` fallback for untyped attributes
- [ ] All 7 operators have comprehensive docstrings (description, usage, attributes, JSON example)
- [ ] All 8 transforms have comprehensive docstrings (description, usage, attributes, JSON example)
- [ ] `ComponentRegistry` discovers all operators, transforms, sources, and destinations
- [ ] CLI command `generate-docs` writes per-component JSON files to `generated/` with: name, description, usage, category, JSON Schema, attributes, example
- [ ] `GET /api/v3/components` returns JSON array of all registered components with schemas
- [ ] `POST /api/v3/validate` validates pipeline payloads with syntactic + structural checks
- [ ] `POST /api/v3/validate` checks: valid operator/transform names, required attributes present, Join/Merge need 2+ inputs, sources must be defined
- [ ] `POST /api/v3/validate` returns `{valid: bool, errors: [{step, field, message}]}`
- [ ] All unit tests pass (`pytest tests/ -v -k multiquery_doc`)
- [ ] No breaking changes to existing MultiQuery pipeline execution

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.

### Verified Imports
```python
# Abstract bases
from querysource.queries.multi.transformations.abstract import AbstractTransform  # verified: transformations/abstract.py:12
from querysource.queries.multi.operators.abstract import AbstractOperator  # verified: operators/abstract.py:15
from querysource.queries.multi.components.abstract import AbstractComponent  # verified: components/abstract.py:15

# Discovery functions
from querysource.queries.multi import get_operator_module  # verified: multi/__init__.py:21
from querysource.queries.multi import get_transform_module  # verified: multi/__init__.py:37
from querysource.queries.multi import MultiQS  # verified: multi/__init__.py:53

# Registries (already on dev from FEAT-093 and FEAT-094)
from querysource.queries.multi.sources import SOURCE_REGISTRY  # verified: sources/__init__.py:22
from querysource.outputs.destinations import DESTINATION_REGISTRY  # verified: destinations/__init__.py:55
from querysource.outputs.destinations import get_destination  # verified: destinations/__init__.py:95

# Handler base
from querysource.handlers.abstract import AbstractHandler  # verified: handlers/abstract.py:24 (extends BaseHandler)

# Service
from querysource.services import QuerySource  # verified: services.py:49 (Singleton metaclass)

# Exceptions
from querysource.exceptions import QueryException  # verified: used in handlers/multi.py:8
from querysource.exceptions import DriverError  # verified: used in handlers/multi.py:9

# ThreadSource (already on dev from FEAT-093)
from querysource.queries.multi.sources.base import ThreadSource  # verified: sources/base.py:11
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: destinations/abstract.py:17
```

### Existing Class Signatures
```python
# querysource/queries/multi/transformations/abstract.py
class AbstractTransform:  # line 12 — NOTE: does NOT inherit ABC
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 13
        self._backend = 'pandas'  # line 14
        self.data = data  # line 15
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')  # line 16
    def colum_info(self, df):  # line 20 — typo in name
    async def start(self):  # line 26
    async def __aenter__(self):  # line 45
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 49
    async def close(self):  # line 56
    @abstractmethod
    async def run(self):  # line 59

# querysource/queries/multi/operators/abstract.py
class AbstractOperator(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self._backend = kwargs.get('backend', 'pandas')  # line 21
        self._pd = mpd or pd  # lines 24/27 — modin/pandas branch
        self.data = data  # line 28
    async def __aenter__(self):  # line 32
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 36
    @abstractmethod
    async def start(self):  # line 43
    @abstractmethod
    async def run(self):  # line 48
    async def close(self):  # line 53
    def _print_info(self, df: pd.DataFrame):  # line 58

# querysource/queries/multi/components/abstract.py
class AbstractComponent(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self.data = data  # line 21
    async def __aenter__(self):  # line 25
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 29
    @abstractmethod
    async def start(self):  # line 36
    @abstractmethod
    async def run(self):  # line 41
    async def close(self):  # line 46
    def _print_info(self, df: pd.DataFrame):  # line 51

# querysource/queries/multi/__init__.py
def get_operator_module(clsname: str):  # line 21
def get_transform_module(clsname: str):  # line 37
class MultiQS(BaseQuery):  # line 53

# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):  # line 11
    def __init__(self, name, options, request, queue) -> None:  # line 22

# querysource/outputs/destinations/abstract.py
class AbstractDestination(ABC):  # line 17
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 26

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):  # line 24 — BaseHandler from navigator.views

# querysource/handlers/multi.py
class QueryHandler(AbstractHandler):  # line 23

# querysource/services.py
class QuerySource(metaclass=Singleton):  # line 49
    def setup(self, app: web.Application) -> web.Application:  # line 97
```

### Concrete Operators (all in `querysource/queries/multi/operators/`)
```python
class Join(AbstractOperator):  # Join.py:13 — attrs: _type, _left, _right, _join_conditions
class Concat(AbstractOperator):  # Concat.py:8 — no custom __init__, docstring: "Concat to Dataframes in one."
class Melt(AbstractOperator):  # Melt.py:9 — attrs: _id_vars, _na_cols
class Merge(AbstractOperator):  # Merge.py:9 — attrs: _on, _how, _left_on, _right_on, _suffixes
class GroupBy(AbstractOperator):  # GroupBy.py:32 — attrs: _columns, _by, _nan_by_with; class attr: supported_functions
class Info(AbstractOperator):  # Info.py:10 — no custom __init__, docstring present
class Filter(AbstractOperator):  # filter/flt.py:12 — attrs: conditions, fields, _filter, _operator
```

### Concrete Transforms (all in `querysource/queries/multi/transformations/`)
```python
class tPandas(AbstractTransform):  # tPandas.py:12 — attrs: type, condition, pd_args; has @abstractmethod _run()
class tOrder(tPandas):  # tOrder.py:9 — attrs: _column, _ascending, _na_position
class Map(AbstractTransform):  # Map.py:14 — attrs: replace_columns, reset_index
class correlation(AbstractTransform):  # correlation.py:41 — attrs: reset_index, numeric_only, method
class crosstab(AbstractTransform):  # crosstab.py:10 — attrs: reset_index, _type
class pivot(AbstractTransform):  # pivot.py:10 — attrs: reset_index, _type, _multilevel, _pd_args, _fill_value
class Forecast(AbstractTransform):  # Forecast.py:14 — attrs: reset_index, _order, _steps, _freq, model_args
class GoogleMaps(AbstractTransform):  # google/maps.py:11 — attrs: zoom, map_scale, timestamp_key, departure_time
```

### Operator Discovery Mechanism
- `operators/__init__.py` statically exports only `Filter` and `GroupBy`
- `transformations/__init__.py` statically exports only `GoogleMaps`
- All others loaded dynamically: `get_operator_module(clsname)` → `importlib.import_module(f'.operators.{clsname}', package=__package__)` → `getattr(module, clsname)`
- Module filename must exactly match class name (case-sensitive)
- Known operator names: `Join`, `Concat`, `Melt`, `Merge`, `Info` (dynamically loaded); `Filter`, `GroupBy` (statically imported)
- Known transform names: `tPandas`, `tOrder`, `Map`, `correlation`, `crosstab`, `pivot`, `Forecast` (dynamically loaded); `GoogleMaps` (statically imported)

### Registry Pattern (from FEAT-093/FEAT-094)
```python
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
```

### Route Registration Pattern (from `services.py:180-207`)
```python
# Instantiate handler, then register routes on app.router
mq = QueryHandler()
r = self.app.router.add_post(r'/api/v3/queries/{slug}{meta:\:?.*}', mq.query)
routes.append(r)
```

### CLI Entry Point (from `pyproject.toml`)
```toml
[project.scripts]
query = "querysource.__cli__:main"
```

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource/queries/multi/abstract.py`~~ — does not exist yet (target for Module 1)
- ~~`querysource/queries/multi/registry.py`~~ — does not exist yet (target for Module 4)
- ~~`querysource/handlers/components.py`~~ — does not exist yet (target for Module 6)
- ~~`querysource/cli/`~~ — directory does not exist yet (target for Module 5)
- ~~`generated/`~~ — directory does not exist yet (CLI output target)
- ~~`AbstractMulti`~~ — no unified base class exists anywhere
- ~~`get_schema()` / `get_description()` / `get_attributes()`~~ — no introspection classmethods exist on any component class
- ~~`ComponentRegistry`~~ — no component registry/catalog class exists
- ~~`/api/v3/components`~~ — no such route
- ~~`/api/v3/validate`~~ — no such route
- ~~`AbstractTransform(ABC)`~~ — AbstractTransform does NOT inherit from ABC (plain class)
- ~~`AbstractOperator.logger`~~ — AbstractOperator does NOT have a `logger` attribute (only AbstractTransform does)

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Use `AbstractHandler` from `querysource.handlers.abstract` as HTTP handler base (same pattern as `QueryHandler`)
- Use `dataclasses` for data models (consistent with the lightweight approach in this codebase — no Pydantic for internal models)
- Follow the route registration pattern in `services.py:180-207`: instantiate handler, call `self.app.router.add_*()`, append to `routes` list
- Use `orjson` for JSON serialization (already used throughout codebase)
- Use `argparse` for CLI (stdlib, no new dependency)
- For `get_attributes()`: inspect `__init__` method body for `kwargs.pop()`/`kwargs.get()` patterns AND use `typing.get_type_hints()` on the class for class-level annotations. Many attrs are set via `setattr(self, k, v)` from kwargs, so type hints may be incomplete — fall back to `Any`.

### Key Design Decisions
- **`get_schema()` dual output**: Returns `{"json_schema": {...}, "attributes": [...]}`  where `json_schema` follows draft-2020-12 and `attributes` is the simplified `[{name, type, default, required}]` list.
- **Category classification**: Each subclass hierarchy maps to a category: `AbstractOperator` subclasses → "Operators", `AbstractTransform` subclasses → "Transformations", `AbstractComponent` subclasses → "Components". Sources and destinations are cataloged via their registries but don't inherit `AbstractMulti` in this feature.
- **Attribute extraction strategy**: Since most attributes are set in `__init__` via `kwargs.pop(key, default)` rather than class-level annotations, `get_attributes()` should: (1) check `typing.get_type_hints(cls)` for class-level hints, (2) inspect `__init__` source to discover `kwargs.pop`/`kwargs.get` patterns with defaults, (3) merge both sources.
- **Validation structural checks**: Join/Merge require 2+ data sources. Transform requires at least 1 data source. Output requires at least 1 data source. Steps reference data keys that must be produced by prior steps.

### Known Risks / Gotchas
- **Attribute extraction from kwargs patterns**: Not all attributes are type-hinted. The `__init__` body parsing will be heuristic (regex on `kwargs.pop`/`kwargs.get`). May miss complex patterns.
- **Modin backend logic**: `AbstractOperator` has modin/pandas branching in `__init__` that must be preserved when extracting to `AbstractMulti`. Either keep it in `AbstractOperator.__init__` or add a hook method.
- **Debug helper inconsistency**: `AbstractTransform` uses `colum_info()` (typo), others use `_print_info()`. Renaming `colum_info` to `_print_info` is a breaking change if any external code calls it — verify no external callers.
- **Merge conflicts with FEAT-093/FEAT-094**: Both are already on dev. The refactoring in Module 2 modifies the abstract classes. `ThreadSource` and `AbstractDestination` are NOT modified, but `sources/__init__.py` imports should not be disturbed.
- **Dynamic import assumption**: `ComponentRegistry` must know the full list of operator/transform class names to call `get_operator_module()`/`get_transform_module()`. Either hardcode the known names or scan the filesystem.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `orjson` | (existing) | JSON serialization for HTTP responses and CLI output |
| No new dependencies required | — | argparse is stdlib; all other deps already present |

---

## 8. Open Questions

- [x] Schema format → **Resolved in proposal**: Both JSON Schema (draft-2020-12) AND simplified attribute dict.
- [x] Validation depth → **Resolved in proposal**: Syntactic + structural (no semantic/data-type flow analysis).
- [ ] Should `ComponentRegistry` hardcode the known operator/transform names or scan the filesystem (`operators/*.py`, `transformations/*.py`) to discover them dynamically? — *Owner: developer*
- [ ] Should the debug helper rename (`colum_info` → `_print_info`) be done in this feature or deferred to avoid breaking changes? — *Owner: developer*

---

## Worktree Strategy

- **Isolation unit**: per-spec (single worktree, sequential tasks)
- **Rationale**: Modules 1-7 have linear dependencies. Module 2 depends on Module 1, Module 3 depends on Module 2, etc.
- **Cross-feature dependencies**: FEAT-093 and FEAT-094 are already merged to dev. No blockers.
- **Merge conflicts**: Module 2 modifies abstract base files and Module 7 modifies `services.py` — keep changes minimal and well-scoped to reduce conflict risk.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-20 | Jesus Lara | Initial draft from research-grounded proposal FEAT-095 |
