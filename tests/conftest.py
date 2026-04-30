"""Shared pytest fixtures for PBAC unit tests (TASK-641)."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def session_factory():
    """Factory: build a SessionData-like dict with given userinfo."""
    def _build(username="alice", groups=("analysts",), service=False, **extra):
        return {
            "username": username,
            "user_id": username,
            "groups": list(groups),
            "roles": [],
            "service": service,
            **extra,
        }
    return _build


@pytest.fixture
def mock_evaluator_allow():
    """PolicyEvaluator mock that always returns ALLOW."""
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=True, effect="ALLOW", matched_policy="P", reason=""))
    return ev


@pytest.fixture
def mock_evaluator_deny():
    """PolicyEvaluator mock that always returns DENY."""
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=False, effect="DENY", matched_policy="P", reason="denied"))
    return ev
