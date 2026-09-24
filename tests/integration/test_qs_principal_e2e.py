"""FEAT-150 end-to-end: real setup_pbac + real Rust evaluator + QS(principal=...)."""
import pytest
from aiohttp import web

from querysource.auth.pbac import clear_pbac_runtime, setup_pbac
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.models import QueryModel
from querysource.queries.qs import QS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


def _evaluator_available() -> bool:
    try:
        from navigator_auth.abac.policies.evaluator import _RS_PEP_AVAILABLE  # noqa: F401
        return bool(_RS_PEP_AVAILABLE)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _evaluator_available(), reason="rs_pep evaluator not available")


@pytest.fixture(autouse=True)
def _reset_runtime():
    clear_pbac_runtime()
    yield
    clear_pbac_runtime()


@pytest.fixture
def pbac_app(tmp_path):
    """Real navigator-auth PBAC bootstrap: allow group 'sales' slug:execute on slug:report_a only."""
    policy_yaml = tmp_path / "sales.yaml"
    policy_yaml.write_text(
        """
policies:
  - name: sales_execute_report_a
    effect: allow
    resources:
      - "slug:report_a"
    actions:
      - "slug:execute"
    subjects:
      groups:
        - sales
"""
    )
    app = web.Application()
    pdp, evaluator, guardian = setup_pbac(app, policy_dir=str(tmp_path))
    assert guardian is not None, "setup_pbac must succeed with a valid policy dir"
    assert evaluator is not None
    return app


class FakeProviderInstance:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def prepare_connection(self):
        return None


def _fake_qs(slug: str, principal: QSPrincipal) -> tuple[QS, list]:
    """Build QS with a faked definition repository and get_provider; return (qs, provider_calls)."""
    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="client_a",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )
    identity = QueryIdentity(store=store, slug=slug)
    runtime = QueryModel(query_slug=slug, program_slug="client_a", provider="db")
    loaded_def = LoadedDefinition(identity=identity, runtime=runtime, revision="rev1")

    class FakeRegistry:
        def resolve(self, tenant):
            return store

    class FakeRepo:
        registry = FakeRegistry()

        async def get(self, ident):
            return loaded_def

    qs = QS(slug=slug, tenant="client_a", principal=principal)

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository

    provider_calls = []

    async def fake_get_provider(entry, session=None, app=None):
        provider_calls.append({"entry": entry, "session": session, "app": app})
        return ("fake_conn", FakeProviderInstance)

    qs.connection.get_provider = fake_get_provider

    return qs, provider_calls


@pytest.mark.asyncio
async def test_allowed_slug_reaches_provider(pbac_app):
    principal = QSPrincipal(user_id="35", username="jdoe", groups=("sales",))
    qs, provider_calls = _fake_qs("report_a", principal)

    await qs.build_provider()

    assert len(provider_calls) == 1
    assert provider_calls[0]["session"] is None
    assert provider_calls[0]["app"] is None


@pytest.mark.asyncio
async def test_denied_slug_raises(pbac_app):
    principal = QSPrincipal(user_id="35", username="jdoe", groups=("sales",))
    qs, provider_calls = _fake_qs("report_b", principal)

    with pytest.raises(QueryAccessDenied):
        await qs.build_provider()

    assert provider_calls == []


@pytest.mark.asyncio
async def test_authz_principal_follows_flag(pbac_app, monkeypatch):
    """QS_PBAC_ALLOW_SESSIONLESS_AUTHZ off -> the authz form is denied without evaluation."""
    import querysource.auth.enforcement as enforcement

    monkeypatch.setattr(enforcement, "QS_PBAC_ALLOW_SESSIONLESS_AUTHZ", False)
    principal = QSPrincipal.for_authz("ip")
    qs, provider_calls = _fake_qs("report_a", principal)

    with pytest.raises(QueryAccessDenied):
        await qs.build_provider()

    assert provider_calls == []
