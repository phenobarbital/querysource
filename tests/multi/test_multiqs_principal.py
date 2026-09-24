"""Unit tests for MultiQS(principal=...) and the pre-flight PBAC gate (FEAT-150, TASK-754)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied, SlugNotFound
from querysource.queries.multi import MultiQS
from querysource.tenant_errors import TenantError


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", groups=("sales",))


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


def test_principal_stored_and_request_conflict(principal):
    mqs = MultiQS(slug="pipeline_a", principal=principal)
    assert mqs._principal is principal

    with pytest.raises(ValueError):
        MultiQS(slug="pipeline_a", request=MagicMock(), principal=principal)


@pytest.mark.asyncio
async def test_parent_slug_checked_before_get_slug(principal, deny):
    mqs = MultiQS(slug="pipeline_a", principal=principal)

    async def _must_not_be_called(slug, tenant=None):
        raise AssertionError("get_slug must not run before the principal check")

    mqs.get_slug = _must_not_be_called

    with pytest.raises(QueryAccessDenied):
        await mqs.query()

    deny.assert_awaited_once()


@pytest.mark.asyncio
async def test_parent_slug_not_found_collapses(principal, allow):
    mqs = MultiQS(slug="pipeline_a", principal=principal)

    async def _raise_not_found(slug, tenant=None):
        raise SlugNotFound("not found")

    mqs.get_slug = _raise_not_found

    with pytest.raises(QueryAccessDenied):
        await mqs.query()


@pytest.mark.asyncio
async def test_denied_child_runs_nothing(principal, deny):
    mqs = MultiQS(
        queries={
            "a": {"slug": "report_a"},
            "b": {"slug": "report_b"},
            "c": {"slug": "report_c"},
        },
        principal=principal,
    )

    async def _must_not_be_called():
        raise AssertionError("no child preflight/dispatch may run on a denied principal")

    mqs.get_definition_repository = _must_not_be_called

    with pytest.raises(QueryAccessDenied):
        await mqs.query()

    # Only the first child is checked before the deny short-circuits.
    deny.assert_awaited_once()


@pytest.mark.asyncio
async def test_files_and_raw_children(principal, allow):
    mqs = MultiQS(
        queries={"raw1": {"query": "SELECT 1"}},
        files={"report.csv": {"path": "/tmp/report.csv"}},
        principal=principal,
    )

    class _Stop(Exception):
        """Sentinel to stop query() right after _preflight_principal runs,
        before the per-child definition-repository preflight/dispatch."""

    async def _raise_stop():
        raise _Stop()

    mqs.get_definition_repository = _raise_stop

    with pytest.raises(_Stop):
        await mqs.query()

    assert allow.await_count == 2
    resource_names = {call.args[2] for call in allow.await_args_list}
    actions = {call.args[3] for call in allow.await_args_list}
    assert resource_names == {"report.csv", "raw_query"}
    assert actions == {"slug:execute", "raw_query:execute"}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["query_not_found", "tenant_not_available"])
async def test_missing_child_not_wrapped(principal, allow, code):
    class FakeRegistry:
        def resolve(self, tenant):
            return object()

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            raise TenantError("missing", error_code=code)

    mqs = MultiQS(queries={"a": {"slug": "report_a"}}, principal=principal)
    mqs.get_definition_repository = AsyncMock(return_value=FakeRepo())

    with pytest.raises(QueryAccessDenied):
        await mqs.query()


@pytest.mark.asyncio
async def test_no_principal_no_enforcement(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(enforcement, "enforce_principal", mock)

    mqs = MultiQS(queries={"a": {"slug": "s"}})
    await mqs._preflight_principal()

    mock.assert_not_awaited()
