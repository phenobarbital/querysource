"""Tests for propagating a library caller principal to child queries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.queries.multi.sources.executors import LocalExecutor
from querysource.queries.multi.sources.query import ThreadQuery
from querysource.queries.obj import QueryObject


@pytest.mark.asyncio
async def test_local_executor_forwards_principal(monkeypatch):
    """LocalExecutor passes the principal into the child QueryObject."""
    principal = QSPrincipal(user_id="user-1")
    recorded = {}

    class RecorderQueryObject:
        def __init__(self, *args, **kwargs):
            recorded.update(kwargs)

        async def build_provider(self):
            return self

        async def query(self):
            return None

    monkeypatch.setattr(
        "querysource.queries.multi.sources.executors.QueryObject", RecorderQueryObject
    )

    await LocalExecutor().execute(
        "child",
        {"slug": "orders"},
        asyncio.Queue(),
        None,
        principal=principal,
    )

    assert recorded["principal"] is principal


@pytest.mark.asyncio
async def test_query_object_passes_principal_to_provider(monkeypatch):
    """QueryObject includes its principal in slug provider construction args."""
    principal = QSPrincipal(user_id="user-1")
    provider_kwargs = {}

    class RecorderProvider:
        def __init__(self, **kwargs):
            provider_kwargs.update(kwargs)

        async def prepare_connection(self):
            return None

    runtime = SimpleNamespace(is_cached=False, cache_timeout=3600, conditions={})
    loaded_definition = SimpleNamespace(
        identity=SimpleNamespace(slug="orders"), revision="rev-1", runtime=runtime
    )
    query_object = QueryObject(
        "child", {"slug": "orders"}, definition=loaded_definition, principal=principal
    )

    async def fake_get_provider(*args, **kwargs):
        return None, RecorderProvider

    monkeypatch.setattr(query_object, "get_provider", fake_get_provider)
    await query_object.build_provider()

    assert provider_kwargs["principal"] is principal


@pytest.mark.asyncio
async def test_thread_query_forwards_principal():
    """ThreadQuery passes its principal to the configured executor."""
    principal = QSPrincipal(user_id="user-1")
    query = ThreadQuery(
        "child", {"slug": "orders"}, None, asyncio.Queue(), principal=principal
    )
    query._executor = AsyncMock()

    await query.fetch()

    call_kwargs = query._executor.execute.await_args.kwargs
    assert call_kwargs["principal"] is principal
