"""FEAT-151: MultiQS reuses a pre-loaded definition; child owner rule helper."""
import pytest

from querysource.models import QueryModel
from querysource.queries import MultiQS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore

MULTI_RAW = '{"queries": {"a": {"slug": "child_a"}}}'


def _store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(database_namespace="localhost:5432/qs", schema=schema, table="queries",
                      contract="tenant", columns=frozenset({"query_slug"}))


class _FakeRegistry:
    def resolve(self, tenant):
        return _store(tenant or "public")


def test_resolve_child_owner_rules() -> None:
    reg = _FakeRegistry()
    assert MultiQS.resolve_child_owner({"slug": "c"}, "tenant1", reg)[0] == "tenant1"
    assert MultiQS.resolve_child_owner({"slug": "c", "tenant": "t2"}, "tenant1", reg)[0] == "t2"
    assert MultiQS.resolve_child_owner({"slug": "c", "tenant": None}, "tenant1", reg)[0] is None


class _StopSentinel(Exception):
    """Raised by the stubbed repository to halt query() right after the loader."""


@pytest.mark.asyncio
async def test_multiqs_uses_preloaded_definition_and_records_identity() -> None:
    identity = QueryIdentity(store=_store(), slug="parent")
    runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi", query_raw=MULTI_RAW)
    loaded = LoadedDefinition(identity=identity, runtime=runtime, revision="rev-9")
    qs = MultiQS(slug="parent", tenant="tenant1", definition=loaded)

    async def _no_get_slug(*args, **kwargs):
        raise AssertionError("get_slug must not be called")

    qs.get_slug = _no_get_slug

    async def _stop_after_loader():
        raise _StopSentinel()

    qs.get_definition_repository = _stop_after_loader

    with pytest.raises(_StopSentinel):
        await qs.query()

    assert qs._queries == {"a": {"slug": "child_a"}}
    assert qs._definition_identity == identity
    assert qs._definition_revision == "rev-9"


def test_multiqs_without_definition_unchanged() -> None:
    assert MultiQS(slug="parent")._preloaded_definition is None


@pytest.mark.asyncio
async def test_multiqs_get_slug_branch_also_records_identity() -> None:
    """FEAT-151 code review: spec §3 Module 1 requires BOTH branches of the
    slug loader to set _definition_identity/_definition_revision — the
    get_slug() (non-preloaded) branch was previously silently discarding
    them (get_query_slug returns only the runtime model). get_query_slug
    (interfaces/connections.py) now stashes them onto the calling
    executor itself before returning."""
    identity = QueryIdentity(store=_store(), slug="parent")
    runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi", query_raw=MULTI_RAW)
    loaded = LoadedDefinition(identity=identity, runtime=runtime, revision="rev-42")

    class _FakeRepo:
        registry = _FakeRegistry()

        async def get(self, ident):
            assert ident.slug == "parent"
            return loaded

    qs = MultiQS(slug="parent", tenant="tenant1")
    assert qs._preloaded_definition is None

    class _StopAfterLoader(Exception):
        pass

    # The top-level slug loader (get_slug -> get_query_slug) calls
    # get_definition_repository() once; the child preflight loop (which
    # runs next, for {"a": {"slug": "child_a"}}) calls it a second time —
    # stop there, right after the loader has done its job.
    call_count = {"n": 0}

    async def _get_definition_repository_once_then_stop():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeRepo()
        raise _StopAfterLoader()

    qs.get_definition_repository = _get_definition_repository_once_then_stop

    with pytest.raises(_StopAfterLoader):
        await qs.query()

    assert qs._queries == {"a": {"slug": "child_a"}}
    assert qs._definition_identity == identity
    assert qs._definition_revision == "rev-42"
