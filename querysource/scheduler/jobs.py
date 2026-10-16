"""Job Definitions for QSScheduler.

Three async callable job types for APScheduler:
- scheduled_query_job: Executes a single-source query by slug on a schedule
  (result discarded).
- scheduled_multiqs_job: Executes a multi-query by slug on a schedule via
  MultiQS (result tuple discarded).
- cache_refresh_job: Executes a single-source query to refresh its cache.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from navconfig.logging import logging

from querysource.ownership_logging import ownership_fields
from querysource.tenant_errors import TenantError

if TYPE_CHECKING:
    from querysource.scheduler.handoff import ScheduleHandoffCoordinator
    from querysource.scheduler.notifications import NotificationManager
    from querysource.tenants import TenantOwnerEnvelope

logger = logging.getLogger("QSScheduler.Jobs")


def _owner_selector(owner: TenantOwnerEnvelope | None) -> str | None:
    """Return the tenant selector for an owner envelope by PHYSICAL default identity.

    ``None`` only for the exact configured default (``QueryModel.Meta`` schema and
    table, the same configuration the registry resolves). Literal ``public.queries``
    next to a custom default keeps the selector ``"public"``; owner.schema alone
    never identifies a configured custom table.
    """
    if owner is None:
        return None
    from querysource.models import QueryModel

    schema = owner.get("schema")
    if schema == QueryModel.Meta.schema and owner.get("table") == QueryModel.Meta.name:
        return None
    return schema


async def _revalidate_owner(query_obj: Any, owner: TenantOwnerEnvelope, slug: str) -> None:
    """Re-resolve the envelope's owner against the CURRENT registry before executing.

    The envelope was captured at scheduler startup / registration time; the
    registry/allowlist configuration can differ by the time the job actually
    runs (e.g. after a restart with a changed allowlist) — so the stale
    envelope alone is never trusted for execution (TASK-730 AC-1). The selector
    comes from physical default identity, never from ``owner.schema`` alone.

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
    current_store = repo.registry.resolve(_owner_selector(owner))
    if (
        current_store.database_namespace != owner.get("database_namespace")
        or current_store.schema != owner.get("schema")
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


async def _fenced(
    *,
    label: str,
    slug: str,
    owner: TenantOwnerEnvelope | None,
    job_id: str,
    notification_manager: NotificationManager | None,
    coordinator: ScheduleHandoffCoordinator | None,
    logical_schedule_id: str | None,
    generation: int | None,
    execute: Callable[[], Awaitable[None]],
) -> None:
    """Admit through the shared generation gate BEFORE building any QS/MultiQS, then run.

    Without a ``coordinator`` the job runs exactly as before (uncoordinated
    registration). With one, a stale/frozen/paused/duplicate occurrence is skipped
    without any business execution, and the shared owner lock is held until the
    query/pipeline (including awaited remote work) finishes. Losing the metadata
    service fails closed (skip + notify); an admitted run is never replayed, and a
    cancelled/interrupted run is recorded as ``unknown`` for operator reconciliation.
    """
    run = None
    if coordinator is not None:
        try:
            run = await coordinator.admit(str(logical_schedule_id), int(generation or 0))
        except Exception as exc:  # noqa: BLE001 - fail closed: never run unfenced
            logger.warning(
                "%s admission failed for slug '%s' (%s): %s", label, slug, ownership_fields(owner), exc
            )
            if notification_manager:
                notification_manager.notify(job_id=job_id, slug=slug, error=exc)
            return
        if run is None:
            logger.debug("%s for slug '%s' not admitted (stale, frozen, paused or already claimed)", label, slug)
            return
    outcome, error = "completed", None
    try:
        if run is not None:
            await run.mark_running()
        await execute()
    except asyncio.CancelledError:
        outcome, error = "unknown", "interrupted before the outcome was established"
        raise
    except Exception as exc:
        outcome, error = "failed", str(exc)
        logger.warning(
            "%s failed for slug '%s' (%s): %s",
            label, slug, ownership_fields(owner), exc,
        )
        if notification_manager:
            notification_manager.notify(
                job_id=job_id,
                slug=slug,
                error=exc
            )
    finally:
        if run is not None:
            await run.finish(outcome, error)


async def scheduled_query_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    job_id: str | None = None,
    coordinator: ScheduleHandoffCoordinator | None = None,
    logical_schedule_id: str | None = None,
    generation: int | None = None,
    **kwargs: Any
) -> None:
    """Revalidate owner and execute QS with matching runtime/cache context.

    Args:
        slug: The query slug to execute.
        notification_manager: Optional NotificationManager for error reporting.
        owner: Optional TenantOwnerEnvelope for tenant ownership validation.
            None preserves the pre-TASK-730 legacy/default behavior exactly.
        job_id: The job's own real registered scheduler id, threaded in by
            QSScheduler at registration time. A non-default (tenant) store
            registers its job under the qualified ``qsj2-...`` id, not the
            legacy ``query_<slug>`` shape — falls back to that legacy guess
            only when the caller (e.g. an older registration path) never
            supplied it.
        coordinator: Shared handoff coordinator; when given the job is admitted
            through the durable generation gate before any QS is built.
        logical_schedule_id: Durable schedule identity used for admission.
        generation: The schedule generation this mirror was registered for.
        **kwargs: Additional keyword arguments (ignored).
    """
    async def execute() -> None:
        from querysource.queries.qs import QS
        qs = QS(slug=slug, tenant=_owner_selector(owner))
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()

    await _fenced(
        label="Scheduled job", slug=slug, owner=owner, job_id=job_id or f"query_{slug}",
        notification_manager=notification_manager, coordinator=coordinator,
        logical_schedule_id=logical_schedule_id, generation=generation, execute=execute,
    )


async def scheduled_multiqs_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    job_id: str | None = None,
    run_as_user_id: int | None = None,
    identity_auth: Any = None,
    coordinator: ScheduleHandoffCoordinator | None = None,
    logical_schedule_id: str | None = None,
    generation: int | None = None,
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
    ``notification_manager.notify(job_id=<real registered id>, slug=slug,
    error=exc)`` exactly once, then returns without re-raising (mirroring
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
        job_id: The job's own real registered scheduler id — see
            ``scheduled_query_job``'s docstring for why this must not be
            re-guessed as the legacy ``multi_<slug>`` shape.
        run_as_user_id: Stored user id for sessionless delegated sources.
        identity_auth: Auth handler used to resolve the delegated identity.
        coordinator: Shared handoff coordinator (durable admission, see
            ``scheduled_query_job``).
        logical_schedule_id: Durable schedule identity used for admission.
        generation: The schedule generation this mirror was registered for.
        **kwargs: Additional keyword arguments (ignored).
    """
    async def execute() -> None:
        from querysource.queries import MultiQS
        tenant = _owner_selector(owner)
        if run_as_user_id is not None:
            from querysource.auth.identity_tokens import SourceIdentityContext

            qs = MultiQS(
                slug=slug,
                tenant=tenant,
                identity_context=SourceIdentityContext.for_scheduler(run_as_user_id, identity_auth),
            )
        else:
            qs = MultiQS(slug=slug, tenant=tenant)
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()

    await _fenced(
        label="Scheduled multi-query job", slug=slug, owner=owner, job_id=job_id or f"multi_{slug}",
        notification_manager=notification_manager, coordinator=coordinator,
        logical_schedule_id=logical_schedule_id, generation=generation, execute=execute,
    )


async def cache_refresh_job(
    slug: str,
    notification_manager: NotificationManager | None = None,
    *,
    owner: TenantOwnerEnvelope | None = None,
    job_id: str | None = None,
    coordinator: ScheduleHandoffCoordinator | None = None,
    logical_schedule_id: str | None = None,
    generation: int | None = None,
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
        job_id: The job's own real registered scheduler id — see
            ``scheduled_query_job``'s docstring for why this must not be
            re-guessed as the legacy ``cache_<slug>`` shape.
        coordinator: Shared handoff coordinator (durable admission, see
            ``scheduled_query_job``).
        logical_schedule_id: Durable schedule identity used for admission.
        generation: The schedule generation this mirror was registered for.
        **kwargs: Additional keyword arguments (ignored).
    """
    async def execute() -> None:
        from querysource.queries.qs import QS
        qs = QS(slug=slug, tenant=_owner_selector(owner))
        if owner is not None:
            await _revalidate_owner(qs, owner, slug)
        await qs.query()

    await _fenced(
        label="Cache refresh job", slug=slug, owner=owner, job_id=job_id or f"cache_{slug}",
        notification_manager=notification_manager, coordinator=coordinator,
        logical_schedule_id=logical_schedule_id, generation=generation, execute=execute,
    )
