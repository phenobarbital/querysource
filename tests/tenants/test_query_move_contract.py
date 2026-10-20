"""Freeze the FEAT-176 move error mappings and the shared v1 API fixture.

The fixture is the contract the frontend (FEAT-252) codes against; this module keeps it
and ``OWNERSHIP_STATUS`` from drifting apart, and keeps connection details out of every
documented response body.
"""
import json
from pathlib import Path

import pytest

from querysource.tenant_errors import OWNERSHIP_STATUS, TenantError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_move_contract_v1.json"
DESCRIPTOR_KEYS = {"tenant", "schema", "table", "contract", "is_default"}
PLAN_KEYS = {
    "version", "source", "destination", "query_slug", "plan_revision", "can_move", "blockers",
    "warnings", "inherited_references", "runtime_program", "consumer_review_required",
    "schedule_policy", "schedules",
}
RESULT_KEYS = {
    "version", "query_slug", "source", "destination", "moved", "schedules_transferred",
    "scheduler_sync", "updated_at",
}
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
    assert desc["contract"] in ("legacy", "tenant")
    assert desc["tenant"] is None or isinstance(desc["tenant"], str)


# -- error mapping ---------------------------------------------------------------------


def test_every_move_error_code_is_documented_with_its_status(fx) -> None:
    documented = {name: spec["status"] for name, spec in fx["errors"].items()}
    declared = {name: status for name, status in OWNERSHIP_STATUS.items() if name.startswith("move_")}
    assert documented == declared, "the fixture and OWNERSHIP_STATUS must agree exactly"


def test_each_documented_error_constructs_with_the_documented_status(fx) -> None:
    for name, spec in fx["errors"].items():
        err = TenantError(spec["body"]["message"], error_code=name)
        assert err.code == spec["status"], name
        assert err.error_code == spec["body"]["error_code"] == name


def test_preexisting_ownership_mappings_are_unchanged(fx) -> None:
    for name, status in PREEXISTING.items():
        assert OWNERSHIP_STATUS[name] == status, f"{name} must keep its pre-FEAT-176 status"


def test_the_removed_async_operation_codes_are_gone() -> None:
    """A move is synchronous: there is no operation journal, lease or idempotency key."""
    for name in ("move_in_progress", "move_idempotency_conflict", "move_outcome_unknown",
                 "move_scheduler_not_ready"):
        assert name not in OWNERSHIP_STATUS, f"{name} belongs to the removed control plane"


# -- shapes ----------------------------------------------------------------------------


def test_catalog_response_shape(fx) -> None:
    catalog = fx["catalog"]
    assert catalog["request"] == {"method": "GET", "path": "/api/v1/management/query-stores"}
    assert catalog["response"]["version"] == 1
    descriptors = catalog["response"]["data"]
    for desc in descriptors:
        _descriptor_ok(desc)
    assert sum(1 for d in descriptors if d["is_default"]) == 1, "exactly one configured default"


def test_preview_is_a_post_with_both_selectors_and_returns_a_plan(fx) -> None:
    request = fx["preview"]["request"]
    assert request["method"] == "POST" and request["path"].endswith("/move/preview")
    assert set(request["body"]) == {"tenant", "destination_tenant"}

    for key in ("response", "blocked_response"):
        plan = fx["preview"][key]
        assert set(plan) == PLAN_KEYS, key
        assert plan["version"] == 1
        assert plan["schedule_policy"] == "pause-and-transfer"
        assert plan["consumer_review_required"] is True
        _descriptor_ok(plan["source"])
        _descriptor_ok(plan["destination"])
        assert plan["can_move"] is (not plan["blockers"])


def test_blocked_plan_blockers_carry_a_known_code_and_a_path(fx) -> None:
    for blocker in fx["preview"]["blocked_response"]["blockers"]:
        assert blocker["code"] in OWNERSHIP_STATUS
        assert blocker["message"]
        assert "path" in blocker, "a blocker must point at what to fix"


def test_move_is_synchronous_and_returns_a_result(fx) -> None:
    move = fx["move"]
    assert move["request"]["method"] == "POST"
    assert set(move["request"]["body"]) == {
        "tenant", "destination_tenant", "plan_revision",
        "acknowledge_external_consumers", "acknowledge_runtime_program_change",
    }
    assert "idempotency_key" not in move["request"]["body"]
    assert move["response_status"] == 200, "the HTTP response IS the acknowledgement"

    for key in ("response", "response_unscheduled"):
        result = move[key]
        assert set(result) == RESULT_KEYS, key
        assert result["version"] == 1 and result["moved"] is True
        assert result["scheduler_sync"] in ("ok", "not_configured")
        _descriptor_ok(result["source"])
        _descriptor_ok(result["destination"])


def test_no_operation_lifecycle_remains_in_the_contract(fx) -> None:
    assert not {"operations", "status", "reconcile", "abort", "journal_error_codes"} & set(fx)
    blob = json.dumps(fx)
    for term in ("operation_id", "idempotency_key", "draining", "cutover", "reconciling"):
        assert term not in blob, f"{term} belongs to the removed async lifecycle"


# -- leakage ---------------------------------------------------------------------------


def test_no_documented_response_leaks_connection_or_identity_details(fx) -> None:
    blob = json.dumps(fx).lower()
    for term in FORBIDDEN_TEXT:
        assert term not in blob, term
    for desc in fx["catalog"]["response"]["data"]:
        assert "database_namespace" not in desc and "host" not in desc
