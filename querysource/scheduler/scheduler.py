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
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional, Union
from urllib.parse import quote

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiohttp import web
from navconfig.logging import logging

from querysource.conf import (
    QS_SCHEDULER_TIMEZONE,
    QS_SCHEDULER_MAX_INSTANCES,
    QS_SCHEDULER_COALESCE,
)
from querysource.repositories import DefinitionRepository
from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,
)
from querysource.scheduler.notifications import NotificationManager
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantOwnerEnvelope, TenantRegistry

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
        self._notification_manager = NotificationManager()
        # Overwritten in startup() with the app's published qs_tenant_registry
        # (set by QuerySource.qs_start(), which the app's on_startup order
        # guarantees runs before scheduler startup — see querysource/
        # services.py QuerySource.qs_start()). The placeholder TenantRegistry()
        # here is intentionally never discovered — its .stores() is always
        # empty — it exists only so resolve()/_is_default_store() have a
        # working legacy fallback for any call before startup() runs.
        self._registry: TenantRegistry = TenantRegistry()
        # Populated in startup() from the app's published qs_definition_repository.
        # Never construct one locally: it needs the real, loop-bound
        # connection_factory from QuerySource's own connection (TASK-720),
        # not a scheduler-owned one.
        self._repository: DefinitionRepository | None = None

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

    def _owner_envelope(self, store: QueryStore) -> TenantOwnerEnvelope:
        """Build the validated owner envelope carried in every scheduled job's kwargs."""
        return TenantOwnerEnvelope(
            version=1,
            database_namespace=store.database_namespace,
            schema=store.schema,
            table=store.table,
            contract=store.contract,
        )

    def _is_default_store(self, store: QueryStore) -> bool:
        """True when ``store`` is the registry's configured/legacy default.

        Uses the registry's own public ``resolve()`` fallback (never reaches
        into a private attribute) so this stays correct whether or not a
        configured default was discovered.
        """
        if self._registry is None:
            return True
        return store == self._registry.resolve()

    def _qualified_job_id(self, base_id: str, slug: str, store: QueryStore) -> str:
        """Retain the legacy id for the configured default store; qualify every other store.

        ``qsj2-<kind>-<store_digest>-<url-safe-slug>`` keeps an identical
        slug living in two different physical stores from colliding in the
        scheduler's single flat job-id namespace (AC-2).

        Args:
            base_id: The legacy id (e.g. ``"query_<slug>"``).
            slug: The query slug.
            store: The resolved QueryStore owning this row.

        Returns:
            ``base_id`` unchanged for the default store, else a qualified
            ``qsj2-...`` id.
        """
        if self._is_default_store(store):
            return base_id
        kind = base_id.split("_", 1)[0]
        store_key = f"{store.database_namespace}:{store.schema}:{store.table}"
        store_digest = hashlib.sha256(store_key.encode()).hexdigest()[:12]
        return f"qsj2-{kind}-{store_digest}-{quote(slug, safe='')}"

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

    def _register_query_row(
        self, row: dict, store: QueryStore | None = None
    ) -> Optional[str]:
        """Register the scheduled-query (or multi-query) job for a single row.

        Shared by the startup bulk-load and the runtime :meth:`register_slug`
        path so both behave identically.

        Args:
            row: A query row from the resolved store's queries table.
            store: The resolved QueryStore owning this row; None resolves
                to the registry's configured/legacy default (TASK-729).

        Returns:
            The registered job id, or None when the row has no valid
            ``attributes.scheduler`` definition.
        """
        if store is None:
            store = self._registry.resolve()
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

        owner_envelope = self._owner_envelope(store)
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

            job_id = self._qualified_job_id(f"multi_{slug}", slug, store)
            self._scheduler.add_job(
                scheduled_multiqs_job,
                trigger=trigger,
                id=job_id,
                name=f"Scheduled multi-query: {slug}",
                replace_existing=True,
                kwargs={
                    "slug": slug,
                    "notification_manager": self._notification_manager,
                    "owner": owner_envelope,
                },
            )
            self.logger.info("Registered scheduled multi-query job: %s", job_id)
            return job_id

        # Single-query path.
        job_id = self._qualified_job_id(f"query_{slug}", slug, store)
        self._scheduler.add_job(
            scheduled_query_job,
            trigger=trigger,
            id=job_id,
            name=f"Scheduled query: {slug}",
            replace_existing=True,
            kwargs={
                "slug": slug,
                "notification_manager": self._notification_manager,
                "owner": owner_envelope,
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

    def _register_cache_row(
        self, row: dict, store: QueryStore | None = None
    ) -> Optional[str]:
        """Register the cache-refresh job for a single row.

        Multi-slugs (provider='multi') are skipped unconditionally: their
        sub-slug caches are written by normal QS execution.

        Args:
            row: A query row from the resolved store's queries table.
            store: The resolved QueryStore owning this row; None resolves
                to the registry's configured/legacy default (TASK-729).

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
        if store is None:
            store = self._registry.resolve()
        owner_envelope = self._owner_envelope(store)
        job_id = self._qualified_job_id(f"cache_{slug}", slug, store)
        self._scheduler.add_job(
            cache_refresh_job,
            trigger=trigger,
            id=job_id,
            name=f"Cache refresh: {slug}",
            replace_existing=True,
            kwargs={
                "slug": slug,
                "notification_manager": self._notification_manager,
                "owner": owner_envelope,
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

    def _slug_job_ids(self, slug: str, *, store: QueryStore | None = None) -> list:
        """All possible APScheduler job ids derived from a slug, owned by ``store`` alone.

        Retains the legacy ids for the configured default store; for any
        other store, returns ONLY the qualified ``qsj2-...`` ids — never
        the legacy ids too. Mixing in the legacy ids for a non-default
        store would make :meth:`register_slug` remove the DEFAULT store's
        identically-named job as a side effect of syncing an unrelated
        tenant's slug (AC-3 "mutation of one owner must not remove another
        owner job").

        Args:
            slug: The query slug.
            store: The resolved QueryStore; None resolves to the registry's
                configured/legacy default.

        Returns:
            The job ids that belong to this slug in this store only.
        """
        if store is None:
            store = self._registry.resolve()
        legacy_ids = [f"query_{slug}", f"multi_{slug}", f"cache_{slug}"]
        if self._is_default_store(store):
            return legacy_ids
        return [self._qualified_job_id(base_id, slug, store) for base_id in legacy_ids]

    async def _fetch_slug_row(
        self, slug: str, *, tenant: str | None = None
    ) -> dict | None:
        """Use the definition repository; distinguish missing row from unavailable store.

        Args:
            slug: The query slug.
            tenant: The tenant selector; None resolves to the registry's
                configured/legacy default store.

        Returns:
            A row dict shaped like the startup loader expects, or None when
            the slug doesn't exist in the resolved store. A store/connection
            failure is logged and also returns None (AC-4 distinguishes the
            two in the log message — a missing definition is DEBUG, a store
            failure is WARNING — but both are equally "nothing to
            (re)register" for the caller).
        """
        if self._repository is None:
            self.logger.error(
                "QSScheduler: definition repository unavailable; cannot fetch "
                "slug '%s' — has the scheduler been started?", slug
            )
            return None
        try:
            store = self._registry.resolve(tenant)
        except TenantError as exc:
            self.logger.warning(
                "QSScheduler: tenant '%s' not available: %s", tenant, exc
            )
            return None

        try:
            identity = QueryIdentity(store=store, slug=slug)
            loaded = await self._repository.get(identity)
        except TenantError as exc:
            if exc.error_code == "query_not_found":
                self.logger.debug(
                    "QSScheduler: slug '%s' not found in %s.%s",
                    slug, store.schema, store.table,
                )
            else:
                self.logger.warning(
                    "QSScheduler: store %s.%s unavailable for slug '%s': %s",
                    store.schema, store.table, slug, exc,
                )
            return None
        except Exception as exc:
            # A raw driver/connection failure — never TenantError-wrapped by
            # the repository — must not be mistaken for a missing definition.
            self.logger.warning(
                "QSScheduler: store %s.%s unavailable for slug '%s': %s",
                store.schema, store.table, slug, exc,
            )
            return None

        runtime = loaded.runtime
        return {
            "query_slug": getattr(runtime, "query_slug", slug),
            "attributes": getattr(runtime, "attributes", None),
            "cache_options": getattr(runtime, "cache_options", None),
            "provider": getattr(runtime, "provider", None),
            "is_cached": getattr(runtime, "is_cached", False),
            "query_raw": getattr(runtime, "query_raw", None),
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

    async def register_slug(self, slug: str, *, tenant: str | None = None) -> dict:
        """Synchronize only the selected owner's scheduled jobs (no restart).

        Reads the current row from the resolved store, removes any existing
        jobs for the slug **in that same store only** (AC-3 — mutating one
        owner's slug must never remove another owner's job for the same
        slug text), then (re)registers query/multi/cache jobs from its
        current ``attributes.scheduler`` / ``cache_options``. Removing the
        scheduler definition and calling this effectively unregisters the
        slug's job for that owner.

        Args:
            slug: The query slug to (re)sync.
            tenant: The tenant selector; None resolves to the registry's
                configured/legacy default store.

        Returns:
            ``{"slug": ..., "tenant": ..., "store": {...}, "registered":
            [job_id, ...], "removed": [job_id, ...]}``.

        Raises:
            RuntimeError: If the scheduler is not running.
            TenantError: If the tenant is not available.
        """
        if self._scheduler is None:
            raise RuntimeError("QSScheduler is not running")

        # Resolve the owner store first — every following step (job removal,
        # fetch, registration) is scoped to this one store only.
        store = self._registry.resolve(tenant)

        # Remove existing jobs for this slug in this store only (idempotent
        # re-register); never touches another owner's job for the same slug.
        removed = []
        for jid in self._slug_job_ids(slug, store=store):
            if self._scheduler.get_job(jid) is not None:
                self._scheduler.remove_job(jid)
                removed.append(jid)

        registered = []
        row = await self._fetch_slug_row(slug, tenant=tenant)
        if row is not None:
            qjob = self._register_query_row(row, store=store)
            if qjob:
                registered.append(qjob)
            cjob = self._register_cache_row(row, store=store)
            if cjob:
                registered.append(cjob)

        # Don't report a job as both removed and re-registered.
        removed = [r for r in removed if r not in registered]
        return {
            "slug": slug,
            "tenant": tenant,
            "store": {
                "database_namespace": store.database_namespace,
                "schema": store.schema,
                "table": store.table,
                "contract": store.contract,
            },
            "registered": registered,
            "removed": removed,
        }

    def setup(self, app: web.Application) -> None:
        """Register startup/shutdown hooks on the aiohttp app.

        Args:
            app: The aiohttp web application.
        """
        app.on_startup.append(self.startup)
        app.on_shutdown.append(self.shutdown)

    async def startup(self, app: web.Application) -> None:
        """Load schedulable jobs from every registry store, then start the scheduler.

        Replaces the old hardcoded ``public.queries`` read (AC-1): the
        definition repository and the already-discovered tenant registry
        are read from the app (published by ``QuerySource.qs_start()``,
        which the app's ``on_startup`` order guarantees runs first — see
        ``querysource/services.py`` and
        ``tests/tenants/test_tenant_bootstrap_lookup.py::
        test_startup_order_before_scheduler``), so discovery is always
        complete before any job is registered.

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

        self._registry = app.get("qs_tenant_registry") or TenantRegistry()
        self._repository = app.get("qs_definition_repository")
        if self._repository is None:
            self.logger.error(
                "QSScheduler: qs_definition_repository is not published on "
                "the app — QuerySource.qs_start() must run before scheduler "
                "startup. No scheduled jobs will be loaded."
            )

        # Create the scheduler
        self.logger.info("QSScheduler: creating AsyncIOScheduler instance")
        self._scheduler = self._create_scheduler()

        # Discovery is already complete (see docstring); enumerate every
        # unique eligible physical store, falling back to just the
        # configured/legacy default when discovery found none.
        stores = self._registry.stores() or (self._registry.resolve(),)
        self.logger.info(
            "QSScheduler: loading schedulable queries from %d store(s)",
            len(stores),
        )
        total_query_count = 0
        total_cache_count = 0
        # Keyed on (physical store identity, slug) — NOT slug alone. A bare
        # slug key would collapse two DIFFERENT tenants' same-named queries
        # (e.g. both client_a.queries and client_b.queries defining
        # "daily_report") into "the same job", silently dropping every
        # store after the first that happens to share a slug name — a real
        # cross-tenant scheduling loss, not just a naming collision. "physical
        # aliases schedule once" (AC-2) means a single physical store
        # enumerated more than once (e.g. discovery yielding a duplicate
        # identity) is only scheduled once — it does not mean two distinct
        # tenant stores sharing a slug string collapse into one job.
        seen_store_slugs: set[tuple[str, str, str, str]] = set()

        if self._repository is not None:
            for store in stores:
                try:
                    rows = await self._repository.schedulable(store)
                except Exception as exc:
                    # A store/connection failure must not be mistaken for
                    # "no schedulable queries" — log and move on to the
                    # next store (AC-4).
                    self.logger.error(
                        "QSScheduler: failed to load from %s.%s: %s",
                        store.schema, store.table, exc,
                    )
                    continue
                self.logger.info(
                    "QSScheduler: fetched %d candidate row(s) from %s.%s",
                    len(rows), store.schema, store.table,
                )
                for row in rows:
                    slug = row.get("query_slug")
                    if not slug:
                        continue
                    store_slug_key = (
                        store.database_namespace, store.schema, store.table, slug,
                    )
                    if store_slug_key in seen_store_slugs:
                        continue
                    seen_store_slugs.add(store_slug_key)
                    if self._register_query_row(row, store=store) is not None:
                        total_query_count += 1
                    if self._register_cache_row(row, store=store) is not None:
                        total_cache_count += 1

        self.logger.info(
            f"QSScheduler loaded {total_query_count} scheduled query jobs "
            f"and {total_cache_count} cache refresh jobs "
            f"from {len(stores)} store(s)"
        )
        # Start the scheduler
        self._scheduler.start()
        self.logger.info(
            "QSScheduler started with %d active job(s)",
            len(self._scheduler.get_jobs()),
        )
        app["qs_scheduler"] = self

    async def shutdown(self, app: web.Application) -> None:
        """Gracefully stop the scheduler.

        The scheduler no longer owns a database pool (TASK-729): every read
        goes through the app's shared ``qs_definition_repository``, whose
        connections are already loop-local, per-call, and closed by the
        repository itself — there is nothing scheduler-owned left to close.

        Args:
            app: The aiohttp web application.
        """
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=True)
            self.logger.info("QSScheduler stopped")

    def add_notification_callback(self, callback: Callable) -> None:
        """Register a callback invoked on job errors.

        Args:
            callback: Callable with signature (job_id, slug, error) -> None.
        """
        self._notification_manager.add_callback(callback)
