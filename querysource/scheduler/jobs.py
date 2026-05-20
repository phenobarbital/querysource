"""Job Definitions for QSScheduler.

Three async callable job types for APScheduler:
- scheduled_query_job: Executes a single-source query by slug on a schedule
  (result discarded).
- scheduled_multiqs_job: Executes a multi-query by slug on a schedule via
  MultiQS (result tuple discarded).
- cache_refresh_job: Executes a single-source query to refresh its cache.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from navconfig.logging import logging

if TYPE_CHECKING:
    from querysource.scheduler.notifications import NotificationManager

logger = logging.getLogger("QSScheduler.Jobs")


async def scheduled_query_job(
    slug: str,
    notification_manager: Optional["NotificationManager"] = None,
    **kwargs
) -> None:
    """Execute a scheduled query by slug. Result is discarded.

    Args:
        slug: The query slug to execute.
        notification_manager: Optional NotificationManager for error reporting.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries.qs import QS
        qs = QS(slug=slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Scheduled job failed for slug '%s': %s", slug, exc
        )
        if notification_manager:
            notification_manager.notify(
                job_id=f"query_{slug}",
                slug=slug,
                error=exc
            )


async def scheduled_multiqs_job(
    slug: str,
    notification_manager: Optional["NotificationManager"] = None,
    **kwargs
) -> None:
    """Execute a scheduled multi-query by slug. Result is discarded.

    Lazy-imports MultiQS and instantiates it with slug only (no request,
    no user_session, no conditions). Awaits ``MultiQS(slug=slug).query()``
    and discards the returned ``(result, options)`` tuple.

    On any exception, calls
    ``notification_manager.notify(job_id=f"multi_{slug}", slug=slug, error=exc)``
    exactly once, then returns without re-raising (mirroring
    ``scheduled_query_job`` APScheduler semantics).

    Reserved JSON sub-key: ``attributes.scheduler.output`` is
    forward-compatible and NOT interpreted in v1. It is parsed by the
    loader at startup (which logs a DEBUG line) but is not passed to
    this callable.

    Currently accepts only ``slug``; condition-based filtering is not yet
    supported.

    Args:
        slug: The multi-query slug to execute.
        notification_manager: Optional NotificationManager for error reporting.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries import MultiQS
        qs = MultiQS(slug=slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Scheduled multi-query job failed for slug '%s': %s", slug, exc
        )
        if notification_manager:
            notification_manager.notify(
                job_id=f"multi_{slug}",
                slug=slug,
                error=exc
            )


async def cache_refresh_job(
    slug: str,
    notification_manager: Optional["NotificationManager"] = None,
    **kwargs
) -> None:
    """Execute a query to refresh its cache.

    Relies on the QS internal pipeline: when ``is_cached=True`` for the
    query slug, ``save_cache`` is called automatically by ``QS.query()``.

    Args:
        slug: The query slug whose cache should be refreshed.
        notification_manager: Optional NotificationManager for error reporting.
        **kwargs: Additional keyword arguments (ignored).
    """
    try:
        from querysource.queries.qs import QS
        qs = QS(slug=slug)
        await qs.query()
    except Exception as exc:
        logger.warning(
            "Cache refresh job failed for slug '%s': %s", slug, exc
        )
        if notification_manager:
            notification_manager.notify(
                job_id=f"cache_{slug}",
                slug=slug,
                error=exc
            )
