"""Unit tests for QS(principal=...) and the build_provider() PBAC gate (FEAT-150, TASK-753)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.models import QueryModel
from querysource.queries.qs import QS
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


def _tenant_store(schema: str = "client_a") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )


class FakeProviderInstance:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def prepare_connection(self):
        return None


async def _fake_get_provider(entry, session=None, app=None):
    return ("fake_conn", FakeProviderInstance)


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", username="jdoe", groups=("sales",), tenant_id="client_a")


@pytest.fixture
def deny(monkeypatch):
    """Patch enforce_principal to raise QueryAccessDenied."""
    mock = AsyncMock(side_effect=QueryAccessDenied())
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    return mock


@pytest.fixture
def allow(monkeypatch):
    """Patch enforce_principal to return an allowed decision and record calls."""
    mock = AsyncMock(
        return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True)
    )
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    return mock


def test_request_and_principal_is_value_error(principal):
    with pytest.raises(ValueError):
        QS(slug="x", request=MagicMock(), principal=principal)

    qs = QS(slug="x")
    assert qs._principal is None


@pytest.mark.asyncio
async def test_no_principal_never_calls_core(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(enforcement, "enforce_principal", mock)

    qs = QS(raw_query="SELECT 1", tenant="client_a")
    await qs.build_provider()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_before_store_resolution(principal, deny):
    qs = QS(slug="report_a", tenant="client_a", principal=principal)

    async def _must_not_be_called():
        raise AssertionError("store resolution must not run on a denied principal")

    qs.get_definition_repository = _must_not_be_called
    qs.connection.get_provider = AsyncMock(
        side_effect=AssertionError("get_provider must not run on a denied principal")
    )

    with pytest.raises(QueryAccessDenied):
        await qs.build_provider()

    deny.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["query_not_found", "tenant_not_available"])
async def test_not_found_collapses(principal, allow, code):
    store = _tenant_store()

    class FakeRegistry:
        def resolve(self, tenant):
            return store

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            raise TenantError("not available", error_code=code)

    qs = QS(slug="report_a", tenant="client_a", principal=principal)

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository

    with pytest.raises(QueryAccessDenied):
        await qs.build_provider()


@pytest.mark.asyncio
async def test_store_unavailable_not_collapsed(principal, allow):
    store = _tenant_store()

    class FakeRegistry:
        def resolve(self, tenant):
            return store

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            raise TenantError("store down", error_code="tenant_store_unavailable")

    qs = QS(slug="report_a", tenant="client_a", principal=principal)

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository

    with pytest.raises(TenantError) as excinfo:
        await qs.build_provider()
    assert excinfo.value.error_code == "tenant_store_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw_query": "SELECT 1"},
        {"query": "SELECT 1", "driver": "pg"},
        {"driver": {"driver": "pg"}},
    ],
)
async def test_non_slug_checks_raw_query(principal, allow, kwargs):
    qs = QS(tenant="client_a", principal=principal, **kwargs)
    qs.connection.get_provider = _fake_get_provider

    await qs.build_provider()

    allow.assert_awaited_once()
    args, call_kwargs = allow.await_args
    assert args[0] is principal
    assert args[2] == "raw_query"
    assert args[3] == "raw_query:execute"
    assert call_kwargs["tenant"] == "client_a"


@pytest.mark.asyncio
async def test_tenant_selector_not_principal_tenant(principal, allow):
    store = _tenant_store(schema="client_b")
    resolved_with = {}

    class FakeRegistry:
        def resolve(self, tenant):
            resolved_with["tenant"] = tenant
            return store

    identity = QueryIdentity(store=store, slug="report_a")
    runtime = QueryModel(query_slug="report_a", program_slug="client_b", provider="db")
    loaded_def = LoadedDefinition(identity=identity, runtime=runtime, revision="rev1")

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            return loaded_def

    qs = QS(slug="report_a", tenant="client_b", principal=principal)

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository
    qs.connection.get_provider = _fake_get_provider

    await qs.build_provider()

    assert resolved_with["tenant"] == "client_b"


@pytest.mark.asyncio
async def test_allowed_uses_trusted_credentials(principal, allow):
    store = _tenant_store()
    identity = QueryIdentity(store=store, slug="report_a")
    runtime = QueryModel(query_slug="report_a", program_slug="client_a", provider="db")
    loaded_def = LoadedDefinition(identity=identity, runtime=runtime, revision="rev1")

    class FakeRegistry:
        def resolve(self, tenant):
            return store

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            return loaded_def

    qs = QS(slug="report_a", tenant="client_a", principal=principal)

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository

    calls = {}

    async def spy_get_provider(entry, session=None, app=None):
        calls["session"] = session
        calls["app"] = app
        return ("fake_conn", FakeProviderInstance)

    qs.connection.get_provider = spy_get_provider

    await qs.build_provider()

    assert calls["session"] is None
    assert calls["app"] is None
