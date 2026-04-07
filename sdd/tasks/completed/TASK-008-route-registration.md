# TASK-008: Route Registration for Component API

**Feature**: Component Documentation API
**Spec**: `sdd/specs/component-documentation-api.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-007
**Assigned-to**: claude-session-2026-03-03

---

## Context

This task registers the `FlowtaskComponentHandler` routes with the Navigator application. The routes need to be added to the appropriate service/app configuration so they're available at runtime.

Reference: Spec Section 3 - Module 6: Route Registration

---

## Scope

- Register route `GET /api/v1/flowtask/components` to `FlowtaskComponentHandler`
- Register route `GET /api/v1/flowtask/components/{component_name}` to `FlowtaskComponentHandler`
- Add routes to appropriate Navigator app configuration
- Ensure handler is properly imported and instantiated

**NOT in scope**:
- Handler implementation (TASK-007)
- Documentation generation (TASK-003-006)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/services/web.py` or similar | MODIFY | Add route registration |
| `flowtask/handlers/__init__.py` | MODIFY | Export handler if not done |
| `tests/integration/test_component_api.py` | CREATE | Integration tests for API |

---

## Implementation Notes

### Pattern to Follow
```python
# In the appropriate app/service configuration file
from flowtask.handlers.component import FlowtaskComponentHandler

# Route registration pattern (depends on Navigator's API)
app.router.add_route(
    "*",
    "/api/v1/flowtask/components",
    FlowtaskComponentHandler
)
app.router.add_route(
    "*",
    "/api/v1/flowtask/components/{component_name}",
    FlowtaskComponentHandler
)
```

### Finding the Right Location
1. Search for existing route registrations in the codebase
2. Look for where `TaskService` or `PluginHandler` routes are registered
3. Follow the same pattern for `FlowtaskComponentHandler`

### Key Constraints
- Use Navigator's route registration pattern
- Both routes should use the same handler class
- The handler's `get()` method distinguishes between list and detail views
- Routes should be registered during app startup

### References in Codebase
- Search for `add_route` or `add_view` in existing code
- Check how `flowtask/plugins/handler/__init__.py` routes are registered

---

## Acceptance Criteria

- [x] Routes registered in appropriate service/app configuration
- [x] `GET /api/v1/flowtask/components` accessible and returns component list
- [x] `GET /api/v1/flowtask/components/{name}` accessible and returns component doc
- [x] Routes work with Navigator's authentication (if applicable)
- [x] Integration tests pass: `pytest tests/integration/test_component_api.py -v`
- [x] No import errors on startup

---

## Test Specification

```python
# tests/integration/test_component_api.py
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from pathlib import Path
import orjson


class TestComponentAPI:
    """Integration tests for component documentation API."""

    @pytest.fixture
    def setup_docs(self, tmp_path):
        """Setup test documentation files."""
        docs = tmp_path / "documentation"
        docs.mkdir()
        components = docs / "components"
        components.mkdir()

        index = {
            "updated_at": "2026-03-03T12:00:00",
            "components": {
                "AddDataset": {
                    "schema": "components/AddDataset.schema.json",
                    "doc": "components/AddDataset.doc.json"
                }
            }
        }
        (docs / "index.json").write_bytes(orjson.dumps(index))

        schema = {"title": "AddDataset", "properties": {"dataset": {"type": "string"}}}
        doc = {"description": "Joins DataFrames", "examples": ["AddDataset:\n  dataset: my_data"]}
        (components / "AddDataset.schema.json").write_bytes(orjson.dumps(schema))
        (components / "AddDataset.doc.json").write_bytes(orjson.dumps(doc))

        return docs

    @pytest.mark.asyncio
    async def test_list_components_endpoint(self, aiohttp_client, setup_docs):
        """GET /api/v1/flowtask/components returns list."""
        # Test depends on how the app is created for testing
        pass

    @pytest.mark.asyncio
    async def test_get_component_endpoint(self, aiohttp_client, setup_docs):
        """GET /api/v1/flowtask/components/AddDataset returns doc."""
        pass

    @pytest.mark.asyncio
    async def test_component_not_found(self, aiohttp_client, setup_docs):
        """GET /api/v1/flowtask/components/Unknown returns 404."""
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/component-documentation-api.spec.md` for full context
2. **Check dependencies** — verify TASK-007 is in `sdd/tasks/completed/`
3. **Research** — find where existing routes are registered (search for `add_route`, `TaskService`)
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-008-route-registration.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-session-2026-03-03
**Date**: 2026-03-03
**Notes**:
- Created `flowtask/extensions/component_docs.py` with `ComponentDocumentation` extension
- Extension inherits from `BaseExtension` and registers routes in its `setup()` method
- Registers both `/api/v1/flowtask/components` and `/api/v1/flowtask/components/{component_name}` routes
- Updated `flowtask/extensions/__init__.py` to export `ComponentDocumentation`
- Created comprehensive integration tests (9 tests, all passing)
- Tests use `aiohttp.test_utils.TestClient` for testing routes

**Deviations from spec**:
- Created a dedicated extension class (`ComponentDocumentation`) instead of modifying `flowtask/services/web.py`
- This follows the existing pattern used by `LoggingFacility` extension
- Users can enable the extension by calling `ComponentDocumentation().setup(app)` during app setup
