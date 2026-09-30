"""Tests for caller-loop source preparation and delegated identity errors."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.auth.identity_tokens import DelegatedIdentityError
from querysource.handlers.multi import QueryHandler
from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources.base import ThreadSource


class _Rec(ThreadSource):
    events: list = []

    async def prepare(self, context):
        self.events.append(("prepare", self._name, context))

    def start(self):
        self.events.append(("start", self._name, None))
        super().start()

    async def fetch(self):
        return None


class _FailingRec(_Rec):
    async def prepare(self, context):
        raise DelegatedIdentityError(
            "No linked identity",
            provider="onedrive",
            user_id=42,
            reason="not_linked",
        )


@pytest.mark.asyncio
async def test_prepare_called_before_start():
    """Every source is prepared before the first source thread starts."""
    _Rec.events = []
    context = object()
    query = {
        "sources": [
            {"_Rec": {"value": 1}},
            {"_Rec": {"value": 2}},
        ]
    }

    with patch.dict("querysource.queries.multi.sources.SOURCE_REGISTRY", {"_Rec": _Rec}, clear=True):
        await MultiQS(query=query, identity_context=context).query()

    assert [event[0] for event in _Rec.events] == ["prepare", "prepare", "start", "start"]
    assert [event[2] for event in _Rec.events[:2]] == [context, context]


@pytest.mark.asyncio
async def test_failing_prepare_starts_no_thread():
    """A failed preparation aborts dispatch before any source starts."""
    _Rec.events = []
    query = {"sources": [{"_FailingRec": {}}, {"_Rec": {}}]}

    with patch.dict(
        "querysource.queries.multi.sources.SOURCE_REGISTRY",
        {"_FailingRec": _FailingRec, "_Rec": _Rec},
        clear=True,
    ):
        with pytest.raises(DelegatedIdentityError):
            await MultiQS(query=query).query()

    assert not any(event[0] == "start" for event in _Rec.events)


@pytest.mark.asyncio
async def test_multi_handler_maps_delegated_error_409(monkeypatch):
    """The multi handler exposes the identity-link response as HTTP 409."""
    error = DelegatedIdentityError(
        "No linked identity",
        provider="onedrive",
        user_id=42,
        reason="not_linked",
    )

    class _FailingMultiQS:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def query(self):
            raise error

    import querysource.handlers.multi as multi_handler

    monkeypatch.setattr(multi_handler, "MultiQS", _FailingMultiQS)
    handler = QueryHandler()
    handler.query_parameters = MagicMock(return_value={})
    handler.match_parameters = MagicMock(return_value={})
    handler.json_data = AsyncMock(return_value={"sources": [{"OneDriveSource": {}}]})
    handler.format = MagicMock(return_value="json")
    handler._preflight_multiquery = AsyncMock()
    handler._preflight_multiquery_owned = AsyncMock()
    handler.Error = MagicMock(side_effect=RuntimeError("mapped"))

    request = MagicMock()
    request.headers = {}
    request.app.get.return_value = object()
    request.get.return_value = None

    with pytest.raises(RuntimeError, match="mapped"):
        await handler.query(request)

    handler.Error.assert_called_once()
    kwargs = handler.Error.call_args.kwargs
    assert kwargs["code"] == 409
    assert kwargs["detail"]["link"] == "/api/v1/user/identities/link/onedrive"
