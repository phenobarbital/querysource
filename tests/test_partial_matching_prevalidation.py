"""FEAT-180: pre-dispatch validation in AbstractParser._where_element (spec AC4, AC6, AC14)."""

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers.sql import SQLParser

SQL = "SELECT * FROM t {where_cond}"


def _parser() -> SQLParser:
    parser = SQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    return parser


async def test_where_element_passthrough_raw():
    parser = _parser()
    await parser.set_where({"n": {"startswith": "o'brien"}}, None)
    assert parser.filter == {"n": {"startswith": "o'brien"}}


@pytest.mark.parametrize("operator", ["contains", "icontains", "not_contains", "not_icontains"])
async def test_contains_operators_require_three_characters(operator: str):
    parser = _parser()
    with pytest.raises(ParserError, match="requires at least 3 characters"):
        await parser.set_where({"n": {operator: "ab"}}, None)


async def test_partial_match_operator_requires_string_operand():
    parser = _parser()
    with pytest.raises(ParserError, match="requires a string operand"):
        await parser.set_where({"n": {"startswith": 123}}, None)


async def test_partial_match_operator_cannot_be_combined_with_other_keys():
    parser = _parser()
    with pytest.raises(ParserError, match="one operator per field"):
        await parser.set_where({"n": {"startswith": "abc", ">=": "5"}}, None)


@pytest.mark.parametrize("operator", ["regex", "not_regex", "iregex", "not_iregex"])
async def test_regex_operators_are_rejected_by_default(operator: str):
    parser = _parser()
    with pytest.raises(ParserError, match="regex operators are not supported"):
        await parser.set_where({"n": {operator: "a+"}}, None)


async def test_comparison_operator_still_uses_existing_validation():
    parser = _parser()
    await parser.set_where({"n": {">=": "5"}}, None)
    assert parser.filter == {"n": {">=": "'5'"}}


async def test_uppercase_ilike_still_uses_existing_validation():
    parser = _parser()
    await parser.set_where({"city": {"ILIKE": "%san%"}}, None)
    assert parser.filter == {"city": {"ILIKE": "'%san%'"}}


def test_regex_filter_support_is_disabled_by_default():
    assert _parser().supports_regex_filter is False
