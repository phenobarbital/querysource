"""FEAT-157 / TASK-825: HTTP-level gate for source hooks."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from querysource.handlers.multi import QueryHandler


class _Stop(Exception):
    """Raised by the mocked preflight so query() stops right after the gate call."""


def _handler(payload: dict) -> QueryHandler:
    h = QueryHandler.__new__(QueryHandler)
    h.logger = MagicMock()
    h.query_parameters = MagicMock(return_value={})
    h.match_parameters = MagicMock(return_value={})
    h.json_data = AsyncMock(return_value=payload)
    h.format = MagicMock(return_value="application/json")
    h._preflight_multiquery = AsyncMock(side_effect=_Stop())
    return h


async def _write_access(payload: dict) -> bool:
    h = _handler(payload)
    with pytest.raises(_Stop):
        await h.query(MagicMock())
    return h._preflight_multiquery.await_args.kwargs["write_access"]


async def test_inline_hook_requires_pg_admin():
    payload = {"queries": {"q": {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a=1"}}}
    assert await _write_access(payload) is True
    payload = {"queries": {"q": {"query": "SELECT 1", "driver": "pg", "post-hook": "UPDATE t SET a=1"}}}
    assert await _write_access(payload) is True


async def test_no_hooks_no_write_access():
    payload = {"queries": {"q": {"query": "SELECT 1", "driver": "pg"}}}
    assert await _write_access(payload) is False
