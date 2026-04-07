# Feature Specification: Component Documentation API

**Feature ID**: FEAT-002
**Date**: 2026-03-03
**Author**: Claude
**Status**: approved
**Target version**: 5.9.0

---

## 1. Motivation & Business Requirements

### Problem Statement
Flowtask components in `flowtask/components` contain valuable documentation in their class docstrings (attributes, examples, usage). However, this documentation is:
1. Not easily accessible to users building task YAML files
2. Not available programmatically for IDE integrations or AI assistants
3. Scattered across many Python files with no centralized index

Users need an API to query component schemas and documentation to build valid YAML task definitions.

### Goals
- Extract component docstrings and generate structured JSON schemas
- Create a centralized documentation index in `flowtask/documentation/`
- Provide an HTTP API to query component documentation
- Support both `flowtask/components` and `plugins/components` directories

### Non-Goals (explicitly out of scope)
- Automatic validation of YAML task files against schemas
- Real-time documentation updates (regeneration is manual via CLI)
- Documentation for non-component classes

---

## 2. Architectural Design

### Overview
The solution has two parts:
1. **CLI Command** (`flowtask docs generate`): Scans component files, extracts docstrings, generates JSON schemas and markdown docs, stores them in `flowtask/documentation/`
2. **HTTP Handler** (`FlowtaskComponentHandler`): Serves component documentation via REST API

### Component Diagram
```
CLI Command
    │
    ├── Scan flowtask/components/*.py
    ├── Scan plugins/components/*.py
    │
    ├── For each component class:
    │   ├── Extract docstring
    │   ├── Parse attributes table
    │   ├── Parse examples
    │   └── Generate JSON schema
    │
    └── Write to flowtask/documentation/
        ├── components/
        │   ├── AddDataset.schema.json
        │   ├── AddDataset.doc.json
        │   └── ...
        └── index.json

HTTP Handler
    │
    └── GET /api/v1/flowtask/components[/{name}]
        ├── Read from flowtask/documentation/
        └── Return JSON response
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `navigator.views.BaseView` | extends | Handler inherits from BaseView (same as PluginHandler) |
| `FlowComponent` | inspects | Base class for all components to scan |
| `flowtask/plugins/handler/` | pattern | Follow existing handler patterns |

### Data Models
```python
from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from datetime import datetime

class ComponentAttribute(BaseModel):
    """Single attribute definition."""
    name: str
    required: bool
    description: str
    default: Optional[str] = None

class ComponentDoc(BaseModel):
    """Documentation for a single component."""
    name: str
    version: Optional[str] = None
    description: str
    attributes: List[ComponentAttribute]
    examples: List[str] = Field(default_factory=list)

class ComponentSchema(BaseModel):
    """JSON Schema representation of a component."""
    type: str = "object"
    title: str
    description: str
    properties: Dict[str, dict]
    required: List[str] = Field(default_factory=list)

class ComponentDocResponse(BaseModel):
    """API response for component documentation."""
    schema_: str = Field(alias="schema")  # JSON schema as string
    doc: str  # Markdown documentation
    example: str  # YAML/JSON example with newlines preserved

class DocumentationIndex(BaseModel):
    """Index of all documented components."""
    updated_at: datetime
    components: Dict[str, dict]  # name -> {"schema": path, "doc": path}
```

### New Public Interfaces
```python
# flowtask/documentation/generator.py
class ComponentDocGenerator:
    """Generates documentation from component classes."""

    def scan_components(self, paths: List[Path]) -> List[type]:
        """Scan directories for component classes."""
        ...

    def extract_docstring(self, component: type) -> ComponentDoc:
        """Parse docstring into structured format."""
        ...

    def generate_schema(self, doc: ComponentDoc) -> ComponentSchema:
        """Generate JSON schema from component doc."""
        ...

    def write_documentation(self, output_dir: Path) -> DocumentationIndex:
        """Generate all docs and write to output directory."""
        ...

# flowtask/handlers/component.py
class FlowtaskComponentHandler(BaseView):
    """HTTP handler for component documentation API."""

    async def get(self) -> Response:
        """
        GET /api/v1/flowtask/components - List all components
        GET /api/v1/flowtask/components/{name} - Get component docs
        """
        ...
```

---

## 3. Module Breakdown

### Module 1: Docstring Parser
- **Path**: `flowtask/documentation/parser.py`
- **Responsibility**: Parse component docstrings to extract attributes table, examples, and description. Handle the table format (`:widths: auto`, `|---|---|---|`).
- **Depends on**: none

### Module 2: Schema Generator
- **Path**: `flowtask/documentation/schema.py`
- **Responsibility**: Generate JSON Schema from parsed ComponentDoc. Map attribute types to JSON Schema types.
- **Depends on**: Module 1

### Module 3: Documentation Generator
- **Path**: `flowtask/documentation/generator.py`
- **Responsibility**: Orchestrate scanning, parsing, and writing. Generate index.json with component references.
- **Depends on**: Module 1, Module 2

### Module 4: CLI Command
- **Path**: `flowtask/cli/docs.py`
- **Responsibility**: Add `flowtask docs generate` command. Accept output directory option.
- **Depends on**: Module 3

### Module 5: HTTP Handler
- **Path**: `flowtask/handlers/component.py`
- **Responsibility**: Serve component documentation via REST API. Read from `flowtask/documentation/` directory.
- **Depends on**: none (reads static files)

### Module 6: Route Registration
- **Path**: `flowtask/services/web.py` (or appropriate service file)
- **Responsibility**: Register `/api/v1/flowtask/components` route.
- **Depends on**: Module 5

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_parse_attribute_table` | Module 1 | Parse `:widths: auto` table format correctly |
| `test_parse_example_yaml` | Module 1 | Extract YAML example blocks from docstring |
| `test_parse_empty_docstring` | Module 1 | Handle components without documentation |
| `test_generate_schema_required_fields` | Module 2 | Required fields appear in schema.required |
| `test_generate_schema_optional_fields` | Module 2 | Optional fields have defaults in schema |
| `test_scan_components_finds_all` | Module 3 | Scans both flowtask/components and plugins/components |
| `test_index_json_created` | Module 3 | Index file references all component docs |

### Integration Tests
| Test | Description |
|---|---|
| `test_cli_generate_docs` | Run CLI command, verify files created |
| `test_api_list_components` | GET /api/v1/flowtask/components returns list |
| `test_api_get_component` | GET /api/v1/flowtask/components/AddDataset returns doc |
| `test_api_component_not_found` | GET /api/v1/flowtask/components/NotReal returns 404 |

### Test Data / Fixtures
```python
@pytest.fixture
def sample_docstring():
    return '''
    AddDataset Component

    Overview
    This component joins two pandas DataFrames.

    :widths: auto

    | fields    | Yes | List of field names |
    | dataset   | Yes | Name of dataset     |
    | type      | No  | Join type (default: left) |

    Example:

    ```yaml
    AddDataset:
      dataset: my_data
      fields:
        - col1
        - col2
    ```
    '''

@pytest.fixture
def sample_component_class():
    class MockComponent(FlowComponent):
        """Mock docstring here"""
        _version = "1.0.0"
    return MockComponent
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] CLI command `flowtask docs generate` scans components and generates docs
- [ ] JSON schemas generated for all documented components in `flowtask/documentation/components/`
- [ ] Index file `flowtask/documentation/index.json` lists all components with paths
- [ ] GET `/api/v1/flowtask/components` returns list of component names
- [ ] GET `/api/v1/flowtask/components/{name}` returns `{schema, doc, example}` JSON
- [ ] GET `/api/v1/flowtask/components/{invalid}` returns 404 with error message
- [ ] All unit tests pass (`pytest tests/unit/documentation/ -v`)
- [ ] All integration tests pass (`pytest tests/integration/documentation/ -v`)
- [ ] Docstring table format with `:widths: auto` is parsed correctly
- [ ] YAML examples in docstrings are extracted with preserved formatting

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Use `BaseView` from `navigator.views` for HTTP handler (same as `PluginHandler`)
- Use Pydantic models for all data structures
- Follow async-first design for HTTP handlers
- Use `orjson` for JSON serialization (already used in codebase)

### Docstring Format Recognition
Components to document must have docstrings with the table format:
```
:widths: auto

| attribute_name | Required (Yes/No) | Description |
```

The parser should detect this pattern to identify documentable components.

### Known Risks / Gotchas
- **Inconsistent docstring formats**: Not all components follow the same format. Parser must be lenient.
- **Plugin components**: May not be present in all installations. Scanner should gracefully handle missing directories.
- **Large number of components**: ~100+ components. Index generation should be efficient.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pydantic` | `>=2.0` | Data models (already in codebase) |
| `orjson` | `>=3.0` | Fast JSON serialization (already in codebase) |

---

## 7. Open Questions

> Questions that must be resolved before or during implementation.

- [ ] Should the CLI regenerate all docs or only changed components? — *Owner: TBD*: only changed components.
- [ ] Should the documentation directory be inside `flowtask/` package or at project root? — *Owner: TBD*: at project root, using "from navconfig import BASE_DIR" at Handler, allow us to reach the files.
- [ ] Should the API support filtering by category/tag? — *Owner: TBD*: Preferable.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-03 | Claude | Initial draft from proposal |
