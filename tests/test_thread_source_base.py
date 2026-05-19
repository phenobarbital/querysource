"""Unit tests for ThreadSource base class (TASK-644)."""
import asyncio

import pandas as pd
import pytest

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
