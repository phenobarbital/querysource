# TASK-666: Route Registration

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-665
**Assigned-to**: unassigned

---

## Context

> Implements Module 7 from the spec. Registers the two new HTTP routes
> (`GET /api/v3/components` and `POST /api/v3/validate`) in the QuerySource
> service setup method, following the exact same pattern used for existing
> MultiQuery routes.

---

## Scope

- Modify `querysource/services.py` to import `ComponentHandler`
- Register two new routes in `QuerySource.setup()`:
  - `GET /api/v3/components` → `ch.list_components`
  - `POST /api/v3/validate` → `ch.validate_pipeline`
- Add routes in the existing v3 section (after MultiQuery routes, before driver routes)
- Write a smoke test verifying routes are registered

**NOT in scope**: Handler implementation (TASK-665), registry (TASK-663), CLI (TASK-664)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Import ComponentHandler, register 2 routes |
| `tests/test_route_registration.py` | CREATE | Smoke test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Handler (created by TASK-665)
from .handlers.components import ComponentHandler

# Existing imports in services.py (pattern to follow)
from .handlers.multi import QueryHandler  # verified: services.py imports this
```

### Existing Signatures to Use
```python
# querysource/services.py:49
class QuerySource(metaclass=Singleton):
    def setup(self, app: web.Application) -> web.Application:  # line 97
        # ...
        routes = []
        # ... existing route registrations ...

        ## Multi-Query:  (line 180)
        mq = QueryHandler()
        r = self.app.router.add_post(r'/api/v3/queries/{slug}{meta:\:?.*}', mq.query)
        routes.append(r)
        # ... more routes ...

        # INSERT NEW ROUTES HERE (after line ~207, before driver routes)
```

### Route Registration Pattern (exact pattern to follow)
```python
## Component Documentation:
ch = ComponentHandler()
r = self.app.router.add_get(
    r'/api/v3/components',
    ch.list_components
)
routes.append(r)
r = self.app.router.add_post(
    r'/api/v3/validate',
    ch.validate_pipeline
)
routes.append(r)
```

### Does NOT Exist
- ~~`/api/v3/components`~~ — no such route exists yet; you register it
- ~~`/api/v3/validate`~~ — no such route exists yet; you register it

---

## Implementation Notes

### Exact Insertion Point

The new routes go after the existing `/api/v3/queries` block (ending around line 207)
and before the `/api/v2/queries/{driver}` block (starting around line 209).

### Key Constraints
- Follow the exact pattern: instantiate handler, add routes to `self.app.router`, append to `routes` list
- Use `add_get` for the components listing (not `add_view`)
- Use `add_post` for the validation endpoint
- Import `ComponentHandler` at the top of `services.py` alongside existing handler imports
- Keep the change minimal — only add the import and route registrations

---

## Acceptance Criteria

- [ ] `GET /api/v3/components` route is registered in `services.py`
- [ ] `POST /api/v3/validate` route is registered in `services.py`
- [ ] `ComponentHandler` is imported in `services.py`
- [ ] Existing routes are unaffected
- [ ] No import errors: `source .venv/bin/activate && python -c "from querysource.services import QuerySource"`
- [ ] Tests pass: `pytest tests/test_route_registration.py -v`

---

## Test Specification

```python
# tests/test_route_registration.py
import pytest


class TestRouteRegistration:
    def test_services_imports_component_handler(self):
        from querysource.services import QuerySource
        # Verify the import doesn't fail
        assert QuerySource is not None

    def test_component_handler_importable(self):
        from querysource.handlers.components import ComponentHandler
        handler = ComponentHandler()
        assert hasattr(handler, 'list_components')
        assert hasattr(handler, 'validate_pipeline')
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-665 is in `sdd/tasks/completed/`
3. **Read** `querysource/services.py` around lines 180-210 to find the insertion point
4. **Add** the import and route registrations
5. **Run tests**: `source .venv/bin/activate && pytest tests/test_route_registration.py -v`
6. **Move this file** to `sdd/tasks/completed/TASK-666-route-registration.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-20
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none
