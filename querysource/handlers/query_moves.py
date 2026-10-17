"""HTTP surface of explicit query moves (FEAT-176, M3).

Routes (static, registered before the broad management/tenant routes):

    GET  /api/v1/management/query-stores
    POST /api/v1/management/queries/{slug}/move/preview
    POST /api/v1/management/queries/{slug}/move
    GET  /api/v1/management/query-moves/{operation_id}
    POST /api/v1/management/query-moves/{operation_id}/reconcile
    POST /api/v1/management/query-moves/{operation_id}/abort

Authentication/authorization is the SAME deployed management boundary as the existing CRUD
routes (app-level auth, database permissions, existing PBAC). No administrators-only rule, no
new role and no ``slug:move`` action is added: the numeric authenticated actor is resolved for
move audit attribution only and a client acknowledgement is never authorization. The existing
``datasource:use`` on ``pg_admin`` gate still applies to a scheduled write-capable multi.
"""
import uuid
from collections.abc import Mapping
from typing import Any

from aiohttp import web
from navconfig.logging import logging
from navigator_session import get_session

from querysource.auth import ResourceType
from querysource.auth.identity_tokens import user_id_from_session
from querysource.auth.request_gate import enforce_request_pbac
from querysource.handlers.query_stores import QueryStoreCatalog
from querysource.queries.multi import definition_requires_scheduler_grant
from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM, QueryMoveService
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

logger = logging.getLogger("QS.QueryMoves")

_PREVIEW_KEYS = frozenset({"tenant", "destination_tenant"})
_MOVE_KEYS = _PREVIEW_KEYS | {"plan_revision", ACK_CONSUMERS, ACK_PROGRAM, "idempotency_key"}
_ABORT_KEYS = frozenset({"resolve_unknown_runs"})
_TERMINAL = ("completed", "failed")


def _error(code: str, message: str, status: int, details: Mapping[str, Any] | None = None) -> web.Response:
    body: dict[str, Any] = {"error_code": code, "message": message}
    if details:
        body["details"] = dict(details)
    return web.json_response(body, status=status)


def _tenant_error(exc: TenantError) -> web.Response:
    return _error(exc.error_code, str(exc), exc.code, exc.details)


def _invalid(message: str) -> TenantError:
    return TenantError(message, error_code="move_validation_failed")


async def _actor(request: web.Request) -> int | None:
    """Numeric authenticated user id for audit attribution (None when unauthenticated)."""
    try:
        session = await get_session(request, new=False)
    except Exception as exc:  # noqa: BLE001 - the session system may be absent
        logger.warning("Move actor lookup failed (%s); treating as unauthenticated", exc)
        return None
    return user_id_from_session(session)


class QueryMoveHandler:
    """Preview, submit, inspect and reconcile explicit single-definition moves."""

    # -- request plumbing ---------------------------------------------------------------

    @staticmethod
    def _service(request: web.Request) -> QueryMoveService:
        service = request.app.get("qs_move_service")
        if service is None:
            raise TenantError(
                "Query moves are not available: the control tables are not deployed or the service did not start",
                error_code="move_scheduler_not_ready",
            )
        return service

    @staticmethod
    def _registry(request: web.Request) -> TenantRegistry:
        registry = request.app.get("qs_tenant_registry")
        if registry is None:
            raise TenantError("The query store registry is not available", error_code="tenant_store_unavailable")
        return registry

    @staticmethod
    async def _body(request: web.Request, allowed: frozenset[str]) -> dict[str, Any]:
        """Parse the JSON object body; routing selectors live ONLY in it (never URL/query)."""
        if "tenant" in request.query or "tenant" in request.match_info:
            raise _invalid("The tenant selectors of a move belong in the JSON body only")
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001 - any parse failure is the same 400
            raise _invalid("The request body must be a JSON object") from exc
        if not isinstance(body, dict):
            raise _invalid("The request body must be a JSON object")
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise _invalid("Unsupported move option(s): " + ", ".join(unknown))
        for key in ("tenant", "destination_tenant"):
            if key not in body:
                raise _invalid(f"'{key}' is required (use null for the configured default)")
            value = body[key]
            if value is not None and (not isinstance(value, str) or value == ""):
                raise _invalid(f"'{key}' must be null or a non-empty string")
        return body

    def _stores(self, request: web.Request, body: Mapping[str, Any]) -> tuple[QueryStore, QueryStore]:
        registry = self._registry(request)
        return registry.resolve(body["tenant"]), registry.resolve(body["destination_tenant"])

    @staticmethod
    def _slug(request: web.Request) -> str:
        slug = request.match_info.get("slug")
        if not slug:
            raise _invalid("A query slug is required in the path")
        return slug

    @staticmethod
    async def _require_actor(request: web.Request) -> int:
        actor = await _actor(request)
        if actor is None:
            raise TenantError("An authenticated user is required to move a definition",
                              error_code="tenant_write_forbidden")
        return actor

    async def _enforce_scheduler_grant(self, request: web.Request, identity: QueryIdentity) -> None:
        """Keep the ``pg_admin`` gate for a scheduled write-capable multi (same rule as CRUD/sync)."""
        if request.app.get("security") is None:
            return
        repo = request.app.get("qs_definition_repository")
        if repo is None:
            return
        try:
            loaded = await repo.get(identity)
        except TenantError as exc:
            if exc.error_code == "query_not_found":
                return
            raise
        runtime = loaded.runtime
        row = {
            "provider": getattr(runtime, "provider", None),
            "query_raw": getattr(runtime, "query_raw", None),
            "attributes": getattr(runtime, "attributes", None),
        }
        if definition_requires_scheduler_grant(row):
            await enforce_request_pbac(request, ResourceType.DATASOURCE, "pg_admin", "datasource:use", logger=logger)

    @staticmethod
    def _unexpected(request: web.Request, exc: Exception) -> web.Response:
        logger.exception("Unexpected error in %s %s", request.method, request.path)
        return _error("internal_error", "The request could not be processed.", 500)

    # -- endpoints ----------------------------------------------------------------------

    async def preview(self, request: web.Request) -> web.Response:
        """``200 MovePlan``: read-only plan for ``{tenant, destination_tenant}``."""
        try:
            service = self._service(request)
            body = await self._body(request, _PREVIEW_KEYS)
            source_store, destination = self._stores(request, body)
            identity = QueryIdentity(store=source_store, slug=self._slug(request))
            actor = await self._require_actor(request)
            await self._enforce_scheduler_grant(request, identity)
            plan = await service.preview(identity, destination, actor=actor)
            return web.json_response(plan, status=200)
        except TenantError as exc:
            return _tenant_error(exc)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._unexpected(request, exc)

    async def move(self, request: web.Request) -> web.Response:
        """``202 MoveOperation``: durable submission (or the same operation on an authorized retry)."""
        try:
            service = self._service(request)
            body = await self._body(request, _MOVE_KEYS)
            source_store, destination = self._stores(request, body)
            identity = QueryIdentity(store=source_store, slug=self._slug(request))
            actor = await self._require_actor(request)
            acknowledgements: dict[str, bool] = {}
            for name in (ACK_CONSUMERS, ACK_PROGRAM):
                value = body.get(name, False)
                if not isinstance(value, bool):
                    raise _invalid(f"'{name}' must be a boolean")
                acknowledgements[name] = value
            plan_revision = body.get("plan_revision")
            if not isinstance(plan_revision, str) or not plan_revision:
                raise _invalid("'plan_revision' is required")
            key = body.get("idempotency_key")
            try:
                if not isinstance(key, str):
                    raise ValueError
                uuid.UUID(key)
            except ValueError as exc:
                raise _invalid("'idempotency_key' must be a UUID") from exc
            await self._enforce_scheduler_grant(request, identity)
            operation = await service.move(
                identity, destination, plan_revision=plan_revision, idempotency_key=key, actor=actor,
                acknowledgements=acknowledgements,
                request_info={"method": request.method, "path": request.path},
            )
            worker = request.app.get("qs_move_worker")
            if worker is not None:
                worker.wake()
            return web.json_response(operation, status=202)
        except TenantError as exc:
            return _tenant_error(exc)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._unexpected(request, exc)

    async def status(self, request: web.Request) -> web.Response:
        """``200 MoveOperation``: pure read under the existing management read boundary."""
        try:
            service = self._service(request)
            operation = await service.status(request.match_info.get("operation_id", ""))
            return web.json_response(operation, status=200)
        except TenantError as exc:
            return _tenant_error(exc)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._unexpected(request, exc)

    @staticmethod
    async def _abort_options(request: web.Request) -> bool:
        """Parse the optional abort body ``{"resolve_unknown_runs": bool}`` (an empty body is allowed)."""
        if getattr(request, "body_exists", True) is False:
            return False
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001 - any parse failure is the same 400
            raise _invalid("The request body must be a JSON object") from exc
        if body is None:
            return False
        if not isinstance(body, dict):
            raise _invalid("The request body must be a JSON object")
        unknown = sorted(set(body) - _ABORT_KEYS)
        if unknown:
            raise _invalid("Unsupported abort option(s): " + ", ".join(unknown))
        value = body.get("resolve_unknown_runs", False)
        if not isinstance(value, bool):
            raise _invalid("'resolve_unknown_runs' must be a boolean")
        return value

    async def abort(self, request: web.Request) -> web.Response:
        """``200 MoveOperation`` (``failed``/``move_aborted``): abandon a move that has not cut over.

        Only ``preparing``/``draining`` operations with ``moved=false`` can be aborted; the source
        gates are re-opened and the reservations released. ``409 move_in_progress`` once the
        cutover marker exists (an indeterminate or committed cutover is only ever reconciled).
        """
        try:
            service = self._service(request)
            actor = await self._require_actor(request)
            operation_id = request.match_info.get("operation_id", "")
            await service.status(operation_id)  # 404/400 before any mutation
            resolve = await self._abort_options(request)
            operation = await service.abort(operation_id, actor=actor, resolve_unknown_runs=resolve)
            return web.json_response(operation, status=200)
        except TenantError as exc:
            return _tenant_error(exc)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._unexpected(request, exc)

    async def reconcile(self, request: web.Request) -> web.Response:
        """Repair the SAME operation (never repeats the row move).

        ``200`` once the operation is final, ``202`` while it is still in progress (a committed
        move that is still transferring schedules is NOT a completed success).
        """
        try:
            service = self._service(request)
            await self._require_actor(request)
            operation_id = request.match_info.get("operation_id", "")
            await service.status(operation_id)  # 404/400 before any mutation
            operation = await service.reconcile(operation_id)
            final = operation["state"] in _TERMINAL
            return web.json_response(operation, status=200 if final else 202)
        except TenantError as exc:
            return _tenant_error(exc)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._unexpected(request, exc)


def register_routes(router: web.UrlDispatcher) -> list[Any]:
    """Register the exact static routes. Call BEFORE the broad management/tenant routes.

    The management ``queries{meta:\\:?.*}`` pattern can swallow ``/queries/{slug}/move`` paths,
    so these must be registered first.
    """
    handler = QueryMoveHandler()
    catalog = QueryStoreCatalog()
    return [
        router.add_get("/api/v1/management/query-stores", catalog.list, allow_head=False),
        router.add_post("/api/v1/management/queries/{slug}/move/preview", handler.preview),
        router.add_post("/api/v1/management/queries/{slug}/move", handler.move),
        router.add_get("/api/v1/management/query-moves/{operation_id}", handler.status, allow_head=False),
        router.add_post("/api/v1/management/query-moves/{operation_id}/reconcile", handler.reconcile),
        router.add_post("/api/v1/management/query-moves/{operation_id}/abort", handler.abort),
    ]
