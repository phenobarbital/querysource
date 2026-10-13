"""FEAT-167 TASK-877 — Rust quote_string/to_string are idempotent (AC4)."""

from __future__ import annotations

import pytest

from querysource.qs_parsers import _qs_parsers as rs

RAW = ["O'Brien", "plain", "it''s", "50%", "a\\b", "x) OR (1=1", "Acme (US)"]


@pytest.mark.parametrize("raw", RAW)
def test_rust_quote_string_idempotent(raw: str) -> None:
    """Rust quoting escapes raw values once and accepts its own output."""
    once = rs.quote_string(raw, True)
    assert once == "'" + raw.replace("'", "''") + "'"
    assert rs.quote_string(once, True) == once


def test_rust_to_string_keeps_apostrophe() -> None:
    """Typed Rust string conversion preserves apostrophes."""
    assert rs.to_string("O'Brien") == "'O''Brien'"
    assert rs.is_valid("n", "O'Brien", "string", False) == "'O''Brien'"


def test_rust_quote_string_special_cases_unchanged() -> None:
    """Existing sentinel and double-quote handling remains stable."""
    assert rs.quote_string("null", True) == "null"
    assert rs.quote_string("None", True) == "''"
    assert rs.quote_string("True", True) == "True"
    assert rs.quote_string('"hello"', True) == "'hello'"
