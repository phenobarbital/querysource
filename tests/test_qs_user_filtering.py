"""FEAT-184 TASK-956 — QS identity plumbing and 403 propagation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel
from querysource.providers.sql import sqlProvider
from querysource.queries.qs import QS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"}], "program": {"restricted": True}}


def _request(session: dict | None) -> MagicMock:
    """Build the minimal request mapping consumed by ``QS.build_provider``."""
    request = MagicMock(spec=web.Request)
    request.app = {}
    request.get.side_effect = lambda key, default=None: {"user_session": session}.get(key, default)
    return request


def _loaded_definition(*, options: dict | None = OPTIONS, query_raw: str = "SELECT * FROM orders {where_cond}"):
    """Build a detached definition suitable for the QS provider path."""
    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="public",
        table="queries",
        has_program_slug=False,
        columns=frozenset({"query_slug"}),
    )
    slug = "orders.by_user"
    identity = QueryIdentity(store=store, slug=slug)
    runtime = QueryModel(
        query_slug=slug,
        program_slug="restricted",
        provider="db",
        query_raw=query_raw,
        user_filtering=options is not None,
        user_filter_options=options or {},
    )
    return LoadedDefinition(identity=identity, runtime=runtime, revision="rev1")


class _Provider:
    """Capture provider construction arguments for QS identity assertions."""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)

    async def prepare_connection(self):
        """Complete the provider lifecycle without a datasource."""


async def _build_qs(monkeypatch: pytest.MonkeyPatch, request: MagicMock, provider=_Provider, definition=None) -> QS:
    """Build a slug QS with an in-memory definition repository and provider."""
    loaded = definition or _loaded_definition()
    qs = QS(slug=loaded.identity.slug, request=request)

    class Registry:
        def resolve(self, tenant):
            return loaded.identity.store

    class Repository:
        registry = Registry()

        async def get(self, identity):
            return loaded

    async def get_repository():
        return Repository()

    async def get_provider(entry, session=None, app=None):
        return "connection", provider

    qs.get_definition_repository = get_repository
    qs.connection.get_provider = get_provider
    return qs


@pytest.mark.asyncio
async def test_qs_resolves_principal_from_session(monkeypatch: pytest.MonkeyPatch):
    """Slug providers receive both the typed principal and raw session userinfo."""
    _Provider.calls = []
    session = {"user": {"user_id": "7", "username": "jdoe", "programs": ["restricted"]}}
    qs = await _build_qs(monkeypatch, _request(session))

    await qs.build_provider()

    args = _Provider.calls[0]
    assert args["principal"] == QSPrincipal(user_id="7", username="jdoe", programs=("restricted",), channel="http")
    assert args["userinfo"] == session["user"]


@pytest.mark.asyncio
async def test_qs_reraises_user_filter_denied(monkeypatch: pytest.MonkeyPatch):
    """A provider's per-user denial keeps its 403 exception type and code."""
    class DenyingProvider(_Provider):
        async def prepare_connection(self):
            raise UserFilterDenied(reason_code="program_restricted")

    session = {"user": {"user_id": "7", "username": "jdoe", "programs": []}}
    qs = await _build_qs(monkeypatch, _request(session), provider=DenyingProvider)

    with pytest.raises(UserFilterDenied) as exc_info:
        await qs.build_provider()
    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_qs_user_filtering_ignores_pbac_flag(monkeypatch: pytest.MonkeyPatch):
    """Per-user filtering remains enforced when the separate PBAC flag is disabled."""
    monkeypatch.setattr("querysource.conf.QS_PBAC_ENABLED", False)
    definition = _loaded_definition(options={"rules": [], "program": {"restricted": True}})

    class DenyingProvider(_Provider):
        async def prepare_connection(self):
            raise UserFilterDenied(reason_code="program_restricted")

    qs = await _build_qs(
        monkeypatch,
        _request({"user": {"user_id": "7", "programs": []}}),
        provider=DenyingProvider,
        definition=definition,
    )
    with pytest.raises(UserFilterDenied):
        await qs.build_provider()


@pytest.mark.asyncio
async def test_user_filter_end_to_end_sql(monkeypatch: pytest.MonkeyPatch):
    """The resolved session identity reaches the real SQL parser and changes its SQL."""
    definition = _loaded_definition(
        options={"rules": [{"column": "user_id", "attribute": "user_id"}], "program": {"restricted": False}}
    )
    sql_by_user = []
    for user_id in ("7", "8"):
        session = {"user": {"user_id": user_id, "programs": []}}
        qs = await _build_qs(monkeypatch, _request(session), provider=sqlProvider, definition=definition)
        await qs.build_provider()
        sql_by_user.append(qs._qs._query)

    assert "'7'" in sql_by_user[0]
    assert "'8'" in sql_by_user[1]
    assert sql_by_user[0] != sql_by_user[1]


def test_placeholder_import() -> None:
    """Keep the exception's HTTP contract explicit for callers."""
    assert UserFilterDenied().code == 403
