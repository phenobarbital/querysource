# TASK-680: Register `/connect` and `/callback` routes inside `QuerySource.setup()`

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-678, TASK-679
**Assigned-to**: unassigned

---

## Context

The two OAuth view classes from `TASK-679` are useless until they're bound to URLs. This task wires them into `QuerySource.setup()` (the *real* method name — the user prompt called it `configure()`, but verified at `querysource/services.py:97` it is `setup()`). The wiring is conditional on `QS_AIRTABLE_OAUTH_ENABLED` from `TASK-678` so existing deployments are unaffected.

Implements §3 Module 5 (route-registration half) of the spec.

---

## Scope

Modify `querysource/services.py`:

- After the existing Component Documentation block (around line 218-227 in the current file — the `ComponentHandler` registration), add a new gated block that conditionally registers two GET routes when `QS_AIRTABLE_OAUTH_ENABLED` is true.
- Use a lazy import inside `setup()` for `AirtableConnectView` and `AirtableCallbackView` (mirrors the existing lazy-import of `ComponentHandler` at line 216).
- Register both routes via `self.app.router.add_get(...)`.

```python
# Inside QuerySource.setup(), after the Component Documentation block:

# ── Airtable OAuth integration (FEAT-096) — gated by env flag ───────
if QS_AIRTABLE_OAUTH_ENABLED:
    from .handlers.integrations.airtable import (   # noqa: PLC0415
        AirtableConnectView,
        AirtableCallbackView,
    )
    _airtable_connect = AirtableConnectView()
    _airtable_callback = AirtableCallbackView()
    r = self.app.router.add_get(
        '/api/v1/qs/integrations/airtable/connect',
        _airtable_connect.get,
    )
    routes.append(r)
    r = self.app.router.add_get(
        '/api/v1/qs/integrations/airtable/callback',
        _airtable_callback.get,
    )
    routes.append(r)
    _svc_logger.info("Airtable OAuth routes registered (QS_AIRTABLE_OAUTH_ENABLED=True)")
```

Also add the import at the top of the conf-imports block (around line 38-46):

```python
from .conf import (
    ENABLE_QS_SCHEDULER,
    USE_VECTORS,
    vector_models,
    GENSIM_DATA_DIR,
    QS_PBAC_ENABLED,
    QS_POLICY_PATH,
    QS_PBAC_CACHE_TTL,
    QS_AIRTABLE_OAUTH_ENABLED,    # added (FEAT-096)
)
```

Add tests in `tests/test_querysource_setup_airtable.py`.

**NOT in scope**:
- Adding the routes when the flag is `False` — the spec ACs require them to be **absent** in that case.
- Adding the routes to the deprecated `/api/v2/` namespace — only the v1 `/api/v1/qs/integrations/...` namespace per the spec.
- Wiring middleware / PBAC enforcement for these routes — out of scope for v1 (the consent flow is user-self-service, not subject to PBAC slug:execute checks).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Add `QS_AIRTABLE_OAUTH_ENABLED` import + conditional route block |
| `tests/test_querysource_setup_airtable.py` | CREATE | Assert routes are present/absent per the flag |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# querysource/services.py header (verified, line 38-47):
from .conf import (
    ENABLE_QS_SCHEDULER,
    USE_VECTORS,
    vector_models,
    GENSIM_DATA_DIR,
    QS_PBAC_ENABLED,
    QS_POLICY_PATH,
    QS_PBAC_CACHE_TTL,
)

# After TASK-678 lands, append `QS_AIRTABLE_OAUTH_ENABLED` to that import list.
```

### Existing Signatures to Use

```python
# querysource/services.py:49 (verified):
class QuerySource(metaclass=Singleton):

    def setup(self, app: web.Application) -> web.Application:
        # line 97 — the route-registration entry point
        ...

# Pattern for lazy-import + handler-class registration
# (verified at querysource/services.py:216-227 — ComponentHandler):
from .handlers.components import ComponentHandler  # noqa: PLC0415
ch = ComponentHandler()
r = self.app.router.add_get(
    r'/api/v3/qs/components',
    ch.list_components
)
routes.append(r)
r = self.app.router.add_post(
    r'/api/v3/qs/validate',
    ch.validate_pipeline
)
routes.append(r)

# Conditional-subsystem registration precedent
# (verified at querysource/services.py:293-296 — QSScheduler):
if ENABLE_QS_SCHEDULER:
    from .scheduler import QSScheduler
    self._scheduler = QSScheduler()
    self._scheduler.setup(self.app)


# _svc_logger is local to setup() — verified at querysource/services.py:111:
_svc_logger = logging.getLogger("querysource.services")
```

### Does NOT Exist

- ~~`QuerySource.configure()`~~ — the method is `setup()`. Implementation MUST target `setup()`.
- ~~`QuerySource.register_routes()`~~ — there is no helper; routes are added inline inside `setup()`.
- ~~A route-table builder pattern (`web.RouteTableDef()`)~~ — routes are added imperatively via `self.app.router.add_get(...)`. Match the existing style.
- ~~`querysource.handlers.integrations.airtable.routes`~~ — there is no top-level `routes = [...]` constant in the integrations module. Import the view classes and register them here.

---

## Implementation Notes

### Pattern to Follow

Find the `ComponentHandler` block at `querysource/services.py:216-227`:

```python
## Component Documentation:
from .handlers.components import ComponentHandler  # noqa: PLC0415
ch = ComponentHandler()
r = self.app.router.add_get(
    r'/api/v3/qs/components',
    ch.list_components
)
routes.append(r)
r = self.app.router.add_post(
    r'/api/v3/qs/validate',
    ch.validate_pipeline
)
routes.append(r)
```

Insert the new Airtable block **after** this one (before the "querying directly to drivers" section that starts ~line 230). Keep the same `# ── Section header ──` style and the `routes.append(r)` lines for parity with existing code.

### Key Constraints

- Use `add_get`, NOT `add_view`. View classes here are plain handler classes with an `async def get(self, request)`, not `aiohttp.web.View` subclasses.
- Use the `# noqa: PLC0415` comment on the lazy import (matches the existing convention at line 216).
- The `_svc_logger.info(...)` log line MUST run inside the `if` branch so it only fires when the feature is enabled.
- Do NOT also gate the route registrations on `QS_PBAC_ENABLED` — these are unauthenticated user-self-service endpoints (PBAC applies to query slugs, not integration callbacks).
- Keep the diff minimal: only the conf import (one line) and the new block (~15 lines).

### References in Codebase

- `querysource/services.py:97-310` — the full `setup()` method; read top-to-bottom before adding your block.
- `querysource/services.py:216-227` — direct pattern to mirror.
- `querysource/services.py:293-296` — conditional-subsystem precedent (`ENABLE_QS_SCHEDULER`).

---

## Acceptance Criteria

- [ ] With `QS_AIRTABLE_OAUTH_ENABLED=True`, `app.router` resolves both `/api/v1/qs/integrations/airtable/connect` and `/api/v1/qs/integrations/airtable/callback` to a callable.
- [ ] With `QS_AIRTABLE_OAUTH_ENABLED=False` (the default), neither path is registered (verifiable via `app.router._resources` enumeration).
- [ ] The conf import statement at the top of `querysource/services.py` now includes `QS_AIRTABLE_OAUTH_ENABLED`.
- [ ] No existing route registration in `setup()` was modified or reordered.
- [ ] `pytest tests/test_querysource_setup_airtable.py -v` passes.
- [ ] `ruff check querysource/services.py tests/test_querysource_setup_airtable.py` passes.

---

## Test Specification

```python
# tests/test_querysource_setup_airtable.py
import importlib

import pytest
from aiohttp import web


def _all_registered_paths(app: web.Application) -> list[str]:
    """Enumerate registered route paths from an aiohttp app."""
    paths = []
    for resource in app.router.resources():
        info = resource.get_info()
        # info is one of: {'path': ...} (PlainResource) or {'formatter': ...} (DynamicResource)
        path = info.get('path') or info.get('formatter')
        if path:
            paths.append(path)
    return paths


class TestAirtableOAuthRoutes:
    @pytest.fixture
    def querysource_fresh(self, monkeypatch):
        """Reload conf + services so the flag value takes effect."""
        # Force reload to re-evaluate module-level config reads
        from querysource import conf, services
        importlib.reload(conf)
        importlib.reload(services)
        # Singleton — clear instance cache between tests
        if hasattr(services.QuerySource, '_instances'):
            services.QuerySource._instances.clear()
        return services

    def test_routes_absent_when_flag_off(self, monkeypatch, querysource_fresh):
        monkeypatch.setattr(querysource_fresh, 'QS_AIRTABLE_OAUTH_ENABLED', False)
        app = web.Application()
        qs = querysource_fresh.QuerySource(lazy=True)
        qs.setup(app)
        paths = _all_registered_paths(app)
        assert not any('/integrations/airtable' in p for p in paths), (
            f"Expected no airtable integration routes when flag is off, got: {paths}"
        )

    def test_routes_present_when_flag_on(self, monkeypatch, querysource_fresh):
        monkeypatch.setattr(querysource_fresh, 'QS_AIRTABLE_OAUTH_ENABLED', True)
        app = web.Application()
        qs = querysource_fresh.QuerySource(lazy=True)
        qs.setup(app)
        paths = _all_registered_paths(app)
        assert '/api/v1/qs/integrations/airtable/connect' in paths
        assert '/api/v1/qs/integrations/airtable/callback' in paths
```

---

## Agent Instructions

1. Confirm `TASK-678` and `TASK-679` are `completed`.
2. Re-read `querysource/services.py` around lines 38-50 (imports) and 216-227 (ComponentHandler block).
3. Apply the two edits per Scope (one import line + one conditional block).
4. Run `pytest tests/test_querysource_setup_airtable.py -v`. The `QuerySource` Singleton may need explicit reset between tests — that is handled in the fixture, but if you see flakiness, double-check the import-reload sequence.
5. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:
