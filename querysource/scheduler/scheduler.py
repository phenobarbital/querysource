"""QSScheduler Core — Embedded APScheduler for QuerySource.

Creates scheduled jobs from public.queries definitions.
Gated behind ENABLE_QS_SCHEDULER config flag.
"""
import asyncio
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiohttp import web
from asyncdb import AsyncDB
from navconfig.logging import logging

from querysource.conf import (
    QS_SCHEDULER_TIMEZONE,
    QS_SCHEDULER_MAX_INSTANCES,
    QS_SCHEDULER_COALESCE,
    default_dsn,
)
from querysource.scheduler.jobs import scheduled_query_job, cache_refresh_job
from querysource.scheduler.notifications import NotificationManager

logger = logging.getLogger("QSScheduler")


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
    ):
        """Parse a schedule definition into an APScheduler trigger.

        Args:
            schedule_type: One of 'cron', 'crontab', or 'interval'.
            schedule: Trigger-specific kwargs.

        Returns:
            An APScheduler trigger instance, or None if parsing fails.
        """
        try:
            if schedule_type == "interval":
                return IntervalTrigger(**schedule)
            elif schedule_type == "crontab":
                crontab_expr = schedule["crontab"]
                tz = schedule.get("timezone", self._timezone)
                return CronTrigger.from_crontab(crontab_expr, timezone=tz)
            elif schedule_type == "cron":
                return CronTrigger(**schedule)
            else:
                self.logger.error(
                    f"Unknown schedule_type '{schedule_type}' — skipping"
                )
                return None
        except Exception as exc:
            self.logger.error(
                f"Failed to parse trigger (type={schedule_type}): {exc}"
            )
            return None

    def _load_scheduled_queries(self, rows: list) -> int:
        """Register ScheduledQueryJob for rows with attributes.scheduler.

        Args:
            rows: Query rows from public.queries.

        Returns:
            Number of jobs registered.
        """
        count = 0
        for row in rows:
            slug = row["query_slug"]
            attributes = row.get("attributes") or {}
            scheduler_def = attributes.get("scheduler")
            if not scheduler_def:
                continue
            schedule_type = scheduler_def.get("schedule_type")
            schedule = scheduler_def.get("schedule")
            if not schedule_type or not schedule:
                self.logger.warning(
                    f"Query '{slug}' has incomplete scheduler definition — skipping"
                )
                continue
            trigger = self._parse_trigger(schedule_type, schedule)
            if trigger is None:
                continue
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
            count += 1
        return count

    def _load_cache_refresh_jobs(self, rows: list) -> int:
        """Register CacheRefreshJob for rows with cache_options schedule and is_cached=True.

        Args:
            rows: Query rows from public.queries.

        Returns:
            Number of jobs registered.
        """
        count = 0
        for row in rows:
            slug = row["query_slug"]
            is_cached = row.get("is_cached", False)
            if not is_cached:
                continue
            cache_options = row.get("cache_options") or {}
            schedule_type = cache_options.get("schedule_type")
            schedule = cache_options.get("schedule")
            if not schedule_type or not schedule:
                continue
            trigger = self._parse_trigger(schedule_type, schedule)
            if trigger is None:
                continue
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
            count += 1
        return count

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
        if not self._loop:
            self._loop = asyncio.get_event_loop()
        # Create own PostgreSQL pool
        self._db = AsyncDB(
            "pg",
            dsn=default_dsn,
            loop=self._loop,
        )
        # Create the scheduler
        self._scheduler = self._create_scheduler()
        # Query public.queries for schedulable rows
        try:
            async with await self._db.connection() as conn:
                sql = (
                    "SELECT query_slug, attributes, cache_options, is_cached "
                    "FROM public.queries "
                    "WHERE (attributes IS NOT NULL AND attributes != '{}') "
                    "   OR (cache_options IS NOT NULL AND cache_options != '{}')"
                )
                rows, error = await conn.query(sql)
                if error:
                    self.logger.error(f"Error loading schedulable queries: {error}")
                    rows = []
        except Exception as exc:
            self.logger.error(f"Failed to query schedulable rows: {exc}")
            rows = []
        # Register jobs
        query_count = self._load_scheduled_queries(rows)
        cache_count = self._load_cache_refresh_jobs(rows)
        self.logger.info(
            f"QSScheduler loaded {query_count} scheduled query jobs "
            f"and {cache_count} cache refresh jobs"
        )
        # Start the scheduler
        self._scheduler.start()
        self.logger.info("QSScheduler started")
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
