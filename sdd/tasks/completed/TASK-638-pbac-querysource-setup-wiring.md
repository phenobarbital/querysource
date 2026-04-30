# TASK-638: QuerySource.setup() PBAC wiring

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-629
**Assigned-to**: unassigned

---

## Context

Implements **Module 12** of the spec. Wires the PBAC bootstrap (TASK-629)
into `QuerySource.setup(app)` at `querysource/services.py:80`. With this
task complete, every consuming app that calls `QuerySource(...).setup(app)`
gets PBAC for free when `QS_PBAC_ENABLED=True`. No extra setup call is
needed in the consuming app.

The bootstrap is invoked **after** the existing `connection.setup(app)` and
`TemplateParser.setup(app)` calls, and **before** the route registrations
for `QueryService`, `QueryExecutor`, `QueryHandler`, etc.

---

## Scope

- Inside `QuerySource.setup(app)`, after `TemplateParser.setup(app)` (line
  ~89) and before the route registrations (line ~92 onward), read
  `QS_PBAC_ENABLED` from `querysource.conf`. When `True`, call
  `setup_pbac(app, policy_dir=QS_POLICY_PATH, cache_ttl=QS_PBAC_CACHE_TTL)`.
- Log success/failure at info level. On failure (returns `(None, None,
  None)` while `QS_PBAC_ENABLED=True`), log a **warning** and continue —
  the spec's fail-closed semantics are enforced inside `_enforce_pbac`
  (no `app['security']` ⇒ requests with sessions still pass; requests
  without sessions ⇒ 404 because `_enforce_pbac` checks for `security`
  presence). If you want strict fail-closed-on-bootstrap-failure, raise
  `RuntimeError` — discuss in Completion Note.
- When `QS_PBAC_ENABLED=False`: log "PBAC disabled" at debug level, do
  nothing else.

**NOT in scope**: editing `setup_pbac` itself (TASK-629); installing
nav-auth's middleware or REST routes (the spec excludes these from
QuerySource — handlers enforce explicitly).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/services.py` | MODIFY | Insert PBAC bootstrap call into `QuerySource.setup(app)`. |
| `tests/services/test_querysource_setup_pbac.py` | CREATE | Tests for both `QS_PBAC_ENABLED` modes. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# In querysource/services.py — to be added at module level:
from querysource.conf import QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL
from querysource.auth import setup_pbac
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/services.py:45
class QuerySource(metaclass=Singleton):
    def __init__(self, **kwargs):                                       # line 60
        if hasattr(self, '__initialized__') and self.__initialized__ is True:
            return
        self.lazy: bool = kwargs.get('lazy', False)
        self._loop: asyncio.AbstractEventLoop = kwargs.get('loop', asyncio.get_event_loop())
        self.connection = QueryConnection(loop=self._loop, lazy=self.lazy)
        # ...iterates providers...

    def setup(self, app: web.Application) -> web.Application:           # line 80
        if isinstance(app, BaseApplication):
            self.app = app.get_app()
        elif isinstance(app, WebApp):
            self.app = app
        # register the Connection Object:
        self.connection.setup(app=app)                                  # line 86
        ## Start the template System
        tpl = TemplateParser()
        tpl.setup(app=app)                                              # line 89
        # ↓ INSERT PBAC BOOTSTRAP HERE (this task) — before any route
        #   registration begins.

        ## making the registration of handlers (services and managers)
        qs = QueryService()                                             # line 91
        routes = []
        r = self.app.router.add_get('/api/v2/services/queries', qs.run_queries, ...)
        # ... 50+ more route registrations ...
```

### Does NOT Exist

- ~~`pdp.setup(app)` call here~~ — that registers nav-auth's middleware
  and REST routes which the spec **excludes** from QuerySource. Only
  `setup_pbac()` is invoked.
- ~~A QuerySource-private aiohttp middleware~~ — handlers enforce
  explicitly per spec.

---

## Implementation Notes

### Insertion point

In `services.py:setup`, immediately after `tpl.setup(app=app)` (line 89):

```python
def setup(self, app: web.Application) -> web.Application:
    if isinstance(app, BaseApplication):
        self.app = app.get_app()
    elif isinstance(app, WebApp):
        self.app = app
    self.connection.setup(app=app)
    tpl = TemplateParser()
    tpl.setup(app=app)

    # ── FEAT-091: PBAC bootstrap ──────────────────────────────────────
    if QS_PBAC_ENABLED:
        pdp, evaluator, guardian = setup_pbac(
            self.app,
            policy_dir=QS_POLICY_PATH,
            cache_ttl=QS_PBAC_CACHE_TTL,
        )
        logger = logging.getLogger("querysource.services")
        if guardian is not None:
            logger.info(
                "QS PBAC enabled (policy_dir=%s, cache_ttl=%ds)",
                QS_POLICY_PATH, QS_PBAC_CACHE_TTL,
            )
        else:
            logger.warning(
                "QS_PBAC_ENABLED=True but bootstrap returned None. "
                "Requests without sessions will be denied by handlers; "
                "policy enforcement is OFF for this process."
            )
    else:
        logging.getLogger("querysource.services").debug("QS PBAC disabled")
    # ──────────────────────────────────────────────────────────────────

    # ── existing route registrations continue unchanged ───────────────
    qs = QueryService()
    # ...
```

### Imports at module top

Add at the top of `services.py` (alongside other imports):

```python
from querysource.conf import QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL
from querysource.auth import setup_pbac
```

If `services.py` already has a logging import, reuse it; otherwise add
`import logging`.

### Key Constraints

- **Order matters**: `setup_pbac` runs **after** `connection.setup` and
  `TemplateParser.setup` (which may register their own app keys) and
  **before** route registrations (handlers may peek at `app['security']`
  during their own setup hooks).
- **Singleton-safe**: `QuerySource.setup` may be called more than once if a
  test re-instantiates. `setup_pbac` is idempotent (TASK-629), so a second
  call is safe.
- **Don't fail the whole boot** on PBAC bootstrap failure. Log a warning
  and let the handlers do fail-closed enforcement at request time.

### References in Codebase

- `querysource/services.py:80-260` — `setup` method end-to-end.
- `parrot/app.py:230-248` (ai-parrot) — analogous wiring shape.

---

## Acceptance Criteria

- [ ] With `QS_PBAC_ENABLED=False` (default), `QuerySource.setup(app)`
      does NOT call `setup_pbac`, does NOT populate `app['security']`, and
      existing tests pass unchanged.
- [ ] With `QS_PBAC_ENABLED=True` and a valid policy dir,
      `QuerySource.setup(app)` populates `app['security']`,
      `app['policy_evaluator']`, `app['abac']`, and
      `app['credential_resolver']`.
- [ ] With `QS_PBAC_ENABLED=True` and an invalid policy dir, setup logs a
      warning, returns the app cleanly, and `app['security']` is `None`
      (or absent).
- [ ] PBAC bootstrap runs **after** connection/template setup and
      **before** the route registrations (verifiable by checking line
      order in services.py).
- [ ] No regressions: full test suite green.

---

## Test Specification

```python
# tests/services/test_querysource_setup_pbac.py
import pytest
from unittest.mock import MagicMock, patch
from aiohttp import web


class TestQuerySourceSetupPbac:
    def test_disabled_skips_bootstrap(self, monkeypatch):
        monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", False)
        with patch("querysource.services.setup_pbac") as mock_setup:
            from querysource.services import QuerySource
            qs = QuerySource(lazy=True)
            app = web.Application()
            qs.setup(app)
            mock_setup.assert_not_called()

    def test_enabled_invokes_bootstrap(self, monkeypatch, tmp_path):
        monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", True)
        monkeypatch.setattr("querysource.services.QS_POLICY_PATH", str(tmp_path))
        monkeypatch.setattr("querysource.services.QS_PBAC_CACHE_TTL", 300)
        with patch("querysource.services.setup_pbac",
                   return_value=(None, None, None)) as mock_setup:
            from querysource.services import QuerySource
            qs = QuerySource(lazy=True)
            app = web.Application()
            qs.setup(app)
            mock_setup.assert_called_once()
            args, kwargs = mock_setup.call_args
            assert kwargs["policy_dir"] == str(tmp_path)
            assert kwargs["cache_ttl"] == 300
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 12) + 6 + 7.
2. Re-read `querysource/services.py:80-100` to confirm the insertion point.
3. Add the imports and the conditional bootstrap block.
4. Add the unit tests.
5. Run `pytest tests/ -x -q`.
6. Move task to `done/` and update the index.

---

## Completion Note

**Completed by**: Claude (SDD Worker)
**Date**: 2026-04-30
**Notes**: Inserted PBAC bootstrap after `tpl.setup(app=app)` and before first route registration (`qs = QueryService()`). Fail-open semantics preserved — warning logged when bootstrap returns None, app continues normally. 3 tests pass.

**Deviations from spec**: none
