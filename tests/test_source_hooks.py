"""Unit tests for querysource.interfaces.source_hooks (FEAT-157, TASK-823)."""
from unittest.mock import AsyncMock, patch

import pytest

from querysource.interfaces import source_hooks
from querysource.interfaces.source_hooks import (
    HOOK_KEYS,
    GuardedSQLError,
    SourceHooks,
    SourceHooksMixin,
    build_hooks,
    pop_hooks,
)


class _Holder(SourceHooksMixin):
    """Bare mixin host (no __init__ of its own)."""


def test_pop_hooks_mutates_entry():
    entry = {"slug": "s", "pre-hook": "UPDATE t SET a = 1", "post-hook": ["INSERT INTO l VALUES (1)"], "x": 1}
    pre, post = pop_hooks(entry)
    assert pre == "UPDATE t SET a = 1"
    assert post == ["INSERT INTO l VALUES (1)"]
    assert entry == {"slug": "s", "x": 1}
    assert HOOK_KEYS == ("pre-hook", "post-hook")


def test_build_hooks_none_when_absent():
    with patch.object(source_hooks, "guard_statements") as guard:
        assert build_hooks(None, None) is None
    guard.assert_not_called()


def test_build_hooks_guarded():
    err = GuardedSQLError("statement 1: drop is not allowed", category="data")
    with patch.object(source_hooks, "guard_statements", side_effect=err):
        with pytest.raises(GuardedSQLError):
            build_hooks("DROP TABLE x", None)
        with pytest.raises(GuardedSQLError):
            build_hooks(None, "DROP TABLE x")

    with patch.object(source_hooks, "guard_statements", return_value=["A", "B"]) as guard:
        hooks = build_hooks(["A; B", "B"], None)
    assert hooks == SourceHooks(pre=("A", "B"), post=())
    guard.assert_called_once_with(["A; B", "B"])

    with patch.object(source_hooks, "guard_statements", return_value=["C"]):
        hooks = build_hooks(None, "C")
    assert hooks.pre == () and hooks.post == ("C",)


def test_build_hooks_fails_closed_when_post_rejected():
    def _guard(sql):
        if sql == "BAD":
            raise GuardedSQLError("blocked", category="data")
        return [sql]

    with patch.object(source_hooks, "guard_statements", side_effect=_guard):
        with pytest.raises(GuardedSQLError):
            build_hooks("OK", "BAD")


async def test_mixin_runs_execute_guarded():
    holder = _Holder()
    assert holder.has_hooks is False
    with patch.object(source_hooks, "execute_guarded", AsyncMock(return_value=["UPDATE 3"])) as ex:
        assert await holder.run_pre_hook() == []
        assert await holder.run_post_hook() == []
        ex.assert_not_awaited()
        holder.set_hooks(SourceHooks(pre=("UPDATE t SET a = 1",), post=("INSERT INTO l VALUES (1)",)))
        assert holder.has_hooks is True
        assert await holder.run_pre_hook() == ["UPDATE 3"]
        ex.assert_awaited_with(["UPDATE t SET a = 1"])
        assert await holder.run_post_hook() == ["UPDATE 3"]
        ex.assert_awaited_with(["INSERT INTO l VALUES (1)"])
        holder.set_hooks(None)
        assert holder.has_hooks is False


def test_mixin_defaults_are_class_level():
    assert SourceHooksMixin.sql_hooks_dialect is None
    assert SourceHooksMixin._source_hooks is None
    assert "__init__" not in SourceHooksMixin.__dict__
