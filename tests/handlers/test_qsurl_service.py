"""QSUrlService: 400 detail, PBAC ordering, ?q= form, decode-once (spec AC5-AC8).

NOTE (TASK-775, environment limitation): the qsurl parser back-ends (Rust
extension, TASK-764/777; Lark fallback, TASK-766) are blocked in this sandbox
— porting the user-provided reference tarball requires explicit operator
permission the sandbox denied (see TASK-764's Completion Note). ``parse()``
is therefore not functional here (``querysource.qsurl._fallback`` does not
exist yet). Every test below patches ``querysource.handlers.qsurl.parse``
directly instead of exercising the real grammar, so the handler's own logic
(source assembly, PBAC ordering, capability probe wiring, translate.split
integration, error envelope) is fully covered independent of the blocked
parser back-ends. ``translate.split`` itself (TASK-771) is real and
unmocked wherever a full IR is supplied.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.handlers.qsurl import QSUrlService
from querysource.qsurl import QSUrlError


def _handler() -> QSUrlService:
    h = QSUrlService.__new__(QSUrlService)
    h.logger = MagicMock()
    h._loop = None
    h.debug = False
    h._json = MagicMock()
    h._json.dumps = json.dumps
    return h


def _request(app_dict=None) -> web.Request:
    req = MagicMock(spec=web.Request)
    req.app = app_dict or {}
    req.headers = {}
    req.get = (app_dict or {}).get
    return req


def _ir(**over) -> dict:
    ir = {
        "slug": "s",
        "fields": [],
        "filter": None,
        "sort": [],
        "limit": None,
        "offset": None,
        "distinct": False,
        "requires": [],
    }
    ir.update(over)
    return ir


BASE_CAPS = frozenset({"select", "filter", "in_list", "null_check"})


async def test_parse_error_is_400_with_detail():
    h = _handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={"path": "stores:order(x)"})
    h.json_data = AsyncMock(side_effect=TypeError)
    err = QSUrlError(
        "parse",
        "unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct",
        offset=18,
        pointer="stores:order(x)\n                  ^",
    )
    with patch("querysource.handlers.qsurl.parse", side_effect=err):
        with pytest.raises(web.HTTPBadRequest) as exc_info:
            await h.query(_request())
    body = json.loads(exc_info.value.text)
    assert exc_info.value.status == 400
    assert body["detail"]["kind"] == "parse"
    assert body["detail"]["pointer"]


async def test_pbac_deny_before_capability_probe():
    h = _handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={"path": "stores"})
    h.json_data = AsyncMock(side_effect=TypeError)
    h._enforce_slug_execute = AsyncMock(side_effect=web.HTTPNotFound())
    h.resolve_capabilities = AsyncMock()
    h.get_source = AsyncMock()
    with patch("querysource.handlers.qsurl.parse", return_value=_ir(slug="stores")):
        with pytest.raises(web.HTTPNotFound):
            await h.query(_request())
    h.resolve_capabilities.assert_not_awaited()
    h.get_source.assert_not_awaited()


async def test_q_form_joins_slug_and_rest():
    h = _handler()
    h.query_parameters = MagicMock(return_value={"q": "{a}?b=1"})
    h.match_parameters = MagicMock(return_value={"path": "stores"})
    h.json_data = AsyncMock(side_effect=TypeError)
    captured = {}

    def _fake_parse(source):
        captured["source"] = source
        raise QSUrlError("parse", "boom")

    with patch("querysource.handlers.qsurl.parse", side_effect=_fake_parse):
        with pytest.raises(web.HTTPBadRequest):
            await h.query(_request())
    assert captured["source"] == "stores{a}?b=1"


async def test_q_with_nonbare_path_is_400():
    h = _handler()
    h.query_parameters = MagicMock(return_value={"q": "?b=1"})
    h.match_parameters = MagicMock(return_value={"path": "stores{a}"})
    h.json_data = AsyncMock(side_effect=TypeError)
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        await h.query(_request())
    body = json.loads(exc_info.value.text)
    assert body["detail"]["kind"] == "parse"


async def test_q_not_in_conditions():
    h = _handler()
    h.query_parameters = MagicMock(return_value={"q": "?x=1"})
    h.match_parameters = MagicMock(return_value={"path": "stores"})
    h.json_data = AsyncMock(side_effect=TypeError)
    h._enforce_slug_execute = AsyncMock()
    h.resolve_capabilities = AsyncMock(return_value=(BASE_CAPS, True))
    captured = {}

    async def _fake_get_source(_request, _slug, conditions, **_kwargs):
        captured["conditions"] = conditions
        return None  # short-circuits to "Unable to get Provider" Error

    h.get_source = _fake_get_source
    with patch("querysource.handlers.qsurl.parse", return_value=_ir(slug="stores")):
        with pytest.raises(web.HTTPBadRequest):
            await h.query(_request())
    assert "q" not in captured["conditions"]


async def test_unsupported_capability_is_400():
    h = _handler()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={"path": "s"})
    h.json_data = AsyncMock(side_effect=TypeError)
    h._enforce_slug_execute = AsyncMock()
    h.resolve_capabilities = AsyncMock(return_value=(BASE_CAPS, True))
    ir = _ir(
        filter={"and": [{"column": {"fn": "lower", "args": ["a"]}, "expression": "==", "value": "1"}]},
        requires=["select", "filter", "functions"],
    )
    with patch("querysource.handlers.qsurl.parse", return_value=ir):
        with pytest.raises(web.HTTPBadRequest) as exc_info:
            await h.query(_request())
    body = json.loads(exc_info.value.text)
    assert body["detail"]["kind"] == "unsupported"
    assert "functions" in body["detail"]["message"]


async def test_decodes_once_via_real_router(monkeypatch):
    """GET /api/v1/services/qsurl/s%3Fa%3D'x%2527' -> source == "s?a='x%27'" (decode-once)."""
    app = web.Application()
    handler = QSUrlService()
    app.router.add_get("/api/v1/services/qsurl/{path:.*}", handler.query)
    captured = {}

    def _fake_parse(source):
        captured["source"] = source
        raise QSUrlError("parse", "boom")

    monkeypatch.setattr("querysource.handlers.qsurl.parse", _fake_parse)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/api/v1/services/qsurl/s%3Fa%3D'x%2527'")
        assert resp.status == 400
    finally:
        await client.close()
    assert captured["source"] == "s?a='x%27'"
