"""Slug visibility for the describe API (FEAT-148).

Principal resolution, program pre-filter predicates and fail-closed ABAC checks.
Mirrors ``AbstractHandler._enforce_pbac`` (handlers/abstract.py:317) without modifying it.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aiohttp import web

from ..conf import QS_DESCRIBE_ADMIN_GROUPS, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
from ..models import QueryModel
from ..queries.describe import DescribeGrants
from ._resource_types import ResourceType

logger = logging.getLogger(__name__)


class PrincipalKind(str, Enum):
    """Caller classification (spec §2 principal table)."""

    SUPERUSER = "superuser"
    PROGRAMS = "programs"
    AUTHZ = "authz"
    NO_PROGRAMS = "no_programs"
    NONE = "none"


@dataclass(frozen=True)
class Principal:
    """Resolved caller identity for describe checks."""

    kind: PrincipalKind
    userinfo: dict = field(default_factory=dict)
    groups: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    session: Any = None


@dataclass(frozen=True)
class DescribeStore:
    """Definitions table addressed by the describe API (NOT FEAT-147's QueryStore)."""

    schema: str
    table: str
    has_program_slug: bool = True
    tenant: str | None = None
    loader: Callable[[Any, str], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class ProgramPredicate:
    """SQL pre-filter fragment + bound args, or deny-all."""

    deny_all: bool = False
    sql: str = ""
    args: tuple = ()


def normalize_programs(raw: Any) -> tuple[str, ...]:
    """Program objects/dicts/strings → sorted, lowercase, de-duplicated slugs."""
    if raw is None:
        return ()
    
    # Convert to list if it's not already
    if isinstance(raw, (str, dict)):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [raw]
    
    result = []
    for item in items:
        if item is None:
            continue
        if hasattr(item, 'slug'):
            slug = item.slug
        elif hasattr(item, 'name'):
            slug = item.name
        elif isinstance(item, dict):
            slug = item.get('slug') or item.get('name')
        else:
            slug = str(item)

        if slug:
            result.append(slug.lower().strip())
    
    # Remove duplicates and sort
    return tuple(sorted(set(filter(None, result))))


async def resolve_principal(request: web.Request, session: Any | None) -> Principal:
    """Classify the caller; never raises (see spec §2 principal table)."""
    # Sessionless authorization branch
    if session is None:
        if QS_PBAC_ALLOW_SESSIONLESS_AUTHZ:
            try:
                from navigator_auth.conf import AUTHZ_BACKEND_KEY
            except ImportError:
                AUTHZ_BACKEND_KEY = 'authz_backend'
            
            authz_backend = request.get(AUTHZ_BACKEND_KEY)
            if authz_backend:
                backend = str(authz_backend)
                userinfo = {
                    'username': f'authz:{backend}',
                    'groups': ['authorized', backend],
                    'roles': [],
                }
                groups = tuple(str(g).lower() for g in userinfo.get('groups', []))
                return Principal(
                    kind=PrincipalKind.AUTHZ,
                    userinfo=userinfo,
                    groups=groups,
                    session=None
                )
        return Principal(kind=PrincipalKind.NONE, session=None)
    
    # Extract userinfo from session
    try:
        from navigator_auth.conf import AUTH_SESSION_OBJECT
    except ImportError:
        AUTH_SESSION_OBJECT = 'user'
    
    userinfo = session.get(AUTH_SESSION_OBJECT, {}) if hasattr(session, 'get') else {}
    if not isinstance(userinfo, dict):
        userinfo = {}
    
    # Check for superuser
    if userinfo.get('superuser') is True:
        groups = tuple(str(g).lower() for g in userinfo.get('groups', []))
        programs = normalize_programs(userinfo.get('programs'))
        return Principal(
            kind=PrincipalKind.SUPERUSER,
            userinfo=userinfo,
            groups=groups,
            programs=programs,
            session=session
        )
    
    # Check for programs
    programs = normalize_programs(userinfo.get('programs'))
    if programs:
        groups = tuple(str(g).lower() for g in userinfo.get('groups', []))
        return Principal(
            kind=PrincipalKind.PROGRAMS,
            userinfo=userinfo,
            groups=groups,
            programs=programs,
            session=session
        )
    
    # No programs
    groups = tuple(str(g).lower() for g in userinfo.get('groups', []))
    return Principal(
        kind=PrincipalKind.NO_PROGRAMS,
        userinfo=userinfo,
        groups=groups,
        session=session
    )


async def _legacy_loader(conn: Any, slug: str) -> QueryModel:
    """Load a legacy definition on an already-acquired connection (never mutates Meta)."""
    return await QueryModel.get(query_slug=slug, _connection=conn)


def legacy_store() -> DescribeStore:
    """Store over ``QueryModel.Meta.schema``/``.name`` (models.py:101-107)."""
    return DescribeStore(
        schema=QueryModel.Meta.schema, table=QueryModel.Meta.name,
        has_program_slug=True, tenant=None, loader=_legacy_loader,
    )


def build_program_predicate(
    principal: Principal, store: DescribeStore, param_index: int = 1
) -> ProgramPredicate:
    """Program pre-filter predicate (spec §2); bound args only, never interpolated values."""
    # Deny all for NONE or NO_PROGRAMS principals
    if principal.kind in (PrincipalKind.NONE, PrincipalKind.NO_PROGRAMS):
        return ProgramPredicate(deny_all=True)
    
    # Allow all for SUPERUSER or AUTHZ principals
    if principal.kind in (PrincipalKind.SUPERUSER, PrincipalKind.AUTHZ):
        return ProgramPredicate()
    
    # PROGRAMS principal
    if principal.kind == PrincipalKind.PROGRAMS:
        if store.has_program_slug:
            # Include 'default' in the programs list
            programs_with_default = sorted(set(principal.programs) | {"default"})
            sql = f'lower("program_slug") = ANY(${param_index}::text[])'
            return ProgramPredicate(sql=sql, args=(programs_with_default,))
        else:
            # Tenant mode: deny if tenant not in programs
            tenant_lower = (store.tenant or "").lower()
            deny_all = tenant_lower not in principal.programs
            return ProgramPredicate(deny_all=deny_all)
    
    # Should not reach here, but default to deny
    return ProgramPredicate(deny_all=True)


def is_admin(principal: Principal) -> bool:
    """Superuser OR a session group in QS_DESCRIBE_ADMIN_GROUPS."""
    if principal.kind == PrincipalKind.SUPERUSER:
        return True

    # Defensive lower-casing: Principal.groups and QS_DESCRIBE_ADMIN_GROUPS are
    # both already normalized to lowercase by their respective constructors
    # (resolve_principal, TASK-735's config parsing), but this check does not
    # rely on that discipline from every caller.
    admin_groups = {str(g).lower() for g in QS_DESCRIBE_ADMIN_GROUPS}
    principal_groups = {str(g).lower() for g in principal.groups}

    return bool(admin_groups & principal_groups)


def _evaluator_state(request: web.Request, *, detached: bool = False) -> tuple[bool, Any]:
    """Return (pbac_enabled, evaluator); logs an error when guardian is set without evaluator.

    Args:
        detached: When True and an evaluator is available, return a shallow
            copy with a cleared decision cache instead of the shared app
            evaluator — mirrors ``AbstractHandler._enforce_owned_slug``
            (querysource/handlers/abstract.py), so tenant-store ABAC
            decisions never read or write the app evaluator's cache (a
            cached decision for one tenant's slug must never leak into
            another tenant's identically-named slug).
    """
    guardian = request.app.get('security')
    if guardian is None:
        return (False, None)

    evaluator = request.app.get('policy_evaluator')
    if evaluator is None:
        logger.error(
            "PBAC misconfigured: 'security' is set but 'policy_evaluator' is missing"
        )
        return (True, None)

    if detached:
        import copy
        detached_evaluator = copy.copy(evaluator)
        detached_evaluator._cache = {}
        detached_evaluator._stats = dict(getattr(evaluator, "_stats", {}))
        return (True, detached_evaluator)

    return (True, evaluator)


def _eval_context(request: web.Request, principal: Principal) -> Any:
    """EvalContext exactly as _enforce_pbac builds it (abstract.py:405-425)."""
    from navigator_auth.abac.context import EvalContext
    
    if principal.kind == PrincipalKind.AUTHZ:
        # Authorized-but-not-authenticated: synthetic identity, no user
        return EvalContext(
            request=request,
            user=None,
            userinfo=principal.userinfo,
            session=None,
        )
    else:
        # Regular authenticated user
        user = principal.userinfo if principal.userinfo else None
        return EvalContext(
            request=request,
            user=user,
            userinfo=principal.userinfo,
            session=principal.session,
        )


async def filter_visible(
    request: web.Request, principal: Principal, slugs: Iterable[str],
    primary_action: str, fallback_action: str | None = None,
    *, detached: bool = False,
) -> list[str]:
    """Order-preserving subset allowed by primary OR fallback; fail-closed; allow-all when PBAC disabled.

    Args:
        detached: Pass True for tenant-store callers — see :func:`_evaluator_state`.
    """
    pbac_enabled, evaluator = _evaluator_state(request, detached=detached)
    
    # PBAC disabled - allow all
    if not pbac_enabled:
        return list(slugs)
    
    # PBAC enabled but no evaluator - deny all
    if evaluator is None:
        return []
    
    # Empty slugs - return empty list
    slugs_list = list(slugs)
    if not slugs_list:
        return []
    
    try:
        from navigator_auth.abac.policies.environment import Environment
        
        # Build evaluation context
        ctx = _eval_context(request, principal)
        
        # Primary action check
        primary_result = evaluator.filter_resources(
            ctx=ctx,
            resource_type=ResourceType.SLUG,
            resource_names=slugs_list,
            action=primary_action,
            env=Environment(),
        )
        
        # Await if it's a coroutine
        if inspect.iscoroutine(primary_result):
            primary_result = await primary_result
        
        allowed_set = set(primary_result.allowed)
        
        # Fallback action check only on denied items
        if fallback_action and primary_result.denied:
            fallback_result = evaluator.filter_resources(
                ctx=ctx,
                resource_type=ResourceType.SLUG,
                resource_names=primary_result.denied,
                action=fallback_action,
                env=Environment(),
            )
            
            # Await if it's a coroutine
            if inspect.iscoroutine(fallback_result):
                fallback_result = await fallback_result
            
            allowed_set.update(fallback_result.allowed)
        
        # Preserve original order
        return [slug for slug in slugs_list if slug in allowed_set]
        
    except Exception:
        logger.exception("Error in filter_visible")
        return []


async def can_access(
    request: web.Request, principal: Principal, slug: str,
    primary_action: str, fallback_action: str | None = None,
    *, detached: bool = False,
) -> bool:
    """Non-raising single-slug check; same semantics as filter_visible.

    Args:
        detached: Pass True for tenant-store callers — see :func:`_evaluator_state`.
    """
    pbac_enabled, evaluator = _evaluator_state(request, detached=detached)
    
    # PBAC disabled - allow
    if not pbac_enabled:
        return True
    
    # PBAC enabled but no evaluator - deny
    if evaluator is None:
        return False
    
    try:
        from navigator_auth.abac.policies.environment import Environment
        
        # Build evaluation context
        ctx = _eval_context(request, principal)
        
        # Primary action check
        primary_result = evaluator.check_access(
            ctx=ctx,
            resource_type=ResourceType.SLUG,
            resource_name=slug,
            action=primary_action,
            env=Environment(),
        )
        
        # Await if it's a coroutine
        if inspect.iscoroutine(primary_result):
            primary_result = await primary_result
        
        # If allowed, return True
        if primary_result.allowed:
            return True
        
        # If fallback action is provided, try it
        if fallback_action:
            fallback_result = evaluator.check_access(
                ctx=ctx,
                resource_type=ResourceType.SLUG,
                resource_name=slug,
                action=fallback_action,
                env=Environment(),
            )
            
            # Await if it's a coroutine
            if inspect.iscoroutine(fallback_result):
                fallback_result = await fallback_result
            
            return fallback_result.allowed
        
        # Neither primary nor fallback allowed
        return False
        
    except Exception:
        logger.exception("Error in can_access")
        return False


async def describe_grants(
    request: web.Request, principal: Principal, slug: str, *, detached: bool = False,
) -> DescribeGrants:
    """raw = slug:describe_raw (no fallback, raw_query:execute never implies it); admin = is_admin.

    Args:
        detached: Pass True for tenant-store callers — see :func:`_evaluator_state`.
    """
    return DescribeGrants(
        raw=await can_access(request, principal, slug, "slug:describe_raw", detached=detached),
        admin=is_admin(principal),
    )


async def tenant_store(request: web.Request, tenant: str) -> DescribeStore | None:
    """DescribeStore for a registered FEAT-147 tenant; None when not available.

    Tenant names are exact (case-sensitive, not trimmed). Program context is the schema name.
    """
    from querysource.tenant_errors import TenantError
    from querysource.tenants import QueryIdentity

    registry = request.app.get("qs_tenant_registry")
    repository = request.app.get("qs_definition_repository")

    if registry is None or repository is None:
        return None

    try:
        # Resolve the tenant store using FEAT-147's registry. TenantRegistry.resolve()
        # raises TenantError (error_code="tenant_not_available") for an unknown tenant —
        # caught narrowly here so a genuine bug in this function surfaces instead of
        # silently degrading to a 404.
        store = registry.resolve(tenant=tenant)
    except TenantError:
        return None

    # Loader wraps DefinitionRepository.get(QueryIdentity(store, slug)).runtime
    async def tenant_loader(conn: Any, slug: str) -> Any:
        """Load a tenant definition via repository."""
        identity = QueryIdentity(store=store, slug=slug)
        loaded = await repository.get(identity)
        return loaded.runtime

    # Build DescribeStore: tenant-contract stores have no program_slug column
    return DescribeStore(
        schema=store.schema,
        table=store.table,
        has_program_slug=False,
        tenant=store.schema,  # Program context is the schema name (tenant)
        loader=tenant_loader,
    )