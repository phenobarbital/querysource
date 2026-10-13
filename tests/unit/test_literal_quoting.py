"""FEAT-167 TASK-876 — unquote_literal grammar and idempotent Entity.quoteString (AC4)."""
from __future__ import annotations

import pytest

from querysource.parsers.filter_values import unquote_literal as fv_unquote
from querysource.types.validators import Entity, is_valid, unquote_literal

RAW = ["O'Brien", "plain", "it''s", "'pre'", "50%", "a\\b", "", "x) OR (1=1", "Acme (US)"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("'O''Brien'", "O'Brien"),
        ("'abc'", "abc"),
        ("''", ""),
        ("'it''''s'", "it''s"),
        ("O'Brien", None),
        ("'a'b'", None),
        ("'", None),
        ("'abc", None),
        ("abc'", None),
        (None, None),
        (5, None),
    ],
)
def test_unquote_literal_grammar(value, expected) -> None:
    assert unquote_literal(value) == expected
    assert fv_unquote is unquote_literal


@pytest.mark.parametrize("raw", RAW)
def test_entity_quote_string_idempotent(raw: str) -> None:
    once = Entity.quoteString(raw)
    expected = "'pre'" if raw == "'pre'" else "'" + raw.replace("'", "''") + "'"
    assert once == expected
    assert Entity.quoteString(once) == once
    assert Entity.quoteString(Entity.quoteString(once)) == once


def test_entity_quote_string_special_cases_unchanged() -> None:
    assert Entity.quoteString("null") == "null"
    assert Entity.quoteString("None") == "''"
    assert Entity.quoteString(True) == "True"
    assert Entity.quoteString('"x"') == "'x'"


def test_typed_string_keeps_apostrophe() -> None:
    assert is_valid("n", "O'Brien", "string") == "'O''Brien'"
    assert is_valid("n", "O'Brien", "varchar") == "'O''Brien'"


def test_prequoted_then_builder_pass() -> None:
    """The is_valid pre-quote composed with the builder quote yields a single escape."""
    pre = is_valid("n", "O'Brien")
    assert pre == "'O''Brien'"
    assert Entity.quoteString(pre) == "'O''Brien'"
