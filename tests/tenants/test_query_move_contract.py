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
