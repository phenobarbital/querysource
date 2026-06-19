"""Regression tests for AbstractParser._grouping_sync (malformed query-slug).

A query-slug whose ``grouping``/``group_by`` condition arrived as a *string*
(e.g. ``?grouping=store_id``) used to crash provider initialization with::

    TypeError: Expected list, got str
    AttributeError: 'NoneType' object has no attribute 'split'

Root cause: ``group1``/``group2`` were declared ``cdef list``; popping a string
value off the ``QueryObject`` removed the key and then failed to coerce into the
typed variable. The broken ``except TypeError`` fallback re-popped the
already-removed key, getting ``None`` (``QueryObject.pop`` returns ``None`` for
missing keys), then called ``.split`` on ``None``.

These tests lock in the ``_ordering_sync``-style handling (``cdef object`` +
``isinstance`` string-splitting).
"""
from __future__ import annotations

import pytest

from querysource.parsers.abstract import AbstractParser


class _StubParser(AbstractParser):
    """Minimal concrete parser to exercise the option extractors."""

    async def build_query(self):  # pragma: no cover - not used here
        return self.query_raw


def _make(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")


@pytest.mark.asyncio
async def test_grouping_as_plain_string():
    """A single bare string must become a one-element list, not crash."""
    parser = _make(grouping="store_id")
    await parser.set_options()
    assert parser.grouping == ["store_id"]


@pytest.mark.asyncio
async def test_grouping_as_comma_string():
    """A comma-separated string must be split and stripped."""
    parser = _make(grouping="store_id, client_id ,region")
    await parser.set_options()
    assert parser.grouping == ["store_id", "client_id", "region"]


@pytest.mark.asyncio
async def test_group_by_as_string():
    """The ``group_by`` alias must behave identically to ``grouping``."""
    parser = _make(group_by="store_id,client_id")
    await parser.set_options()
    assert parser.grouping == ["store_id", "client_id"]


@pytest.mark.asyncio
async def test_grouping_as_list_passthrough():
    """A list value must pass through unchanged."""
    parser = _make(grouping=["store_id", "client_id"])
    await parser.set_options()
    assert parser.grouping == ["store_id", "client_id"]


@pytest.mark.asyncio
async def test_grouping_absent_defaults_empty():
    """No grouping condition yields an empty list, not a crash."""
    parser = _make()
    await parser.set_options()
    assert parser.grouping == []


@pytest.mark.asyncio
async def test_group_by_and_grouping_combined():
    """Both aliases provided as strings are concatenated."""
    parser = _make(group_by="a,b", grouping="c")
    await parser.set_options()
    assert parser.grouping == ["a", "b", "c"]
