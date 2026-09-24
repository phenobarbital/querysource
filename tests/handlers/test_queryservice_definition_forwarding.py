"""FEAT-151: QueryService forwards request['qs_definition'] to get_source/QS."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.handlers.service import QueryService


class _StopSentinel(Exception):
    """Raised by the stubbed get_source to halt the handler right after the call."""


def _make_handler():
    """Construct a QueryService instance without invoking __init__."""
    h = QueryService.__new__(QueryService)
    h.logger = MagicMock()
    h._loop = None
    h._json = MagicMock()
    h._json.dumps = MagicMock(return_value="{}")
    return h


def _make_request(app_dict=None, definition=None):
    """Build a minimal mock request; ``definition`` sets request['qs_definition']."""
    req = MagicMock(spec=web.Request)
    req.app = app_dict or {}
    req.headers = {}
    store = {"qs_definition": definition} if definition is not None else {}
    req.get = MagicMock(side_effect=lambda key, default=None: store.get(key, default))
    return req


@pytest.mark.asyncio
async def test_query_forwards_definition():
    h = _make_handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={'slug': 'test_slug'})
    h.json_data = AsyncMock(side_effect=TypeError)
    h.get_source = AsyncMock(return_value=None)  # falsy -> raises self.Error()

    sentinel = object()
    req = _make_request(definition=sentinel)

    with pytest.raises(web.HTTPBadRequest):
        await h.query(req)

    h.get_source.assert_awaited_once()
    assert h.get_source.call_args.kwargs.get('definition') is sentinel


@pytest.mark.asyncio
async def test_get_columns_and_columns_forward_definition():
    sentinel = object()

    h = _make_handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={'slug': 'test_slug'})
    h.json_data = AsyncMock(side_effect=TypeError)
    h.get_source = AsyncMock(return_value=None)  # falsy -> returns self.Error()

    req = _make_request(definition=sentinel)
    await h.get_columns(req)
    h.get_source.assert_awaited_once()
    assert h.get_source.call_args.kwargs.get('definition') is sentinel

    h2 = _make_handler()
    h2.query_parameters = MagicMock(return_value={})
    h2.match_parameters = MagicMock(return_value={'slug': 'test_slug'})
    h2.json_data = AsyncMock(side_effect=TypeError)
    h2.get_source = AsyncMock(return_value=None)
    h2.format = MagicMock(return_value='json')

    req2 = _make_request(definition=sentinel)
    await h2.columns(req2)
    h2.get_source.assert_awaited_once()
    assert h2.get_source.call_args.kwargs.get('definition') is sentinel


@pytest.mark.asyncio
async def test_test_slug_forwards_definition():
    sentinel = object()

    h = _make_handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={'slug': 'test_slug'})
    h.json_data = AsyncMock(side_effect=TypeError)
    h.get_source = AsyncMock(side_effect=_StopSentinel)

    req = _make_request(definition=sentinel)

    with pytest.raises(Exception):  # noqa: B017 — any exception halts test_slug after get_source
        await h.test_slug(req)

    h.get_source.assert_awaited_once()
    assert h.get_source.call_args.kwargs.get('definition') is sentinel


@pytest.mark.asyncio
async def test_legacy_request_forwards_none():
    """A request without request['qs_definition'] (v2/v3 legacy callers) forwards None."""
    h = _make_handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={'slug': 'test_slug'})
    h.json_data = AsyncMock(side_effect=TypeError)
    h.get_source = AsyncMock(return_value=None)

    req = _make_request()  # no definition -> request.get('qs_definition') is None

    with pytest.raises(web.HTTPBadRequest):
        await h.query(req)

    h.get_source.assert_awaited_once()
    assert h.get_source.call_args.kwargs.get('definition') is None
