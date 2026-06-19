"""QSScheduler Core — Embedded APScheduler for QuerySource.

Creates scheduled jobs from public.queries definitions.
Gated behind ENABLE_QS_SCHEDULER config flag.

Job routing:
    - provider='multi'  -> scheduled_multiqs_job (id: multi_<slug>)
    - otherwise         -> scheduled_query_job   (id: query_<slug>)

Cache-refresh jobs (id: cache_<slug>) are registered ONLY for
non-multi rows where is_cached=True.

Reserved JSON sub-key:
    attributes.scheduler.output -- parsed but ignored in v1; reserved
    for a future result-handling patch (see FEAT-092).
"""
import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional, Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiohttp import web
from asyncdb import AsyncDB, AsyncPool
from navconfig.logging import logging

from querysource.conf import (
    QS_SCHEDULER_TIMEZONE,
    QS_SCHEDULER_MAX_INSTANCES,
    QS_SCHEDULER_COALESCE,
    default_dsn,
)
from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,
)
from querysource.scheduler.notifications import NotificationManager

logger = logging.getLogger("QSScheduler")

# Mapping from day-of-week string to Python weekday int (mon=0 .. sun=6).
# Used by _biweekly_anchor to roll an anchor date to the target weekday.
_DOW_TO_INT = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _biweekly_anchor(
    start_date: Union[str, datetime],
    day_of_week: Union[str, int],
    hour: int,
    minute: int,
) -> datetime:
    """Return a datetime anchored on the requested day-of-week at hour:minute.

    Rolls forward from start_date until the target weekday is reached, then
    applies the requested hour and minute.

    Note:
        ``start_date`` is the recommended way to pin the biweekly cadence
        phase.  Without it the ``week='*/2'`` CronTrigger path is used, which
        is subject to ISO-week phase ambiguity when the scheduler is deployed
        at different times of the year.

    Args:
        start_date: A ``"YYYY-MM-DD"`` / ISO-8601 string or a datetime object.
        day_of_week: APScheduler day-of-week string (``"mon"``..``"sun"``)
            or integer 0-6 (mon=0).
        hour: Hour component for the anchor time (0-23).
        minute: Minute component for the anchor time (0-59).

    Returns:
        A naive datetime at the first occurrence of day_of_week on or after
        start_date, at hour:minute:00.

    Raises:
        TypeError: If start_date is neither a str nor a datetime.
        ValueError: If day_of_week is an integer outside [0, 6].
        KeyError: If day_of_week is an unrecognised string (caught by the
            caller's outer except block in _parse_trigger).
    """
    if isinstance(start_date, str):
        anchor = datetime.fromisoformat(start_date)
    elif isinstance(start_date, datetime):
        anchor = start_date
    else:
        raise TypeError(
            f"biweekly start_date must be str or datetime, got {type(start_date).__name__}"
        )
    anchor = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if isinstance(day_of_week, str):
        target_dow = _DOW_TO_INT[day_of_week.lower()]  # KeyError propagates
    else:
        target_dow = int(day_of_week)
        if not (0 <= target_dow <= 6):
            raise ValueError(
                f"biweekly day_of_week int must be 0-6 (mon=0..sun=6), got {target_dow}"
            )
    while anchor.weekday() != target_dow:
        anchor = anchor + timedelta(days=1)
    return anchor


class QSScheduler:
    """Embedded APScheduler for QuerySource.

    Creates scheduled jobs from public.queries definitions.
    Gated behind ENABLE_QS_SCHEDULER config flag.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop = None):
        self.logger = logger
        self._loop = loop
        self._timezone = QS_SCHEDULER_TIMEZONE
        self._scheduler: AsyncIOScheduler = None
        self._db: AsyncDB = None
        self._notification_manager = NotificationManager()

    def _create_scheduler(self) -> AsyncIOScheduler:
        """Create an AsyncIOScheduler with MemoryJobStore and AsyncIOExecutor."""
        return AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": AsyncIOExecutor()},
            job_defaults={
                "coalesce": QS_SCHEDULER_COALESCE,
                "max_instances": QS_SCHEDULER_MAX_INSTANCES,
            },
            timezone=self._timezone,
        )

    def _parse_trigger(
        self, schedule_type: str, schedule: dict
    ) -> Optional[BaseTrigger]:
        """Parse a schedule definition into an APScheduler trigger.

        Args:
            schedule_type: One of 'cron', 'crontab', 'interval',
                'hourly', 'daily', 'weekly', 'monthly', 'biweekly'.
            schedule: Trigger-specific kwargs (shape per spec §2 Data Models).

        Returns:
            An APScheduler trigger instance, or None if parsing fails.
        """
        try:
            tz = schedule.get("timezone", self._timezone)
            if schedule_type == "interval":
                return IntervalTrigger(**schedule)
            elif schedule_type == "crontab":
                crontab_expr = schedule["crontab"]
                return CronTrigger.from_crontab(crontab_expr, timezone=tz)
            elif schedule_type == "cron":
                return CronTrigger(**schedule)
            elif schedule_type == "hourly":
                return CronTrigger(
                    minute=schedule["minute"],
                    timezone=tz,
                )
            elif schedule_type == "daily":
                return CronTrigger(
                    hour=schedule["hour"],
                    minute=schedule["minute"],
                    timezone=tz,
                )
            elif schedule_type == "weekly":
                return CronTrigger(
                    day_of_week=schedule["day_of_week"],
                    hour=schedule["hour"],
                    minute=schedule["minute"],
                    timezone=tz,
                )
            elif schedule_type == "monthly":
                return CronTrigger(
                    day=schedule["day"],
                    hour=schedule["hour"],
                    minute=schedule["minute"],
                    timezone=tz,
                )
            elif schedule_type == "biweekly":
                day_of_week = schedule["day_of_week"]
                hour = schedule["hour"]
                minute = schedule["minute"]
                start_date = schedule.get("start_date")
                if start_date is not None:
                    anchor = _biweekly_anchor(start_date, day_of_week, hour, minute)
                    return IntervalTrigger(
                        weeks=2,
                        start_date=anchor,
                        timezone=tz,
                    )
                return CronTrigger(
                    week="*/2",
                    day_of_week=day_of_week,
                    hour=hour,
                    minute=minute,
                    timezone=tz,
                )
            else:
                self.logger.error(
                    "Unknown schedule_type '%s' — skipping", schedule_type
                )
                return None
        except Exception as exc:
            self.logger.error(
                "Failed to parse trigger (type=%s): %s", schedule_type, exc
            )
            return None

    def _register_query_row(self, row: dict) -> Optional[str]:
        """Register the scheduled-query (or multi-query) job for a single row.

        Shared by the startup bulk-load and the runtime :meth:`register_slug`
        path so both behave identically.

        Args:
            row: A query row from public.queries.

        Returns:
            The registered job id, or None when the row has no valid
            ``attributes.scheduler`` definition.
        """
        slug = row["query_slug"]
        attributes = row.get("attributes") or {}
        scheduler_def = attributes.get("scheduler")
        if not scheduler_def:
            return None
        schedule_type = scheduler_def.get("schedule_type")
        schedule = scheduler_def.get("schedule")
        if not schedule_type or not schedule:
            self.logger.warning(
                f"Query '{slug}' has incomplete scheduler definition — skipping"
            )
            return None
        trigger = self._parse_trigger(schedule_type, schedule)
        if trigger is None:
            return None

        provider = row.get("provider")
        if provider == "multi":
            # Reserved output sub-key — parse, log at DEBUG, do NOT pass into kwargs.
            reserved_output = scheduler_def.get("output")
            if reserved_output:
                self.logger.debug(
                    "Query '%s' declares reserved attributes.scheduler.output — "
                    "ignored in v1 (forward-compatible).",
                    slug,
                )

            # Misconfig WARN (Q1 resolution from spec §8).
            raw = row.get("query_raw") or ""
            try:
                payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
            except json.JSONDecodeError:
                payload = None
            if not (isinstance(payload, dict)
                    and ("queries" in payload or "files" in payload
                         or "sources" in payload)):
                self.logger.warning(
                    "Multi-query slug '%s' has query_raw that is not a multi-query "
                    "JSON payload — MultiQS will fall back to single-query mode "
                    "at runtime.",
                    slug,
                )

            job_id = f"multi_{slug}"
            self._scheduler.add_job(
                scheduled_multiqs_job,
                trigger=trigger,
                id=job_id,
                name=f"Scheduled multi-query: {slug}",
                replace_existing=True,
                kwargs={
                    "slug": slug,
                    "notification_manager": self._notification_manager,
                },
            )
            self.logger.info("Registered scheduled multi-query job: %s", job_id)
            return job_id

        # Single-query path.
        job_id = f"query_{slug}"
        self._scheduler.add_job(
            scheduled_query_job,
            trigger=trigger,
            id=job_id,
            name=f"Scheduled query: {slug}",
            replace_existing=True,
            kwargs={
                "slug": slug,
                "notification_manager": self._notification_manager,
            },
        )
        self.logger.info(f"Registered scheduled query job: {job_id}")
        return job_id

    def _load_scheduled_queries(self, rows: list) -> int:
        """Register ScheduledQueryJob for rows with attributes.scheduler.

        Routes by provider:
            - provider='multi' -> scheduled_multiqs_job (id: multi_<slug>)
            - otherwise        -> scheduled_query_job   (id: query_<slug>)

        Args:
            rows: Query rows from public.queries.

        Returns:
            Number of jobs registered.
        """
        return sum(1 for row in rows if self._register_query_row(row) is not None)

    def _register_cache_row(self, row: dict) -> Optional[str]:
        """Register the cache-refresh job for a single row.

        Multi-slugs (provider='multi') are skipped unconditionally: their
        sub-slug caches are written by normal QS execution.

        Args:
            row: A query row from public.queries.

        Returns:
            The registered job id, or None when the row is not cache-schedulable.
        """
        if row.get("provider") == "multi":
            return None
        slug = row["query_slug"]
        if not row.get("is_cached", False):
            return None
        cache_options = row.get("cache_options") or {}
        schedule_type = cache_options.get("schedule_type")
        schedule = cache_options.get("schedule")
        if not schedule_type or not schedule:
            return None
        trigger = self._parse_trigger(schedule_type, schedule)
        if trigger is None:
            return None
        job_id = f"cache_{slug}"
        self._scheduler.add_job(
            cache_refresh_job,
            trigger=trigger,
            id=job_id,
            name=f"Cache refresh: {slug}",
            replace_existing=True,
            kwargs={
                "slug": slug,
                "notification_manager": self._notification_manager,
            },
        )
        self.logger.info(f"Registered cache refresh job: {job_id}")
        return job_id

    def _load_cache_refresh_jobs(self, rows: list) -> int:
        """Register CacheRefreshJob for rows with cache_options schedule and is_cached=True.

        Args:
            rows: Query rows from public.queries.

        Returns:
            Number of jobs registered.
        """
        return sum(1 for row in rows if self._register_cache_row(row) is not None)

    # ─── Runtime job management (no restart) ────────────────────────────────
    # APScheduler supports add/remove/pause/resume on a running scheduler, so a
    # slug's schedule can be (un)registered live. The DB remains the source of
    # truth: on the next restart, startup rebuilds every job from public.queries.

    def _slug_job_ids(self, slug: str) -> list:
        """All possible APScheduler job ids derived from a slug."""
        return [f"query_{slug}", f"multi_{slug}", f"cache_{slug}"]

    async def _fetch_slug_row(self, slug: str) -> Optional[dict]:
        """Fetch the schedulable columns for a single slug from public.queries.

        Returns a row dict shaped like the startup loader expects, or None when
        the slug doesn't exist or the DB pool is unavailable.
        """
        if self._db is None:
            self.logger.error(
                "QSScheduler: DB pool unavailable; cannot fetch slug '%s'", slug
            )
            return None
        from ..models import QueryModel  # lazy import to avoid an import cycle
        try:
            async with await self._db.acquire() as conn:
                query = await QueryModel.get(query_slug=slug, _connection=conn)
        except Exception as exc:
            self.logger.warning(
                "QSScheduler: slug '%s' not found or query failed: %s", slug, exc
            )
            return None
        if query is None:
            return None
        return {
            "query_slug": getattr(query, "query_slug", slug),
            "attributes": getattr(query, "attributes", None),
            "cache_options": getattr(query, "cache_options", None),
            "provider": getattr(query, "provider", None),
            "is_cached": getattr(query, "is_cached", False),
            "query_raw": getattr(query, "query_raw", None),
        }

    def remove_job(self, job_id: str) -> bool:
        """Remove a single job from the live scheduler.

        Returns:
            True if a job was removed, False if it didn't exist.
        """
        if self._scheduler is None or self._scheduler.get_job(job_id) is None:
            return False
        self._scheduler.remove_job(job_id)
        self.logger.info("Removed scheduler job: %s", job_id)
        return True

    def set_job_paused(self, job_id: str, paused: bool) -> bool:
        """Pause or resume a live job.

        Returns:
            True on success, False if the job doesn't exist.
        """
        if self._scheduler is None or self._scheduler.get_job(job_id) is None:
            return False
        if paused:
            self._scheduler.pause_job(job_id)
            self.logger.info("Paused scheduler job: %s", job_id)
        else:
            self._scheduler.resume_job(job_id)
            self.logger.info("Resumed scheduler job: %s", job_id)
        return True

    async def register_slug(self, slug: str) -> dict:
        """Sync a single slug's scheduled jobs into the live scheduler (no restart).

        Reads the current public.queries row, removes any existing jobs for the
        slug, then (re)registers query/multi/cache jobs from its current
        ``attributes.scheduler`` / ``cache_options``. Removing the scheduler
        definition and calling this effectively unregisters the slug's job.

        Args:
            slug: The query slug to (re)sync.

        Returns:
            ``{"slug": ..., "registered": [job_id, ...], "removed": [job_id, ...]}``.

        Raises:
            RuntimeError: If the scheduler is not running.
        """
        if self._scheduler is None:
            raise RuntimeError("QSScheduler is not running")

        # Remove existing jobs for this slug first (idempotent re-register).
        removed = []
        for jid in self._slug_job_ids(slug):
            if self._scheduler.get_job(jid) is not None:
                self._scheduler.remove_job(jid)
                removed.append(jid)

        registered = []
        row = await self._fetch_slug_row(slug)
        if row is not None:
            qjob = self._register_query_row(row)
            if qjob:
                registered.append(qjob)
            cjob = self._register_cache_row(row)
            if cjob:
                registered.append(cjob)

        # Don't report a job as both removed and re-registered.
        removed = [r for r in removed if r not in registered]
        return {"slug": slug, "registered": registered, "removed": removed}

    def setup(self, app: web.Application) -> None:
        """Register startup/shutdown hooks on the aiohttp app.

        Args:
            app: The aiohttp web application.
        """
        app.on_startup.append(self.startup)
        app.on_shutdown.append(self.shutdown)

    async def startup(self, app: web.Application) -> None:
        """Initialize DB pool, load jobs from public.queries, start scheduler.

        Args:
            app: The aiohttp web application.
        """
        self.logger.info(
            "Starting QSScheduler (timezone=%s, coalesce=%s, max_instances=%s)",
            self._timezone,
            QS_SCHEDULER_COALESCE,
            QS_SCHEDULER_MAX_INSTANCES,
        )
        if not self._loop:
            self._loop = asyncio.get_event_loop()
        # Create own PostgreSQL pool
        self.logger.info("QSScheduler: initializing PostgreSQL pool")
        self._db = AsyncPool(
            "pg",
            dsn=default_dsn,
            loop=self._loop,
        )
        # Starts the pool (establishes initial connections)
        try:
            await self._db.connect()
            self.logger.info("QSScheduler: DB pool started")
        except Exception as exc:
            self.logger.error(f"QSScheduler: failed to start DB pool: {exc}")
            self._db = None
        # Create the scheduler
        self.logger.info("QSScheduler: creating AsyncIOScheduler instance")
        self._scheduler = self._create_scheduler()
        # Query public.queries for schedulable rows
        self.logger.info("QSScheduler: loading schedulable queries from public.queries")
        try:
            async with await self._db.acquire() as conn:
                sql = (
                    "SELECT query_slug, attributes, cache_options, provider, is_cached, "
                    "       query_raw "
                    "FROM public.queries "
                    "WHERE (attributes IS NOT NULL AND attributes != '{}') "
                    "   OR (cache_options IS NOT NULL AND cache_options != '{}')"
                )
                rows, error = await conn.query(sql)
                if error:
                    self.logger.error(f"QSScheduler: error loading schedulable queries: {error}")
                    rows = []
        except Exception as exc:
            self.logger.error(f"QSScheduler: failed to query schedulable rows: {exc}")
            rows = []
        self.logger.info(
            "QSScheduler: fetched %d candidate row(s) from public.queries", len(rows)
        )
        # Register jobs
        query_count = self._load_scheduled_queries(rows)
        cache_count = self._load_cache_refresh_jobs(rows)
        self.logger.info(
            f"QSScheduler loaded {query_count} scheduled query jobs "
            f"and {cache_count} cache refresh jobs"
        )
        # Start the scheduler
        self._scheduler.start()
        self.logger.info(
            "QSScheduler started with %d active job(s)",
            len(self._scheduler.get_jobs()),
        )
        app["qs_scheduler"] = self

    async def shutdown(self, app: web.Application) -> None:
        """Gracefully stop scheduler and close DB pool.

        Args:
            app: The aiohttp web application.
        """
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=True)
            self.logger.info("QSScheduler stopped")
        if self._db:
            try:
                await self._db.close()
            except Exception as exc:
                self.logger.error(f"Error closing scheduler DB pool: {exc}")

    def add_notification_callback(self, callback: Callable) -> None:
        """Register a callback invoked on job errors.

        Args:
            callback: Callable with signature (job_id, slug, error) -> None.
        """
        self._notification_manager.add_callback(callback)
