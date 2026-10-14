"""Wiring tests for the semantic provider integration."""

from unittest.mock import AsyncMock

import pytest
from datamodel.typedefs import AttrDict

from querysource.auth.principal import QSPrincipal
from querysource.interfaces.connections import Connection
from querysource.queries.qs import QS
from querysource.semantic.exceptions import SemanticRequestError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


class _Provider:
    """Provider test double that records construction arguments."""

    received: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        """Record provider construction keyword arguments."""
        type(self).received = kwargs

    async def prepare_connection(self) -> None:
        """Complete the provider setup successfully."""


class _FailingProvider(_Provider):
    """Provider test double that raises a semantic request error."""

    async def prepare_connection(self) -> None:
        """Raise an unwrapped semantic request error."""
        raise SemanticRequestError("bad")


@pytest.fixture
def definitions(monkeypatch: pytest.MonkeyPatch):
    """Provide an in-memory slug definition repository for QS wiring tests."""
    definitions_by_slug: dict[str, AttrDict] = {}
    store = QueryStore(
        database_namespace="test/querysource",
        schema="public",
        table="queries",
        contract="legacy",
        columns=frozenset({"query_slug", "provider"}),
    )

    class _Registry:
        def resolve(self, tenant: str | None) -> QueryStore:
            return store

    class _Definitions:
        registry = _Registry()

        def add(self, **fields: object) -> AttrDict:
            definition = AttrDict(fields)
            definitions_by_slug[definition.query_slug] = definition
            return definition

        async def get(self, identity: QueryIdentity) -> LoadedDefinition:
            return LoadedDefinition(
                identity=identity,
                runtime=definitions_by_slug[identity.slug],
                revision="test",
            )

    repository = _Definitions()

    async def get_definition_repository() -> _Definitions:
        return repository

    monkeypatch.setattr(QS, "get_definition_repository", get_definition_repository)
    return repository


@pytest.mark.asyncio
async def test_get_provider_semantic_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve semantic without attempting a default database driver."""
    connection = Connection()
    sentinel = object()
    monkeypatch.setattr(connection, "load_provider", lambda provider: sentinel)
    monkeypatch.setattr(connection, "default_driver", AsyncMock(side_effect=AssertionError("unexpected")))

    assert await connection.get_provider(AttrDict(provider="semantic")) == [None, sentinel]


@pytest.mark.asyncio
async def test_provider_receives_principal(definitions, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass a slug query's principal to its semantic provider."""
    definitions.add(query_slug="s", provider="semantic", query_raw="x", conditions={}, is_cached=False)
    principal = QSPrincipal(user_id="1", username="semantic", groups=(), tenant_id="public")
    query = QS(slug="s", principal=principal)

    async def get_provider(entry, session=None, app=None):
        return None, _Provider

    monkeypatch.setattr(query.connection, "get_provider", get_provider)

    await query.build_provider()

    assert _Provider.received["principal"] is principal


@pytest.mark.asyncio
async def test_provider_semantic_error_surfaces_with_code(definitions, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep semantic request errors intact instead of wrapping them as 500s."""
    definitions.add(query_slug="s", provider="semantic", query_raw="x", conditions={}, is_cached=False)
    query = QS(slug="s")

    async def get_provider(entry, session=None, app=None):
        return None, _FailingProvider

    monkeypatch.setattr(query.connection, "get_provider", get_provider)

    with pytest.raises(SemanticRequestError) as error:
        await query.build_provider()

    assert error.value.code == 400


def test_conf_semantic_defaults() -> None:
    """Keep semantic settings' documented defaults stable."""
    from querysource import conf

    assert conf.QS_SEMANTIC_DEFAULT_LIMIT == 500
    assert conf.QS_SEMANTIC_MAX_LIMIT == 5000
