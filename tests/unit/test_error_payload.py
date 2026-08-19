"""Unit tests for the error layer: 422 support + OutputError detail exposure.

FEAT-146, TASK-712.

Note (Codebase Contract drift): the task's Test Specification called
``build_error_payload(category, status, ...)`` with positional args, but the
live signature in ``querysource/utils/errors.py`` declares ``*`` before
``category`` — every parameter is keyword-only. Tests below use keyword
arguments to match the live signature.
"""
import json
import logging

from aiohttp import web

from querysource.exceptions import OutputError, QueryException
from querysource.handlers.abstract import AbstractHandler
from querysource.utils.errors import build_error_payload


class _FakeJson:
    """Minimal stand-in for AbstractHandler's ``self._json`` serializer."""

    @staticmethod
    def dumps(payload):
        return json.dumps(payload)


def _make_handler(debug: bool = False) -> AbstractHandler:
    # AbstractHandler/BaseHandler normally requires an aiohttp request to
    # construct; Error()/build_error_payload only need debug/logger/_json,
    # so build a bare instance via __new__ (no request context needed).
    handler = AbstractHandler.__new__(AbstractHandler)
    handler.debug = debug
    handler.logger = logging.getLogger("test.error_payload")
    handler._json = _FakeJson()
    return handler


def test_output_error_detail_exposed_in_prod():
    p = build_error_payload(
        category="query_error",
        status=422,
        exception=OutputError("duplicate key value ..."),
        debug=False,
    )
    assert "duplicate key value" in (p.get("detail") or p.get("error", ""))


def test_non_output_error_still_redacted_in_prod():
    p = build_error_payload(
        category="server_error",
        status=500,
        exception=QueryException("internal boom"),
        debug=False,
    )
    assert "internal boom" not in p.get("error", "")
    assert "error_id" in p


def test_output_error_respects_explicit_public_message():
    p = build_error_payload(
        category="query_error",
        status=422,
        exception=OutputError("raw db detail"),
        debug=False,
        public_message="explicit override",
    )
    assert p["error"] == "explicit override"


def test_http_unprocessable_entity_available():
    # sanity check the primitive used by AbstractHandler.Error(code=422)
    assert issubclass(web.HTTPUnprocessableEntity, web.HTTPClientError)
    assert web.HTTPUnprocessableEntity.status_code == 422


def test_abstract_handler_error_returns_genuine_422():
    handler = _make_handler(debug=False)
    resp = handler.Error(message="duplicate key value ...", code=422)
    assert isinstance(resp, web.HTTPUnprocessableEntity)
    assert resp.status == 422
