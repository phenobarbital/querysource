# TASK-665: HTTP Component Handler

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-663
**Assigned-to**: unassigned

---

## Context

> Implements Module 6 from the spec. Creates the HTTP handler with two endpoints:
> GET /api/v3/components returns a JSON catalog of all registered components,
> and POST /api/v3/validate validates a MultiQuery pipeline definition payload.

---

## Scope

- Create `querysource/handlers/components.py` with `ComponentHandler(AbstractHandler)`
- Implement `list_components(request)` method:
  - Calls `ComponentRegistry.get_catalog()`
  - Supports optional `?category=Operators` query param for filtering
  - Returns JSON array of component info objects
- Implement `validate_pipeline(request)` method:
  - Reads JSON body from request
  - Calls `ComponentRegistry.validate_pipeline(payload)`
  - Returns `{"valid": bool, "errors": [{"step": str, "field": str, "message": str}]}`
  - Returns HTTP 400 if body is not valid JSON
- Write unit tests

**NOT in scope**: Route registration (TASK-666), registry logic (TASK-663), CLI (TASK-664)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/components.py` | CREATE | ComponentHandler with list + validate endpoints |
| `tests/test_component_handler.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Handler base
from querysource.handlers.abstract import AbstractHandler  # verified: handlers/abstract.py:25

# Registry (created by TASK-663)
from querysource.queries.multi.registry import ComponentRegistry

# Web framework
from aiohttp import web  # verified: existing dep

# Serialization
import orjson  # verified: existing dep
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py:25
class AbstractHandler(BaseHandler):  # BaseHandler from navigator.views
    def post_init(self, *args, **kwargs):
        self.logger = logging.getLogger('QS.Handler')

# querysource/handlers/multi.py:23 — REFERENCE pattern for handler implementation
class QueryHandler(AbstractHandler):
    async def query(self, request: web.Request) -> web.Response: ...
    async def columns(self, request: web.Request) -> web.Response: ...

# ComponentRegistry (created by TASK-663)
@classmethod def get_catalog(cls) -> list[ComponentInfo]: ...
@classmethod def validate_pipeline(cls, payload: dict) -> ValidationResult: ...

# Route registration pattern from services.py:180-207
mq = QueryHandler()
r = self.app.router.add_post(r'/api/v3/queries/{slug}{meta:\:?.*}', mq.query)
routes.append(r)
```

### Does NOT Exist
- ~~`querysource/handlers/components.py`~~ — this is the file YOU create
- ~~`ComponentHandler`~~ — doesn't exist yet
- ~~`/api/v3/components`~~ — no such route (TASK-666 registers it)
- ~~`/api/v3/validate`~~ — no such route (TASK-666 registers it)

---

## Implementation Notes

### Handler Pattern

Follow the existing `QueryHandler` pattern:
```python
class ComponentHandler(AbstractHandler):

    async def list_components(self, request: web.Request) -> web.Response:
        category = request.rel_url.query.get("category")
        catalog = ComponentRegistry.get_catalog()
        if category:
            catalog = [c for c in catalog if c.category == category]
        # Serialize to JSON
        result = [asdict(c) for c in catalog]
        return web.json_response(result, dumps=lambda x: orjson.dumps(x).decode())

    async def validate_pipeline(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response(
                {"valid": False, "errors": [{"step": "", "field": "", "message": "Invalid JSON body"}]},
                status=400
            )
        result = ComponentRegistry.validate_pipeline(payload)
        return web.json_response(asdict(result), dumps=lambda x: orjson.dumps(x).decode())
```

### Key Constraints
- Use `orjson` for JSON serialization (consistent with codebase)
- Return proper HTTP status codes: 200 for success, 400 for bad request
- The handler is instantiated once and methods are bound to routes (same as QueryHandler)
- `list_components` returns 200 even if catalog is empty (empty array)
- `validate_pipeline` returns 200 with `valid: false` for invalid pipelines (400 only for malformed requests)

---

## Acceptance Criteria

- [ ] `ComponentHandler` class exists at `querysource/handlers/components.py`
- [ ] `list_components()` returns JSON array of all components
- [ ] `list_components()` supports `?category=` query param filtering
- [ ] `validate_pipeline()` accepts POST with JSON body and returns validation result
- [ ] `validate_pipeline()` returns 400 for non-JSON body
- [ ] `validate_pipeline()` returns `{valid, errors}` format
- [ ] Tests pass: `pytest tests/test_component_handler.py -v`
- [ ] Import works: `from querysource.handlers.components import ComponentHandler`

---

## Test Specification

```python
# tests/test_component_handler.py
import pytest
from querysource.handlers.components import ComponentHandler


class TestComponentHandler:
    def test_handler_instantiates(self):
        handler = ComponentHandler()
        assert hasattr(handler, 'list_components')
        assert hasattr(handler, 'validate_pipeline')
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-663 is in `sdd/tasks/completed/`
3. **Read** `querysource/handlers/multi.py` for the handler pattern to follow
4. **Implement** `querysource/handlers/components.py`
5. **Run tests**: `source .venv/bin/activate && pytest tests/test_component_handler.py -v`
6. **Move this file** to `sdd/tasks/completed/TASK-665-http-component-handler.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-20
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none
