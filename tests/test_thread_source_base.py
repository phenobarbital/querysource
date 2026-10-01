"""Unit tests for ThreadSource base class (TASK-644)."""
import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from querysource.exceptions import DataNotFound, DriverError
from querysource.interfaces import source_hooks
from querysource.interfaces.source_hooks import GuardedSQLError, SourceHooks, SourceHooksMixin
from querysource.queries.multi.sources.base import ThreadSource


class ConcreteSource(ThreadSource):
    """Test-only concrete implementation of ThreadSource."""

    async def fetch(self) -> pd.DataFrame:
        return pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})


class FailingSource(ThreadSource):
    """Test-only concrete implementation that raises an exception in fetch()."""

    async def fetch(self) -> pd.DataFrame:
        raise ValueError("test error")


class TestThreadSource:
    def test_run_puts_dataframe_in_queue(self):
        queue = asyncio.Queue()
        source = ConcreteSource("test", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        assert "test" in result
        assert isinstance(result["test"], pd.DataFrame)

    def test_dataframe_contents_correct(self):
        queue = asyncio.Queue()
        source = ConcreteSource("my_source", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        df = result["my_source"]
        assert list(df.columns) == ["col1", "col2"]
        assert len(df) == 2

    def test_exception_captured(self):
        queue = asyncio.Queue()
        source = FailingSource("test", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is not None
        assert "test error" in str(source.exc)

    def test_exception_does_not_put_to_queue(self):
        queue = asyncio.Queue()
        source = FailingSource("test", {}, None, queue)
        source.start()
        source.join()
        assert queue.empty()

    def test_resolve_credential_literal(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        assert source.resolve_credential("key", "literal_value") == "literal_value"

    def test_resolve_credential_lowercase_is_literal(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        assert source.resolve_credential("key", "not_an_env_var") == "not_an_env_var"

    def test_resolve_credential_env_var_returns_string(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        # SOME_VAR_NAME looks like an env var — navconfig may or may not have it.
        # Either way the result must be a string (resolved or literal fallback).
        result = source.resolve_credential("key", "SOME_VAR_NAME")
        assert isinstance(result, str)

    def test_resolve_credential_non_string_passthrough(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        # Non-string values should be returned as-is (they are not env var names).
        assert source.resolve_credential("key", 42) == 42  # type: ignore[arg-type]

    def test_inherits_from_thread(self):
        import threading

        assert issubclass(ConcreteSource, threading.Thread)

    def test_exc_is_none_on_success(self):
        queue = asyncio.Queue()
        source = ConcreteSource("test", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is None


class _RecordingSource(ThreadSource):
    """Records fetch() into a shared event list; outcome configurable."""

    def __init__(self, *args, events: list, fetch_exc: Exception | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._events = events
        self._fetch_exc = fetch_exc

    async def fetch(self) -> pd.DataFrame:
        self._events.append("fetch")
        if self._fetch_exc is not None:
            raise self._fetch_exc
        return pd.DataFrame({"a": [1]})


_HOOKS = SourceHooks(pre=("UPDATE t SET a = 1",), post=("INSERT INTO l VALUES (1)",))


def _run(source) -> None:
    source.start()
    source.join()


def _recording_exec(events: list, fail_on: str | None = None):
    async def _exec(statements, **_kw):
        kind = "pre" if statements == list(_HOOKS.pre) else "post"
        if kind == fail_on:
            raise GuardedSQLError("boom", category="infra")
        events.append(kind)
        return ["OK 1"]

    return AsyncMock(side_effect=_exec)


class TestThreadSourceHooks:
    def test_fetch_with_hooks_order(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events)
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", _recording_exec(events)):
            _run(src)
        assert src.exc is None
        assert events == ["pre", "fetch", "post"]

    def test_pre_hook_failure_skips_fetch(self):
        events: list = []
        queue: asyncio.Queue = asyncio.Queue()
        src = _RecordingSource("s", {}, None, queue, events=events)
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", _recording_exec(events, fail_on="pre")):
            _run(src)
        assert events == []
        assert isinstance(src.exc, GuardedSQLError)
        assert queue.empty()

    def test_post_hook_skipped_on_fetch_failure(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events, fetch_exc=DriverError("x"))
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", _recording_exec(events)) as ex:
            _run(src)
        assert ex.await_count == 1
        assert events == ["pre", "fetch"]
        assert isinstance(src.exc, DriverError)

    def test_post_hook_runs_on_empty_result(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events, fetch_exc=DataNotFound("empty"))
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", _recording_exec(events)):
            _run(src)
        assert events == ["pre", "fetch", "post"]
        assert isinstance(src.exc, DataNotFound)

    def test_post_hook_error_on_empty_result(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events, fetch_exc=DataNotFound("empty"))
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", _recording_exec(events, fail_on="post")):
            _run(src)
        assert isinstance(src.exc, GuardedSQLError)
        assert not isinstance(src.exc, DataNotFound)

    def test_no_hooks_unchanged(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events)
        with patch.object(source_hooks, "execute_guarded", AsyncMock()) as ex:
            _run(src)
        ex.assert_not_awaited()
        assert src.exc is None and events == ["fetch"]

    def test_mixin_mro(self):
        mro = ThreadSource.__mro__
        assert mro.index(SourceHooksMixin) < mro.index(threading.Thread)
