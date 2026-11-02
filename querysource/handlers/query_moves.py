"""Synchronous single-definition move between query stores (FEAT-176).

``POST /api/v1/management/queries/{slug}/move`` with body
``{"tenant": str|null, "destination_tenant": str|null}`` (null = default store).

Procedure: pause this worker's origin jobs, move the row in one database
transaction, then re-register the slug from the destination and drop the
origin jobs. A failure before commit resumes the paused jobs. Other gunicorn
workers keep their own in-memory schedulers: their stale origin jobs skip
themselves at fire time (``QSScheduler.definition_present``) and they start
the destination schedule after a restart.
"""
from typing import Any

from aiohttp import web

from querysource.handlers.manager import QueryManager
from querysource.handlers.query_stores import store_selector, tenant_error_response
from querysource.scheduler.jobs import in_flight
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

MOVE_VERSION = 1
_MOVE_KEYS = frozenset({"tenant", "destination_tenant"})


def _invalid(message: str) -> TenantError:
    """Return a ``move_validation_failed`` error."""
    return TenantError(message, error_code="move_validation_failed")


class QueryMoveView(QueryManager):
    """Same management boundary and scheduler grant as the CRUD write endpoints."""

    async def _move_body(self) -> dict[str, Any]:
        """Parse and validate the move body (raises ``move_validation_failed``)."""
        if "tenant" in self.request.query:
            raise _invalid("The tenant selectors of a move belong in the JSON body only")
        try:
            body = await self.request.json()
        except Exception as exc:  # noqa: BLE001 - any parse failure is the same 400
            raise _invalid("The request body must be a JSON object") from exc
        if not isinstance(body, dict):
            raise _invalid("The request body must be a JSON object")
        unknown = sorted(set(body) - _MOVE_KEYS)
        if unknown:
            raise _invalid("Unsupported move option(s): " + ", ".join(unknown))
        for key in sorted(_MOVE_KEYS):
            if key not in body:
                raise _invalid(f"'{key}' is required (use null for the default store)")
            value = body[key]
            if value is not None and (not isinstance(value, str) or not value):
                raise _invalid(f"'{key}' must be null or a non-empty string")
        return body

    @staticmethod
    def _describe(registry: TenantRegistry, store: QueryStore) -> dict[str, Any]:
        """Return the ``from``/``to`` descriptor of a store."""
        return {"tenant": store_selector(registry, store), "schema": store.schema, "table": store.table}

    async def _resync(self, scheduler: Any, slug: str, origin: QueryStore, destination_tenant: str | None) -> dict:
        """After commit: register the destination jobs, drop the origin ones; return status/count."""
        try:
            result = await scheduler.register_slug(slug, tenant=destination_tenant)
            return {"status": "ok", "registered": len(result.get("registered") or [])}
        except Exception:  # noqa: BLE001 - the move is committed; report, never undo
            self.logger.exception("Scheduler re-registration failed after moving '%s'", slug)
            return {"status": "failed", "registered": 0}
        finally:
            scheduler.remove_slug_jobs(slug, origin)

    async def post(self) -> web.Response:
        """Move one definition; 200 with the result or a ``{error_code, message, details?}`` error."""
        slug = self.request.match_info.get("slug")
        # FEAT-182: same slug:manage gate as the CRUD write methods.
        await self._enforce_definition_access(slug, "slug:manage")
        try:
            body = await self._move_body()
            registry = self.request.app.get("qs_tenant_registry")
            repo = self.request.app.get("qs_definition_repository")
            if registry is None or repo is None:
                raise TenantError("The query store registry is not available", error_code="tenant_store_unavailable")
            origin = registry.resolve(body["tenant"])
            destination = registry.resolve(body["destination_tenant"])
            if (origin.database_namespace, origin.schema, origin.table) == (
                destination.database_namespace, destination.schema, destination.table,
            ):
                raise TenantError("Origin and destination are the same store", error_code="move_same_store")
            actor, request_info = await self._run_as_context()
            if actor is None:
                raise TenantError(
                    "An authenticated user is required to move a definition", error_code="tenant_write_forbidden"
                )
            await self._enforce_scheduler_grant(repo, QueryIdentity(store=origin, slug=slug), {})

            scheduler = self.request.app.get("qs_scheduler")
            if in_flight(origin, slug):
                raise TenantError(
                    "A scheduled run of this definition is executing right now; retry when it finishes",
                    error_code="move_run_in_flight",
                )
            paused = scheduler.pause_slug_jobs(slug, origin) if scheduler is not None else []
            try:
                # Pausing stops future dispatches, not one that started in between.
                if in_flight(origin, slug):
                    raise TenantError(
                        "A scheduled run of this definition started while the move was being "
                        "prepared; retry it",
                        error_code="move_run_in_flight",
                    )
                await repo.move(slug, origin, destination, actor=actor, request_info=request_info)
            except BaseException:
                for job_id in paused:
                    scheduler.set_job_paused(job_id, False)
                raise
            self.logger.info(
                "Moved '%s' from %s.%s to %s.%s (actor=%s)",
                slug, origin.schema, origin.table, destination.schema, destination.table, actor,
            )
            if scheduler is None:
                sched = {"status": "not_configured", "paused": 0, "registered": 0}
            else:
                sched = {"paused": len(paused), **await self._resync(scheduler, slug, origin, body["destination_tenant"])}
        except TenantError as exc:
            return tenant_error_response(exc)
        except web.HTTPException:
            raise
        except Exception:  # noqa: BLE001 - never leak internal exception text
            self.logger.exception("Unexpected error moving '%s'", slug)
            return tenant_error_response(
                TenantError("The move could not be completed", error_code="tenant_store_unavailable")
            )
        return web.json_response({
            "version": MOVE_VERSION,
            "data": {
                "query_slug": slug,
                "from": self._describe(registry, origin),
                "to": self._describe(registry, destination),
                "scheduler": {"status": sched["status"], "paused": sched["paused"], "registered": sched["registered"]},
            },
        })
