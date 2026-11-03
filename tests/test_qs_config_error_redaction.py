"""FEAT-184 TASK-960 — user_filter_options validation detail stays out of the HTTP response."""

from __future__ import annotations

import logging

import pytest
from test_qs_user_filtering import _build_qs, _Provider, _request

from querysource.auth.user_filter import INVALID_OPTIONS_MESSAGE, parse_user_filter_options
from querysource.exceptions import ConfigError, QueryError

SESSION = {"user": {"user_id": "7", "username": "jdoe", "programs": ["restricted"]}}
BAD_VALUE = "leaky-column-value; DROP TABLE x"


@pytest.mark.parametrize(
    "raw",
    [
        {"rules": [{"column": BAD_VALUE, "attribute": "user_id"}]},
        {"rules": [{"column": "user_id", "attribute": "user_id"}], "unknown_key": BAD_VALUE},
    ],
)
def test_parse_raises_constant_message(raw: dict) -> None:
    with pytest.raises(ConfigError) as exc:
        parse_user_filter_options(raw)
    assert str(exc.value) == INVALID_OPTIONS_MESSAGE
    assert BAD_VALUE not in str(exc.value)
    assert "pydantic" not in str(exc.value).lower()
    assert exc.value.__cause__ is not None


def test_parse_logs_detail_at_error(caplog: pytest.LogCaptureFixture) -> None:
    raw = {"rules": [{"column": "user_id", "attribute": "user_id"}], "unknown_key": BAD_VALUE}
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConfigError):
            parse_user_filter_options(raw)
    assert "unknown_key" in caplog.text


@pytest.mark.asyncio
async def test_qs_config_error_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadConfigProvider(_Provider):
        def __init__(self, **kwargs):
            raise ConfigError(INVALID_OPTIONS_MESSAGE)

    qs = await _build_qs(monkeypatch, _request(SESSION), provider=BadConfigProvider)
    with pytest.raises(ConfigError) as exc:
        await qs.build_provider()
    assert not isinstance(exc.value, QueryError)
    assert str(exc.value) == INVALID_OPTIONS_MESSAGE
    assert "Cannot Initialize" not in str(exc.value)
    assert exc.value.code == 500


@pytest.mark.asyncio
async def test_qs_other_init_failures_still_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomProvider(_Provider):
        def __init__(self, **kwargs):
            raise RuntimeError("boom")

    qs = await _build_qs(monkeypatch, _request(SESSION), provider=BoomProvider)
    with pytest.raises(QueryError) as exc:
        await qs.build_provider()
    assert "Cannot Initialize the provider" in str(exc.value)
    assert exc.value.code == 500
