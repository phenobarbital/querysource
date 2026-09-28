"""FEAT-153: the ``having`` condition is extracted and never leaks into WHERE filters."""
from __future__ import annotations

import pytest

from querysource.parsers.abstract import AbstractParser
from querysource.providers.sql import sqlProvider


class _StubParser(AbstractParser):
    """Minimal concrete parser to exercise the option extractors."""

    async def build_query(self):  # pragma: no cover - not used here
        return self.query_raw


def _make(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")


@pytest.mark.asyncio
async def test_having_is_extracted_not_filtered():
    parser = _make(having={"graduates": {">": 5}}, group_by=["course"])
    await parser.set_options()
    assert parser.having == {"graduates": {">": 5}}
    assert "having" not in parser.filter


def test_having_is_a_parser_condition_key():
    assert sqlProvider._has_parser_conditions({"having": {"total": {">": 1}}})


@pytest.mark.asyncio
async def test_having_defaults_to_empty_dict():
    parser = _make()
    await parser.set_options()
    assert parser.having == {}


@pytest.mark.asyncio
async def test_having_none_becomes_empty_dict():
    parser = _make(having=None)
    await parser.set_options()
    assert parser.having == {}
    assert "having" not in parser.filter


@pytest.mark.asyncio
async def test_non_dict_having_is_kept_verbatim():
    parser = _make(having="x")
    await parser.set_options()
    assert parser.having == "x"
    assert "having" not in parser.filter
