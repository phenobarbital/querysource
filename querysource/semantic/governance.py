"""Semantic governance (FEAT-175 M5): PBAC on slug/fields/datasource, identity, row filters."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..auth._resource_types import ResourceType
from ..auth.enforcement import AccessDecision, build_eval_context, evaluate, resolve_evaluator
from ..auth.principal import QSPrincipal
from ..exceptions import QueryAccessDenied
from .exceptions import SemanticFieldDenied
from .models import TIME_GRAINS, SemanticFilter, SemanticQuery, SemanticResource


def principal_from_session(session: Any | None) -> QSPrincipal | None:
    """Convert the navigator-auth session user into an HTTP ``QSPrincipal``."""
    if session is None or not hasattr(session, "get"):
        return None

    try:
        from navigator_auth.conf import AUTH_SESSION_OBJECT
    except ImportError:
        AUTH_SESSION_OBJECT = "user"

    userinfo = session.get(AUTH_SESSION_OBJECT, session.get("user", {}))
    if not isinstance(userinfo, dict):
        return None
    user_id = userinfo.get("user_id")
    if user_id is None or not str(user_id).strip():
        return None
    return QSPrincipal(
        user_id=str(user_id),
        username=userinfo.get("username"),
        groups=userinfo.get("groups") or (),
        roles=userinfo.get("roles") or (),
        programs=userinfo.get("programs") or (),
        superuser=bool(userinfo.get("superuser", False)),
        tenant_id=userinfo.get("tenant_id"),
        channel="http",
    )


def user_attributes(principal: QSPrincipal | None) -> dict[str, Any] | None:
    """Return evaluator user attributes for a principal, including its tenant."""
    if principal is None:
        return None
    return {**principal.to_userinfo(), "tenant_id": principal.tenant_id}


async def check(
    request: web.Request | None,
    principal: QSPrincipal | None,
    resource_name: str,
    action: str,
    *,
    logger: logging.Logger,
    resource_type: Any = ResourceType.SEMANTIC,
) -> AccessDecision:
    """Evaluate one PBAC resource, allowing all when PBAC is disabled."""
    enabled, evaluator = resolve_evaluator(request, detached=request is None)
    if not enabled:
        return AccessDecision(allowed=True, pbac_enabled=False)
    if principal is None:
        decision = AccessDecision(allowed=False, pbac_enabled=True, reason="no principal")
        logger.debug("PBAC denied: %s/%s action=%s reason=%s", resource_type, resource_name, action, decision.reason)
        return decision

    ctx = build_eval_context(
        userinfo=principal.to_userinfo(),
        user=principal.to_userinfo(),
        session=None,
        request=request,
    )
    decision = await evaluate(evaluator, ctx, resource_type, resource_name, action)
    logger.debug(
        "PBAC decision: %s/%s action=%s allowed=%s policy=%s reason=%s",
        resource_type,
        resource_name,
        action,
        decision.allowed,
        decision.matched_policy,
        decision.reason,
    )
    return decision


async def enforce_query(
    request: web.Request | None,
    principal: QSPrincipal | None,
    slug: str,
    query: SemanticQuery,
    *,
    logger: logging.Logger,
) -> None:
    """Enforce semantic execution on a slug and use access on each requested field."""
    decision = await check(request, principal, slug, "semantic:execute", logger=logger)
    if not decision.allowed:
        raise QueryAccessDenied()

    metrics, dimensions = referenced_fields(query)
    for metric in metrics:
        decision = await check(request, principal, f"{slug}:metric:{metric}", "semantic:use", logger=logger)
        if not decision.allowed:
            raise SemanticFieldDenied(metric)
    for dimension in dimensions:
        decision = await check(request, principal, f"{slug}:dimension:{dimension}", "semantic:use", logger=logger)
        if not decision.allowed:
            raise SemanticFieldDenied(dimension)


def _bare_name(name: str) -> str:
    """Return the canonical PBAC field name: no model prefix and no ``__grain`` suffix."""
    bare = name.strip().rsplit(".", 1)[-1]
    base, sep, grain = bare.rpartition("__")
    return base if sep and grain in TIME_GRAINS else bare


def referenced_fields(query: SemanticQuery) -> tuple[list[str], list[str]]:
    """Return canonical ``(metrics, dimensions)`` referenced anywhere in the request.

    Covers requested metrics and dimensions, filter fields (structured and string) and
    ``order_by`` entries, so a denied field cannot be used to filter or sort. An
    ``order_by`` entry may name either kind, so it is checked as both.
    """
    metrics = [_bare_name(name) for name in query.metrics]
    dimensions = [_bare_name(name) for name in query.dimensions]
    for flt in query.filters:
        if isinstance(flt, SemanticFilter):
            dimensions.append(_bare_name(flt.field))
            continue
        try:
            import sqlglot  # noqa: PLC0415
            from sqlglot import exp  # noqa: PLC0415

            for column in sqlglot.parse_one(flt).find_all(exp.Column):
                dimensions.append(_bare_name(column.sql()))
        except Exception:  # noqa: BLE001 - the compiler rejects unparsable filters with a 400
            continue
    for entry in query.order_by:
        head = _bare_name(entry.strip().split(" ", 1)[0])
        metrics.append(head)
        dimensions.append(head)
    return list(dict.fromkeys(metrics)), list(dict.fromkeys(dimensions))


async def enforce_datasource(
    request: web.Request | None,
    principal: QSPrincipal | None,
    datasource: str,
    driver: str,
    *,
    logger: logging.Logger,
) -> None:
    """Enforce use access on the resolved datasource and its driver."""
    decision = await check(
        request,
        principal,
        datasource,
        "datasource:use",
        logger=logger,
        resource_type=ResourceType.DATASOURCE,
    )
    if not decision.allowed:
        raise QueryAccessDenied()
    decision = await check(
        request,
        principal,
        driver,
        "driver:use",
        logger=logger,
        resource_type=ResourceType.DRIVER,
    )
    if not decision.allowed:
        raise QueryAccessDenied()


def governance_filters(
    resource: SemanticResource, principal: QSPrincipal | None
) -> tuple[list[SemanticFilter | str], list[str]]:
    """Return default filters and role filters in their resource-defined order."""
    row_filters = resource.governance.row_filters
    roles = set(principal.roles) if principal is not None else set()
    filters: list[SemanticFilter | str] = []
    matched_roles: list[str] = []
    for key, block in row_filters.items():
        if key == "default" or key in roles:
            filters.extend(block)
            if key != "default":
                matched_roles.append(key)
    return filters, matched_roles
