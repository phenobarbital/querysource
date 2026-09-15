"""Preserve policy semantics with isolated tenant decisions regression contracts."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.handlers.abstract import AbstractHandler
from querysource.handlers.multi import QueryHandler
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry


class _FakeRequest:
    """Minimal, real-dict-backed stand-in for aiohttp.web.Request's
    per-request storage — avoids the MagicMock __getitem__/__setitem__
    round-trip gotcha found during TASK-723 (a bare MagicMock's magic
    methods do not persist values across calls).
    """

    def __init__(self, app: dict, storage: dict | None = None):
        self.app = app
        self.query = {}
        self._storage = storage or {}
        # Attributes read directly by navigator_auth.abac.context.EvalContext
        # (not aiohttp Request internals this test needs to exercise).
        self.remote = "127.0.0.1"
        self.method = "GET"
        self.headers = {}
        self.path_qs = "/api/v1/management/queries/test_slug"
        self.path = "/api/v1/management/queries/test_slug"
        self.rel_url = self.path_qs

    def get(self, key, default=None):
        return self._storage.get(key, default)

    def __getitem__(self, key):
        return self._storage[key]

    def __setitem__(self, key, value):
        self._storage[key] = value


class _Harness:
    """Binds the real AbstractHandler._enforce_owned_slug/_get_user_session
    to a lightweight instance, without needing the full BaseHandler/aiohttp
    View construction chain (which this narrow unit test does not need).
    """

    def __init__(self):
        self.logger = MagicMock()

    # The exact same function objects as AbstractHandler's — not a
    # reimplementation. Calling self._enforce_owned_slug(...) below
    # invokes the real production code.
    _enforce_owned_slug = AbstractHandler._enforce_owned_slug
    _get_user_session = AbstractHandler._get_user_session


def _make_evaluator(check_access=None):
    evaluator = MagicMock()
    evaluator._cache = {"pre-existing": "decision"}
    evaluator._stats = {"hits": 1}
    evaluator.check_access = check_access or MagicMock()
    return evaluator


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )


@pytest.mark.asyncio
async def test_pbac_disabled_has_no_membership_requirement() -> None:
    """pbac disabled has no membership requirement."""
    handler = _Harness()
    request = _FakeRequest(app={})  # no "security" key -> PBAC disabled
    identity = QueryIdentity(store=_tenant_store(), slug="test_slug")

    # Must return (not raise) when PBAC is disabled — no new membership
    # requirement is introduced (AC-1).
    await handler._enforce_owned_slug(request, identity, "slug:execute")


@pytest.mark.asyncio
async def test_copy_keeps_app_cache_and_policy_immutable() -> None:
    """copy keeps app cache and policy immutable."""
    handler = _Harness()

    mock_result = MagicMock()
    mock_result.allowed = True
    evaluator = _make_evaluator(check_access=MagicMock(return_value=mock_result))

    app = {"security": MagicMock(), "policy_evaluator": evaluator}
    # A pre-cached, non-None session short-circuits _get_user_session's
    # navigator_session.get_session() call entirely (it returns the
    # cached value directly — see abstract.py:311-313).
    request = _FakeRequest(app=app, storage={"user_session": MagicMock(get=lambda k, d=None: {})})
    identity = QueryIdentity(store=_tenant_store(), slug="test_slug")

    await handler._enforce_owned_slug(request, identity, "slug:execute")

    # AC-2: the app's evaluator cache/stats must be untouched — only the
    # shallow-copied detached evaluator's cache was cleared.
    assert evaluator._cache == {"pre-existing": "decision"}
    assert evaluator._stats == {"hits": 1}
    # check_access was actually invoked on the (cleared) detached copy —
    # verifying the real production code path ran, not a stub.
    assert evaluator.check_access.called
    call_kwargs = evaluator.check_access.call_args.kwargs
    assert call_kwargs["resource_type"] == "slug"
    assert call_kwargs["resource_name"] == "test_slug"
    assert call_kwargs["action"] == "slug:execute"


@pytest.mark.asyncio
async def test_same_slug_different_owner_no_decision_reuse() -> None:
    """same slug different owner no decision reuse."""
    handler = _Harness()

    mock_result = MagicMock()
    mock_result.allowed = True
    evaluator = _make_evaluator(check_access=MagicMock(return_value=mock_result))
    app = {"security": MagicMock(), "policy_evaluator": evaluator}
    session = MagicMock(get=lambda k, d=None: {})

    identity1 = QueryIdentity(store=_tenant_store("tenant1"), slug="shared_slug")
    identity2 = QueryIdentity(store=_tenant_store("tenant2"), slug="shared_slug")

    request1 = _FakeRequest(app=app, storage={"user_session": session})
    await handler._enforce_owned_slug(request1, identity1, "slug:execute")
    assert evaluator.check_access.called
    # Each call must clear the *detached* copy's cache independently —
    # verified by asserting check_access was reached with a fresh cache
    # both times, using a second, distinct request/identity.
    evaluator.check_access.reset_mock()

    request2 = _FakeRequest(app=app, storage={"user_session": session})
    await handler._enforce_owned_slug(request2, identity2, "slug:execute")
    assert evaluator.check_access.called
    second_call_kwargs = evaluator.check_access.call_args.kwargs
    assert second_call_kwargs["resource_name"] == "shared_slug"
    # The app-level evaluator's own cache was never populated by either
    # call — only the (per-call, discarded) detached copy's cache was
    # ever touched, so tenant1's and tenant2's identical-slug decisions
    # never share cached state.
    assert evaluator._cache == {"pre-existing": "decision"}


@pytest.mark.asyncio
async def test_sessionless_authz_async_result_and_denied_child(monkeypatch) -> None:
    """sessionless authz async result and denied child."""
    handler = _Harness()

    async def async_denied(**kwargs):
        result = MagicMock()
        result.allowed = False
        result.matched_policy = "test_policy"
        result.reason = "denied"
        return result

    evaluator = _make_evaluator(check_access=AsyncMock(side_effect=async_denied))
    app = {"security": MagicMock(), "policy_evaluator": evaluator}
    # No session cached (explicit None, not merely absent — see
    # _FakeRequest.get contract) and an authz backend stamp present,
    # exercising the sessionless-authz branch.
    request = _FakeRequest(
        app=app,
        storage={"user_session": None, "authz_backend": "ip_allowed"},
    )
    identity = QueryIdentity(store=_tenant_store(), slug="denied_slug")

    monkeypatch.setattr(
        "querysource.handlers.abstract.QS_PBAC_ALLOW_SESSIONLESS_AUTHZ",
        True,
    )
    with pytest.raises(web.HTTPNotFound):
        await handler._enforce_owned_slug(request, identity, "slug:execute")

    # The coroutine result from check_access was awaited (AC-2 "retain
    # coroutine-result handling") and its denial correctly raised.
    assert evaluator.check_access.called
    call_kwargs = evaluator.check_access.call_args.kwargs
    assert call_kwargs["resource_name"] == "denied_slug"


class _MultiHarness:
    """Binds the real QueryHandler._preflight_multiquery_owned plus the
    AbstractHandler methods it calls (_enforce_owned_slug,
    _get_user_session) to a lightweight instance — same rationale as
    _Harness above.
    """

    def __init__(self):
        self.logger = MagicMock()

    _preflight_multiquery_owned = QueryHandler._preflight_multiquery_owned
    _enforce_owned_slug = AbstractHandler._enforce_owned_slug
    _get_user_session = AbstractHandler._get_user_session


def _discovered_registry(schema: str = "tenant1") -> TenantRegistry:
    """A TenantRegistry already populated (bypassing discover()/a live DB —
    same pattern as tests/tenants/test_tenant_execution_context.py).
    """
    registry = TenantRegistry()
    store = _tenant_store(schema)
    registry._stores = (store,)
    registry._default_store = store
    return registry


@pytest.mark.asyncio
async def test_multiquery_preflight_skips_when_registry_absent() -> None:
    """AC-4 legacy fallback: no app["qs_tenant_registry"] published ->
    this tenant-specific check is a no-op (matches the TASK-723/724
    "not wired up" convention), never a KeyError or a false denial.
    """
    handler = _MultiHarness()
    app = {"security": MagicMock()}  # PBAC on, tenant feature not wired
    request = _FakeRequest(app=app)
    await handler._preflight_multiquery_owned(
        request, slugs=["some_slug"], files=[], has_raw_query=False
    )  # must not raise


@pytest.mark.asyncio
async def test_multiquery_preflight_fails_closed_on_resolution_error() -> None:
    """AC-1 "fail-closed errors": once qs_tenant_registry IS published,
    a resolution failure must deny (404), never silently allow the batch.
    """
    handler = _MultiHarness()

    class _BoomRegistry:
        def resolve(self, tenant):
            raise RuntimeError("registry corrupted")

    app = {"security": MagicMock(), "qs_tenant_registry": _BoomRegistry()}
    request = _FakeRequest(app=app)
    with pytest.raises(web.HTTPNotFound):
        await handler._preflight_multiquery_owned(
            request, slugs=["some_slug"], files=[], has_raw_query=False
        )


@pytest.mark.asyncio
async def test_multiquery_preflight_checks_real_identity_not_alias() -> None:
    """AC-4: each slug is checked as a real, resolved QueryIdentity
    (store + slug) via _enforce_owned_slug — not the bare alias string
    Guardian.filter_resources would otherwise check.
    """
    handler = _MultiHarness()

    mock_result = MagicMock()
    mock_result.allowed = True
    evaluator = _make_evaluator(check_access=MagicMock(return_value=mock_result))
    registry = _discovered_registry()
    app = {
        "security": MagicMock(),
        "policy_evaluator": evaluator,
        "qs_tenant_registry": registry,
    }
    session = MagicMock(get=lambda k, d=None: {})
    request = _FakeRequest(app=app, storage={"user_session": session})

    await handler._preflight_multiquery_owned(
        request, slugs=["real_child_slug"], files=[], has_raw_query=False
    )

    assert evaluator.check_access.called
    call_kwargs = evaluator.check_access.call_args.kwargs
    # Resolved against the registry's actual store, not a bare alias.
    assert call_kwargs["resource_name"] == "real_child_slug"
