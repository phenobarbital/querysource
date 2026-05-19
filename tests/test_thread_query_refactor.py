"""Backward compatibility tests for refactored ThreadQuery (TASK-646)."""
import asyncio

import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.query import ThreadQuery


class TestThreadQueryRefactor:
    def test_inherits_thread_source(self):
        assert issubclass(ThreadQuery, ThreadSource)

    def test_has_fetch_method(self):
        assert hasattr(ThreadQuery, 'fetch')

    def test_has_slug_property(self):
        # slug must be a property on ThreadQuery
        assert isinstance(ThreadQuery.__dict__.get('slug'), property)

    def test_slug_from_dict_before_fetch(self):
        """Before fetch(), slug should read from the dict."""
        queue = asyncio.Queue()
        q = ThreadQuery("myname", {"slug": "my-slug"}, None, queue)
        assert q.slug == "my-slug"

    def test_slug_fallback_to_name(self):
        """If no slug key in dict, fallback to _name."""
        queue = asyncio.Queue()
        q = ThreadQuery("fallback_name", {}, None, queue)
        assert q.slug == "fallback_name"

    def test_constructor_signature_preserved(self):
        """ThreadQuery(name, query, request, queue) must still work."""
        queue = asyncio.Queue()
        q = ThreadQuery("test", {"slug": "abc"}, None, queue)
        assert q._name == "test"
        assert isinstance(q._query, dict)

    def test_run_not_overridden(self):
        """run() should come from ThreadSource, not ThreadQuery."""
        assert 'run' not in ThreadQuery.__dict__
