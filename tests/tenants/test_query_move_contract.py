"""Freeze FEAT-176 move error mappings and the shared v1 API fixture."""
import json
from pathlib import Path

import pytest

from querysource.tenant_errors import OWNERSHIP_STATUS, TenantError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_move_contract_v1.json"
STATES = {"preparing", "draining", "cutover", "reconciling", "completed", "failed"}
SYNC = {"pending", "ok", "failed", "not_configured"}
DESCRIPTOR_KEYS = {"tenant", "schema", "table", "contract", "is_default"}
PREEXISTING = {
    "invalid_tenant": 400,
    "tenant_not_available": 404,
    "query_not_found": 404,
    "tenant_store_unavailable": 503,
    "tenant_write_forbidden": 403,
    "tenant_worker_unsupported": 502,
}
FORBIDDEN_TEXT = ("password", "dsn", "secret", "postgresql://", "run_as_user")


@pytest.fixture(scope="module")
def fx() -> dict:
    return json.loads(FIXTURE.read_text())


def _descriptor_ok(desc: dict) -> None:
    assert set(desc) == DESCRIPTOR_KEYS
    assert desc["contract"] in {"legacy", "tenant"}
    assert desc["tenant"] is None or isinstance(desc["tenant"], str)


def test_preexisting_mappings_retained() -> None:
    for code, status in PREEXISTING.items():
        assert OWNERSHIP_STATUS[code] == status


def test_every_move_error_constructs_with_documented_status(fx: dict) -> None:
    assert len(fx["errors"]) == 14
    for code, entry in fx["errors"].items():
        err = TenantError("x", error_code=code)
        assert err.error_code == code
        assert OWNERSHIP_STATUS[code] == entry["status"]
        assert entry["body"]["error_code"] == code
    assert TenantError("x", error_code="move_in_progress", details={"a": 1}).details == {"a": 1}
    with pytest.raises(KeyError):
        TenantError("x", error_code="not_a_code")


def test_operation_state_invariants(fx: dict) -> None:
    seen_states = set()
    for name, op in fx["operations"].items():
        assert op["version"] == 1
        assert op["state"] in STATES, name
        assert op["moved"] in (False, True, None)
        assert op["scheduler_sync"] in SYNC
        _descriptor_ok(op["source"])
        _descriptor_ok(op["destination"])
        seen_states.add(op["state"])
        # Only completed+moved=true is final success.
        if op["state"] == "completed":
            assert op["moved"] is True
        if op["state"] == "failed":
            assert op["moved"] is False and "error_code" in op
            assert op["error_code"] in fx["errors"] or op["error_code"] in fx["journal_error_codes"]
        if op["state"] == "reconciling":
            assert op["moved"] is True
        if op.get("error_code") == "move_outcome_unknown":
            assert op["moved"] is None
    assert seen_states == STATES
    syncs = {op["scheduler_sync"] for op in fx["operations"].values()}
    assert syncs == SYNC
    assert {op["moved"] for op in fx["operations"].values()} == {False, True, None}


def test_plan_catalog_and_request_shapes(fx: dict) -> None:
    for desc in fx["catalog"]["response"]["data"]:
        _descriptor_ok(desc)
    assert fx["catalog"]["response"]["version"] == 1
    plan = fx["preview"]["response"]
    assert plan["schedule_policy"] == "live-transfer"
    assert plan["consumer_review_required"] is True
    assert {s["run_as"] for s in plan["schedules"]} == {"preserve-existing"}
    assert set(plan["runtime_program"]) == {"before", "after"}
    assert fx["preview"]["blocked_response"]["can_move"] is False
    body = fx["move"]["request"]["body"]
    assert set(body) == {
        "tenant", "destination_tenant", "plan_revision",
        "acknowledge_external_consumers", "acknowledge_runtime_program_change",
        "idempotency_key",
    }
    assert fx["move"]["response_status"] == 202
    assert body["plan_revision"] == plan["plan_revision"]


def test_fixture_is_credential_free(fx: dict) -> None:
    text = json.dumps(fx).lower()
    for needle in FORBIDDEN_TEXT:
        assert needle not in text


# -- FEAT-176 TASK-927: runtime responses validated against the frozen v1 fixture ------------------

import hashlib  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402

from test_handoff_state import pg_dsn  # noqa: E402,F401 - fixture

# Pinned content hash of the approved v1 fixture: the frontend release gate consumes THIS file.
# Changing the approved contract silently must fail here; bump only with an explicit contract revision.
FIXTURE_SHA256 = "3c3e745d3f957b02b8194aa99b2a673a9425084bef3e7b2e974773ecd871553f"
PLAN_KEYS = {
    "version", "source", "destination", "query_slug", "plan_revision", "can_move", "blockers", "warnings",
    "inherited_references", "runtime_program", "consumer_review_required", "schedule_policy", "schedules",
    "handoff_ready",
}
SCHEDULE_KEYS = {"kind", "enabled", "paused", "timezone", "next_fire_at", "run_as"}
OPERATION_KEYS = {"version", "operation_id", "source", "destination", "query_slug", "state", "moved",
                  "scheduler_sync", "updated_at"}
INTERNAL_KEYS = ("lease_owner", "lease_expires_at", "actor_id", "request_digest", "plan_snapshot", "_internal",
                 "deployment_id", "scheduler_run_as_user_id", "idempotency", "worker", "raw_sha256")


def test_fixture_content_hash_is_pinned() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def _assert_plan_shape(plan: dict, fx: dict) -> None:
    expected = fx["preview"]["response"]
    assert set(plan) == PLAN_KEYS == set(expected)
    assert plan["version"] == 1 and plan["schedule_policy"] == "live-transfer"
    assert plan["consumer_review_required"] is True
    _descriptor_ok(plan["source"])
    _descriptor_ok(plan["destination"])
    assert set(plan["runtime_program"]) == {"before", "after"}
    assert re.fullmatch(r"[0-9a-f]{64}", plan["plan_revision"])
    assert isinstance(plan["can_move"], bool) and isinstance(plan["handoff_ready"], bool)
    for blocker in plan["blockers"]:
        assert {"code", "message"} <= set(blocker) <= {"code", "message", "path"}
        assert blocker["code"] in fx["errors"]
    for warning in plan["warnings"]:
        assert set(warning) == {"code", "message"}
    for ref in plan["inherited_references"]:
        assert set(ref) == {"path", "slug"}
    for schedule in plan["schedules"]:
        assert set(schedule) == SCHEDULE_KEYS and schedule["run_as"] == "preserve-existing"
    assert plan["can_move"] == (not plan["blockers"])


def _assert_operation_shape(op: dict, fx: dict) -> None:
    sample = fx["operations"][op["state"] if op["state"] != "cutover" else "cutover_committed"]
    assert OPERATION_KEYS <= set(op) <= OPERATION_KEYS | {"error_code", "message"}
    assert set(op) - {"error_code", "message"} == OPERATION_KEYS == set(sample) - {"error_code", "message"}
    assert op["version"] == 1 and op["state"] in STATES and op["scheduler_sync"] in SYNC
    assert op["moved"] in (False, True, None)
    _descriptor_ok(op["source"])
    _descriptor_ok(op["destination"])
    uuid.UUID(op["operation_id"])
    if op["state"] == "completed":
        assert op["moved"] is True and "error_code" not in op
    if op["state"] == "failed":
        assert op["moved"] is False
        assert op["error_code"] in fx["errors"] or op["error_code"] in fx["journal_error_codes"]
    if op["state"] == "reconciling":
        assert op["moved"] is True


def _assert_no_internals(payload: object) -> None:
    text = json.dumps(payload, default=str).lower()
    for needle in INTERNAL_KEYS + FORBIDDEN_TEXT:
        assert needle.lower() not in text, needle
    assert not re.search(r"\b127\.0\.0\.1\b|localhost|:\d{4,5}/", text)


@pytest.mark.asyncio
async def test_runtime_plan_and_every_reachable_operation_state_match_the_fixture(fx: dict, pg_dsn) -> None:
    from test_handoff_state import _factory, _reset
    from test_query_moves import LEGACY, SCHEDULED, TENANT, _row, _service

    from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM
    from querysource.repositories.definitions import RUN_AS_COLUMN
    from querysource.scheduler.handoff import ScheduleOwner
    from querysource.tenants import QueryIdentity

    await _reset(pg_dsn)
    rows = {("public", "s"): _row(attributes=SCHEDULED, **{RUN_AS_COLUMN: 5})}
    svc, repo, coord = await _service(pg_dsn, rows)
    ident = QueryIdentity(store=LEGACY, slug="s")
    async with await _factory(pg_dsn)() as conn:
        for store in (LEGACY, TENANT):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{store.schema}"')
            await conn.execute(f'CREATE TABLE IF NOT EXISTS "{store.schema}"."{store.table}_run_as_audit" (id int)')
    await coord.bootstrap_row(ScheduleOwner.from_store(LEGACY, "s"), rows[("public", "s")])

    plan = await svc.preview(ident, TENANT, actor=7)
    _assert_plan_shape(plan, fx)
    assert plan["can_move"] is True and [s["kind"] for s in plan["schedules"]] == ["query"]

    seen: dict[str, dict] = {}
    op = await svc.move(ident, TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                        actor=7, acknowledgements={ACK_CONSUMERS: True, ACK_PROGRAM: True})
    seen["preparing"] = op
    oid = op["operation_id"]
    await coord.prepare(oid)
    seen["draining"] = await svc.status(oid)
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="cutover", moved=None)
        await conn.commit()
    seen["cutover"] = await svc.status(oid)  # indeterminate acknowledgement: moved null
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="reconciling", moved=True, scheduler_sync="failed",
                                    error_code="move_scheduler_not_ready", message="Scheduler mirror update pending.")
        await conn.commit()
    seen["reconciling"] = await svc.status(oid)
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="completed", moved=True, scheduler_sync="ok",
                                    error_code=None, message=None)
        await conn.commit()
    seen["completed"] = await svc.status(oid)
    async with await _factory(pg_dsn)() as conn:
        await conn.transaction()
        await coord.update_operation(conn, oid, state="failed", moved=False, error_code="move_target_exists",
                                     message="Destination already holds this slug.")
        await conn.commit()
    seen["failed"] = await svc.status(oid)
    assert set(seen) == STATES
    for state, view in seen.items():
        _assert_operation_shape(view, fx)
        assert view["state"] == state
    assert seen["cutover"]["moved"] is None and seen["completed"]["moved"] is True
    # Nothing internal and no raw delegated user id leaks through plans or operations.
    for payload in (plan, *seen.values()):
        _assert_no_internals(payload)
        assert '"run_as_user' not in json.dumps(payload)


@pytest.mark.asyncio
async def test_verification_never_executes_queries_or_pipelines(pg_dsn, monkeypatch) -> None:
    """Preview, submission, drain, cutover, reconcile and status never call QS/MultiQS."""
    from test_handoff_state import _reset
    from test_query_moves import LEGACY, TENANT, _row, _service

    import querysource.queries as queries_pkg
    from querysource.queries.multi import MultiQS
    from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM
    from querysource.tenants import QueryIdentity

    calls: list[str] = []

    def boom(name):
        async def _fail(self, *a, **k):
            calls.append(name)
            raise AssertionError(f"{name} must not run during a move")
        return _fail

    monkeypatch.setattr(MultiQS, "query", boom("MultiQS.query"), raising=False)
    for attr in ("QS",):
        cls = getattr(queries_pkg, attr, None)
        if cls is not None:
            monkeypatch.setattr(cls, "query", boom(f"{attr}.query"), raising=False)

    await _reset(pg_dsn)
    svc, repo, coord = await _service(pg_dsn, {("public", "s"): _row()})
    ident = QueryIdentity(store=LEGACY, slug="s")
    plan = await svc.preview(ident, TENANT, actor=7)
    op = await svc.move(ident, TENANT, plan_revision=plan["plan_revision"], idempotency_key=str(uuid.uuid4()),
                        actor=7, acknowledgements={ACK_CONSUMERS: True, ACK_PROGRAM: True})
    await coord.prepare(op["operation_id"])
    await svc.status(op["operation_id"])
    await svc.reconcile(op["operation_id"])
    assert calls == []


def test_no_admin_only_rule_or_new_move_action_was_introduced() -> None:
    """Q2: existing management permissions; no slug:move action, role or administrators-only check."""
    import ast

    root = Path(__file__).resolve().parents[2] / "querysource"
    for path in ("handlers/query_moves.py", "handlers/query_stores.py", "query_moves.py"):
        tree = ast.parse((root / path).read_text())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        code_strings = [n.value.lower() for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
        identifiers = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not any("slug:move" in text or "admin" in text.replace("pg_admin", "") for text in code_strings), path
        assert not any("admin" in name.replace("pg_admin", "") for name in identifiers), path
    handler = (root / "handlers" / "query_moves.py").read_text()
    # The only PBAC check is the pre-existing scheduled write-capable multi gate.
    assert handler.count("enforce_request_pbac(") == 1
    assert '"pg_admin", "datasource:use"' in handler


def test_abort_contract_shape(fx: dict) -> None:
    """B2: the explicit pre-cutover abort is part of the shared contract."""
    abort = fx["abort"]
    assert abort["request"]["method"] == "POST"
    assert abort["request"]["path"].endswith("/abort")
    assert set(abort["request"]["body"]) == {"resolve_unknown_runs"}
    assert abort["response_status"] == 200
    assert abort["abortable_states"] == ["preparing", "draining"]
    assert abort["refused"] == {"status": 409, "error_code": "move_in_progress"}
    aborted = fx["operations"]["aborted"]
    assert aborted["state"] == "failed" and aborted["moved"] is False
    assert aborted["error_code"] == "move_aborted" and "move_aborted" in fx["journal_error_codes"]
    # A journal-only code is never an HTTP error and never a preview blocker code.
    assert "move_aborted" not in fx["errors"]
