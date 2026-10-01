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
