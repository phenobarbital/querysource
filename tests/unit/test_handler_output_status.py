"""Unit tests for QueryHandler's OutputError -> HTTP status mapping.

FEAT-146, TASK-714. Drives the REAL ``QueryHandler.query()`` with
``MultiQS.query`` patched to raise an enriched ``OutputError`` (as
``MultiQS`` now does per TASK-713), verifying the handler maps
``category="data"`` -> 422 and ``category="infra"``/unknown -> 500, with
the real detail and the ``X-Output-Errors`` header present.
"""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from querysource.exceptions import OutputError
from querysource.handlers.multi import QueryHandler


class _FakeJson:
    @staticmethod
    def dumps(payload):
        return json.dumps(payload)


def _make_handler() -> QueryHandler:
    h = QueryHandler.__new__(QueryHandler)
    h.debug = False
    h.logger = logging.getLogger("test.handler_output_status")
    h._json = _FakeJson()
    h.no_return = False
    return h


def _make_request():
    request = MagicMock(spec=web.Request)
    request.app = {}  # PBAC disabled -> _preflight_multiquery is a no-op
    request.get.return_value = None  # request.get('user_session')
    return request


async def _drive_query_expecting_output_error(oe: OutputError):
    handler = _make_handler()
    handler.query_parameters = MagicMock(return_value={})
    handler.match_parameters = MagicMock(return_value={})
    handler.json_data = AsyncMock(
        return_value={"queries": {"q": {"slug": "s"}}, "Output": [{"TableOutput": {}}]}
    )
    handler.format = MagicMock(return_value="json")
    request = _make_request()

    with patch(
        "querysource.handlers.multi.MultiQS.query",
        new=AsyncMock(side_effect=oe),
    ), pytest.raises(web.HTTPException) as exc_info:
        await handler.query(request)
    return exc_info.value


@pytest.mark.asyncio
async def test_output_error_category_data_maps_to_422():
    oe = OutputError(
        "duplicate key value violates unique constraint",
        step_name="TableOutput",
        category="data",
    )
    resp = await _drive_query_expecting_output_error(oe)
    assert resp.status == 422
    body = json.loads(resp.text)
    assert "duplicate key value" in body["error"]


@pytest.mark.asyncio
async def test_output_error_category_infra_maps_to_500():
    oe = OutputError(
        "connection refused",
        step_name="TableOutput",
        category="infra",
    )
    resp = await _drive_query_expecting_output_error(oe)
    assert resp.status == 500


@pytest.mark.asyncio
async def test_output_error_unknown_category_defaults_to_500():
    oe = OutputError("mystery failure", step_name="TableOutput")
    resp = await _drive_query_expecting_output_error(oe)
    assert resp.status == 500


@pytest.mark.asyncio
async def test_output_error_x_output_errors_header_populated():
    oe = OutputError(
        "duplicate key value ...", step_name="TableOutput", category="data"
    )
    resp = await _drive_query_expecting_output_error(oe)
    assert "X-Output-Errors" in resp.headers
    assert "TableOutput" in resp.headers["X-Output-Errors"]


@pytest.mark.asyncio
async def test_output_error_crlf_step_name_does_not_crash_response():
    """Regression: step_name is attacker-influenced (the Output step's dict
    key straight from the request body — e.g. get_destination() echoes an
    unregistered destination name verbatim into its OutputError message).
    A step_name containing embedded CR/LF must not crash header
    serialization (aiohttp raises ValueError on raw CR/LF in a header
    value) — it must be sanitized to a single line."""
    oe = OutputError(
        "duplicate key value ...",
        step_name="TableOutput\r\nX-Injected: evil",
        category="data",
    )
    resp = await _drive_query_expecting_output_error(oe)
    assert resp.status == 422
    header_value = resp.headers["X-Output-Errors"]
    assert "\r" not in header_value
    assert "\n" not in header_value
    # Reproduce the exact failure mode aiohttp hits when writing headers to
    # the wire: _serialize_headers raises ValueError on embedded CR/LF.
    from aiohttp._http_writer import _serialize_headers

    status_line = f"HTTP/1.1 {resp.status} {resp.reason}"
    _serialize_headers(status_line, resp.headers)  # must not raise
