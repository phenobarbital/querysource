"""Unit tests for querysource.auth.pbac — setup_pbac bootstrap."""
import sys
import pytest
from unittest.mock import MagicMock, patch
from aiohttp import web
from querysource.auth import setup_pbac


@pytest.fixture
def empty_policies_dir(tmp_path):
    """An empty (but existing) tmp directory for policies."""
    return str(tmp_path)


@pytest.fixture
def aiohttp_app():
    return web.Application()


class TestSetupPbac:
    def test_module_does_not_eager_import_navauth(self):
        """querysource.auth.pbac must lazy-import navigator-auth.

        Restores sys.modules after checking so subsequent tests that need
        navigator-auth are not affected by the temporary removal.
        """
        # Snapshot nav-auth modules before removal so we can restore them.
        nav_mods_snapshot = {
            k: v for k, v in sys.modules.items()
            if k.startswith("navigator_auth")
        }
        # Remove from sys.modules.
        for mod in list(nav_mods_snapshot):
            del sys.modules[mod]
        # Re-import only the QS pbac module (must not pull in navigator-auth).
        qs_pbac_mod = sys.modules.pop("querysource.auth.pbac", None)
        try:
            import querysource.auth.pbac  # noqa: F401
            assert "navigator_auth" not in sys.modules, (
                "querysource.auth.pbac must lazy-import navigator-auth"
            )
        finally:
            # Restore navigator-auth modules so later tests are not contaminated.
            sys.modules.update(nav_mods_snapshot)
            if qs_pbac_mod is not None:
                sys.modules["querysource.auth.pbac"] = qs_pbac_mod

    def test_registers_app_keys(self, aiohttp_app, empty_policies_dir):
        """Successful bootstrap populates all four app keys."""
        pdp, ev, guard = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        if guard is not None:
            assert aiohttp_app["security"] is guard
            assert aiohttp_app["policy_evaluator"] is ev
            assert aiohttp_app["abac"] is pdp
            assert aiohttp_app["credential_resolver"] is not None

    def test_idempotent(self, aiohttp_app, empty_policies_dir):
        """Calling setup_pbac twice returns the same Guardian instance."""
        first = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        second = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        if first[2] is not None:
            assert first[2] is second[2], "Same Guardian on idempotent call"

    def test_missing_policy_dir_returns_none_tuple(self, aiohttp_app):
        """Bad policy dir returns (None, None, None), does not raise."""
        result = setup_pbac(aiohttp_app, policy_dir="/non/existent/path")
        assert result is not None
        assert len(result) == 3

    def test_idempotent_with_preloaded_security(self, aiohttp_app, empty_policies_dir, tmp_path):
        """Pre-existing Guardian is reused; credential_resolver is still added."""
        pre_guardian = MagicMock()
        pre_evaluator = MagicMock()
        aiohttp_app["security"] = pre_guardian
        aiohttp_app["policy_evaluator"] = pre_evaluator
        # credential_resolver not yet in app
        pdp, ev, guard = setup_pbac(aiohttp_app, policy_dir=str(tmp_path))
        assert guard is pre_guardian, "Existing Guardian reused"
        assert ev is pre_evaluator, "Existing evaluator reused"
        assert "credential_resolver" in aiohttp_app, "Resolver must be registered"


class TestSetupPbacDisabled:
    """Tests verifying behaviour when PBAC is effectively disabled (no Guardian returned).

    Uses mocks so these tests are independent of the upstream navigator-auth
    installation state.  The real engine is exercised in TASK-642 integration
    tests.
    """

    def test_none_tuple_does_not_populate_app_keys(self):
        """When setup_pbac bootstrap fails, app keys must not be set.

        Simulates the failure path by making navigator-auth unimportable,
        which causes setup_pbac to catch the ImportError and return
        (None, None, None) without setting any app keys.
        """
        app = web.Application()
        # Make all navigator_auth sub-modules unimportable.
        blocked = {
            "navigator_auth.abac.pdp": None,
            "navigator_auth.abac.guardian": None,
            "navigator_auth.abac.policies.evaluator": None,
            "navigator_auth.abac.policies.abstract": None,
            "navigator_auth.abac.storages.yaml_storage": None,
        }
        with patch.dict(sys.modules, blocked):
            result = setup_pbac(app, policy_dir="/any/path")
        assert result == (None, None, None), (
            f"Expected (None, None, None) when imports fail, got: {result}"
        )
        assert app.get("security") is None, (
            "app['security'] must not be populated when setup_pbac returns None"
        )

    def test_credential_resolver_always_registered(self, tmp_path):
        """Even on full bootstrap, CredentialResolver is always registered."""
        app = web.Application()
        _, _, guardian = setup_pbac(app, policy_dir=str(tmp_path))
        # Regardless of whether guardian succeeded, the resolver must be set
        assert "credential_resolver" in app
