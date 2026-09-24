"""FEAT-151: QS reuses a pre-loaded LoadedDefinition instead of re-reading it."""
import pytest

from querysource.models import QueryModel
from querysource.queries.qs import QS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


def _store() -> QueryStore:
    return QueryStore(database_namespace="localhost:5432/qs", schema="tenant1",
                      table="queries", contract="tenant",
                      columns=frozenset({"query_slug"}))


def _loaded(slug: str = "s1") -> LoadedDefinition:
    identity = QueryIdentity(store=_store(), slug=slug)
    runtime = QueryModel(query_slug=slug, program_slug="tenant1", provider="db", is_cached=False)
    return LoadedDefinition(identity=identity, runtime=runtime, revision="rev-1")


def _stub_provider(qs: QS) -> None:
    class _Provider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def prepare_connection(self):
            return None

    async def _get_provider(objquery, session=None, app=None):
        return ("fake_conn", _Provider)

    qs.connection.get_provider = _get_provider


@pytest.mark.asyncio
async def test_qs_uses_preloaded_definition_and_skips_repo() -> None:
    loaded = _loaded()
    qs = QS(slug="s1", tenant="tenant1", definition=loaded)

    async def _must_not_be_called():
        raise AssertionError("repository must not be read")

    qs.get_definition_repository = _must_not_be_called
    _stub_provider(qs)
    await qs.build_provider()
    assert qs._definition_identity == loaded.identity
    assert qs._definition_revision == "rev-1"


@pytest.mark.asyncio
async def test_qs_ignores_mismatched_preloaded_definition() -> None:
    # The pre-loaded definition is for slug "other"; QS is built for slug "s1".
    # The slug mismatch must fall back to the repository path (S3 mismatch guard).
    mismatched = _loaded(slug="other")
    qs = QS(slug="s1", tenant="tenant1", definition=mismatched)

    matching = _loaded(slug="s1")

    class FakeRegistry:
        def resolve(self, tenant):
            assert tenant == "tenant1"
            return _store()

    class FakeRepo:
        registry = FakeRegistry()
        calls = 0

        async def get(self, identity):
            FakeRepo.calls += 1
            assert identity.slug == "s1"
            return matching

    async def fake_get_definition_repository():
        return FakeRepo()

    qs.get_definition_repository = fake_get_definition_repository
    _stub_provider(qs)
    await qs.build_provider()

    assert FakeRepo.calls == 1
    assert qs._definition_identity == matching.identity
    assert qs._definition_revision == "rev-1"


def test_legacy_constructor_default_is_none() -> None:
    assert QS(slug="s1")._preloaded_definition is None
