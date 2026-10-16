"""HTTP surface of query moves: validation, statuses, auth boundary, route precedence."""
import json
import uuid

import pytest
from aiohttp import web
from multidict import MultiDict
from test_handoff_state import _factory, _reset, _store, pg_dsn  # noqa: F401 - pg_dsn is a fixture
from test_query_moves import LEGACY, SCHEDULED, TENANT, _row, _service

from querysource.handlers import query_moves as mod
from querysource.handlers.query_moves import QueryMoveHandler, register_routes
from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM
from querysource.tenants import QueryIdentity  # noqa: F401

SLUG = "s"
ACKS = {ACK_CONSUMERS: True, ACK_PROGRAM: True}


class _Req(dict):
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


async def _app(dsn, rows=None, *, security=False):
    svc, repo, coord = await _service(dsn, rows if rows is not None else {("public", SLUG): _row()})
    repo.registry._stores = (LEGACY, TENANT)  # the published immutable snapshot
    app = {"qs_tenant_registry": repo.registry, "qs_definition_repository": repo, "qs_move_service": svc}
    if security:
        app["security"] = object()
    return app, svc, repo, coord


@pytest.fixture(autouse=True)
def _actor(monkeypatch):
    async def fake(request):
        return request.get("actor", 7)

    monkeypatch.setattr(mod, "_actor", fake)


def _json(response):
    return json.loads(response.body)


def _move_body(plan, **over):
    body = {"tenant": None, "destination_tenant": "wm_assembly", "plan_revision": plan["plan_revision"],
            ACK_CONSUMERS: True, ACK_PROGRAM: True, "idempotency_key": str(uuid.uuid4())}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_preview_returns_200_plan_and_move_returns_202_then_status_200(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, svc, _, _ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}))
    assert resp.status == 200
    plan = _json(resp)
    assert plan["can_move"] is True and plan["source"]["tenant"] is None

    body = _move_body(plan)
    resp = await handler.move(_Req(app, body))
    assert resp.status == 202
    op = _json(resp)
    assert op["state"] == "preparing" and op["moved"] is False and op["version"] == 1
    again = await handler.move(_Req(app, body))  # lost response: the same authorized retry
    assert again.status == 202 and _json(again)["operation_id"] == op["operation_id"]

    resp = await handler.status(_Req(app, match={"operation_id": op["operation_id"]}, method="GET"))
    assert resp.status == 200 and _json(resp) == op


@pytest.mark.asyncio
async def test_selector_and_body_validation_returns_structured_400(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, *_ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    cases = [
        {"destination_tenant": "wm_assembly"},                                  # tenant key omitted
        {"tenant": None},                                                      # destination omitted
        {"tenant": "", "destination_tenant": "wm_assembly"},
        {"tenant": None, "destination_tenant": 5},
        {"tenant": None, "destination_tenant": "wm_assembly", "overwrite": True},  # extra option
        ["not", "an", "object"],
        ValueError("bad json"),
    ]
    for body in cases:
        resp = await handler.preview(_Req(app, body))
        assert resp.status == 400, body
        assert _json(resp)["error_code"] == "move_validation_failed"
    # selectors never travel in the URL/query for a move
    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"},
                                      query={"tenant": "wm_assembly"}))
    assert resp.status == 400
    # identical canonical stores
    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "public"}))
    assert resp.status == 400 and _json(resp)["error_code"] == "move_same_store"


@pytest.mark.asyncio
async def test_unknown_store_and_missing_source_use_existing_mappings(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, *_ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "nope"}))
    assert resp.status == 404 and _json(resp)["error_code"] == "tenant_not_available"
    resp = await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"},
                                      match={"slug": "missing"}))
    assert resp.status == 404 and _json(resp)["error_code"] == "query_not_found"


@pytest.mark.asyncio
async def test_move_validates_key_acks_revision_and_returns_move_errors(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, *_ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    for over in ({"idempotency_key": "nope"}, {"idempotency_key": 5}, {"plan_revision": ""},
                 {ACK_CONSUMERS: "yes"}):
        resp = await handler.move(_Req(app, _move_body(plan, **over)))
        assert resp.status == 400 and _json(resp)["error_code"] == "move_validation_failed", over
    body = _move_body(plan)
    body.pop(ACK_PROGRAM)
    resp = await handler.move(_Req(app, body))
    assert resp.status == 400 and _json(resp)["error_code"] == "move_acknowledgement_required"
    resp = await handler.move(_Req(app, _move_body(plan, plan_revision="0" * 64)))
    assert resp.status == 409 and _json(resp)["error_code"] == "move_plan_stale"


@pytest.mark.asyncio
async def test_idempotency_conflict_and_overlap_are_409(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, *_ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    body = _move_body(plan)
    assert (await handler.move(_Req(app, body))).status == 202
    resp = await handler.move(_Req(app, {**body, ACK_PROGRAM: False}))
    assert resp.status == 409 and _json(resp)["error_code"] == "move_idempotency_conflict"
    resp = await handler.move(_Req(app, _move_body(plan)))  # new key, same identities
    assert resp.status == 409 and _json(resp)["error_code"] == "move_in_progress"


@pytest.mark.asyncio
async def test_unauthenticated_actor_is_refused_and_status_is_a_read(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, *_ = await _app(pg_dsn)
    handler = QueryMoveHandler()
    anon = _Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})
    anon["actor"] = None
    resp = await handler.preview(anon)
    assert resp.status == 403 and _json(resp)["error_code"] == "tenant_write_forbidden"
    # status needs only the management read boundary (no numeric actor, no admin role)
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    reader = _Req(app, match={"operation_id": op["operation_id"]}, method="GET")
    reader["actor"] = None
    assert (await handler.status(reader)).status == 200
    for bad in ("nope", str(uuid.uuid4())):
        resp = await handler.status(_Req(app, match={"operation_id": bad}, method="GET"))
        assert resp.status in (400, 404)


@pytest.mark.asyncio
async def test_pg_admin_gate_is_kept_for_scheduled_write_capable_multi(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    pipeline = json.dumps({"queries": {}, "Output": [{"TableOutput": {}}]})
    row = _row(provider="multi", attributes=SCHEDULED, query_raw=pipeline)
    app, *_ = await _app(pg_dsn, {("public", SLUG): row}, security=True)
    calls = []

    async def deny(request, resource, name, action, logger=None):
        calls.append((name, action))
        raise web.HTTPNotFound()

    monkeypatch.setattr(mod, "enforce_request_pbac", deny)
    monkeypatch.setattr(mod, "definition_requires_scheduler_grant", lambda r: r["provider"] == "multi")
    with pytest.raises(web.HTTPNotFound):
        await QueryMoveHandler().preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"}))
    assert calls == [("pg_admin", "datasource:use")]
    # An ordinary definition never reaches the gate.
    calls.clear()
    app2, *_ = await _app(pg_dsn, {("public", SLUG): _row()}, security=True)
    assert (await QueryMoveHandler().preview(
        _Req(app2, {"tenant": None, "destination_tenant": "wm_assembly"}))).status == 200
    assert calls == []


@pytest.mark.asyncio
async def test_reconcile_is_202_until_final_and_never_repeats_the_move(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, svc, repo, coord = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    # Not cut over yet: reconcile cannot start a move.
    resp = await handler.reconcile(_Req(app, match={"operation_id": op["operation_id"]}))
    assert resp.status == 202 and _json(resp)["moved"] is False and _json(resp)["state"] == "preparing"
    async with await _factory(pg_dsn)() as conn:  # durable cutover committed, mirrors pending
        await conn.execute("UPDATE management.qs_query_moves SET state = 'cutover', moved = true")
    resp = await handler.reconcile(_Req(app, match={"operation_id": op["operation_id"]}))
    assert resp.status == 200 and _json(resp)["state"] == "completed"
    unauth = _Req(app, match={"operation_id": op["operation_id"]})
    unauth["actor"] = None
    assert (await handler.reconcile(unauth)).status == 403
    missing = await handler.reconcile(_Req(app, match={"operation_id": str(uuid.uuid4())}))
    assert missing.status == 404


@pytest.mark.asyncio
async def test_missing_service_is_an_explicit_conflict_not_a_crash(pg_dsn) -> None:
    resp = await QueryMoveHandler().preview(_Req({}, {"tenant": None, "destination_tenant": "x"}))
    assert resp.status == 409 and _json(resp)["error_code"] == "move_scheduler_not_ready"


@pytest.mark.asyncio
async def test_static_routes_win_over_the_broad_management_patterns() -> None:
    app = web.Application()
    routes = register_routes(app.router)
    assert len(routes) == 5
    # The broad management pattern registered AFTER would otherwise swallow /queries/{slug}/move.
    async def broad(request):  # pragma: no cover - must never be selected
        return web.Response(text="broad")

    app.router.add_view(r"/api/v1/management/queries{meta:\:?.*}", broad)
    app.router.add_get("/api/v1/{tenant}/queries/{slug}", broad)
    for method, path, name in [
        ("POST", "/api/v1/management/queries/wm_pipeline/move/preview", "preview"),
        ("POST", "/api/v1/management/queries/wm_pipeline/move", "move"),
        ("GET", "/api/v1/management/query-moves/abc", "status"),
        ("POST", "/api/v1/management/query-moves/abc/reconcile", "reconcile"),
        ("GET", "/api/v1/management/query-stores", "list"),
    ]:
        match = await app.router.resolve(type("R", (), {"method": method, "rel_url": __import__("yarl").URL(path),
                                                         "path": path, "host": "h"})())
        assert match.handler.__name__ == name, path
