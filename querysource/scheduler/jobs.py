"""Job Definitions for QSScheduler.

Two async callable job types for APScheduler:
- scheduled_query_job: Executes a query by slug on a schedule (result discarded).
- cache_refresh_job: Executes a query to refresh its cache.
"""
from navconfig.logging import logging

logger = logging.getLogger("QSScheduler.Jobs")


async def scheduled_query_job(
    slug: str,
    notification_manager=None,
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
            f"Scheduled job failed for slug '{slug}': {exc}"
        )
        if notification_manager:
            notification_manager.notify(
                job_id=f"query_{slug}",
                slug=slug,
                error=exc
            )


async def cache_refresh_job(
    slug: str,
    notification_manager=None,
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
            f"Cache refresh job failed for slug '{slug}': {exc}"
        )
        if notification_manager:
            notification_manager.notify(
                job_id=f"cache_{slug}",
                slug=slug,
                error=exc
            )
