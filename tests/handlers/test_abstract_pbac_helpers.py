"""Unit tests for AbstractHandler PBAC helpers.

Tests _get_user_session and _enforce_pbac using mocks only —
no real PBAC stack is started.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

from querysource.handlers.abstract import AbstractHandler


class _Handler(AbstractHandler):
    """Test-only subclass exposing the helpers as plain methods."""


@pytest.fixture
def handler():
    h = _Handler.__new__(_Handler)
    h.logger = MagicMock()
    return h


@pytest.fixture
def request_no_pbac():
    """Request with no app['security'] — PBAC disabled."""
    req = MagicMock(spec=web.Request)
    req.app = {}
    req.__contains__ = lambda self, k: False
    req.__getitem__ = MagicMock(side_effect=KeyError)
    req.__setitem__ = MagicMock()
    req.get = lambda k, d=None: d
    return req


class TestGetUserSession:
    @pytest.mark.asyncio
    async def test_returns_none_on_runtime_error(self, handler, request_no_pbac):
        """_get_user_session returns None when get_session raises RuntimeError."""
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(side_effect=RuntimeError),
        ):
            result = await handler._get_user_session(request_no_pbac)
            assert result is None

    @pytest.mark.asyncio
    async def test_memoizes(self, handler):
        """_get_user_session only calls get_session once per request."""
        store = {}
        req = MagicMock()
        req.get = lambda k, d=None: store.get(k, d)

        def setitem(k, v):
            store[k] = v

        req.__setitem__ = MagicMock(side_effect=setitem)
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value={"username": "alice"}),
        ) as mocked:
            await handler._get_user_session(req)
            await handler._get_user_session(req)
            assert mocked.call_count == 1

    @pytest.mark.asyncio
    async def test_memoizes_none(self, handler):
        """A None session is also memoized — subsequent call returns None directly."""
        store = {}
        req = MagicMock()
        req.get = lambda k, d=None: store.get(k, d)

        def setitem(k, v):
            store[k] = v

        req.__setitem__ = MagicMock(side_effect=setitem)
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value=None),
        ) as mocked:
            result1 = await handler._get_user_session(req)
            result2 = await handler._get_user_session(req)
            assert result1 is None
            assert result2 is None
            assert mocked.call_count == 1


class TestEnforcePbac:
    @pytest.mark.asyncio
    async def test_noop_when_pbac_disabled(self, handler, request_no_pbac):
        """_enforce_pbac is a no-op when app['security'] is absent."""
        # Should NOT raise anything
        await handler._enforce_pbac(
            request_no_pbac,
            resource_type="slug",
            resource_name="anything",
            action="slug:execute",
        )

    @pytest.mark.asyncio
    async def test_404_when_no_session_and_pbac_enabled(self, handler):
        """_enforce_pbac raises HTTPNotFound when session is None and PBAC is enabled."""
        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": MagicMock()}
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(web.HTTPNotFound):
                await handler._enforce_pbac(
                    req,
                    resource_type="slug",
                    resource_name="x",
                    action="slug:execute",
                )

    @pytest.mark.asyncio
    async def test_404_when_evaluator_denies(self, handler):
        """_enforce_pbac raises HTTPNotFound when evaluator returns allowed=False."""
        evaluator = MagicMock()
        evaluator.check_access = MagicMock(
            return_value=MagicMock(allowed=False, matched_policy="P", reason="denied")
        )
        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": evaluator}
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value={"username": "alice"}),
        ):
            with pytest.raises(web.HTTPNotFound):
                await handler._enforce_pbac(
                    req,
                    resource_type="slug",
                    resource_name="x",
                    action="slug:execute",
                )

    @pytest.mark.asyncio
    async def test_allows_when_evaluator_permits(self, handler):
        """_enforce_pbac returns silently when evaluator returns allowed=True."""
        evaluator = MagicMock()
        evaluator.check_access = MagicMock(
            return_value=MagicMock(allowed=True, matched_policy="P", reason=None)
        )
        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": evaluator}
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value={"username": "alice"}),
        ):
            # Must NOT raise
            await handler._enforce_pbac(
                req,
                resource_type="slug",
                resource_name="x",
                action="slug:execute",
            )

    @pytest.mark.asyncio
    async def test_404_when_no_evaluator_but_security_set(self, handler):
        """_enforce_pbac raises HTTPNotFound when security is set but policy_evaluator is missing."""
        req = MagicMock()
        req.app = {"security": MagicMock()}  # no policy_evaluator
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))
        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value={"username": "alice"}),
        ):
            with pytest.raises(web.HTTPNotFound):
                await handler._enforce_pbac(
                    req,
                    resource_type="slug",
                    resource_name="x",
                    action="slug:execute",
                )

    @pytest.mark.asyncio
    async def test_enforce_pbac_passes_request_into_evalcontext(self, handler):
        """The live request object must be forwarded into EvalContext."""
        captured_kwargs = {}
        evaluator = MagicMock()

        def capture(ctx, **kwargs):
            captured_kwargs['ctx'] = ctx
            captured_kwargs['call_kwargs'] = kwargs
            return MagicMock(allowed=True)

        evaluator.check_access = capture

        req = MagicMock()
        req.app = {"security": MagicMock(), "policy_evaluator": evaluator}
        store = {}
        req.get = lambda k, d=None: store.get(k, d)
        req.__setitem__ = MagicMock(side_effect=lambda k, v: store.update({k: v}))

        with patch(
            "querysource.handlers.abstract.get_session",
            AsyncMock(return_value={"username": "alice"}),
        ):
            await handler._enforce_pbac(
                req,
                resource_type="slug",
                resource_name="x",
                action="slug:execute",
            )

        # The EvalContext must have been built with the request
        assert captured_kwargs, "check_access was not called"
        ctx = captured_kwargs['ctx']
        # EvalContext stores request as an attribute
        assert hasattr(ctx, 'request') or hasattr(ctx, '_request'), (
            "EvalContext must have a 'request' attribute"
        )
        # Verify it's the same request object
        req_in_ctx = getattr(ctx, 'request', None) or getattr(ctx, '_request', None)
        assert req_in_ctx is req, (
            f"EvalContext.request should be the live request, got: {req_in_ctx!r}"
        )
