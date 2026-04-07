# TASK-007: HTTP Handler for Component Documentation

**Feature**: Component Documentation API
**Spec**: `sdd/specs/component-documentation-api.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: claude-session-2026-03-03

---

## Context

This task implements the HTTP handler that serves component documentation via REST API. It reads pre-generated documentation files and returns them in the format specified in the spec: `{schema, doc, example}`.

Reference: Spec Section 3 - Module 5: HTTP Handler

---

## Scope

- Implement `FlowtaskComponentHandler` inheriting from `BaseView`
- Handle `GET /api/v1/flowtask/components` — list all components
- Handle `GET /api/v1/flowtask/components/{name}` — get component documentation
- Return 404 for unknown components
- Read from `documentation/` directory (static files)
- Support optional category/tag filtering (if index includes categories)

**NOT in scope**:
- Documentation generation (TASK-003-006)
- Route registration (TASK-008)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/handlers/__init__.py` | CREATE | Handler package init |
| `flowtask/handlers/component.py` | CREATE | FlowtaskComponentHandler implementation |
| `tests/unit/handlers/__init__.py` | CREATE | Test package init |
| `tests/unit/handlers/test_component.py` | CREATE | Unit tests for handler |

---

## Implementation Notes

### Pattern to Follow
```python
# flowtask/handlers/component.py
"""HTTP handler for Flowtask component documentation API."""
import logging
from pathlib import Path
from typing import Optional
import orjson
from aiohttp import web
from navigator.views import BaseView
from navigator.responses import JSONResponse
from navconfig import BASE_DIR


class FlowtaskComponentHandler(BaseView):
    """Handler for component documentation API.

    Endpoints:
        GET /api/v1/flowtask/components - List all components
        GET /api/v1/flowtask/components/{name} - Get component documentation
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)
        self.docs_dir = BASE_DIR / "documentation"

    def _load_index(self) -> dict:
        """Load the component index."""
        index_path = self.docs_dir / "index.json"
        if not index_path.exists():
            return {"components": {}}
        return orjson.loads(index_path.read_bytes())

    def _load_component_doc(self, name: str) -> Optional[dict]:
        """Load documentation for a specific component."""
        index = self._load_index()
        if name not in index.get("components", {}):
            return None

        component_info = index["components"][name]
        schema_path = self.docs_dir / component_info["schema"]
        doc_path = self.docs_dir / component_info["doc"]

        if not schema_path.exists() or not doc_path.exists():
            return None

        schema_data = orjson.loads(schema_path.read_bytes())
        doc_data = orjson.loads(doc_path.read_bytes())

        # Format response as specified
        return {
            "schema": orjson.dumps(schema_data).decode("utf-8"),
            "doc": doc_data.get("description", ""),
            "example": "\n".join(doc_data.get("examples", []))
        }

    async def get(self):
        """Handle GET requests.

        GET /api/v1/flowtask/components - List components
        GET /api/v1/flowtask/components/{name} - Get component doc
        """
        params = self.match_parameters()
        component_name = params.get("component_name")

        if component_name:
            # Get specific component
            doc = self._load_component_doc(component_name)
            if doc is None:
                return self.error(
                    response={"error": f"Component '{component_name}' not found"},
                    status=404
                )
            return self.json_response(doc)
        else:
            # List all components
            index = self._load_index()
            components = list(index.get("components", {}).keys())
            return self.json_response({
                "components": components,
                "count": len(components)
            })
```

### Key Constraints
- Inherit from `navigator.views.BaseView` (same pattern as `TaskService`, `PluginHandler`)
- Use `navconfig.BASE_DIR` to locate documentation directory
- Use `orjson` for JSON parsing (already in codebase)
- Return proper HTTP status codes (200, 404)
- Handler is stateless — reads files on each request

### References in Codebase
- `flowtask/services/tasks/service.py` — `TaskService` pattern
- `flowtask/plugins/handler/__init__.py` — `PluginHandler` pattern

---

## Acceptance Criteria

- [x] `FlowtaskComponentHandler` inherits from `BaseView`
- [x] `GET /api/v1/flowtask/components` returns list of component names
- [x] `GET /api/v1/flowtask/components/{name}` returns `{schema, doc, example}`
- [x] Unknown component returns 404 with error message
- [x] Missing documentation directory handled gracefully (empty list)
- [x] All tests pass: `pytest tests/unit/handlers/test_component.py -v`
- [x] No linting errors: `ruff check flowtask/handlers/`

---

## Test Specification

```python
# tests/unit/handlers/test_component.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import orjson


class TestFlowtaskComponentHandler:
    @pytest.fixture
    def mock_docs_dir(self, tmp_path):
        """Create mock documentation directory."""
        docs = tmp_path / "documentation"
        docs.mkdir()
        components = docs / "components"
        components.mkdir()

        # Create index
        index = {
            "updated_at": "2026-03-03T12:00:00",
            "components": {
                "TestComponent": {
                    "schema": "components/TestComponent.schema.json",
                    "doc": "components/TestComponent.doc.json"
                }
            }
        }
        (docs / "index.json").write_bytes(orjson.dumps(index))

        # Create component files
        schema = {"title": "TestComponent", "properties": {}}
        doc = {"description": "Test description", "examples": ["TestComponent:\n  key: value"]}
        (components / "TestComponent.schema.json").write_bytes(orjson.dumps(schema))
        (components / "TestComponent.doc.json").write_bytes(orjson.dumps(doc))

        return docs

    def test_list_components_returns_names(self, mock_docs_dir):
        """GET /components returns list of component names."""
        # Test implementation depends on how handler is instantiated
        pass

    def test_get_component_returns_doc(self, mock_docs_dir):
        """GET /components/{name} returns schema, doc, example."""
        pass

    def test_get_unknown_component_404(self, mock_docs_dir):
        """GET /components/{unknown} returns 404."""
        pass

    def test_missing_docs_dir_empty_list(self, tmp_path):
        """Missing docs directory returns empty list."""
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/component-documentation-api.spec.md` for full context
2. **Check dependencies** — this task has no dependencies (reads static files)
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-007-http-handler.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-session-2026-03-03
**Date**: 2026-03-03
**Notes**:
- Created `flowtask/handlers/` package with `FlowtaskComponentHandler` class
- Handler inherits from `navigator.views.BaseView` following existing patterns
- Implemented `_load_index()`, `_load_component_doc()`, and `_filter_components()` methods
- Added support for optional category/tag filtering via query parameters
- Created comprehensive unit tests (14 tests, all passing)
- All linting checks pass

**Deviations from spec**:
- Added `_filter_components()` method for category/tag filtering (optional enhancement from spec)
- Error handling improved with try/except blocks for JSON parsing and file operations
