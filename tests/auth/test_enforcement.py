"""Unit + contract tests for the request-optional PBAC evaluation core (FEAT-150, TASK-750)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from querysource.auth import enforcement
from querysource.auth.enforcement import (
    AccessDecision,
    build_eval_context,
    enforce_principal,
    evaluate,
    resolve_evaluator,
)
from querysource.auth.pbac import _set_pbac_runtime, clear_pbac_runtime
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied


def _evaluator_available() -> bool:
    """True when navigator-auth + the Rust rs_pep engine are importable."""
    try:
        from navigator_auth.abac.policies.evaluator import (  # noqa: F401
            _RS_PEP_AVAILABLE,
            PolicyEvaluator,
        )
        return _RS_PEP_AVAILABLE
    except Exception:
        return False


def _make_evaluator(check_access=None):
    evaluator = MagicMock()
    evaluator._cache = {"pre-existing": "decision"}
    evaluator._stats = {"hits": 1}
    evaluator.check_access = check_access or MagicMock()
    return evaluator


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    clear_pbac_runtime()
    monkeypatch.setattr(enforcement, "_WARNED_ABSENT_RUNTIME", False)
    yield
    clear_pbac_runtime()


def test_resolve_evaluator_request_and_runtime():
    """AC-1: request path reads app; None path reads the process runtime."""
    evaluator = _make_evaluator()
    guardian = MagicMock()

    app = web.Application()
    app["security"] = guardian
    app["policy_evaluator"] = evaluator
    request = MagicMock()
    request.app = app

    pbac_enabled, resolved = resolve_evaluator(request, detached=False)
    assert pbac_enabled is True
    assert resolved is evaluator

    clear_pbac_runtime()
    pbac_enabled, resolved = resolve_evaluator(None, detached=False)
    assert pbac_enabled is False
    assert resolved is None

    _set_pbac_runtime(guardian, evaluator)
    pbac_enabled, resolved = resolve_evaluator(None, detached=False)
    assert pbac_enabled is True
    assert resolved is evaluator


def test_resolve_evaluator_guardian_without_evaluator():
    """A guardian without an evaluator returns (True, None)."""
    _set_pbac_runtime(MagicMock(), None)
    pbac_enabled, resolved = resolve_evaluator(None, detached=False)
    assert pbac_enabled is True
    assert resolved is None


def test_resolve_evaluator_detached_copy():
    """AC-2: detached copy has a fresh _cache and copied _stats; original untouched."""
    evaluator = _make_evaluator()
    _set_pbac_runtime(MagicMock(), evaluator)

    pbac_enabled, detached = resolve_evaluator(None, detached=True)

    assert pbac_enabled is True
    assert detached is not evaluator
    assert detached._cache == {}
    assert detached._stats == {"hits": 1}
    assert evaluator._cache == {"pre-existing": "decision"}
    assert evaluator._stats == {"hits": 1}


@pytest.mark.asyncio
async def test_enforce_principal_pbac_off_and_warn_once(monkeypatch, caplog):
    """AC-3: no runtime -> allowed, pbac_enabled False; exactly one warning across two calls."""
    principal = QSPrincipal(user_id="35")
    monkeypatch.setattr(enforcement, "QS_PBAC_ENABLED", True)

    with caplog.at_level("WARNING", logger="querysource.auth.enforcement"):
        decision1 = await enforce_principal(principal, "slug", "s1", "slug:execute")
        decision2 = await enforce_principal(principal, "slug", "s2", "slug:execute")

    assert decision1 == AccessDecision(allowed=True, pbac_enabled=False)
    assert decision2 == AccessDecision(allowed=True, pbac_enabled=False)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_enforce_principal_missing_evaluator():
    """AC-4: a runtime whose evaluator is None (guardian set) -> QueryAccessDenied."""
    _set_pbac_runtime(MagicMock(), None)
    principal = QSPrincipal(user_id="35")

    with pytest.raises(QueryAccessDenied):
        await enforce_principal(principal, "slug", "s1", "slug:execute")


@pytest.mark.asyncio
async def test_evaluate_exception_is_deny():
    """AC-5: an evaluator raising -> deny."""
    def _raise(**kwargs):
        raise RuntimeError("boom")

    evaluator = _make_evaluator(check_access=_raise)
    decision = await evaluate(evaluator, MagicMock(), "slug", "s1", "slug:execute")
    assert decision.allowed is False
    assert decision.pbac_enabled is True


@pytest.mark.asyncio
async def test_evaluate_awaits_coroutine():
    """AC-5: an async check_access result is awaited, not truthy-bypassed."""
    async def _check_access(**kwargs):
        result = MagicMock()
        result.allowed = True
        result.matched_policy = "p1"
        result.reason = None
        return result

    evaluator = _make_evaluator(check_access=lambda **kwargs: _check_access(**kwargs))
    decision = await evaluate(evaluator, MagicMock(), "slug", "s1", "slug:execute")
    assert decision.allowed is True
    assert decision.matched_policy == "p1"


@pytest.mark.asyncio
async def test_enforce_principal_authz_flag(monkeypatch):
    """AC-6: authz principal: flag off -> denied w/o evaluation; flag on -> evaluated with user=None."""
    principal = QSPrincipal.for_authz("ip")
    evaluator = _make_evaluator()
    _set_pbac_runtime(MagicMock(), evaluator)

    monkeypatch.setattr(enforcement, "QS_PBAC_ALLOW_SESSIONLESS_AUTHZ", False)
    with pytest.raises(QueryAccessDenied):
        await enforce_principal(principal, "slug", "s1", "slug:execute")
    evaluator.check_access.assert_not_called()

    monkeypatch.setattr(enforcement, "QS_PBAC_ALLOW_SESSIONLESS_AUTHZ", True)
    result = MagicMock()
    result.allowed = True
    result.matched_policy = None
    result.reason = None
    evaluator.check_access.return_value = result

    decision = await enforce_principal(principal, "slug", "s1", "slug:execute")

    assert decision.allowed is True
    _, kwargs = evaluator.check_access.call_args
    ctx = kwargs["ctx"]
    assert ctx.user is None
    assert ctx.userinfo == principal.to_userinfo()


def test_build_eval_context_without_request():
    """AC-7: request=None builds a real EvalContext via _ServiceRequest on 0.26.0."""
    from navigator_auth.abac.context import EvalContext

    if hasattr(EvalContext, "from_userinfo"):
        pytest.skip("navigator-auth provides from_userinfo(); covered by the other test")

    userinfo = {"username": "u", "user_id": "35", "groups": [], "roles": [],
                "programs": [], "superuser": False}
    ctx = build_eval_context(userinfo=userinfo, user=userinfo, session=None, request=None)
    assert ctx.userinfo == userinfo


def test_build_eval_context_prefers_from_userinfo(monkeypatch):
    """AC-7: when EvalContext.from_userinfo exists, it is called and _ServiceRequest is not built."""
    from navigator_auth.abac.context import EvalContext

    sentinel = MagicMock()
    from_userinfo = MagicMock(return_value=sentinel)
    monkeypatch.setattr(EvalContext, "from_userinfo", from_userinfo, raising=False)

    service_request_spy = MagicMock(side_effect=enforcement._ServiceRequest)
    monkeypatch.setattr(enforcement, "_ServiceRequest", service_request_spy)

    userinfo = {"username": "u"}
    result = build_eval_context(userinfo=userinfo, user=userinfo, session=None, request=None)

    assert result is sentinel
    from_userinfo.assert_called_once_with(userinfo, user=userinfo, session=None)
    service_request_spy.assert_not_called()


@pytest.mark.skipif(not _evaluator_available(), reason="navigator-auth rs_pep evaluator not available")
@pytest.mark.asyncio
async def test_real_evaluator_contract(tmp_path: Path):
    """AC-8: real Rust evaluator, request-less context, decided from userinfo groups alone."""
    from navigator_auth.abac.policies.evaluator import PolicyEvaluator, PolicyLoader

    policy_yaml = tmp_path / "sales.yaml"
    policy_yaml.write_text(
        """
policies:
  - name: sales_execute_report_a
    effect: allow
    resources: ["slug:report_a"]
    actions: ["slug:execute"]
    subjects:
      groups: ["sales"]
"""
    )
    evaluator = PolicyEvaluator()
    evaluator.load_policies(PolicyLoader.load_from_directory(tmp_path))

    principal = QSPrincipal(user_id="35", groups=("sales",))
    ctx = build_eval_context(
        userinfo=principal.to_userinfo(), user=principal.to_userinfo(),
        session=None, request=None,
    )

    allowed_decision = await evaluate(evaluator, ctx, "slug", "report_a", "slug:execute")
    denied_decision = await evaluate(evaluator, ctx, "slug", "report_b", "slug:execute")

    assert allowed_decision.allowed is True
    assert denied_decision.allowed is False
