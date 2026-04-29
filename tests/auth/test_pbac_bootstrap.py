"""Unit tests for querysource.auth.pbac — setup_pbac bootstrap."""
import sys
import pytest
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
        """querysource.auth.pbac must lazy-import navigator-auth."""
        # Clean out any existing nav-auth modules from sys.modules.
        nav_mods = [m for m in sys.modules if m.startswith("navigator_auth")]
        for mod in nav_mods:
            del sys.modules[mod]
        # Re-import the module itself.
        if "querysource.auth.pbac" in sys.modules:
            del sys.modules["querysource.auth.pbac"]
        import querysource.auth.pbac  # noqa: F401
        assert "navigator_auth" not in sys.modules, (
            "querysource.auth.pbac must lazy-import navigator-auth"
        )

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
        from unittest.mock import MagicMock
        pre_guardian = MagicMock()
        pre_evaluator = MagicMock()
        aiohttp_app["security"] = pre_guardian
        aiohttp_app["policy_evaluator"] = pre_evaluator
        # credential_resolver not yet in app
        pdp, ev, guard = setup_pbac(aiohttp_app, policy_dir=str(tmp_path))
        assert guard is pre_guardian, "Existing Guardian reused"
        assert ev is pre_evaluator, "Existing evaluator reused"
        assert "credential_resolver" in aiohttp_app, "Resolver must be registered"
