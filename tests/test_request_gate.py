"""FEAT-160 / TASK-827: request-scoped PBAC gate parity with AbstractHandler._enforce_pbac."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from querysource.auth.request_gate import enforce_request_pbac, get_request_session
from querysource.handlers.abstract import AbstractHandler

_GATE = "querysource.auth.request_gate"


class _Handler(AbstractHandler):
    """Test-only subclass (same pattern as tests/handlers/test_abstract_pbac_helpers.py)."""


def _request(app: dict, store: dict | None = None) -> MagicMock:
    """A MagicMock request whose ``get``/``__setitem__`` share a real dict."""
    backing = {} if store is None else store
    req = MagicMock()
    req.app = app
    req.get = lambda k, d=None: backing.get(k, d)
    req.__setitem__ = MagicMock(side_effect=lambda k, v: backing.update({k: v}))
    return req


def _evaluator(allowed: bool) -> MagicMock:
    evaluator = MagicMock()
    evaluator.check_access = MagicMock(
        return_value=MagicMock(allowed=allowed, matched_policy="P", reason="r")
    )
    return evaluator


async def test_get_request_session_memoizes_and_handles_runtime_error():
    getter = AsyncMock(return_value={"username": "alice"})
    req = _request({})
    first = await get_request_session(req, logger=MagicMock(), session_getter=getter)
    second = await get_request_session(req, logger=MagicMock(), session_getter=getter)
    assert first == second == {"username": "alice"}
    assert getter.await_count == 1

    req2 = _request({})
    with patch(f"{_GATE}.get_session", AsyncMock(side_effect=RuntimeError)):
        assert await get_request_session(req2, logger=MagicMock()) is None


async def test_request_gate_parity_noop_when_pbac_disabled():
    await enforce_request_pbac(_request({}), "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_missing_resource_name_404():
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with pytest.raises(web.HTTPNotFound):
        await enforce_request_pbac(req, "slug", "", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_no_session_404():
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(req, "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_allow_and_deny():
    allow_req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(True)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        await enforce_request_pbac(
            allow_req, "datasource", "pg_admin", "datasource:use", logger=MagicMock()
        )
    deny_req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(False)})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(
                deny_req, "datasource", "pg_admin", "datasource:use", logger=MagicMock()
            )


async def test_request_gate_parity_no_evaluator_404():
    req = _request({"security": MagicMock()})
    with patch(f"{_GATE}.get_session", AsyncMock(return_value={"username": "alice"})):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(req, "slug", "x", "slug:execute", logger=MagicMock())


async def test_request_gate_parity_sessionless_authz():
    captured = {}
    evaluator = MagicMock()

    def capture(ctx, **kwargs):
        captured["ctx"] = ctx
        return MagicMock(allowed=True)

    evaluator.check_access = capture
    req = _request(
        {"security": MagicMock(), "policy_evaluator": evaluator},
        store={"authz_backend": "authz_useragent"},
    )
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        await enforce_request_pbac(
            req, "slug", "x", "slug:execute", logger=MagicMock(), allow_sessionless=True
        )
    assert captured["ctx"].userinfo["username"] == "authz:authz_useragent"
    assert "authorized" in captured["ctx"].userinfo["groups"]

    req_off = _request(
        {"security": MagicMock(), "policy_evaluator": evaluator},
        store={"authz_backend": "authz_useragent"},
    )
    with patch(f"{_GATE}.get_session", AsyncMock(return_value=None)):
        with pytest.raises(web.HTTPNotFound):
            await enforce_request_pbac(
                req_off, "slug", "x", "slug:execute", logger=MagicMock(), allow_sessionless=False
            )


async def test_abstract_handler_delegates_to_request_gate():
    handler = _Handler.__new__(_Handler)
    handler.logger = MagicMock()
    handler._get_user_session = AsyncMock(return_value={"username": "alice"})
    req = _request({"security": MagicMock(), "policy_evaluator": _evaluator(False)})
    with pytest.raises(web.HTTPNotFound):
        await handler._enforce_pbac(req, "slug", "x", "slug:execute")
    handler._get_user_session.assert_awaited_once_with(req)
