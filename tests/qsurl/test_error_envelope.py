"""AbstractHandler.Error(detail=...) and DataOutput's QSUrlError pass-through (spec AC7)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import querysource.outputs.output as output_module
from querysource.handlers.abstract import AbstractHandler
from querysource.outputs.output import DataOutput
from querysource.qsurl import QSUrlError


def _handler(debug: bool) -> AbstractHandler:
    h = AbstractHandler.__new__(AbstractHandler)
    h.logger = MagicMock()
    h.debug = debug
    h._json = MagicMock()
    h._json.dumps = json.dumps
    return h


@pytest.mark.parametrize("debug", [False, True])
def test_error_with_detail_is_400_with_detail(debug):
    err = QSUrlError("parse", "boom", offset=2, pointer="ab\n  ^")
    resp = _handler(debug).Error(message=err.message, exception=err, code=400, detail=err.to_dict())
    body = json.loads(resp.text)
    assert resp.status == 400 and body["detail"] == err.to_dict() and body["error"] == "boom"


def test_error_without_detail_unchanged_in_production():
    resp = _handler(False).Error(message="raw db text", code=400)
    body = json.loads(resp.text)
    assert resp.status == 400
    assert "detail" not in body
    # No caller-asserted detail and debug=False -> the generic redacted message, not "raw db text".
    assert body["error"] != "raw db text"


class _StubWriter:
    """Minimal writer stub whose get_result() raises, per the WRITERS[format] contract."""

    def __init__(self, **_kwargs) -> None:
        pass

    async def get_result(self):
        raise QSUrlError("cost", "residual-only filter on a provider that forbids scans")


class _FakeRequest:
    """Minimal aiohttp-request-shaped stub for DataOutput.__init__."""

    def __init__(self) -> None:
        self.headers: dict = {}


async def test_dataoutput_reraises_qsurlerror(monkeypatch):
    monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)
    do = DataOutput(request=_FakeRequest(), query=[], ctype="json")
    with pytest.raises(QSUrlError):
        await do.response()
