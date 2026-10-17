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
    # status is a pure read for the submitting actor (no admin role) ...
    plan = _json(await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "wm_assembly"})))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    assert (await handler.status(_Req(app, match={"operation_id": op["operation_id"]}, method="GET"))).status == 200
    # ... but never for an anonymous or a different user (I1: no cross-actor access by id).
    for other in (None, 8):
        reader = _Req(app, match={"operation_id": op["operation_id"]}, method="GET")
        reader["actor"] = other
        resp = await handler.status(reader)
        assert resp.status == 403 and _json(resp)["error_code"] == "tenant_write_forbidden"
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
    assert len(routes) == 6
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
        ("POST", "/api/v1/management/query-moves/abc/abort", "abort"),
        ("GET", "/api/v1/management/query-stores", "list"),
    ]:
        match = await app.router.resolve(type("R", (), {"method": method, "rel_url": __import__("yarl").URL(path),
                                                         "path": path, "host": "h"})())
        assert match.handler.__name__ == name, path


# -- FEAT-176 TASK-927: real HTTP responses vs the frozen v1 fixture --------------------------------

from pathlib import Path  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_move_contract_v1.json"
PREVIEW = {"tenant": None, "destination_tenant": "wm_assembly"}


async def _scenario(code, dsn):
    """Provoke ``code`` through the real handler/service/PostgreSQL stack; return the HTTP response."""
    await _reset(dsn)
    handler = QueryMoveHandler()
    rows = {("public", SLUG): _row()}
    if code == "move_target_exists":
        rows[("wm_assembly", SLUG)] = _row()
    elif code == "move_stale_run_as":
        rows[("public", SLUG)] = _row(scheduler_run_as_user_id=5)
    elif code in ("move_inherited_references", "move_dependency_analysis_unsupported"):
        raw = json.dumps({"queries": {"child": {"slug": "c"}}}) if code == "move_inherited_references" else "{nope"
        rows[("public", SLUG)] = _row(provider="multi", query_raw=raw)
    elif code == "move_scheduler_unsupported":
        rows[("public", SLUG)] = _row(dwh=True, dwh_scheduler={"cron": "* * * * *"})
    elif code in ("move_scheduler_not_ready", "move_schedule_invalid"):
        rows[("public", SLUG)] = _row(attributes=SCHEDULED)
    app, svc, repo, coord = await _app(dsn, rows)
    if code == "move_schedule_invalid":
        coord._trigger_parser = lambda t, s: None
    if code == "move_same_store":
        return await handler.preview(_Req(app, {"tenant": None, "destination_tenant": "public"}))
    if code == "move_validation_failed":
        return await handler.preview(_Req(app, {"tenant": None}))

    async def submit(**over):
        plan = await svc.preview(QueryIdentity(store=LEGACY, slug=SLUG), TENANT, actor=7)
        return await handler.move(_Req(app, _move_body(plan, **over)))

    if code == "move_acknowledgement_required":
        return await submit(**{ACK_PROGRAM: False})
    if code == "move_plan_stale":
        return await submit(plan_revision="f" * 64)
    if code == "move_in_progress":
        assert (await submit()).status == 202
        return await submit()
    if code == "move_idempotency_conflict":
        plan = await svc.preview(QueryIdentity(store=LEGACY, slug=SLUG), TENANT, actor=7)
        body = _move_body(plan)
        assert (await handler.move(_Req(app, body))).status == 202
        return await handler.move(_Req(app, {**body, ACK_PROGRAM: False}))
    if code == "move_outcome_unknown":
        plan = await svc.preview(QueryIdentity(store=LEGACY, slug=SLUG), TENANT, actor=7)
        op = _json(await handler.move(_Req(app, _move_body(plan))))
        await coord.prepare(op["operation_id"])
        async with await _factory(dsn)() as conn:
            await conn.execute("UPDATE management.qs_query_moves SET state = 'cutover', moved = NULL")
        from querysource.scheduler.handoff import ScheduleOwner

        key = ScheduleOwner.from_store(LEGACY, SLUG).lock_key()
        async with await _factory(dsn)() as holder:  # the original cutover transaction is still alive
            await holder.fetchval("SELECT pg_advisory_lock($1)", key, column=0)
            return await handler.reconcile(_Req(app, match={"operation_id": op["operation_id"]}))
    if code == "move_scheduler_not_ready":
        return await submit()  # scheduled, but no durable bootstrap / no compatible worker yet
    # remaining codes are submission-time blockers
    return await submit()


@pytest.mark.asyncio
async def test_every_documented_error_is_reachable_with_the_fixture_status_and_body_shape(pg_dsn) -> None:
    fx = json.loads(FIXTURE.read_text())
    for code, expected in fx["errors"].items():
        response = await _scenario(code, pg_dsn)
        body = _json(response)
        assert response.status == expected["status"], (code, body)
        assert body["error_code"] == code, (code, body)
        assert set(body) <= {"error_code", "message", "details"} and isinstance(body["message"], str), code
        assert set(expected["body"]) <= set(body)
        text = json.dumps(body).lower()
        for needle in ("password", "dsn", "postgresql://", "secret", "scheduler_run_as_user_id", "traceback"):
            assert needle not in text, (code, needle)


@pytest.mark.asyncio
async def test_success_statuses_and_shapes_follow_the_fixture(pg_dsn) -> None:
    fx = json.loads(FIXTURE.read_text())
    await _reset(pg_dsn)
    app, svc, repo, coord = await _app(pg_dsn)
    handler = QueryMoveHandler()
    preview = await handler.preview(_Req(app, fx["preview"]["request"]["body"]))
    assert preview.status == 200 and set(_json(preview)) == set(fx["preview"]["response"])
    body = _move_body(_json(preview))
    assert set(body) == set(fx["move"]["request"]["body"])
    move = await handler.move(_Req(app, body))
    assert move.status == fx["move"]["response_status"]
    op = _json(move)
    status = await handler.status(_Req(app, match={"operation_id": op["operation_id"]}, method="GET"))
    assert status.status == fx["status"]["response_status"] and _json(status) == op
    assert set(op) - {"error_code", "message"} == set(fx["operations"]["preparing"]) - {"error_code", "message"}
    reconcile = await handler.reconcile(_Req(app, match={"operation_id": op["operation_id"]}))
    assert reconcile.status in fx["reconcile"]["response_status"]
    catalog = await _catalog(app)
    assert catalog == fx["catalog"]["response"] or {d["schema"] for d in catalog["data"]} >= {"public"}


async def _catalog(app):
    from querysource.handlers.query_stores import QueryStoreCatalog

    request = type("R", (), {"app": app})()
    return json.loads((await QueryStoreCatalog().list(request)).body)


@pytest.mark.asyncio
async def test_numeric_actor_alone_is_not_a_grant_and_operators_keep_current_access(pg_dsn, monkeypatch) -> None:
    await _reset(pg_dsn)
    pipeline = json.dumps({"queries": {}, "Output": [{"TableOutput": {}}]})
    calls = []

    async def deny(request, resource, name, action, logger=None):
        calls.append((name, action))
        raise web.HTTPNotFound()

    monkeypatch.setattr(mod, "enforce_request_pbac", deny)
    monkeypatch.setattr(mod, "definition_requires_scheduler_grant", lambda r: r["provider"] == "multi")
    # A write-capable scheduled multi: the numeric actor does not bypass the pg_admin gate.
    app, *_ = await _app(pg_dsn, {("public", SLUG): _row(provider="multi", attributes=SCHEDULED,
                                                         query_raw=pipeline)}, security=True)
    handler = QueryMoveHandler()
    for verb, body in ((handler.preview, PREVIEW),):
        with pytest.raises(web.HTTPNotFound):
            await verb(_Req(app, body))
    assert calls == [("pg_admin", "datasource:use")]
    # An ordinary definition: the same operator (no admin role) keeps current access end to end.
    app2, *_ = await _app(pg_dsn, {("public", SLUG): _row()}, security=True)
    resp = await handler.preview(_Req(app2, PREVIEW))
    assert resp.status == 200 and calls == [("pg_admin", "datasource:use")]


@pytest.mark.asyncio
async def test_abort_endpoint_statuses_and_body_validation(pg_dsn) -> None:
    fx = json.loads(FIXTURE.read_text())
    await _reset(pg_dsn)
    app, svc, repo, coord = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, PREVIEW)))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    oid = op["operation_id"]
    bad = await handler.abort(_Req(app, {"force": True}, match={"operation_id": oid}))
    assert bad.status == 400 and _json(bad)["error_code"] == "move_validation_failed"
    bad = await handler.abort(_Req(app, {"resolve_unknown_runs": "yes"}, match={"operation_id": oid}))
    assert bad.status == 400
    anon = _Req(app, None, match={"operation_id": oid})
    anon["actor"] = None
    assert (await handler.abort(anon)).status == 403
    assert (await handler.abort(_Req(app, None, match={"operation_id": str(uuid.uuid4())}))).status == 404
    resp = await handler.abort(_Req(app, fx["abort"]["request"]["body"], match={"operation_id": oid}))
    body = _json(resp)
    assert resp.status == fx["abort"]["response_status"]
    assert body["state"] == "failed" and body["moved"] is False and body["error_code"] == "move_aborted"
    assert set(body) == set(fx["operations"]["aborted"])
    # Past cutover the abort is refused with the documented status/code.
    plan = _json(await handler.preview(_Req(app, PREVIEW)))
    op2 = _json(await handler.move(_Req(app, _move_body(plan))))
    async with await _factory(pg_dsn)() as conn:
        await conn.execute("UPDATE management.qs_query_moves SET state = 'cutover', moved = true "
                           "WHERE operation_id = $1::uuid", uuid.UUID(op2["operation_id"]))
    refused = await handler.abort(_Req(app, None, match={"operation_id": op2["operation_id"]}))
    assert refused.status == fx["abort"]["refused"]["status"]
    assert _json(refused)["error_code"] == fx["abort"]["refused"]["error_code"]


@pytest.mark.asyncio
async def test_operation_actions_require_the_submitter_or_the_scheduler_grant(pg_dsn, monkeypatch) -> None:
    """I1: status/reconcile/abort of another actor's move need the pg_admin grant; ids never leak."""
    await _reset(pg_dsn)
    app, svc, repo, coord = await _app(pg_dsn, security=True)
    handler = QueryMoveHandler()
    decisions = {"allow": False}
    calls = []

    async def pbac(request, resource, name, action, logger=None):
        calls.append((name, action))
        if not decisions["allow"]:
            raise web.HTTPNotFound()

    monkeypatch.setattr(mod, "enforce_request_pbac", pbac)
    plan = _json(await handler.preview(_Req(app, PREVIEW)))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    oid = op["operation_id"]

    def as_user(actor, verb_method="POST", body=None):
        req = _Req(app, body, match={"operation_id": oid}, method=verb_method)
        req["actor"] = actor
        return req

    # Another user without the grant: 403 everywhere, nothing mutated.
    for verb, method in ((handler.status, "GET"), (handler.reconcile, "POST"), (handler.abort, "POST")):
        resp = await verb(as_user(8, method))
        assert resp.status == 403 and _json(resp)["error_code"] == "tenant_write_forbidden"
        assert oid not in _json(resp)["message"]
    assert calls == [("pg_admin", "datasource:use")] * 3
    assert (await svc.status(oid))["state"] == "preparing"
    # A second actor's overlapping submission is refused without revealing the operation id.
    other = _Req(app, _move_body(plan))
    other["actor"] = 8
    resp = await handler.move(other)
    assert resp.status == 409 and oid not in json.dumps(_json(resp))
    preview = await handler.preview(as_user(8, body=PREVIEW))
    assert oid not in json.dumps(_json(preview))
    # An operator holding the scheduler grant keeps access.
    decisions["allow"] = True
    assert (await handler.status(as_user(8, "GET"))).status == 200
    resp = await handler.abort(as_user(8))
    assert resp.status == 200 and _json(resp)["error_code"] == "move_aborted"


@pytest.mark.asyncio
async def test_crud_conflict_message_never_names_the_operation(pg_dsn) -> None:
    await _reset(pg_dsn)
    app, svc, repo, coord = await _app(pg_dsn)
    handler = QueryMoveHandler()
    plan = _json(await handler.preview(_Req(app, PREVIEW)))
    op = _json(await handler.move(_Req(app, _move_body(plan))))
    from querysource.scheduler.handoff import ScheduleOwner
    from querysource.tenant_errors import TenantError

    with pytest.raises(TenantError) as err:
        await coord.assert_not_moving([ScheduleOwner.from_store(LEGACY, SLUG)])
    assert err.value.error_code == "move_in_progress" and op["operation_id"] not in str(err.value)
