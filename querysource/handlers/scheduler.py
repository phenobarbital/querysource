"""Scheduler jobstore handler — FEAT-100.

HTTP surface on top of QSScheduler's APScheduler jobstore.

Routes (registered in querysource/services.py when ENABLE_QS_SCHEDULER=True):
    GET    /api/v1/qs/scheduler/jobs            -> list all jobs
    GET    /api/v1/qs/scheduler/jobs/{job_id}   -> single job, or 404
    POST   /api/v1/qs/scheduler/jobs            -> sync a slug's job(s) live
    DELETE /api/v1/qs/scheduler/jobs/{job_id}   -> remove a job live
    PATCH  /api/v1/qs/scheduler/jobs/{job_id}   -> pause/resume a job live

The write verbs mutate the running APScheduler so schedules take effect
without a restart. The DB (public.queries) stays the source of truth:
startup rebuilds every job, and POST mirrors a single slug's row into the
live scheduler.

Note: Per-process visibility only. MemoryJobStore lives in the running
process — multiple QS instances each report (and mutate) their own jobs.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from aiohttp import web
from navigator.views import BaseView

if TYPE_CHECKING:
    from apscheduler.job import Job
    from querysource.scheduler import QSScheduler

logger = logging.getLogger("QS.SchedulerJobsView")


def _kind_from_id(job_id: str) -> str:
    """Map job ID prefix to its kind.

    Args:
        job_id: APScheduler job ID, e.g. ``"query_foo"``.

    Returns:
        One of ``"query"``, ``"multi"``, ``"cache"``, ``"unknown"``.
    """
    if job_id.startswith("query_"):
        return "query"
    if job_id.startswith("multi_"):
        return "multi"
    if job_id.startswith("cache_"):
        return "cache"
    return "unknown"


class SchedulerJobsView(BaseView):
    """Class-based aiohttp view exposing the QSScheduler jobstore.

        GET     /api/v1/qs/scheduler/jobs            — list all jobs
        GET     /api/v1/qs/scheduler/jobs/{job_id}   — single job by ID
        POST    /api/v1/qs/scheduler/jobs            — sync a slug's job(s) live
        DELETE  /api/v1/qs/scheduler/jobs/{job_id}   — remove a job live
        PATCH   /api/v1/qs/scheduler/jobs/{job_id}   — pause/resume a job live

    Note: Per-process visibility only. MemoryJobStore lives in the running
    process — if multiple QS instances run with the scheduler enabled, each
    instance answers only for (and mutates) its own jobstore.
    """

    def _get_scheduler(self) -> Optional["QSScheduler"]:
        """Retrieve the QSScheduler instance from the app state.

        Returns:
            The QSScheduler stored in ``app["qs_scheduler"]``, or None if
            not present (e.g. during startup or if scheduler was not
            initialised).
        """
        return self.request.app.get("qs_scheduler")

    def _serialize_job(self, job: "Job") -> dict:
        """Serialize an APScheduler Job to a plain dict.

        Only extracts ``slug`` from ``job.kwargs`` — never includes the raw
        kwargs dict because it contains non-serialisable internal objects
        such as ``notification_manager``.

        Args:
            job: An APScheduler ``Job`` instance.

        Returns:
            A dict with keys: ``id``, ``name``, ``kind``, ``slug``,
            ``next_run_time``, ``trigger``, ``coalesce``, ``max_instances``,
            ``misfire_grace_time``, ``pending``.
        """
        # APScheduler Job uses __slots__; some slots are only set after the
        # scheduler starts (e.g. next_run_time). Use getattr with a default
        # to avoid AttributeError when the scheduler is pending/unstarted.
        nrt = getattr(job, "next_run_time", None)
        trigger_cls_name = type(job.trigger).__name__  # e.g. "IntervalTrigger"
        # Strip the "Trigger" suffix and lowercase to get "interval" / "cron"
        trigger_type = trigger_cls_name.lower()
        if trigger_type.endswith("trigger"):
            trigger_type = trigger_type[: -len("trigger")]

        slug: Optional[str] = (job.kwargs or {}).get("slug")

        return {
            "id": job.id,
            "name": job.name,
            "kind": _kind_from_id(job.id),
            "slug": slug,
            "next_run_time": nrt.isoformat() if nrt is not None else None,
            "trigger": {
                "type": trigger_type,
                "repr": str(job.trigger),
            },
            "coalesce": bool(getattr(job, "coalesce", False)),
            "max_instances": int(getattr(job, "max_instances", 1)),
            "misfire_grace_time": getattr(job, "misfire_grace_time", None),
            "pending": bool(getattr(job, "pending", True)),
        }

    async def get(self) -> web.Response:
        """Handle GET requests for the scheduler jobs endpoint.

        Dispatches based on the presence of ``{job_id}`` in the URL:

        - ``GET /api/v1/qs/scheduler/jobs``            — list all jobs.
        - ``GET /api/v1/qs/scheduler/jobs/{job_id}``   — single job lookup.

        Returns:
            - **200** with the list envelope or single job dict.
            - **404** when ``job_id`` is provided but no matching job exists.
            - **503** when ``app["qs_scheduler"]`` is missing (e.g.
              mid-startup race between route registration and
              ``QSScheduler.startup``).
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            return self._unavailable_response()

        aps = scheduler._scheduler  # AsyncIOScheduler instance
        params = self.match_parameters(self.request)
        job_id = params.get("job_id")

        if job_id:
            job = aps.get_job(job_id) if aps is not None else None
            if job is None:
                logger.debug("Job '%s' not found in scheduler.", job_id)
                return self.json_response(
                    response={"error": f"Job '{job_id}' not found."},
                    status=404,
                )
            return self.json_response(
                response=self._serialize_job(job),
                status=200,
            )

        # List all jobs
        jobs = aps.get_jobs() if aps is not None else []
        logger.debug("Listing %d scheduler job(s).", len(jobs))
        return self.json_response(
            response={
                "scheduler": {
                    "enabled": True,
                    "running": bool(aps.running) if aps is not None else False,
                    "timezone": str(scheduler._timezone),
                    "job_count": len(jobs),
                },
                "jobs": [self._serialize_job(j) for j in jobs],
            },
            status=200,
        )

    def _unavailable_response(self) -> web.Response:
        """503 envelope used when the scheduler isn't in app state yet."""
        logger.warning(
            "scheduler endpoint called but 'qs_scheduler' is not in app state — "
            "scheduler may still be starting up."
        )
        return self.json_response(
            response={
                "error": (
                    "QSScheduler is not available yet — the scheduler is "
                    "enabled but the application has not finished starting up."
                ),
                "scheduler": {"enabled": True, "running": False},
            },
            status=503,
        )

    async def post(self) -> web.Response:
        """Register/sync a slug's scheduled job(s) into the live scheduler.

        Body: ``{"slug": "<query_slug>"}``.

        Reads the slug's current ``public.queries`` row and (re)registers its
        query/multi/cache jobs without a restart. If the slug no longer has a
        scheduler definition, its jobs are removed. The DB row written by the
        slug-management API is the source of truth; this only mirrors it into
        the running scheduler.

        Returns:
            - **200** ``{status, slug, registered: [...], removed: [...]}``.
            - **400** when the body is not JSON or ``slug`` is missing.
            - **503** when the scheduler isn't available yet.
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            return self._unavailable_response()

        try:
            body = await self.request.json()
        except Exception:  # noqa: BLE001
            return self.json_response(
                response={"error": "Request body must be valid JSON."},
                status=400,
            )
        slug = body.get("slug") if isinstance(body, dict) else None
        if not slug or not isinstance(slug, str):
            return self.json_response(
                response={"error": "Body must include a non-empty 'slug' string."},
                status=400,
            )

        try:
            result = await scheduler.register_slug(slug)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error registering slug '%s': %s", slug, exc)
            return self.json_response(
                response={"error": str(exc)},
                status=500,
            )
        return self.json_response(response={"status": "ok", **result}, status=200)

    async def delete(self) -> web.Response:
        """Remove a single job from the live scheduler.

        ``DELETE /api/v1/qs/scheduler/jobs/{job_id}``.

        Returns:
            - **200** ``{status: "removed", id}`` on success.
            - **400** when ``job_id`` is missing from the path.
            - **404** when no such job exists.
            - **503** when the scheduler isn't available yet.
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            return self._unavailable_response()

        job_id = self.match_parameters(self.request).get("job_id")
        if not job_id:
            return self.json_response(
                response={"error": "job_id is required in the path."},
                status=400,
            )
        if not scheduler.remove_job(job_id):
            return self.json_response(
                response={"error": f"Job '{job_id}' not found."},
                status=404,
            )
        return self.json_response(
            response={"status": "removed", "id": job_id},
            status=200,
        )

    async def patch(self) -> web.Response:
        """Pause or resume a live job.

        ``PATCH /api/v1/qs/scheduler/jobs/{job_id}`` with body
        ``{"action": "pause" | "resume"}``.

        Returns:
            - **200** ``{status: "paused"|"resumed", id}`` on success.
            - **400** when ``job_id`` or a valid ``action`` is missing.
            - **404** when no such job exists.
            - **503** when the scheduler isn't available yet.
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            return self._unavailable_response()

        job_id = self.match_parameters(self.request).get("job_id")
        if not job_id:
            return self.json_response(
                response={"error": "job_id is required in the path."},
                status=400,
            )
        try:
            body = await self.request.json()
        except Exception:  # noqa: BLE001
            return self.json_response(
                response={"error": "Request body must be valid JSON."},
                status=400,
            )
        action = body.get("action") if isinstance(body, dict) else None
        if action not in ("pause", "resume"):
            return self.json_response(
                response={"error": "Body 'action' must be 'pause' or 'resume'."},
                status=400,
            )
        if not scheduler.set_job_paused(job_id, action == "pause"):
            return self.json_response(
                response={"error": f"Job '{job_id}' not found."},
                status=404,
            )
        return self.json_response(
            response={"status": f"{action}d", "id": job_id},
            status=200,
        )
