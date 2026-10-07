"""FEAT-180: case-insensitive qsurl tokens parse identically on both back-ends."""
from __future__ import annotations

import pytest

from querysource.qsurl import HAS_RUST, _fallback, parse, to_gbnf

CASES = [
    ("s?name^=*'an'", "istartswith", "text_match"),
    ("s?city~*'san'", "icontains", "text_match"),
    ("s?code$=*'x'", "iendswith", "text_match"),
    ("s?n!~*'yy'", "not_icontains", "text_match"),
    ("s?r=~*'^a'", "iregex", "regex"),
]


def _leaf(ir: dict) -> dict:
    """Return the single filter leaf from a parsed case."""
    return ir["filter"]["and"][0] if "and" in ir["filter"] else ir["filter"]


@pytest.mark.parametrize(("url", "expr", "cap"), CASES)
def test_fallback_tokens(url: str, expr: str, cap: str) -> None:
    """The fallback lowers every case-insensitive token to its canonical expression."""
    ir = _fallback.parse(url)
    assert _leaf(ir)["expression"] == expr
    assert cap in ir["requires"]


@pytest.mark.skipif(not HAS_RUST, reason="qsurl Rust extension not installed")
@pytest.mark.parametrize(("url", "expr", "cap"), CASES)
def test_rust_tokens(url: str, expr: str, cap: str) -> None:
    """The Rust parser exposes the same canonical expressions and capabilities."""
    ir = parse(url)
    assert _leaf(ir)["expression"] == expr
    assert cap in ir["requires"]


@pytest.mark.parametrize(
    ("url", "expr"),
    [
        ("s?name~'san'", "contains"),
        ("s?name!~'san'", "not_contains"),
        ("s?name^='san'", "startswith"),
        ("s?name$='san'", "endswith"),
        ("s?name=~'^san'", "regex"),
    ],
)
def test_legacy_tokens_keep_their_expressions(url: str, expr: str) -> None:
    """Existing token spellings retain their canonical IR expression names."""
    assert _leaf(_fallback.parse(url))["expression"] == expr


def test_gbnf_contains_case_insensitive_tokens() -> None:
    """The grammar-derived GBNF exposes every new token spelling."""
    gbnf = to_gbnf()
    for token in ("!~*", "^=*", "$=*", "=~*", "~*"):
        assert f'"{token}"' in gbnf
