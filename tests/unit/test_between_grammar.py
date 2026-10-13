"""FEAT-168: canonical BETWEEN grammar shared by the builders."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.parsers.filter_values import has_between_prefix, is_canonical_between, parse_between

ACCEPT = ["BETWEEN 1 AND 2", "NOT BETWEEN -1.5 AND 3", "BETWEEN '2025-01-01' AND 'O''Brien'"]
REJECT = [
    "BETWEEN 1 AND 2 OR 1=1",
    "BETWEEN 1 AND 2;",
    "between 1 and 2",
    "BETWEEN  1 AND 2",
    "BETWEEN 'a\\' AND 'b'",
    "BETWEEN x AND y",
    " BETWEEN 1 AND 2",
    "BETWEEN 'a' AND 'b' --",
    "BETWEEN 'unterminated AND 2",
    None,
    5,
]
# Inputs the pre-processing accepts; every rendered clause must pass the builder check.
PARSE_CORPUS = [
    ("amount", "BETWEEN 100 AND 500"),
    ("amount", "between -1.5 and 3"),
    ("created_at", "BETWEEN '2025-01-01' AND 2025-12-31"),
    ("created_at", "NOT BETWEEN '2025-01-01' AND '2025-12-31'"),
    ("amount!", "BETWEEN 1 AND 5"),
    ("name", "BETWEEN 'O''Brien' AND 'Smith'"),
    ("ts", "BETWEEN 2025-01-01T00:00:00 AND 2025-01-02T00:00:00"),
]


@pytest.mark.parametrize("value", ACCEPT)
def test_is_canonical_between_accepts(value: str) -> None:
    """The canonical rendered forms are accepted."""
    assert is_canonical_between(value)


@pytest.mark.parametrize("value", REJECT)
def test_is_canonical_between_rejects(value: object) -> None:
    """Anything outside the exact rendered grammar is refused."""
    assert not is_canonical_between(value)


@pytest.mark.parametrize(("key", "value"), PARSE_CORPUS)
def test_render_is_canonical(key: str, value: str) -> None:
    """``BetweenClause.render()`` output always satisfies the builder check."""
    clause = parse_between(key, value)
    assert clause is not None
    assert is_canonical_between(clause.render())


@pytest.mark.parametrize("value", ["BETWEEN 'a\\' AND 'b'", "BETWEEN a\\b AND c"])
def test_backslash_bound_rejected(value: str) -> None:
    """Pre-processing refuses backslashes, so it never emits a clause builders drop."""
    with pytest.raises(ParserError):
        parse_between("d", value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BETWEEN 1 AND 2", True),
        ("NOT BETWEEN 1 AND 2", True),
        ("BETWEEN 1 AND 2 OR 1=1", True),
        ("IN BETWEEN", False),
        ("note BETWEEN", False),
        (None, False),
        (12, False),
    ],
)
def test_has_between_prefix(value: object, expected: bool) -> None:
    """The prefix test identifies BETWEEN attempts, not valid clauses."""
    assert has_between_prefix(value) is expected
