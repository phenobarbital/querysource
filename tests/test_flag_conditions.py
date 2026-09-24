"""FEAT-149 — flag-condition coercion for ``refresh`` and ``paged`` (TASK-747)."""
from __future__ import annotations

import logging

import pytest

import querysource.types
from querysource.parsers.abstract import AbstractParser
from querysource.providers.abstract import BaseProvider
from querysource.types import to_flag

TRUTHY = [True, 1, "", "  ", "true", "TRUE", " yes ", "on", "t", "y", "1"]
FALSY = [False, None, 0, "false", "False", "no", "off", "f", "n", "0", "null"]
UNRECOGNIZED = ["maybe", "2", 2, -1, 1.0, 0.0, b"true", bytearray(b"1"), [], {}]


class _Provider(BaseProvider):
    """Minimal concrete provider with no parser."""

    async def query(self):  # pragma: no cover - not used here
        return None


class _StubParser(AbstractParser):
    """Minimal concrete parser to exercise the option extractors."""

    async def build_query(self):  # pragma: no cover - not used here
        return self.query_raw


def _make_parser(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")


@pytest.mark.parametrize("value", TRUTHY)
def test_to_flag_truthy(value):
    assert to_flag(value) is True


@pytest.mark.parametrize("value", FALSY)
def test_to_flag_falsy(value):
    assert to_flag(value) is False


@pytest.mark.parametrize("value", UNRECOGNIZED)
def test_to_flag_unrecognized_raises(value):
    with pytest.raises(ValueError):
        to_flag(value)


def test_to_flag_exported():
    assert "to_flag" in querysource.types.__all__


async def test_provider_refresh_false_string():
    provider = _Provider(conditions={"refresh": "false"})
    assert provider.refresh() is False
    assert "refresh" not in provider._conditions


@pytest.mark.parametrize("value,expected", [*[(v, True) for v in TRUTHY], *[(v, False) for v in FALSY]])
async def test_provider_refresh_values(value, expected):
    provider = _Provider(conditions={"refresh": value, "x": 1})
    assert provider.refresh() is expected
    assert "refresh" not in provider._conditions


async def test_provider_refresh_unrecognized_warns(caplog):
    with caplog.at_level(logging.WARNING):
        provider = _Provider(conditions={"refresh": "maybe", "x": 1})
    assert provider.refresh() is False
    assert any(
        "refresh" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


async def test_provider_no_refresh_default():
    provider = _Provider(conditions={"x": 1})
    assert provider.refresh() is False


@pytest.mark.parametrize("value", [*TRUTHY, *FALSY, "maybe"])
async def test_parser_refresh_parity(value):
    parser = _make_parser(refresh=value)
    await parser.set_options()
    provider = _Provider(conditions={"refresh": value, "x": 1})
    assert parser.refresh == provider.refresh()


async def test_parser_paged_unrecognized_no_raise(caplog):
    parser = _make_parser(paged="maybe", page=3)
    with caplog.at_level(logging.WARNING):
        await parser.set_options()
    assert "page" not in dict(parser.conditions)
    assert any(
        "paged" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


async def test_parser_paged_empty_is_true():
    parser = _make_parser(paged="", page=3)
    await parser.set_options()
