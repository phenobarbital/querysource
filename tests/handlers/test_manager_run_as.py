"""Unit tests for QueryManager run-as actor wiring (FEAT-159 TASK-811)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from querysource.handlers import manager as manager_mod
from querysource.handlers.manager import QueryManager


def _make_manager() -> QueryManager:
    mgr = QueryManager.__new__(QueryManager)
    mgr._request = SimpleNamespace(method="PATCH", path="/x", remote="10.0.0.1")
    return mgr


async def test_manager_passes_session_actor():
    async def fake_session(request, new=False):
        return {"session": {"user_id": 42}}

    with patch.object(manager_mod, "get_session", fake_session):
        actor, info = await _make_manager()._run_as_context()
    assert actor == 42
    assert info == {"method": "PATCH", "path": "/x", "remote": "10.0.0.1"}


async def test_manager_without_session_passes_none():
    async def boom(request, new=False):
        raise RuntimeError("no session system")

    with patch.object(manager_mod, "get_session", boom):
        actor, info = await _make_manager()._run_as_context()
    assert actor is None
    assert info["path"] == "/x"


async def test_manager_session_none_gives_none():
    async def none_session(request, new=False):
        return None

    with patch.object(manager_mod, "get_session", none_session):
        actor, _ = await _make_manager()._run_as_context()
    assert actor is None
