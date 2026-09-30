"""FEAT-155 / TASK-817: HTTP-level (handler) write-destination gate."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.handlers.multi import QueryHandler


def _make_handler(enforce=None):
    h = QueryHandler.__new__(QueryHandler)
    h.logger = MagicMock()
    h._get_user_session = AsyncMock(return_value={"username": "alice"})
    h._enforce_pbac = enforce or AsyncMock()
    return h


def _request(guardian=True):
    request = MagicMock(spec=web.Request)
    request.app = {"security": MagicMock()} if guardian else {}
    return request


async def test_write_access_enforces_pg_admin():
    h = _make_handler()
    await h._preflight_multiquery(
        _request(), slugs=[], files=[], has_raw_query=False, write_access=True
    )
    h._enforce_pbac.assert_awaited_once()
    kw = h._enforce_pbac.await_args.kwargs
    assert kw["resource_type"] == ResourceType.DATASOURCE
    assert kw["resource_name"] == "pg_admin"
    assert kw["action"] == "datasource:use"


async def test_write_access_denied_raises_404():
    h = _make_handler(AsyncMock(side_effect=web.HTTPNotFound()))
    with pytest.raises(web.HTTPNotFound):
        await h._preflight_multiquery(
            _request(), slugs=[], files=[], has_raw_query=False, write_access=True
        )


async def test_write_access_false_no_extra_check():
    h = _make_handler()
    await h._preflight_multiquery(_request(), slugs=[], files=[], has_raw_query=False)
    h._enforce_pbac.assert_not_awaited()

    h = _make_handler()
    await h._preflight_multiquery(
        _request(guardian=False), slugs=[], files=[], has_raw_query=False, write_access=True
    )
    h._enforce_pbac.assert_not_awaited()
