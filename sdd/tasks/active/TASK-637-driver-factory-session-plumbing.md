# TASK-637: Driver factory session plumbing

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-636
**Assigned-to**: unassigned

---

## Context

Implements **Module 11** of the spec. The driver factory path
(`get_source` → `build_provider` → `Provider(driver)` → `driver.connect()`)
must be plumbed with the user session so the Postgres driver's
`params_for(session, app)` (TASK-636) can be invoked instead of the legacy
`params()`. Without this plumbing, TASK-636's hook is dead code.

This task is the trickiest piece of the credential-resolver work because the
session needs to flow from the request all the way to driver instantiation.
The agent must trace the actual call chain in the codebase before making
edits.

---

## Scope

- Identify the path between `QueryService` / `QueryExecutor` /
  `QueryHandler` and the moment a `pgDriver` instance opens a connection.
  Document the chain in the Completion Note.
- Plumb `request` (or `app` + `session`) so that, at the connection-open
  moment for a Postgres driver, the call site uses
  `driver.params_for(session, app)` instead of `driver.params()`.
- The plumbing must be **opt-in**: only invoke `params_for` when `app` and
  `session` are both available; otherwise call `params()` (legacy
  behaviour).
- Other drivers continue using `params()` — only the `pgDriver` family
  benefits from per-user credential resolution in v1.
- Plumb the session through `MultiQS._user_session` (set by TASK-633) for
  the multi-query path.

**NOT in scope**: extending `params_for` to non-Postgres drivers; changing
the existing `pgDriver.params()` method; PBAC enforcement (those are
TASK-631..634).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| Driver factory call site(s) | MODIFY | Pass `app` + `session` into the param-build step. |
| `querysource/queries/multi/__init__.py` | MODIFY | Pass `self._user_session` into the per-component driver setup. |
| `tests/datasources/test_driver_factory_session.py` | CREATE | Tests verifying `params_for` is called when session is available. |

> **Important**: the agent **must determine the actual call sites** before
> editing. The candidates below are starting points for the trace, not
> authoritative.

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports & Signatures

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/pg.py:17
class pgDriver(SQLDriver):
    credential_prefix: str = "PG"   # added by TASK-636
    def params(self) -> dict: ...                                     # line 42 (unchanged)
    def params_for(self, session, app=None) -> dict: ...              # added by TASK-636

# Spec §2 says the driver-instantiation moment lives inside
# `QS.build_provider()` — confirm via:
#   grep -rn "build_provider\|connect()\|driver.params\|pgDriver(" querysource/

# /home/jesuslara/proyectos/parallel/querysource/querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(
        self, ..., user_session=None, **kwargs,
    ): ...   # extended by TASK-633
```

### Discovery commands the agent must run

```bash
# Find every place pg/pgDriver/postgresDriver gets a connection or its params:
grep -rn "params()" querysource/ | grep -v test_
grep -rn "build_provider\|build_connection\|connect()" querysource/ | grep -v test_
grep -rn "pgDriver(\|postgresDriver(" querysource/ | grep -v test_
```

### Does NOT Exist (until you discover them)

- ~~A single `app` parameter on `BaseQuery.build_provider`~~ — verify before
  assuming. The query layer historically operated without a session.
- ~~A `request` attribute on the driver itself~~ — drivers are stateless
  for connection params; pass `app` and `session` through the **factory**,
  not through driver state.
- ~~`MultiQS._user_session` accessible without TASK-633~~ — confirm TASK-633
  is in `tasks/done/` before relying on it.

---

## Implementation Notes

### Approach

1. **Trace the call chain** from each handler entry point to driver
   instantiation. Likely depth: `Handler.query` → `Handler.get_source` →
   `QS.build_provider` → `Provider.<connect>`. Document the actual chain.
2. **Pick the smallest plumbing change**:
   - Easiest: pass `app` + `session` into `build_provider(app=None,
     session=None)` as new keyword args. Inside `build_provider`, if both
     are present and the driver is a `pgDriver` subclass, call
     `driver.params_for(session, app)`; else fall back to `driver.params()`.
   - Alternative: stash `request` on the QS instance during construction
     and read `app`/`session` from it inside `build_provider`. Less
     invasive but couples the query layer to aiohttp.
3. For the multi-query path, `MultiQS` already stores `_user_session`
   (TASK-633). Each per-component thread (multi/__init__.py:140-156) must
   forward both the session and `request.app` into its provider build.

### Key Constraints

- **Backwards compatibility**: queue workers and internal callers without a
  request must continue to work. Default `app=None` and `session=None`
  preserve the legacy path (`params()`).
- **Postgres-only opt-in**: when the driver isn't a `pgDriver` subclass,
  always use `params()`. Detect with `isinstance(driver, pgDriver)` or
  `hasattr(driver, 'params_for')`.
- **No exceptions on the legacy path**: failure to find `app['credential_resolver']`
  must silently fall back. The resolver itself returns `None` on any miss
  (TASK-628).

### References in Codebase

- `querysource/queries/qs.py` — likely home of `build_provider`.
- `querysource/queries/multi/__init__.py:140-156` — per-component thread
  starters.
- TASK-636's `params_for` for the new method's signature.

---

## Acceptance Criteria

- [ ] When the user session is available and `app['credential_resolver']`
      is set, the Postgres driver factory calls `params_for(session, app)`
      instead of `params()` — verifiable via mock spy.
- [ ] When PBAC is disabled (no `credential_resolver`), the factory calls
      `params()` — legacy behaviour preserved.
- [ ] When the driver is not a `pgDriver` subclass (e.g., MySQL, Mongo),
      `params()` is always called regardless of session availability.
- [ ] Multi-query path forwards `MultiQS._user_session` into per-component
      driver builds.
- [ ] No regressions: `pytest tests/ -x -q` green.

---

## Test Specification

```python
# tests/datasources/test_driver_factory_session.py
import pytest
from unittest.mock import MagicMock
from querysource.auth.credentials import ResolvedCredentials
# Path imports here depend on what the agent discovers — fill in actual
# factory module after the trace.


class TestDriverFactorySessionPlumbing:
    def test_postgres_uses_params_for_when_session_present(self):
        """When session+resolver are present, pg driver gets params_for() called."""
        # ... build factory with mocked driver instance ...
        # Assertion: driver.params_for.assert_called_once_with(session, app)
        pytest.skip("Fill in after agent traces actual factory module")

    def test_postgres_falls_back_when_no_resolver(self):
        """No credential_resolver on app → params() not params_for()."""
        pytest.skip("Fill in after agent traces actual factory module")

    def test_non_postgres_always_uses_params(self):
        """A MySQL/Mongo driver never sees params_for()."""
        pytest.skip("Fill in after agent traces actual factory module")
```

(The agent fills these in once the actual factory module is identified.
Adjust skip → real assertions in the same task.)

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 11) + 6.
2. **Run the discovery commands** in the Codebase Contract above. Document
   the actual factory call chain in the Completion Note.
3. Pick the minimal plumbing approach (kwarg vs request-stash) and
   implement it.
4. Update `MultiQS.query()` thread starters to forward
   `self._user_session` and `request.app` into the per-component driver
   builds (read `multi/__init__.py:140-156`).
5. Replace the test skips with real assertions once the factory is wired.
6. Run `pytest tests/ -x -q`.
7. Move task to `done/` and update the index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Driver factory call chain (traced)**:
1. `Handler.<method>`
2. `Handler.get_source(request, slug, ...)`
3. `QS.build_provider(...)`
4. ...
**Plumbing approach chosen**: kwarg | request-stash | other (describe)
**Notes**:

**Deviations from spec**: none | describe if any
