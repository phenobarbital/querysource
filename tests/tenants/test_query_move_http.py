"""HTTP surface of query moves: validation, statuses, auth boundary, route precedence."""
import json

import pytest
from _movefixtures import (  # noqa: F401 - pg_dsn is a fixture
    LEGACY,
    SCHEDULED,
    TENANT,
    FakeScheduler,
    _factory,
    _Registry,
    insert_row,
    pg_dsn,
    setup_stores,
)
from aiohttp import web
from multidict import MultiDict

from querysource.handlers import query_moves as mod
from querysource.handlers.query_moves import QueryMoveHandler, register_routes
from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM, QueryMoveService
from querysource.repositories.definitions import DefinitionRepository

SLUG = "s"


class _Req(dict):
    """A minimal aiohttp request stand-in."""

    def __init__(self, app, body=None, *, query=None, match=None, method="POST"):
        super().__init__()
        self.app, self._body = app, body
        self.query = MultiDict(query or {})
        self.match_info = match if match is not None else {"slug": SLUG}
        self.method, self.path = method, "/api/v1/management/queries/s/move"

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


async def _app(dsn, rows=None, *, security=False, service=True):
    """A request-app stand-in over the REAL repository and real store tables."""
    await setup_stores(dsn)
    for (schema, slug), cols in (rows if rows is not None else {("public", SLUG): {}}).items():
        await insert_row(dsn, LEGACY if schema == "public" else TENANT, slug, **cols)
    registry = _Registry(LEGACY)
    registry._stores = (LEGACY, TENANT)  # the published immutable snapshot
    repo = DefinitionRepository(registry, _factory(dsn))
    svc = QueryMoveService(repo, registry=registry, scheduler=FakeScheduler())
    app = {"qs_tenant_registry": registry, "qs_definition_repository": repo}
    if service:
        app["qs_move_service"] = svc
    if security:
        app["security"] = object()
    return app, svc, repo


@pytest.fixture(autouse=True)
def _actor(monkeypatch):
    async def fake(request):
        return request.get("actor", 7)

    monkeypatch.setattr(mod, "_actor", fake)


def _json(response):
    return json.loads(response.body)


def _move_body(plan, **over):
    body = {"tenant": None, "destination_tenant": "wm_assembly", "plan_revision": plan["plan_revision"],
            ACK_CONSUMERS: True, ACK_PROGRAM: True}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_preview_returns_200_plan_and_move_returns_200_result(pg_dsn) -> None:
    app, _, repo = await _app(pg_dsn)
    handler = QueryMoveHandler()

    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}))
    assert resp.status == 200
    plan = _json(resp)
    assert plan["can_move"] is True and plan["source"]["tenant"] is None
    assert plan["version"] == 1

    # The fake repo must actually perform the move for the handler's result to be truthful.
    resp = await handler.move(_Req(app, _move_body(plan)))
    assert resp.status == 200
    result = _json(resp)
    assert result["moved"] is True and result["version"] == 1
    assert result["destination"]["tenant"] == "wm_assembly"
    assert result["scheduler_sync"] == "ok"


@pytest.mark.asyncio
async def test_resubmitting_after_a_lost_response_is_a_plain_404(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    body = _move_body(plan)
    assert (await handler.move(_Req(app, body))).status == 200

    again = await handler.move(_Req(app, body))

    assert again.status == 404 and _json(again)["error_code"] == "query_not_found"


@pytest.mark.asyncio
async def test_selector_and_body_validation_returns_structured_400(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    cases = [
        (_Req(app, {"destination_tenant": "wm_assembly"}), "'tenant' is required"),
        (_Req(app, {"tenant": None}), "'destination_tenant' is required"),
        (_Req(app, {"tenant": "", "destination_tenant": "wm_assembly"}), "non-empty string"),
        (_Req(app, {"tenant": None, "destination_tenant": "wm_assembly", "nope": 1}), "Unsupported move option"),
        (_Req(app, ["not", "a", "dict"]), "must be a JSON object"),
        (_Req(app, ValueError("bad json")), "must be a JSON object"),
        (_Req(app, {"tenant": None, "destination_tenant": "wm"}, query={"tenant": "x"}), "JSON body only"),
    ]
    for request, fragment in cases:
        resp = await handler.preview(request)
        assert resp.status == 400, fragment
        body = _json(resp)
        assert body["error_code"] == "move_validation_failed"
        assert fragment in body["message"]


@pytest.mark.asyncio
async def test_unknown_store_and_missing_source_use_existing_mappings(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()

    resp = await handler.preview(_Req(app, {"tenant": "nope", "destination_tenant": "wm_assembly"}))
    assert resp.status in (404, 503)

    resp = await handler.preview(
        _Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}, match={"slug": "absent"})
    )
    assert resp.status == 404 and _json(resp)["error_code"] == "query_not_found"


@pytest.mark.asyncio
async def test_move_validates_acks_and_revision(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))

    resp = await handler.move(_Req(app, _move_body(plan, plan_revision="")))
    assert resp.status == 400 and "'plan_revision' is required" in _json(resp)["message"]

    resp = await handler.move(_Req(app, _move_body(plan, **{ACK_CONSUMERS: "yes"})))
    assert resp.status == 400 and "must be a boolean" in _json(resp)["message"]

    resp = await handler.move(_Req(app, _move_body(plan, **{ACK_CONSUMERS: False})))
    assert resp.status == 400 and _json(resp)["error_code"] == "move_acknowledgement_required"

    resp = await handler.move(_Req(app, _move_body(plan, plan_revision="stale")))
    assert resp.status == 409 and _json(resp)["error_code"] == "move_plan_stale"


@pytest.mark.asyncio
async def test_blockers_are_reported_as_409_with_their_code(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn, {("public", SLUG): {}, ("wm_assembly", SLUG): {}})
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    assert plan["can_move"] is False

    resp = await handler.move(_Req(app, _move_body(plan)))

    assert resp.status == 409
    body = _json(resp)
    assert body["error_code"] == "move_target_exists"
    assert body["details"]["blockers"][0]["code"] == "move_target_exists"


@pytest.mark.asyncio
async def test_unauthenticated_actor_is_refused(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    request = _Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})
    request["actor"] = None

    resp = await handler.preview(request)

    assert resp.status == 403 and _json(resp)["error_code"] == "tenant_write_forbidden"


@pytest.mark.asyncio
async def test_pg_admin_gate_is_kept_for_scheduled_write_capable_multi(pg_dsn, monkeypatch) -> None:
    row = {"provider": "multi", "attributes": SCHEDULED, "query_raw": "{}"}
    app, _, _ = await _app(pg_dsn, {("public", SLUG): row}, security=True)
    handler = QueryMoveHandler()
    monkeypatch.setattr(mod, "definition_requires_scheduler_grant", lambda row: True)
    calls = []

    async def denied(request, resource, name, action, logger=None):
        calls.append((name, action))
        raise web.HTTPNotFound()

    monkeypatch.setattr(mod, "enforce_request_pbac", denied)

    with pytest.raises(web.HTTPNotFound):
        await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}))

    assert calls == [("pg_admin", "datasource:use")]


@pytest.mark.asyncio
async def test_numeric_actor_alone_is_not_a_grant(pg_dsn, monkeypatch) -> None:
    """An authenticated numeric actor never substitutes for the PBAC decision."""
    row = {"provider": "multi", "attributes": SCHEDULED, "query_raw": "{}"}
    app, _, _ = await _app(pg_dsn, {("public", SLUG): row}, security=True)
    handler = QueryMoveHandler()
    monkeypatch.setattr(mod, "definition_requires_scheduler_grant", lambda row: True)

    async def denied(request, resource, name, action, logger=None):
        raise web.HTTPNotFound()

    monkeypatch.setattr(mod, "enforce_request_pbac", denied)
    request = _Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})
    request["actor"] = 12345  # a perfectly good numeric actor

    with pytest.raises(web.HTTPNotFound):
        await handler.preview(request)


@pytest.mark.asyncio
async def test_missing_service_is_an_explicit_error_not_a_crash(pg_dsn) -> None:
    app, _, _ = await _app(pg_dsn, service=False)
    handler = QueryMoveHandler()

    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}))

    assert resp.status == 503 and _json(resp)["error_code"] == "tenant_store_unavailable"


@pytest.mark.asyncio
async def test_static_routes_win_over_the_broad_management_patterns() -> None:
    router = web.UrlDispatcher()
    routes = register_routes(router)
    assert len(routes) == 3
    # The broad management pattern is registered AFTER, as services.py does.
    router.add_route("*", "/api/v1/management/queries{meta:\\:?.*}", lambda r: None)

    for path, method, canonical in (
        ("/api/v1/management/query-stores", "GET", "/api/v1/management/query-stores"),
        ("/api/v1/management/queries/s/move", "POST", "/api/v1/management/queries/{slug}/move"),
        ("/api/v1/management/queries/s/move/preview", "POST",
         "/api/v1/management/queries/{slug}/move/preview"),
    ):
        resolved = await router.resolve(_Resolve(path, method))
        # The broad ``queries{meta:...}`` pattern must NOT swallow these.
        assert resolved.route.resource.canonical == canonical, path


class _Resolve:
    """The slice of a request aiohttp's router inspects."""

    def __init__(self, path, method):
        self.rel_url = type("U", (), {"path": path, "path_safe": path})()
        self.method = method
        self.path = path
        self.raw_path = path
