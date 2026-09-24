"""Request-optional PBAC evaluation core shared by handlers, describe and QS (FEAT-150)."""
from __future__ import annotations

import copy
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from querysource.auth.pbac import get_pbac_runtime
from querysource.auth.principal import QSPrincipal
from querysource.conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ, QS_PBAC_ENABLED
from querysource.exceptions import QueryAccessDenied

_log = logging.getLogger(__name__)
_WARNED_ABSENT_RUNTIME = False


@dataclass(frozen=True)
class AccessDecision:
    """Outcome of one evaluation. pbac_enabled=False means 'no PBAC configured' (allowed=True)."""

    allowed: bool
    pbac_enabled: bool
    matched_policy: str | None = None
    reason: str | None = None


class _ServiceRequest:
    """Neutral stand-in satisfying EvalContext.__init__ for request-less evaluation.

    Carries no caller-controlled data; navigator-auth 0.26.0 check_access never reads it.
    """

    def __init__(self) -> None:
        from multidict import CIMultiDict, CIMultiDictProxy
        from yarl import URL

        self.remote = None
        self.method = "INTERNAL"
        self.headers = CIMultiDictProxy(CIMultiDict())
        self.path = ""
        self.path_qs = ""
        self.rel_url = URL("")

    def get(self, key: str, default: Any = None) -> Any:
        """Mapping-style access used by request.get(...) callers; always the default."""
        return default


def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]:
    """Return (pbac_enabled, evaluator) from request.app or the process runtime.

    Guardian without evaluator → (True, None) plus an error log. detached=True
    returns a shallow copy with a fresh _cache and copied _stats; the original
    evaluator is never mutated.
    """
    if request is not None:
        guardian = request.app.get("security")
        if guardian is None:
            return (False, None)
        evaluator = request.app.get("policy_evaluator")
    else:
        runtime = get_pbac_runtime()
        if runtime is None or runtime.guardian is None:
            return (False, None)
        evaluator = runtime.evaluator

    if evaluator is None:
        _log.error(
            "PBAC misconfigured: 'security' is set but 'policy_evaluator' is missing"
        )
        return (True, None)

    if detached:
        detached_evaluator = copy.copy(evaluator)
        detached_evaluator._cache = {}
        detached_evaluator._stats = dict(getattr(evaluator, "_stats", {}))
        return (True, detached_evaluator)

    return (True, evaluator)


def build_eval_context(*, userinfo: dict, user: Any, session: Any,
                       request: web.Request | None = None) -> Any:
    """Build a navigator-auth EvalContext.

    request given → EvalContext(request=request, user=, userinfo=, session=) (unchanged handler path).
    request None → EvalContext.from_userinfo(userinfo, user=, session=) when navigator-auth provides
    it, else EvalContext(request=_ServiceRequest(), ...).
    """
    from navigator_auth.abac.context import EvalContext

    if request is not None:
        return EvalContext(request=request, user=user, userinfo=userinfo, session=session)

    if hasattr(EvalContext, "from_userinfo"):
        return EvalContext.from_userinfo(userinfo, user=user, session=session)

    return EvalContext(request=_ServiceRequest(), user=user, userinfo=userinfo, session=session)


async def evaluate(evaluator: Any, ctx: Any, resource_type: Any, resource_name: str,
                   action: str) -> AccessDecision:
    """Run check_access with the iscoroutine guard; any exception or empty name → deny."""
    from navigator_auth.abac.policies.environment import Environment

    if not resource_name:
        return AccessDecision(allowed=False, pbac_enabled=True, reason="missing resource_name")
    try:
        result = evaluator.check_access(
            ctx=ctx,
            resource_type=resource_type,
            resource_name=resource_name,
            action=action,
            env=Environment(),
        )
        if inspect.iscoroutine(result):
            result = await result
    except Exception:  # pylint: disable=W0703
        _log.exception("PBAC evaluator error (fail-closed): %s/%s action=%s",
                         resource_type, resource_name, action)
        return AccessDecision(allowed=False, pbac_enabled=True, reason="evaluator error")
    return AccessDecision(
        allowed=bool(result.allowed),
        pbac_enabled=True,
        matched_policy=getattr(result, "matched_policy", None),
        reason=getattr(result, "reason", None),
    )


async def enforce_principal(principal: QSPrincipal, resource_type: Any, resource_name: str,
                            action: str, *, tenant: str | None = None,
                            logger: logging.Logger | None = None) -> AccessDecision:
    """Principal-path gate (always detached).

    PBAC off → allowed (debug log; one warning per process when QS_PBAC_ENABLED).
    Evaluator missing → QueryAccessDenied. Authz-form principal with PBAC active and
    QS_PBAC_ALLOW_SESSIONLESS_AUTHZ false → QueryAccessDenied; flag on → evaluated with
    user=None. Deny → QueryAccessDenied. Logs principal.log_fields(), tenant selector,
    resource, action, decision, matched policy and reason.
    """
    global _WARNED_ABSENT_RUNTIME
    log = logger or _log
    pbac_enabled, evaluator = resolve_evaluator(None, detached=True)

    if not pbac_enabled:
        if QS_PBAC_ENABLED and not _WARNED_ABSENT_RUNTIME:
            log.warning(
                "QS_PBAC_ENABLED is True but no PBAC runtime is registered in this "
                "process; principal-path checks are no-ops until setup_pbac() runs."
            )
            _WARNED_ABSENT_RUNTIME = True
        log.debug(
            "PBAC no-op (not configured): %s/%s action=%s tenant=%s %s",
            resource_type, resource_name, action, tenant, principal.log_fields(),
        )
        return AccessDecision(allowed=True, pbac_enabled=False)

    if evaluator is None:
        log.error(
            "PBAC denied (misconfigured: evaluator missing): %s/%s action=%s tenant=%s %s",
            resource_type, resource_name, action, tenant, principal.log_fields(),
        )
        raise QueryAccessDenied()

    if principal.is_authz and not QS_PBAC_ALLOW_SESSIONLESS_AUTHZ:
        log.info(
            "PBAC denied (sessionless authz disabled): %s/%s action=%s tenant=%s %s",
            resource_type, resource_name, action, tenant, principal.log_fields(),
        )
        raise QueryAccessDenied()

    userinfo = principal.to_userinfo()
    user = None if principal.is_authz else userinfo

    ctx = build_eval_context(userinfo=userinfo, user=user, session=None, request=None)
    decision = await evaluate(evaluator, ctx, resource_type, resource_name, action)

    if decision.allowed:
        log.info(
            "PBAC allowed: %s/%s action=%s policy=%s tenant=%s %s",
            resource_type, resource_name, action, decision.matched_policy, tenant,
            principal.log_fields(),
        )
    else:
        log.info(
            "PBAC denied: %s/%s action=%s policy=%s reason=%s tenant=%s %s",
            resource_type, resource_name, action, decision.matched_policy, decision.reason,
            tenant, principal.log_fields(),
        )
        raise QueryAccessDenied()

    return decision
