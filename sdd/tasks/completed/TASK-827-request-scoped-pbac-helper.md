# TASK-827: Request-scoped PBAC helper (`auth/request_gate.py`)

**Feature**: FEAT-160 — Scheduler Admin Gate for Write-Capable Multi-Queries
**Spec**: `sdd/specs/multi-scheduler-admin-gate.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 M1 and §3 Module 1. `QueryManager` (`QueryView` → navigator `BaseView`) and
`SchedulerJobsView` (`BaseView`) do not derive from `AbstractHandler`, so they have no
`_enforce_pbac`. This task moves the bodies of `AbstractHandler._get_user_session` and
`AbstractHandler._enforce_pbac` into module-level coroutines in a new
`querysource/auth/request_gate.py`. The two handler methods then become thin delegates with the
same signatures and the same behaviour. TASK-829 and TASK-830 call the new coroutine.

**Drift found while writing this task (decision recorded here, spec §2 intent kept).** Existing tests
patch names **on the handler module**, not on `navigator_session` or `querysource.conf`:
- `tests/handlers/test_abstract_pbac_helpers.py` patches `querysource.handlers.abstract.get_session`
  (10×) and `querysource.handlers.abstract.QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` (4×).
- `tests/tenants/test_tenant_policy_preflight.py:177` patches the same flag.
- `tests/integration/test_pbac_enforcement.py:16`, `test_pbac_listing.py:9`,
  `test_pbac_credentials.py:13` and `tests/perf/test_pbac_overhead.py:37` patch
  `querysource.handlers.abstract.get_session`.
- `tests/handlers/test_multiquery_pbac_smoke.py` (and `tests/test_multiquery_write_gate_http.py`) set
  `h._get_user_session = AsyncMock(...)` on the instance.

A strictly verbatim move, where the new module looks these names up in its own globals, would silently
bypass those patches. The two coroutines therefore take **optional keyword-only injection points**,
a superset of the spec §2 signatures. The spec-mandated `(request, …, *, logger)` call keeps working:
- `get_request_session(request, *, logger, session_getter=None)`: `session_getter` defaults to
  `navigator_session.get_session`;
- `enforce_request_pbac(request, resource_type, resource_name, action, *, logger, session_resolver=None, allow_sessionless=None)`:
  `session_resolver` defaults to `get_request_session`, and `allow_sessionless=None` reads
  `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`.

The `AbstractHandler` delegates pass `session_getter=get_session`,
`session_resolver=self._get_user_session` and `allow_sessionless=QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`.
All three names resolve at call time from `handlers/abstract.py`'s globals or the instance, so every
existing patch and instance mock still applies. This was checked on a scratch copy of the tree: the
regression files listed under Validation Commands all pass with this design.

---

## Scope

- Create `querysource/auth/request_gate.py` with `get_request_session` and `enforce_request_pbac`.
  The bodies are moved from `AbstractHandler._get_user_session` / `_enforce_pbac`. The only changes:
  `self.logger` → `logger`, `self._get_user_session(request)` → the resolver,
  `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` → `allow`.
- Replace the bodies of `AbstractHandler._get_user_session` and `AbstractHandler._enforce_pbac` with
  delegates. Keep their signatures unchanged.
- Add one import line to `querysource/handlers/abstract.py`.
- Create `tests/test_request_gate.py`, covering parity with the existing `_enforce_pbac` tests and the delegation.

**NOT in scope**: `_enforce_owned_slug` (it keeps its own inline body and still uses
`QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`, `resolve_evaluator`, `build_eval_context`, `evaluate` and
`_SENTINEL` from `handlers/abstract.py`). Removing any import from `handlers/abstract.py`. Changing
`_SENTINEL`, which `querysource/queries/qs.py:208` and `querysource/queries/obj.py:80` import.
Any caller in `QueryManager` or `SchedulerJobsView` (TASK-829 / TASK-830).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/request_gate.py` | CREATE | `get_request_session`, `enforce_request_pbac` (moved bodies + injection kwargs) |
| `querysource/handlers/abstract.py` | MODIFY | import + `_get_user_session` / `_enforce_pbac` become delegates |
| `tests/test_request_gate.py` | CREATE | parity + delegation tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `71ebae0` (spec verified at `c26af0c`; no line drift in `handlers/abstract.py`).

### Verified Imports
```python
from navigator_session import SessionData, get_session                          # verified: querysource/handlers/abstract.py:8
from querysource.auth.enforcement import build_eval_context, evaluate, resolve_evaluator  # verified: abstract.py:11 (as ..auth.enforcement); defined enforcement.py:86,105,53
from querysource.conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ                    # verified: abstract.py:12 (as ..conf); enforcement.py:14 imports it too
from navigator_auth.conf import AUTHZ_BACKEND_KEY, AUTH_SESSION_OBJECT          # lazy imports inside the moved body (abstract.py:379,413)
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py
_SENTINEL = object()                                                  # line 29 — KEEP (imported by queries/qs.py:208, queries/obj.py:80)
class AbstractHandler(BaseHandler):                                   # line 32
    def post_init(self, *args, **kwargs): self.logger = logging.getLogger('QS.Handler')  # line 38-39
    async def _get_user_session(self, request: web.Request) -> Optional[SessionData]:  # lines 298-324 (memoises request['user_session'])
    async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None:  # lines 326-442
    async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None:  # line 444 — untouched; uses self._get_user_session

# querysource/auth/enforcement.py
def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]:  # line 53
def build_eval_context(*, userinfo: dict, user: Any, session: Any, request: web.Request | None = None) -> Any  # line 86
async def evaluate(evaluator: Any, ctx: Any, resource_type: Any, resource_name: str, action: str)  # line 105 → decision.allowed / matched_policy / reason
```

### Does NOT Exist
- ~~`querysource.auth.request_gate`~~: created by this task.
- ~~`querysource.auth.request_gate._SENTINEL`~~: the new module uses its own `_SESSION_UNSET`. `handlers/abstract.py:_SENTINEL` stays where it is.
- ~~`_enforce_pbac` on `BaseView` / `QueryView`~~: views get the gate only through this module.
- ~~A re-export of the new functions from `querysource/auth/__init__.py`~~: not added. Import from `querysource.auth.request_gate`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/request_gate.py", "action": "CREATE"},
    {"path": "querysource/handlers/abstract.py", "action": "MODIFY"},
    {"path": "tests/test_request_gate.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/abstract.py#AbstractHandler",
    "sym:querysource/handlers/abstract.py#AbstractHandler._get_user_session",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac",
    "sym:querysource/auth/enforcement.py#resolve_evaluator",
    "sym:querysource/auth/enforcement.py#build_eval_context",
    "sym:querysource/auth/enforcement.py#evaluate"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Behaviour must stay identical: fast-path no-op without `app['security']`, 404 on a missing
  `resource_name`, fail-closed 404 with no session and no sessionless authz, 404 when there is no
  evaluator, 404 on deny, and the same `logger.info` messages.
- Do not remove `get_session`, `SessionData`, `Optional`, `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`,
  `resolve_evaluator`, `build_eval_context`, `evaluate` or `_SENTINEL` from `handlers/abstract.py`.
  The delegates and `_enforce_owned_slug` still use them.
- `ruff check` on `handlers/abstract.py` reports one **pre-existing** `B012` at `abstract.py:86`
  (`return` inside `finally`). Leave it. The gate is "no new findings".

---

## Implementation Blueprint

### Steps (in order)
1. Create `querysource/auth/request_gate.py` from the block below. *Why*: this is the reusable request-scoped gate (spec M1).
2. Add the import to `handlers/abstract.py` and replace the two method bodies with the delegate block. *Why*: one implementation, existing behaviour and patches preserved.
3. Create `tests/test_request_gate.py`. *Why*: covers spec §4 `test_request_gate_parity`.
4. Run the Validation Commands and `ruff check querysource/auth/request_gate.py querysource/handlers/abstract.py tests/test_request_gate.py`.

### `querysource/auth/request_gate.py` (CREATE)
```python
"""Request-scoped PBAC gate usable from any aiohttp view (FEAT-160).

The bodies of ``AbstractHandler._get_user_session`` and
``AbstractHandler._enforce_pbac`` live here so views that do not derive from
``AbstractHandler`` (``QueryManager``, ``SchedulerJobsView``) can enforce the
same decision with the same fail-closed, 404-on-deny semantics.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web
from navigator_session import SessionData, get_session

from ..conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
from .enforcement import build_eval_context, evaluate, resolve_evaluator

# Sentinel used to distinguish "not yet cached" from "cached as None".
_SESSION_UNSET = object()

SessionGetter = Callable[..., Awaitable[Any]]
SessionResolver = Callable[[web.Request], Awaitable[Any]]


async def get_request_session(
    request: web.Request,
    *,
    logger: logging.Logger,
    session_getter: SessionGetter | None = None,
) -> SessionData | None:
    """Extract and memoize the user session from the current request.

    Uses ``navigator_session.get_session(request, new=False)`` and memoizes
    the result on ``request['user_session']`` so subsequent calls within the
    same request are free. Returns ``None`` when navigator_session is
    unavailable or no session exists.

    Args:
        request: The current aiohttp web request.
        logger: Logger used for the "session system not installed" error.
        session_getter: Coroutine function called as
            ``session_getter(request, new=False)``; defaults to
            ``navigator_session.get_session``. ``AbstractHandler`` passes its
            own module-level ``get_session`` so existing patches of
            ``querysource.handlers.abstract.get_session`` keep working.

    Returns:
        SessionData or None.
    """
    cached = request.get('user_session', _SESSION_UNSET)
    if cached is not _SESSION_UNSET:
        return cached
    getter = session_getter if session_getter is not None else get_session
    try:
        session = await getter(request, new=False)
    except RuntimeError:
        logger.error('QS: User Session system is not installed.')
        session = None
    request['user_session'] = session
    return session


async def enforce_request_pbac(
    request: web.Request,
    resource_type: Any,
    resource_name: str,
    action: str,
    *,
    logger: logging.Logger,
    session_resolver: SessionResolver | None = None,
    allow_sessionless: bool | None = None,
) -> None:
    """Evaluate a single PBAC decision; raise web.HTTPNotFound on deny.

    Fast-path no-op when PBAC is not active (``app['security']`` absent).
    Fail-closed: if PBAC is enabled but no session can be extracted, the
    request is denied with 404.

    Args:
        request: The current aiohttp web request.
        resource_type: navigator_auth ResourceType (or string shim value).
        resource_name: The resource identifier string.
        action: The action string, e.g. ``"slug:execute"``.
        logger: Logger for the deny / sessionless-authz info lines.
        session_resolver: Coroutine function ``resolver(request)`` returning
            the session; defaults to :func:`get_request_session`.
            ``AbstractHandler`` passes ``self._get_user_session``.
        allow_sessionless: Overrides ``QS_PBAC_ALLOW_SESSIONLESS_AUTHZ``;
            ``None`` reads the config value.

    Raises:
        web.HTTPNotFound: When the evaluator denies access, or when
            PBAC is enabled but the request has no user session.
    """
    guardian = request.app.get('security')
    if guardian is None:
        return  # PBAC disabled — fast-path no-op

    # Fail-closed: callers must always supply a real resource_name.
    # A ``None`` (or empty) name means the route bound a missing path
    # parameter (e.g. ``slug`` was not in args) — short-circuit to 404
    # instead of feeding ``None`` into navigator-auth, where it could
    # match the wrong policy or raise an internal error.
    if not resource_name:
        logger.info(
            "PBAC denied (missing resource_name): %s action=%s",
            resource_type,
            action,
        )
        raise web.HTTPNotFound()

    if session_resolver is None:
        session = await get_request_session(request, logger=logger)
    else:
        session = await session_resolver(request)
    allow = (
        QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
        if allow_sessionless is None else allow_sessionless
    )
    authz_userinfo = None
    if session is None:
        # Sessionless authorization: requests authorized by a
        # navigator-auth authz backend (IP / host / User-Agent, e.g.
        # authz_allowed_ips, authz_useragent) legitimately carry no user
        # session. When QS_PBAC_ALLOW_SESSIONLESS_AUTHZ is enabled and
        # navigator-auth stamped the request, do NOT bypass PBAC: evaluate
        # it under a synthetic identity whose groups are ``authorized``
        # plus the granting backend name, so explicit allow policies
        # (e.g. policies/authorized.yaml) decide what such clients may do.
        if allow:
            try:
                from navigator_auth.conf import AUTHZ_BACKEND_KEY
            except ImportError:
                AUTHZ_BACKEND_KEY = 'authz_backend'
            authz_backend = request.get(AUTHZ_BACKEND_KEY)
            if authz_backend:
                backend = str(authz_backend)
                authz_userinfo = {
                    'username': f'authz:{backend}',
                    'groups': ['authorized', backend],
                    'roles': [],
                }
                logger.info(
                    "PBAC sessionless authz (backend=%s): evaluating "
                    "%s/%s action=%s as group 'authorized'",
                    backend,
                    resource_type,
                    resource_name,
                    action,
                )
        if authz_userinfo is None:
            # Fail-closed: no session and no sessionless authz → deny
            logger.info(
                "PBAC denied (no session): %s/%s action=%s",
                resource_type,
                resource_name,
                action,
            )
            raise web.HTTPNotFound()

    pbac_enabled, evaluator = resolve_evaluator(request, detached=False)
    if evaluator is None:
        raise web.HTTPNotFound()

    # Lazy-import navigator-auth session constant (only when PBAC is active).
    from navigator_auth.conf import AUTH_SESSION_OBJECT

    if authz_userinfo is not None:
        # Authorized-but-not-authenticated: synthetic identity, no user.
        userinfo = authz_userinfo
        user = None
    else:
        userinfo = (
            session.get(AUTH_SESSION_OBJECT, {})
            if hasattr(session, 'get') else {}
        )
        if not isinstance(userinfo, dict):
            userinfo = {}
        user = userinfo if userinfo else None

    ctx = build_eval_context(
        userinfo=userinfo, user=user, session=session, request=request,
    )

    decision = await evaluate(evaluator, ctx, resource_type, resource_name, action)
    if not decision.allowed:
        logger.info(
            "PBAC denied: %s/%s action=%s policy=%s reason=%s",
            resource_type,
            resource_name,
            action,
            decision.matched_policy,
            decision.reason,
        )
        raise web.HTTPNotFound()
```
**Why this shape**: the bodies are the moved `AbstractHandler` code. The keyword-only injection points
exist only so the delegates can hand over the handler module's patchable names (see Context). Do not
drop them.

### `querysource/handlers/abstract.py` (MODIFY — import)
```python
# occurrences: 1 (verified: grep -c '^from ..auth.enforcement import build_eval_context, evaluate, resolve_evaluator$' querysource/handlers/abstract.py)
# AFTER — insert below `from ..auth.enforcement import build_eval_context, evaluate, resolve_evaluator` (verified: querysource/handlers/abstract.py:11)
from ..auth.request_gate import enforce_request_pbac, get_request_session
```

### `querysource/handlers/abstract.py` (MODIFY — `_get_user_session` + `_enforce_pbac`)
```python
# occurrences: 1 (verified: grep -c '    async def _get_user_session(' querysource/handlers/abstract.py)
# occurrences: 1 (verified: grep -c '    async def _enforce_pbac(' querysource/handlers/abstract.py)
# occurrences: 1 (verified: grep -c '    async def _enforce_owned_slug(' querysource/handlers/abstract.py)
# REPLACE everything from `    async def _get_user_session(` (abstract.py:298) up to, but NOT including,
# `    async def _enforce_owned_slug(` (abstract.py:444) with:
    async def _get_user_session(
        self,
        request: web.Request,
    ) -> Optional[SessionData]:
        """Extract and memoize the user session from the current request.

        Delegates to :func:`querysource.auth.request_gate.get_request_session`
        (FEAT-160), passing this module's ``get_session`` so patches of
        ``querysource.handlers.abstract.get_session`` keep applying.
        Memoizes the result on ``request['user_session']``. Returns ``None``
        when navigator_session is unavailable or no session exists.

        Args:
            request: The current aiohttp web request.

        Returns:
            SessionData or None.
        """
        return await get_request_session(
            request, logger=self.logger, session_getter=get_session,
        )

    async def _enforce_pbac(
        self,
        request: web.Request,
        resource_type,
        resource_name: str,
        action: str,
    ) -> None:
        """Evaluate a single PBAC decision; raise web.HTTPNotFound on deny.

        Delegates to :func:`querysource.auth.request_gate.enforce_request_pbac`
        (FEAT-160). The session comes from ``self._get_user_session`` and the
        sessionless-authz flag from this module's
        ``QS_PBAC_ALLOW_SESSIONLESS_AUTHZ``, both looked up at call time, so
        instance mocks and module patches behave exactly as before.

        Args:
            request: The current aiohttp web request.
            resource_type: navigator_auth ResourceType (or string shim value).
            resource_name: The resource identifier string.
            action: The action string, e.g. ``"slug:execute"``.

        Raises:
            web.HTTPNotFound: When the evaluator denies access, or when
                PBAC is enabled but the request has no user session.
        """
        await enforce_request_pbac(
            request,
            resource_type,
            resource_name,
            action,
            logger=self.logger,
            session_resolver=self._get_user_session,
            allow_sessionless=QS_PBAC_ALLOW_SESSIONLESS_AUTHZ,
        )
```
**Why**: the signatures stay the same. `self._get_user_session` is passed as the resolver, so an
instance-level mock (as in `test_multiquery_pbac_smoke.py`) still decides the session. The module
globals `get_session` / `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` are read at call time, so module patches still apply.

### `tests/test_request_gate.py` (CREATE)
```python
"""FEAT-160 / TASK-827: request-scoped PBAC gate parity with AbstractHandler._enforce_pbac."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from querysource.auth.request_gate import enforce_request_pbac, get_request_session
from querysource.handlers.abstract import AbstractHandler

_GATE = "querysource.auth.request_gate"


class _Handler(AbstractHandler):
    """Test-only subclass (same pattern as tests/handlers/test_abstract_pbac_helpers.py)."""


def _request(app: dict, store: dict | None = None) -> MagicMock:
    """A MagicMock request whose ``get``/``__setitem__`` share a real dict."""
    backing = {} if store is None else store
    req = MagicMock()
    req.app = app
    req.get = lambda k, d=None: backing.get(k, d)
    req.__setitem__ = MagicMock(side_effect=lambda k, v: backing.update({k: v}))
    return req


def _evaluator(allowed: bool) -> MagicMock:
    evaluator = MagicMock()
    evaluator.check_access = MagicMock(
        return_value=MagicMock(allowed=allowed, matched_policy="P", reason="r")
    )
    return evaluator


async def test_get_request_session_memoizes_and_handles_runtime_error():
    getter = AsyncMock(return_value={"username": "alice"})
    req = _request({})
    first = await get_request_session(req, logger=MagicMock(), session_getter=getter)
    second = await get_request_session(req, logger=MagicMock(), session_getter=getter)
    assert first == second == {"username": "alice"}
    assert getter.await_count == 1

    req2 = _request({})
    with patch(f"{_GATE}.get_session", AsyncMock(side_effect=RuntimeError)):
        assert await get_request_session(req2, logger=MagicMock()) is None


async def test_request_gate_parity_noop_when_pbac_disabled():
    await enforce_request_pbac(_request({}), "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_missing_resource_name_404():
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with pytest.raises(web.HTTPNotFound):
        await enforce_request_pbac(req, "slug", "", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_no_session_404():
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(req, "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_allow_and_deny():
    allow_req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        await enforce_request_pbac(
            allow_req, "datasource", "pg_admin", "datasource:use", logger=MagicMock()
        )
    deny_req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(False)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(
                deny_req, "datasource", "pg_admin", "datasource:use", logger=MagicMock()
            )


async def test_request_gate_parity_no_evaluator_404():
    req = _request({"security": MagicMock()})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(req, "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_sessionless_authz():
    captured = {}
    evaluator = MagicMock()

    def capture(ctx, **kwargs):
        captured["ctx"] = ctx
        return MagicMock(allowed=True)

    evaluator.check_access = capture
    req = _request(
        {"security": MagicMock(), "policy_evaluator": evaluator},
        store={"authz_backend": "authz_useragent"},
    )
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        await enforce_request_pbac(
            req, "slug", "x", "slug:execute", logger=MagicMock(), allow_sessionless=True
        )
    assert captured["ctx"].userinfo["username"] == "authz:authz_useragent"
    assert "authorized" in captured["ctx"].userinfo["groups"]

    req_off = _request(
        {"security": MagicMock(), "policy_evaluator": evaluator},
        store={"authz_backend": "authz_useragent"},
    )
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(
                req_off, "slug", "x", "slug:execute", logger=MagicMock(), allow_sessionless=False
            )


async def test_abstract_handler_delegates_to_request_gate():
    handler = _Handler.__new__(_Handler)
    handler.logger = MagicMock()
    handler._get_user_session = AsyncMock(return_value={"username": "alice"})
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(False)})
    with pytest.raises(web.HTTPNotFound):
        await handler._enforce_pbac(req, "slug", "x", "slug:execute")
    handler._get_user_session.assert_awaited_once_with(req)
```
**Why**: this mirrors `tests/handlers/test_abstract_pbac_helpers.py` against the module-level API and adds the delegation check.

### FILL IN checklist
- [ ] None. Every block is complete. If a regression file fails, re-check that the delegate passes all
  three injection kwargs; do not edit the regression tests.

---

## Acceptance Criteria

- [ ] `from querysource.auth.request_gate import enforce_request_pbac, get_request_session` works.
- [ ] `AbstractHandler._enforce_pbac` / `_get_user_session` behaviour is unchanged: every regression file under Validation Commands passes without edits.
- [ ] `tests/test_request_gate.py` passes. It covers PBAC-disabled no-op, missing name → 404, no session → 404, allow, deny → 404, no evaluator → 404, and the sessionless-authz on/off paths.
- [ ] `ruff check querysource/auth/request_gate.py tests/test_request_gate.py` is clean. `querysource/handlers/abstract.py` has no findings other than the pre-existing `B012` at line 86.

## Validation Commands

- `python -m pytest tests/test_request_gate.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_abstract_pbac_helpers.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_multiquery_pbac_smoke.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_queryservice_pbac_smoke.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_queryexecutor_pbac_smoke.py -q -p no:cacheprovider`
- `python -m pytest tests/tenants/test_tenant_policy_preflight.py -q -p no:cacheprovider`
- `python -m pytest tests/test_multiquery_write_gate_http.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_describe_detail.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_describe_list.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_describe_vocabulary.py -q -p no:cacheprovider`

Other files that reference `_enforce_pbac` / `_get_user_session` were verified to exist but are **not**
gates, because they already fail or cannot be collected on `dev` at `71ebae0`, independent of this task:
the integration file `tests/integration/test_pbac_enforcement.py` has 2 failing tests (`TestPbacOff`,
env-dependent). It still exercises `querysource.handlers.abstract.get_session` patching, so check that it
stays at exactly 2 failures. The policy file `tests/policies/test_authorized_policy.py` has 6 failing tests
(`AttributeError: SLUG`, navigator-auth enum). The Airtable file `tests/handlers/test_airtable_oauth.py`
cannot be collected (`aioresponses` is not installed).

---

## Test Specification

See the `tests/test_request_gate.py` block above. `asyncio_mode = auto` (`pytest.ini`), so the tests need no marker.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-scheduler-admin-gate --feature-id FEAT-160`).
   **Environment:** in a fresh worktree, first run `python setup.py build_ext --inplace`, because the Cython
   `.so` files are not versioned. Then run every test from the worktree root with the shared venv, as
   `python -m pytest <file> -q -p no:cacheprovider`.
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: none for this task (`sdd/tasks/index/multi-scheduler-admin-gate.json`).
4. **Verify the Codebase Contract**: re-run the three `grep -c` anchors. If a count is not 1, re-locate the anchor before editing.
5. **Update status** in `sdd/tasks/index/multi-scheduler-admin-gate.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint. Never change a signature or path the blueprint fixes.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-827 multi-scheduler-admin-gate verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Moved _get_user_session/_enforce_pbac bodies into querysource/auth/request_gate.py with injection kwargs; AbstractHandler methods are delegates. All regression files pass; ruff clean except pre-existing B012.

**Deviations from spec**: none
