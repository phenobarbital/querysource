# TASK-004: Schema Generator

**Feature**: Component Documentation API
**Spec**: `sdd/specs/component-documentation-api.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-003
**Assigned-to**: claude-session-2026-03-03

---

## Context

This task generates JSON Schema from parsed component documentation. The schema enables IDE autocomplete and validation for YAML task definitions.

Reference: Spec Section 3 - Module 2: Schema Generator

---

## Scope

- Implement `SchemaGenerator` class to generate JSON Schema from `ComponentDoc`
- Map component attributes to JSON Schema properties
- Mark required fields in schema's `required` array
- Include descriptions from parsed attributes
- Output schema as JSON string with proper formatting

**NOT in scope**:
- Docstring parsing (TASK-003)
- File I/O or directory scanning (TASK-005)
- HTTP API (TASK-007)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/documentation/schema.py` | CREATE | SchemaGenerator implementation |
| `flowtask/documentation/models.py` | MODIFY | Add ComponentSchema model if needed |
| `flowtask/documentation/__init__.py` | MODIFY | Export SchemaGenerator |
| `tests/unit/documentation/test_schema.py` | CREATE | Unit tests for schema generator |

---

## Implementation Notes

### Pattern to Follow
```python
# flowtask/documentation/schema.py
import orjson
from typing import Dict, Any
from .models import ComponentDoc, ComponentSchema


class SchemaGenerator:
    """Generate JSON Schema from ComponentDoc."""

    def generate(self, doc: ComponentDoc) -> ComponentSchema:
        """Generate JSON Schema from component documentation."""
        properties = {}
        required = []

        for attr in doc.attributes:
            properties[attr.name] = {
                "type": "string",  # Default type, could be enhanced
                "description": attr.description
            }
            if attr.required:
                required.append(attr.name)

        return ComponentSchema(
            title=doc.name,
            description=doc.description,
            properties=properties,
            required=required
        )

    def to_json(self, schema: ComponentSchema) -> str:
        """Serialize schema to JSON string."""
        return orjson.dumps(
            schema.model_dump(),
            option=orjson.OPT_INDENT_2
        ).decode('utf-8')
```

### Key Constraints
- Use `orjson` for JSON serialization (already in codebase)
- Schema must be valid JSON Schema draft-07 compatible
- All attributes default to `type: "string"` (no type inference for now)
- Preserve attribute descriptions for IDE tooltips

### References in Codebase
- `flowtask/models.py` — Pydantic model patterns

---

## Acceptance Criteria

- [ ] `SchemaGenerator.generate()` returns valid `ComponentSchema`
- [ ] Required attributes appear in schema's `required` array
- [ ] Optional attributes have properties but not in `required`
- [ ] Schema serializes to valid JSON with `to_json()`
- [ ] All tests pass: `pytest tests/unit/documentation/test_schema.py -v`
- [ ] No linting errors: `ruff check flowtask/documentation/`

---

## Test Specification

```python
# tests/unit/documentation/test_schema.py
import pytest
import json
from flowtask.documentation.schema import SchemaGenerator
from flowtask.documentation.models import ComponentDoc, ComponentAttribute


@pytest.fixture
def generator():
    return SchemaGenerator()


@pytest.fixture
def sample_doc():
    return ComponentDoc(
        name="TestComponent",
        description="A test component for unit testing.",
        attributes=[
            ComponentAttribute(name="required_field", required=True, description="A required field"),
            ComponentAttribute(name="optional_field", required=False, description="An optional field"),
        ],
        examples=["TestComponent:\n  required_field: value"]
    )


class TestSchemaGenerator:
    def test_generate_returns_schema(self, generator, sample_doc):
        """Generator returns ComponentSchema."""
        schema = generator.generate(sample_doc)
        assert schema.title == "TestComponent"
        assert schema.description == "A test component for unit testing."

    def test_required_fields_in_required_array(self, generator, sample_doc):
        """Required attributes appear in schema.required."""
        schema = generator.generate(sample_doc)
        assert "required_field" in schema.required
        assert "optional_field" not in schema.required

    def test_all_fields_have_properties(self, generator, sample_doc):
        """All attributes have corresponding properties."""
        schema = generator.generate(sample_doc)
        assert "required_field" in schema.properties
        assert "optional_field" in schema.properties

    def test_properties_have_descriptions(self, generator, sample_doc):
        """Properties include descriptions."""
        schema = generator.generate(sample_doc)
        assert schema.properties["required_field"]["description"] == "A required field"

    def test_to_json_valid(self, generator, sample_doc):
        """Schema serializes to valid JSON."""
        schema = generator.generate(sample_doc)
        json_str = generator.to_json(schema)
        parsed = json.loads(json_str)
        assert parsed["title"] == "TestComponent"
        assert "properties" in parsed
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/component-documentation-api.spec.md` for full context
2. **Check dependencies** — verify TASK-003 is in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-004-schema-generator.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-session-2026-03-03
**Date**: 2026-03-03
**Notes**:
- Created `flowtask/documentation/schema.py` with SchemaGenerator class
- Generates JSON Schema draft-07 compatible schemas from ComponentDoc
- Added `$schema` field pointing to draft-07 specification
- Added `to_dict()` method for programmatic access to schema data
- Handles default values in properties when specified
- Omits `required` array when no required fields exist
- Updated `__init__.py` to export SchemaGenerator
- Created 15 comprehensive unit tests including integration tests
- Verified with real component (DownloadFromBase) - schema generates correctly

**Deviations from spec**:
- Added `to_dict()` method beyond the spec's `to_json()` for flexibility
- Added `$schema` field in JSON output for full JSON Schema compliance
- Conditionally omit `required` array when empty (cleaner output)
