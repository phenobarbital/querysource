# TASK-630: AbstractHandler PBAC helpers

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-629
**Assigned-to**: unassigned

---

## Context

Implements **Module 4** of the spec. Adds two helpers to `AbstractHandler`
(`querysource/handlers/abstract.py`):

- `_get_user_session(request)` — extracts the session via
  `navigator_session.get_session()` and memoizes on `request['user_session']`.
- `_enforce_pbac(request, resource_type, resource_name, action)` — evaluates
  one PBAC decision and raises `web.HTTPNotFound` on deny. Fast-path no-op
  when `app['security']` is absent (PBAC disabled).

These helpers are consumed by every handler enforcement task (TASK-631,
TASK-632, TASK-633) and the list-filtering task (TASK-634).

---

## Scope

- Add `_get_user_session(self, request: web.Request) -> Optional[SessionData]`
  to `AbstractHandler`. Reuses `request['user_session']` if already set;
  otherwise calls `await get_session(request, new=False)` inside a try/except
  that catches `RuntimeError` (the existing pattern at
  `querysource/interfaces/queries.py:174`) and returns `None`.
- Add `async _enforce_pbac(self, request, resource_type, resource_name,
  action) -> None` to `AbstractHandler`. Raises `web.HTTPNotFound` on deny.
- Build an `EvalContext` from session/userinfo/user using the same pattern as
  ai-parrot's `_build_eval_context()` (see Codebase Contract). Lazy-import
  `EvalContext` and `AUTH_SESSION_OBJECT` inside the helper.
- Fast-path: if `request.app.get('security') is None`, return immediately
  (no enforcement when PBAC is disabled).
- Fail-closed: if PBAC is enabled (`security` present) AND
  `_get_user_session` returns `None`, raise `web.HTTPNotFound`.
- Use `PolicyEvaluator.check_access()` for the single-resource check (the
  evaluator caches decisions, so this is the right entry point).

**NOT in scope**: any handler-specific call sites (those land in TASK-631
through TASK-634); the multi-resource batch filter (Guardian.filter_resources)
is for TASK-633 / TASK-634, not this helper.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/abstract.py` | MODIFY | Add `_get_user_session` and `_enforce_pbac` methods. |
| `tests/handlers/test_abstract_pbac_helpers.py` | CREATE | Unit tests for both helpers (mock-based). |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

Module-level (already imported or to be added):

```python
from aiohttp import web                                              # querysource/handlers/abstract.py (already)
from navigator_session import get_session, SessionData               # querysource/interfaces/queries.py:17-18
```

Lazy imports inside `_enforce_pbac` (only when PBAC is active):

```python
from navigator_auth.abac.context import EvalContext                  # navigator_auth/abac/context.py:6
from navigator_auth.abac.policies.environment import Environment     # navigator_auth/abac/policies/environment.py
from navigator_auth.conf import AUTH_SESSION_OBJECT                  # navigator_auth/conf.py:195
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/abstract.py:18
class AbstractHandler(BaseHandler):  # 214 lines
    def post_init(self, *args, **kwargs):
        self.logger = logging.getLogger('QS.Handler')
        # ...

# Existing pattern to MIRROR — querysource/interfaces/queries.py:165
async def user_session(self, request: web.Request = None) -> SessionData:
    if not request:
        return None
    try:
        session = await get_session(request, new=False)
    except RuntimeError:
        self._logger.error('QS: User Session system is not installed.')
        return None
    return session

# ai-parrot reference (read-only) — parrot/handlers/agent.py:378
async def _build_eval_context(self) -> Any:
    # session = self.request.session OR await get_session(self.request)
    # userinfo = session.get(AUTH_SESSION_OBJECT, {})
    # user = session.decode('user') if hasattr(session, 'decode') else userinfo
    return EvalContext(request=request, user=user, userinfo=userinfo, session=session)
```

```python
# Method to call — navigator_auth/abac/policies/evaluator.py:361
class PolicyEvaluator:
    def check_access(
        self, ctx: EvalContext, resource_type: ResourceType,
        resource_name: str, action: str, env: Environment = None,
        owner_reports_to: str = None,
    ) -> EvaluationResult:
        # Returns EvaluationResult(allowed: bool, effect, matched_policy, reason)
```

### Does NOT Exist

- ~~`request.session`~~ — NOT a QuerySource convention. Use
  `await get_session(request)` plus memoize on `request['user_session']`.
- ~~`AbstractHandler._get_user_session`~~ / ~~`AbstractHandler._enforce_pbac`~~ —
  introduced by this task.
- ~~`request['session']`~~ — use `request['user_session']` (this task's
  memoization key, distinct from any existing convention).
- ~~`Guardian.check_access`~~ — Guardian doesn't expose this directly. Use
  `app['policy_evaluator'].check_access(...)`.
- ~~`PolicyEvaluator.check`~~ — the method is `check_access` (full name).

---

## Implementation Notes

### `_get_user_session` shape

```python
async def _get_user_session(self, request: web.Request) -> Optional["SessionData"]:
    """Extract and memoize the user session.

    Returns None when navigator_session is unavailable or no session exists.
    Subsequent calls in the same request return the memoized value.
    """
    cached = request.get('user_session', _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    try:
        session = await get_session(request, new=False)
    except RuntimeError:
        self.logger.error('QS: User Session system is not installed.')
        session = None
    request['user_session'] = session
    return session
```

Use a module-level `_SENTINEL = object()` so we can distinguish "memoized as
None" from "not yet looked up".

### `_enforce_pbac` shape

```python
async def _enforce_pbac(
    self,
    request: web.Request,
    resource_type,                # navigator_auth ResourceType (passed by caller)
    resource_name: str,
    action: str,
) -> None:
    """Evaluate a single PBAC decision; raise web.HTTPNotFound on deny.

    Fast-path no-op when PBAC is not active on the app. Fail-closed when
    PBAC is active but no session can be extracted.
    """
    guardian = request.app.get('security')
    if guardian is None:
        return  # PBAC disabled — no-op

    session = await self._get_user_session(request)
    if session is None:
        # Fail-closed when PBAC is enabled but caller is unauthenticated.
        self.logger.info(
            "PBAC denied (no session): %s/%s action=%s",
            resource_type, resource_name, action,
        )
        raise web.HTTPNotFound()

    evaluator = request.app.get('policy_evaluator')
    if evaluator is None:
        # Bootstrap inconsistency — Guardian present but no evaluator.
        self.logger.error("PBAC misconfigured: 'security' set but no 'policy_evaluator'")
        raise web.HTTPNotFound()

    # Lazy import nav-auth EvalContext and friends.
    from navigator_auth.abac.context import EvalContext
    from navigator_auth.abac.policies.environment import Environment
    from navigator_auth.conf import AUTH_SESSION_OBJECT

    userinfo = (
        session.get(AUTH_SESSION_OBJECT, {})
        if hasattr(session, 'get') else {}
    )
    user = userinfo if isinstance(userinfo, dict) and userinfo else None
    ctx = EvalContext(
        request=request, user=user, userinfo=userinfo, session=session,
    )

    result = evaluator.check_access(
        ctx=ctx,
        resource_type=resource_type,
        resource_name=resource_name,
        action=action,
        env=Environment(),
    )
    if not result.allowed:
        self.logger.info(
            "PBAC denied: %s/%s action=%s policy=%s reason=%s",
            resource_type, resource_name, action,
            getattr(result, 'matched_policy', None),
            getattr(result, 'reason', None),
        )
        raise web.HTTPNotFound()
```

### Key Constraints

- **Always 404 on deny** — never 403, never include the resource name in the
  response body. The default `web.HTTPNotFound()` body is fine.
- **Lazy imports** — never import navigator-auth at module load. The whole
  point of the fast-path is that `_enforce_pbac` does nothing (and imports
  nothing) when PBAC is disabled.
- **Memoize on `request`, not `self`** — handler instances may be reused
  across requests (aiohttp class-based views).
- **Catch `RuntimeError`** for navigator_session, not `Exception` — the
  existing pattern in `interfaces/queries.py:174` is specific.

### References in Codebase

- `querysource/interfaces/queries.py:165-178` — `user_session` pattern.
- `parrot/handlers/agent.py:378` (ai-parrot, read-only) — EvalContext build.
- `navigator_auth/abac/policies/evaluator.py:361` — `check_access` signature.

---

## Acceptance Criteria

- [ ] `AbstractHandler._get_user_session(request)` returns `None` when
      `get_session` raises `RuntimeError`.
- [ ] `_get_user_session` caches on `request['user_session']` — second call
      does NOT re-invoke `get_session`.
- [ ] `_enforce_pbac` returns silently when `app['security']` is absent
      (no exception, no behavior change).
- [ ] `_enforce_pbac` raises `web.HTTPNotFound` when `app['security']` is
      set but no session can be extracted.
- [ ] `_enforce_pbac` raises `web.HTTPNotFound` when the evaluator returns
      `allowed=False`.
- [ ] `_enforce_pbac` returns silently when the evaluator returns
      `allowed=True`.
- [ ] No regressions: `pytest tests/ -x -q` clean.

---

## Test Specification

Use mocks to avoid bringing up the full PBAC stack here.

```python
# tests/handlers/test_abstract_pbac_helpers.py
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiohttp import web

from querysource.handlers.abstract import AbstractHandler


class _Handler(AbstractHandler):
    """Test-only subclass exposing the helpers as plain methods."""


@pytest.fixture
def handler():
    h = _Handler.__new__(_Handler)
    h.logger = MagicMock()
    return h


@pytest.fixture
def request_no_pbac():
    req = MagicMock(spec=web.Request)
    req.app = {}
    req.__contains__ = lambda self, k: False
    req.__getitem__ = MagicMock(side_effect=KeyError)
    req.__setitem__ = MagicMock()
    req.get = lambda k, d=None: d
    return req


class TestGetUserSession:
    async def test_returns_none_on_runtime_error(self, handler, request_no_pbac):
        with patch("querysource.handlers.abstract.get_session",
                   AsyncMock(side_effect=RuntimeError)):
            result = await handler._get_user_session(request_no_pbac)
            assert result is None

    async def test_memoizes(self, handler):
        store = {}
        req = MagicMock()
        req.get = lambda k, d=None: store.get(k, d)
        def setitem(k, v): store[k] = v
        req.__setitem__ = MagicMock(side_effect=setitem)
        with patch("querysource.handlers.abstract.get_session",
                   AsyncMock(return_value={"username": "alice"})) as mocked:
            await handler._get_user_session(req)
            await handler._get_user_session(req)
            assert mocked.call_count == 1


class TestEnforcePbac:
    async def test_noop_when_pbac_disabled(self, handler, request_no_pbac):
        # Should NOT raise — PBAC disabled
        await handler._enforce_pbac(
            request_no_pbac, resource_type="slug",
            resource_name="anything", action="slug:execute",
        )

    async def test_404_when_no_session_and_pbac_enabled(self, handler):
        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": MagicMock()}
        req.get = lambda k, d=None: d
        req.__setitem__ = MagicMock()
        with patch("querysource.handlers.abstract.get_session",
                   AsyncMock(return_value=None)):
            with pytest.raises(web.HTTPNotFound):
                await handler._enforce_pbac(
                    req, resource_type="slug",
                    resource_name="x", action="slug:execute",
                )

    async def test_404_when_evaluator_denies(self, handler):
        evaluator = MagicMock()
        evaluator.check_access = MagicMock(return_value=MagicMock(
            allowed=False, matched_policy="P", reason="denied"))
        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": evaluator}
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))
        with patch("querysource.handlers.abstract.get_session",
                   AsyncMock(return_value={"username": "alice"})):
            with pytest.raises(web.HTTPNotFound):
                await handler._enforce_pbac(
                    req, resource_type="slug",
                    resource_name="x", action="slug:execute",
                )
```

---

## Agent Instructions

1. Read spec sections 2, 6, 7 for full context.
2. Re-grep `querysource/interfaces/queries.py:165-178` to confirm the
   `user_session` pattern is unchanged.
3. Implement both helpers in `querysource/handlers/abstract.py`.
4. Write the mock-based unit tests.
5. Run `pytest tests/handlers/test_abstract_pbac_helpers.py -v` until green.
6. Run full suite to confirm no regressions.
7. Move task to `done/` and update the index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-04-30
**Notes**: Both helpers implemented as specified. _get_user_session memoizes
via _SENTINEL. _enforce_pbac fast-path no-op when app['security'] absent,
fail-closed (HTTP 404) when PBAC enabled but no session or evaluator. All
nav-auth imports are lazy. 8/8 unit tests pass. Compiled Cython .so files
symlinked into worktree for test runner compatibility.

**Deviations from spec**: none
