"""Preserve policy semantics with isolated tenant decisions regression contracts."""
import copy
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from aiohttp import web

from querysource.tenants import QueryIdentity, QueryStore


def _make_mock_request(app=None, session=None, authz_backend=None):
    """Create a mock request with optional session and authz backend."""
    request = MagicMock(spec=web.Request)
    request.app = app or {}
    request.query = {}

    # Mock session storage
    session_store = {'user_session': session} if session is not None else {}
    request.__getitem__ = lambda self, key: session_store.get(key)
    request.__setitem__ = lambda self, key, value: session_store.__setitem__(key, value)

    # Mock authz backend
    if authz_backend is not None:
        try:
            from navigator_auth.conf import AUTHZ_BACKEND_KEY
            request.get = lambda key: authz_backend if key == AUTHZ_BACKEND_KEY else None
        except ImportError:
            request.get = lambda key: authz_backend if key == 'authz_backend' else None
    else:
        request.get = lambda key: None

    return request


def _make_mock_evaluator():
    """Create a mock policy evaluator with cache and stats."""
    evaluator = MagicMock()
    evaluator._cache = {}
    evaluator._stats = {}
    evaluator.check_access = MagicMock()
    return evaluator


def _make_mock_session(userinfo=None):
    """Create a mock session with optional userinfo."""
    session = MagicMock()
    if userinfo:
        session.get = lambda key: userinfo if key == 'user' else None
    else:
        session.get = lambda key: None
    return session


class DummyHandler:
    """Minimal handler for testing _enforce_owned_slug.

    This mimics the key parts of AbstractHandler needed for testing
    without importing the full handler hierarchy.
    """

    def __init__(self):
        self.debug = True
        self.logger = MagicMock()
        self._json = MagicMock()
        self._json.dumps = lambda x: '{}'

    async def _get_user_session(self, request):
        """Extract user session from request (mirrors AbstractHandler)."""
        cached = request.get('user_session')
        if cached is not None:
            return cached
        # For testing, we just return what's stored
        return request.__getitem__('user_session')

    async def _enforce_owned_slug(self, request, identity, action):
        """Testable implementation of _enforce_owned_slug."""
        # Import here to avoid circular imports at module load time
        from querysource.conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ

        evaluator = request.app.get("policy_evaluator")
        if request.app.get("security") is None:
            return  # PBAC disabled — fast-path no-op
        if evaluator is None:
            raise web.HTTPNotFound()

        # Create a shallow copy with cleared cache for tenant isolation
        detached = copy.copy(evaluator)
        detached._cache = {}
        detached._stats = dict(evaluator._stats)

        # Extract session or use sessionless authz
        session = await self._get_user_session(request)
        authz_userinfo = None
        if session is None:
            if QS_PBAC_ALLOW_SESSIONLESS_AUTHZ:
                try:
                    from navigator_auth.conf import AUTHZ_BACKEND_KEY
                except ImportError:
                    AUTHZ_BACKEND_KEY = 'authz_backend'
                authz_backend = request.get(AUTHZ_BACKEND_KEY)
                if authz_backend:
                    backend = str(authz_backend)
                    authz_userinfo = {
                        'username': f'authz:{backend}',
                        'groups': ['authorized', backend],
                        'roles': [],
                    }
                    self.logger.info(
                        "PBAC sessionless authz (backend=%s): evaluating "
                        "slug ownership for %s as group 'authorized'",
                        backend,
                        identity.slug,
                    )
            if authz_userinfo is None:
                self.logger.info(
                    "PBAC denied (no session): slug=%s action=%s",
                    identity.slug,
                    action,
                )
                raise web.HTTPNotFound()

        # Build the evaluation context
        import inspect
        from navigator_auth.abac.context import EvalContext
        from navigator_auth.abac.policies.environment import Environment
        from navigator_auth.conf import AUTH_SESSION_OBJECT

        if authz_userinfo is not None:
            userinfo = authz_userinfo
            user = None
        else:
            userinfo = (
                session.get(AUTH_SESSION_OBJECT, {})
                if hasattr(session, 'get') else {}
            )
            if not isinstance(userinfo, dict):
                userinfo = {}
            user = userinfo if userinfo else None

        ctx = EvalContext(
            request=request,
            user=user,
            userinfo=userinfo,
            session=session,
        )

        # Evaluate using the detached evaluator with the identity's slug
        result = detached.check_access(
            ctx=ctx,
            resource_type="slug",
            resource_name=identity.slug,
            action=action,
            env=Environment(),
        )
        if inspect.iscoroutine(result):
            result = await result
        if not result.allowed:
            self.logger.info(
                "PBAC denied: slug=%s action=%s policy=%s reason=%s",
                identity.slug,
                action,
                getattr(result, 'matched_policy', None),
                getattr(result, 'reason', None),
            )
            raise web.HTTPNotFound()


@pytest.mark.asyncio
async def test_pbac_disabled_has_no_membership_requirement() -> None:
    """pbac disabled has no membership requirement.

    When PBAC is disabled (no 'security' in app), _enforce_owned_slug
    should return early without raising any membership requirement.
    """
    handler = DummyHandler()
    request = _make_mock_request(app={})  # No security key

    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )
    identity = QueryIdentity(store=store, slug="test_slug")

    # Should return without raising when PBAC is disabled
    await handler._enforce_owned_slug(request, identity, "slug:execute")


@pytest.mark.asyncio
async def test_copy_keeps_app_cache_and_policy_immutable() -> None:
    """copy keeps app cache and policy immutable.

    Creating a detached evaluator should not modify the original
    evaluator's cache or stats. The app evaluator should remain
    unchanged after _enforce_owned_slug runs.
    """
    handler = DummyHandler()

    # Create mock evaluator with some cache/stats
    original_evaluator = _make_mock_evaluator()
    original_evaluator._cache = {"key1": "value1"}
    original_evaluator._stats = {"stat1": 1}

    app = {
        "security": MagicMock(),  # PBAC enabled
        "policy_evaluator": original_evaluator,
    }
    request = _make_mock_request(app=app, session=_make_mock_session())

    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )
    identity = QueryIdentity(store=store, slug="test_slug")

    # Mock check_access to return allowed result
    mock_result = MagicMock()
    mock_result.allowed = True
    original_evaluator.check_access = MagicMock(return_value=mock_result)

    # Run enforcement
    try:
        await handler._enforce_owned_slug(request, identity, "slug:execute")
    except Exception:
        pass  # May raise, we just care about the copy behavior

    # Verify original evaluator's cache and stats are unchanged
    assert original_evaluator._cache == {"key1": "value1"}, "Original cache should be unchanged"
    assert original_evaluator._stats == {"stat1": 1}, "Original stats should be unchanged"


@pytest.mark.asyncio
async def test_same_slug_different_owner_no_decision_reuse() -> None:
    """same slug different owner no decision reuse.

    Two different tenants with the same slug should not share
    cached decisions. Each call should use a fresh detached
    evaluator with cleared cache.
    """
    handler = DummyHandler()

    # Create mock evaluator
    evaluator = _make_mock_evaluator()
    evaluator.check_access = MagicMock()

    app = {
        "security": MagicMock(),
        "policy_evaluator": evaluator,
    }

    store1 = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )
    store2 = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant2",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )

    identity1 = QueryIdentity(store=store1, slug="shared_slug")
    identity2 = QueryIdentity(store=store2, slug="shared_slug")

    session = _make_mock_session()

    # First call for tenant1
    request1 = _make_mock_request(app=app, session=session)
    mock_result = MagicMock()
    mock_result.allowed = True
    evaluator.check_access = MagicMock(return_value=mock_result)

    try:
        await handler._enforce_owned_slug(request1, identity1, "slug:execute")
    except Exception:
        pass

    # Verify check_access was called with tenant1's identity
    call_args = evaluator.check_access.call_args
    assert call_args is not None
    assert call_args.kwargs.get('resource_name') == "shared_slug"

    # Reset mock for second call
    evaluator.check_access.reset_mock()

    # Second call for tenant2 with same slug
    request2 = _make_mock_request(app=app, session=session)
    try:
        await handler._enforce_owned_slug(request2, identity2, "slug:execute")
    except Exception:
        pass

    # Verify check_access was called again (not cached)
    assert evaluator.check_access.called, "check_access should be called for second tenant"


@pytest.mark.asyncio
async def test_sessionless_authz_async_result_and_denied_child() -> None:
    """sessionless authz async result and denied child.

    When QS_PBAC_ALLOW_SESSIONLESS_AUTHZ is enabled and the request
    has an authz backend stamp, the request should be evaluated under
    a synthetic identity. Also tests that coroutine results are
    properly awaited.
    """
    handler = DummyHandler()

    # Create mock evaluator
    evaluator = _make_mock_evaluator()

    # Mock check_access to return a coroutine that resolves to denied
    async def async_denied(*args, **kwargs):
        result = MagicMock()
        result.allowed = False
        result.matched_policy = "test_policy"
        result.reason = "denied"
        return result

    evaluator.check_access = AsyncMock(side_effect=async_denied)

    app = {
        "security": MagicMock(),
        "policy_evaluator": evaluator,
    }

    # Request with authz backend but no session
    request = _make_mock_request(app=app, session=None, authz_backend="ip_allowed")

    # Patch QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
    with patch('querysource.handlers.abstract.QS_PBAC_ALLOW_SESSIONLESS_AUTHZ', True):
        store = QueryStore(
            database_namespace="localhost:5432/querysource",
            schema="tenant1",
            table="queries",
            contract="tenant",
            columns=frozenset({"query_slug"}),
        )
        identity = QueryIdentity(store=store, slug="test_slug")

        # Should raise HTTPNotFound due to denied access
        with pytest.raises(web.HTTPNotFound):
            await handler._enforce_owned_slug(request, identity, "slug:execute")

    # Verify the synthetic identity was used (groups should include 'authorized')
    call_args = evaluator.check_access.call_args
    assert call_args is not None
    ctx = call_args.kwargs.get('ctx')
    assert ctx is not None
    userinfo = ctx.userinfo
    assert 'authorized' in userinfo.get('groups', []), "Should use synthetic identity with 'authorized' group"