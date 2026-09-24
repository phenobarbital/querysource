"""Shared fixtures for the dry-run end-to-end tests of ``QS`` and ``MultiQS``.

The dry-run e2e suite drives the real query stack — slug resolution, provider
construction, parser ``set_options``/``build_query`` and (for MultiQS) the
threaded dispatch plus the operator/transform pipeline — while replacing only
the two pieces that would reach an external service:

* the parser's lazy Redis handle (``querysource.parsers.abstract.AsyncDB``),
  swapped for an in-memory stub; and
* the definition repository (``BaseQuery.get_definition_repository``), served
  from an in-memory catalog of ``QueryModel`` definitions.

Datasource connections are ``asyncdb`` objects that are created lazily and are
never opened, since dry-run never executes a statement.
"""
from __future__ import annotations

import pytest

import querysource.parsers.abstract as parser_abstract
from querysource.connections import QueryConnection
from querysource.models import QueryModel
from querysource.queries.base import BaseQuery
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


class _FakeRedisConnection:
    """Async context manager standing in for an asyncdb Redis connection."""

    async def __aenter__(self) -> _FakeRedisConnection:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeRedis:
    """Drop-in for ``AsyncDB('redis', dsn=...)`` used by the parsers."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs

    async def connection(self) -> _FakeRedisConnection:
        return _FakeRedisConnection()


LEGACY_STORE = QueryStore(
    database_namespace="navigator",
    schema="public",
    table="queries",
    contract="legacy",
    columns=frozenset({"query_slug", "query_raw", "provider"}),
)


class _FakeRegistry:
    """Tenant registry resolving every selector to the legacy store."""

    def resolve(self, tenant: str | None) -> QueryStore:
        return LEGACY_STORE


class FakeDefinitionRepository:
    """In-memory ``DefinitionRepository`` keyed by slug.

    Args:
        definitions: Mapping of slug to the ``QueryModel`` returned for it.
    """

    def __init__(self, definitions: dict[str, QueryModel]) -> None:
        self.registry = _FakeRegistry()
        self.definitions = definitions
        self.requested: list[str] = []

    def add(self, **fields: object) -> QueryModel:
        """Register a definition built from ``QueryModel`` fields."""
        model = QueryModel(**fields)
        self.definitions[model.query_slug] = model
        return model

    async def get(self, identity: QueryIdentity) -> LoadedDefinition:
        self.requested.append(identity.slug)
        try:
            runtime = self.definitions[identity.slug]
        except KeyError as ex:
            raise TenantError(
                f"Query {identity.slug!r} not found", error_code="query_not_found"
            ) from ex
        return LoadedDefinition(identity=identity, runtime=runtime, revision="rev-1")


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[_FakeRedis]:
    """Keep parser condition handling off the network."""
    monkeypatch.setattr(parser_abstract, "AsyncDB", _FakeRedis)
    return _FakeRedis


@pytest.fixture(autouse=True)
def real_provider_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo instance-level fakes other suites leave on the ``QueryConnection`` singleton.

    Some unit tests assign ``qs.connection.get_provider = fake`` on the shared
    singleton; the e2e suite must resolve real providers regardless of order.
    """
    state = vars(QueryConnection(lazy=True))
    for name in ("get_provider", "default_driver", "datasource"):
        if name in state:
            monkeypatch.delitem(state, name)


@pytest.fixture
def definitions(monkeypatch: pytest.MonkeyPatch) -> FakeDefinitionRepository:
    """Serve slug definitions from memory for every ``BaseQuery`` subclass.

    Patched on the class (not an instance) so the ``QueryObject`` children
    MultiQS builds inside its worker threads see the same catalog.
    """
    repo = FakeDefinitionRepository({})

    async def _get_definition_repository(self: BaseQuery) -> FakeDefinitionRepository:
        return repo

    monkeypatch.setattr(BaseQuery, "get_definition_repository", _get_definition_repository)
    return repo
