"""Shared fixtures for ``tests/handlers``.

This module provides the plumbing used by both the fast unit tests and the
aiohttp-based integration tests for :mod:`querysource.handlers.manager` and
:mod:`querysource.handlers._pagination`.

The integration tests need an aiohttp ``Application`` with ``QueryManager``
registered at the same route the production service uses, plus a fake
``qs_connection`` pool so SQL can be observed without a live Postgres
instance. The :class:`FakeQSConnection` / :class:`FakeConn` below implement
just enough of the asyncdb API used by ``QueryManager.get`` and
``_paginate_list`` to satisfy the HTTP contract.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.handlers.manager import QueryManager
from querysource.models import QueryModel


# ---------------------------------------------------------------------------
# Fake pg connection + pool
# ---------------------------------------------------------------------------


class FakeConn:
    """Minimal asyncdb-pg connection stand-in.

    Records every SQL string passed to :meth:`fetch` / :meth:`fetchval` so
    tests can assert what was built, and returns canned results supplied via
    the parent :class:`FakeQSConnection`.
    """

    def __init__(self, pool: "FakeQSConnection") -> None:
        self._pool = pool

    async def fetchval(self, sql: str) -> Any:
        self._pool.calls.append(("fetchval", sql))
        handler = self._pool.fetchval_handler
        if callable(handler):
            return handler(sql)
        return handler

    async def fetch(self, sql: str) -> list[dict]:
        self._pool.calls.append(("fetch", sql))
        handler = self._pool.fetch_handler
        if callable(handler):
            return handler(sql)
        return handler or []

    async def fetchrow(self, sql: str) -> Optional[dict]:
        self._pool.calls.append(("fetchrow", sql))
        return None


class _AcquireCM:
    """``async with`` context manager returned by ``FakeQSConnection.acquire``."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeQSConnection:
    """Test double for the object stored at ``request.app['qs_connection']``.

    The production object is an asyncdb pool; we mimic its
    ``await pool.acquire()`` → async context manager shape:

        async with await db.acquire() as conn:
            await conn.fetch(...)
    """

    def __init__(self) -> None:
        # Default return values — tests override these via the public attrs.
        self.fetchval_handler: Any = 0
        self.fetch_handler: Any = []
        self.calls: list[tuple[str, str]] = []

    async def acquire(self) -> _AcquireCM:
        return _AcquireCM(FakeConn(self))


# ---------------------------------------------------------------------------
# aiohttp app + test client
# ---------------------------------------------------------------------------


def _build_app(qs_connection: FakeQSConnection) -> web.Application:
    """Build a minimal aiohttp app with ``QueryManager`` registered."""
    app = web.Application()
    app["qs_connection"] = qs_connection
    # Same route pattern as ``querysource.services`` (verified at
    # ``querysource/services.py:128-135``).
    app.router.add_view(
        r"/api/v1/management/queries/{slug}", QueryManager
    )
    app.router.add_view(
        r"/api/v1/management/queries{meta:\:?.*}", QueryManager
    )
    return app


@pytest.fixture
def fake_qs_connection() -> FakeQSConnection:
    """A fresh :class:`FakeQSConnection` per test."""
    return FakeQSConnection()


@pytest_asyncio.fixture
async def test_client(fake_qs_connection: FakeQSConnection):
    """An aiohttp :class:`TestClient` talking to an in-process app.

    Yields the client; tearDown is handled automatically.
    """
    app = _build_app(fake_qs_connection)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Seeded data
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_query_slugs() -> list[dict]:
    """Return 120 synthetic slug dicts used as fake DB rows.

    Each row covers just enough columns for pagination to be observable.
    The integration tests typically configure
    ``fake_qs_connection.fetch_handler`` with a slice of this list.
    """
    return [
        {
            "query_slug": f"fixture_slug_{i:03d}",
            "description": f"fixture {i}",
            "program_slug": "default",
            "provider": "db",
            "is_cached": True,
        }
        for i in range(120)
    ]


# Sanity: make sure the QueryModel contract the tests rely on still holds.
assert "query_slug" in QueryModel.columns(QueryModel)
assert "updated_at" in QueryModel.columns(QueryModel)
