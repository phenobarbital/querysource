"""Job Definitions for QSScheduler.

Three async callable job types for APScheduler:
- scheduled_query_job: Executes a single-source query by slug on a schedule
  (result discarded).
- scheduled_multiqs_job: Executes a multi-query by slug on a schedule via
  MultiQS (result tuple discarded).
- cache_refresh_job: Executes a single-source query to refresh its cache.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navconfig.logging import logging

from querysource.ownership_logging import ownership_fields
from querysource.tenant_errors import TenantError

if TYPE_CHECKING:
    from querysource.scheduler.notifications import NotificationManager
    from querysource.tenants import TenantOwnerEnvelope

logger = logging.getLogger("QSScheduler.Jobs")


async def _revalidate_owner(query_obj: Any, owner: "TenantOwnerEnvelope", slug: str) -> None:
    """Re-resolve the envelope's owner against the CURRENT registry before executing.

    The envelope was captured at scheduler startup / registration time; the
    registry/allowlist configuration can differ by the time the job actually
    runs (e.g. after a restart with a changed allowlist) — so the stale
    envelope alone is never trusted for execution (TASK-730 AC-1).

    Args:
        query_obj: A constructed QS/MultiQS instance (Connection subclass),
            used only to reach ``get_definition_repository()``.
        owner: The envelope captured when this job was (re)registered.
        slug: The query slug, for error messages only.

    Raises:
        TenantError: If the owner's tenant is no longer available, or the
            registry now resolves it to a materially different physical
            store than the one this job was scheduled against.
    """
    repo = await query_obj.get_definition_repository()
    current_store = repo.registry.resolve(owner.get("schema"))
    if (
        current_store.database_namespace != owner.get("database_namespace")
        or current_store.table != owner.get("table")
        or current_store.contract != owner.get("contract")
    ):
        raise TenantError(
            f"Scheduled job owner envelope for slug {slug!r} no longer "
            f"matches the current registry (schema={owner.get('schema')!r}); "
            "registry/allowlist configuration must have changed since this "
            "job was registered.",
            error_code="tenant_not_available",
        )


async def scheduled_query_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    **kwargs: Any
) -> None:
    """Revalidate owner and execute QS with matching runtime/cache context.

    Args:
        slug: The query slug to execute.
        notification_manager: Optional NotificationManager for error reporting.
        owner: Optional TenantOwnerEnvelope for tenant ownership validation.
            None preserves the pre-TASK-730 legacy/default behavior exactly.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries.qs import QS
        tenant = owner.get("schema") if owner is not None else None
        qs = QS(slug=slug, tenant=tenant)
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Scheduled job failed for slug '%s' (%s): %s",
            slug, ownership_fields(owner), exc,
        )
        if notification_manager:
            # notify(job_id, slug, error) signature preserved unchanged
            # (TASK-731 AC-4) — ownership only enriches the log line above.
            notification_manager.notify(
                job_id=f"query_{slug}",
                slug=slug,
                error=exc
            )


async def scheduled_multiqs_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    **kwargs: Any
) -> None:
    """Revalidate owner and preserve it through all pipeline children.

    Lazy-imports MultiQS and instantiates it with slug only (no request,
    no user_session, no conditions). Awaits ``MultiQS(slug=slug).query()``
    and discards the returned ``(result, options)`` tuple. The resolved
    ``tenant`` is threaded straight into MultiQS's own ``_tenant_selector``
    (TASK-727's parent-inheritance resolution then applies to every child
    query in the pipeline, exactly as it would for a request-driven call).

    On any exception, calls
    ``notification_manager.notify(job_id=f"multi_{slug}", slug=slug, error=exc)``
    exactly once, then returns without re-raising (mirroring
    ``scheduled_query_job`` APScheduler semantics).

    Reserved JSON sub-key: ``attributes.scheduler.output`` is
    forward-compatible and NOT interpreted in v1. It is parsed by the
    loader at startup (which logs a DEBUG line) but is not passed to
    this callable.

    Args:
        slug: The multi-query slug to execute.
        notification_manager: Optional NotificationManager for error reporting.
        owner: Optional TenantOwnerEnvelope for tenant ownership validation.
            None preserves the pre-TASK-730 legacy/default behavior exactly.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries import MultiQS
        tenant = owner.get("schema") if owner is not None else None
        qs = MultiQS(slug=slug, tenant=tenant)
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Scheduled multi-query job failed for slug '%s' (%s): %s",
            slug, ownership_fields(owner), exc,
        )
        if notification_manager:
            # notify(job_id, slug, error) signature preserved unchanged
            # (TASK-731 AC-4) — ownership only enriches the log line above.
            notification_manager.notify(
                job_id=f"multi_{slug}",
                slug=slug,
                error=exc
            )


async def cache_refresh_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    **kwargs: Any
) -> None:
    """Refresh only this owner's current definition revision.

    Relies on the QS internal pipeline: when ``is_cached=True`` for the
    query slug, ``save_cache`` is called automatically by ``QS.query()``.
    Revalidating the owner before executing (see ``_revalidate_owner``)
    guarantees the refresh always targets the CURRENT definition revision
    for this exact owner, never a stale/reassigned one.

    Args:
        slug: The query slug whose cache should be refreshed.
        notification_manager: Optional NotificationManager for error reporting.
        owner: Optional TenantOwnerEnvelope for tenant ownership validation.
            None preserves the pre-TASK-730 legacy/default behavior exactly.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries.qs import QS
        tenant = owner.get("schema") if owner is not None else None
        qs = QS(slug=slug, tenant=tenant)
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Cache refresh job failed for slug '%s' (%s): %s",
            slug, ownership_fields(owner), exc,
        )
        if notification_manager:
            # notify(job_id, slug, error) signature preserved unchanged
            # (TASK-731 AC-4) — ownership only enriches the log line above.
            notification_manager.notify(
                job_id=f"cache_{slug}",
                slug=slug,
                error=exc
            )
