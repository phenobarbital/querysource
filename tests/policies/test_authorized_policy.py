"""Tests for policies/authorized.yaml — sessionless-authorized clients.

Verifies (a) the YAML shape of the new policy and the baseline exclusion,
and (b) end-to-end evaluation through the real navigator-auth PolicyEvaluator
(Rust rs_pep engine): the synthetic 'authorized' identity may execute slugs
but gets nothing else, and authenticated users keep the baseline floor.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

POLICY_DIR = Path(__file__).parent.parent.parent / "policies"


def _evaluator_available():
    """True when navigator-auth + the Rust rs_pep engine are importable."""
    try:
        from navigator_auth.abac.policies.evaluator import (  # noqa: F401
            PolicyEvaluator,
            _RS_PEP_AVAILABLE,
        )
        return _RS_PEP_AVAILABLE
    except Exception:
        return False


@pytest.mark.skipif(not POLICY_DIR.exists(), reason="policies/ not present")
def test_authorized_policy_shape():
    """authorized.yaml grants slug:execute to group 'authorized' only."""
    with open(POLICY_DIR / "authorized.yaml") as f:
        data = yaml.safe_load(f)
    pol = next(
        (p for p in data["policies"]
         if p["name"] == "authorized_clients_execute_slugs"),
        None,
    )
    assert pol is not None
    assert pol["effect"] == "allow"
    assert pol["resources"] == ["slug:*"]
    assert pol["actions"] == ["slug:execute"]
    assert pol["subjects"]["groups"] == ["authorized"]


@pytest.mark.skipif(not POLICY_DIR.exists(), reason="policies/ not present")
def test_baseline_excludes_authorized_group():
    """The soft baseline must NOT cover sessionless-authorized identities."""
    with open(POLICY_DIR / "baseline.yaml") as f:
        data = yaml.safe_load(f)
    pol = next(
        (p for p in data["policies"] if p["name"] == "authenticated_allow_all"),
        None,
    )
    assert pol is not None
    assert "authorized" in pol["subjects"].get("exclude_groups", []), (
        "baseline.yaml must exclude the 'authorized' group, otherwise "
        "sessionless-authorized clients inherit allow-all"
    )


@pytest.mark.skipif(
    not (POLICY_DIR.exists() and _evaluator_available()),
    reason="navigator-auth rs_pep evaluator not available",
)
class TestAuthorizedEvaluation:
    """End-to-end evaluation with the real Rust policy engine."""

    @pytest.fixture(scope="class")
    def evaluator(self):
        from navigator_auth.abac.policies.evaluator import (
            PolicyEvaluator,
            PolicyLoader,
        )
        ev = PolicyEvaluator()
        ev.load_policies(PolicyLoader.load_from_directory(POLICY_DIR))
        return ev

    def _ctx(self, userinfo):
        from navigator_auth.abac.context import EvalContext
        req = MagicMock()
        req.headers = {}
        return EvalContext(request=req, user=None, userinfo=userinfo, session=None)

    @property
    def _authz_userinfo(self):
        # Mirrors AbstractHandler._enforce_pbac synthetic identity
        return {
            "username": "authz:authz_useragent",
            "groups": ["authorized", "authz_useragent"],
            "roles": [],
        }

    def test_authorized_can_execute_slug(self, evaluator):
        from navigator_auth.abac.policies.resources import ResourceType
        result = evaluator.check_access(
            ctx=self._ctx(self._authz_userinfo),
            resource_type=ResourceType.SLUG,
            resource_name="epson_visit_allocation_us_brian_bi",
            action="slug:execute",
        )
        assert result.allowed, f"denied: {result.reason}"
        assert result.matched_policy == "authorized_clients_execute_slugs"

    def test_authorized_can_use_datasource(self, evaluator):
        """The slug-execution chain also enforces datasource:use (service.py)."""
        from navigator_auth.abac.policies.resources import ResourceType
        result = evaluator.check_access(
            ctx=self._ctx(self._authz_userinfo),
            resource_type=ResourceType.DATASOURCE,
            resource_name="db",
            action="datasource:use",
        )
        assert result.allowed, f"denied: {result.reason}"
        assert result.matched_policy == "authorized_clients_use_datasources"

    def test_authorized_can_use_driver(self, evaluator):
        """The slug-execution chain also enforces driver:use (service.py)."""
        from navigator_auth.abac.policies.resources import ResourceType
        result = evaluator.check_access(
            ctx=self._ctx(self._authz_userinfo),
            resource_type=ResourceType.DRIVER,
            resource_name="pg",
            action="driver:use",
        )
        assert result.allowed, f"denied: {result.reason}"
        assert result.matched_policy == "authorized_clients_use_drivers"

    def test_authorized_cannot_read_datasources(self, evaluator):
        """Only datasource:use is granted — admin/read actions stay denied."""
        from navigator_auth.abac.policies.resources import ResourceType
        result = evaluator.check_access(
            ctx=self._ctx(self._authz_userinfo),
            resource_type=ResourceType.DATASOURCE,
            resource_name="db",
            action="datasource:read",
        )
        assert not result.allowed, (
            "sessionless-authorized identity must not read datasource configs"
        )

    def test_authorized_cannot_execute_raw_queries(self, evaluator):
        """raw_query:execute is not granted to sessionless-authorized."""
        from navigator_auth.abac.policies.resources import ResourceType
        result = evaluator.check_access(
            ctx=self._ctx(self._authz_userinfo),
            resource_type=ResourceType.RAW_QUERY,
            resource_name="raw_query",
            action="raw_query:execute",
        )
        assert not result.allowed, (
            "sessionless-authorized identity must not inherit baseline allow-all"
        )

    def test_authenticated_user_keeps_baseline(self, evaluator):
        from navigator_auth.abac.policies.resources import ResourceType
        userinfo = {"username": "alice", "groups": ["staff"], "roles": []}
        result = evaluator.check_access(
            ctx=self._ctx(userinfo),
            resource_type=ResourceType.SLUG,
            resource_name="whatever",
            action="slug:execute",
        )
        assert result.allowed, f"baseline should allow authenticated: {result.reason}"
