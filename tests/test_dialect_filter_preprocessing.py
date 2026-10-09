"""FEAT-165: request pre-processing through set_options() (not builder-only)."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import QS_VARIABLES
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM public.t {where_cond}"


class _Connection:
    """Minimal async context manager for parser preprocessing tests."""

    async def __aenter__(self) -> None:
        """Return the no-op connection."""
        return None

    async def __aexit__(self, *args: object) -> None:
        """Close the no-op connection."""
        return None


class _Redis:
    """Minimal Redis facade that supplies the no-op test connection."""

    async def connection(self) -> _Connection:
        """Return a no-op asynchronous connection context manager."""
        return _Connection()


async def preprocess(conditions: dict, query: str = SQL) -> pgSQLParser:
    """Run the real pre-processing pipeline and return the parser."""
    parser = pgSQLParser(definition=None, conditions=QueryObject(**conditions), query=query)
    parser._redis = _Redis()
    await parser.set_options()
    return parser


async def test_between_numeric_is_normalised():
    """Numeric BETWEEN bounds remain numeric through request preprocessing."""
    parser = await preprocess({"filter": {"amount": "BETWEEN 100 AND 500"}})
    assert parser.filter == {"amount": "BETWEEN 100 AND 500"}
    assert "(amount BETWEEN 100 AND 500)" in await parser.build_query()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BETWEEN 2025-01-01 AND 2025-12-31", "BETWEEN '2025-01-01' AND '2025-12-31'"),
        ("BETWEEN '2025-01-01' AND 2025-12-31", "BETWEEN '2025-01-01' AND '2025-12-31'"),
    ],
)
async def test_between_dates_are_quoted(value: str, expected: str):
    """Bare and quoted date BETWEEN bounds use canonical quoted literals."""
    parser = await preprocess({"filter": {"created_at": value}})
    assert parser.filter == {"created_at": expected}


async def test_not_between_key_suffix_is_normalised():
    """A negated key suffix becomes a base-key NOT BETWEEN clause."""
    parser = await preprocess({"filter": {"amount!": "BETWEEN 100 AND 500"}})
    assert parser.filter == {"amount": "NOT BETWEEN 100 AND 500"}


async def test_malformed_between_raises_parser_error():
    """A BETWEEN-looking malformed value is rejected before builder rendering."""
    with pytest.raises(ParserError, match="invalid BETWEEN clause"):
        await preprocess({"filter": {"amount": "BETWEEN 100 OR 500"}})


async def test_between_key_collision_raises_parser_error():
    """Two normalized BETWEEN filters cannot silently overwrite one another."""
    with pytest.raises(ParserError, match="conflicting filters for 'amount'"):
        await preprocess({"filter": {"amount": "BETWEEN 1 AND 2", "amount!": "BETWEEN 3 AND 4"}})


async def test_plain_string_containing_between_stays_equality():
    """A non-leading BETWEEN word is a regular quoted equality operand."""
    parser = await preprocess({"filter": {"note": "IN BETWEEN"}})
    assert parser.filter == {"note": "'IN BETWEEN'"}


async def test_implicit_containment_dict_is_preserved():
    """A JSONB payload retains every member without preprocessing quotes."""
    value = {"status": "active", "tier": "gold"}
    parser = await preprocess({"filter": {"metadata": value}})
    assert parser.filter == {"metadata": value}


async def test_comparison_dict_keeps_every_operator():
    """Comparison operands are normalized without dropping earlier operators."""
    parser = await preprocess({"filter": {"amount": {">": 1, "<": 9}}})
    assert parser.filter == {"amount": {">": 1, "<": 9}}


@pytest.mark.parametrize("value", [{"@>": {"status": "active"}}, {"@>|": [{"tier": "gold"}]}])
async def test_explicit_jsonb_dicts_are_preserved(value: dict):
    """Explicit JSONB operators remain entirely owned by the builders."""
    parser = await preprocess({"filter": {"metadata": value}})
    assert parser.filter == {"metadata": value}


async def test_partial_match_dict_is_preserved():
    """Validated partial-match filters retain their raw builder-facing shape."""
    value = {"startswith": "andre"}
    parser = await preprocess({"filter": {"name": value}})
    assert parser.filter == {"name": value}


async def test_unknown_variable_raises_parser_error():
    """An unregistered @variable is a client error rather than a dropped filter."""
    with pytest.raises(ParserError, match="unknown variable '@nope'"):
        await preprocess({"filter": {"created_at": "@nope"}})


async def test_registered_variable_resolves(monkeypatch: pytest.MonkeyPatch):
    """Registered @variables retain their established replacement behavior."""
    monkeypatch.setitem(QS_VARIABLES, "qs_test_var", lambda key, value: "2025-01-01")
    parser = await preprocess({"filter": {"created_at": "@qs_test_var"}})
    assert parser.filter == {}
    assert parser._conditions == {"created_at": "2025-01-01"}


async def test_callers_filter_dict_is_unchanged():
    """Preprocessing copies dictionaries rather than mutating caller-owned data."""
    filter_value = {"amount": {">": 1, "<": 9}}
    expected = {"amount": {">": 1, "<": 9}}
    await preprocess({"filter": filter_value})
    assert filter_value == expected
