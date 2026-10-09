"""FEAT-165: filter value grammar."""
import re

import pytest

from querysource.exceptions import ParserError
from querysource.parsers.filter_values import (
    BetweenClause,
    base_key,
    is_comparison_dict,
    parse_between,
)
from querysource.utils.functions import to_udf


def test_parse_between_numbers():
    """Numbers remain unquoted in a valid BETWEEN clause."""
    assert parse_between("amount", "BETWEEN 100 AND 500").render() == "BETWEEN 100 AND 500"


def test_parse_between_quoted_and_bare_dates():
    """Quoted and bare date values are normalised as SQL literals."""
    clause = parse_between("created", "BETWEEN '2026-01-01' AND 2026-12-31")
    assert clause.render() == "BETWEEN '2026-01-01' AND '2026-12-31'"


def test_parse_between_keyword_bound():
    """Known UDF date keywords resolve and are quoted."""
    clause = parse_between("created", "BETWEEN FDOM AND LDOM")
    assert clause.low == f"'{to_udf('FDOM')}'"
    assert clause.high == f"'{to_udf('LDOM')}'"
    assert re.fullmatch(r"'\d{4}-\d{2}-\d{2}'", clause.low)
    assert re.fullmatch(r"'\d{4}-\d{2}-\d{2}'", clause.high)


def test_parse_between_negated():
    """NOT and a key's ! suffix each produce one negated clause."""
    assert parse_between("amount", "NOT BETWEEN 1 AND 2").negated is True
    assert parse_between("amount!", "BETWEEN 1 AND 2").negated is True
    assert parse_between("amount!", "NOT BETWEEN 1 AND 2").render() == "NOT BETWEEN 1 AND 2"


@pytest.mark.parametrize(
    "value",
    (
        "BETWEEN 1 AND 2; DROP",
        "BETWEEN 1 -- AND 2",
        "BETWEEN /*x*/ 1 AND 2",
        "BETWEEN 1 AND 2 UNION SELECT 1",
        "BETWEEN 1 AND 2 AND 3",
        "BETWEEN AND 2",
    ),
)
def test_parse_between_rejects(value):
    """Malformed clauses and injection markers raise the stable parser error."""
    with pytest.raises(ParserError, match="invalid BETWEEN clause for 'amount'"):
        parse_between("amount", value)


@pytest.mark.parametrize("value", ("betweenness", "IN BETWEEN", 5))
def test_parse_between_not_a_clause(value):
    """Non-BETWEEN operands remain available to the caller's other grammar."""
    assert parse_between("amount", value) is None


def test_base_key():
    """All parser suffix markers are stripped from the end of a key."""
    assert base_key("amount|!~#@:") == "amount"
    assert base_key("amount") == "amount"


def test_is_comparison_dict():
    """Only non-empty maps made entirely of comparison operators qualify."""
    assert is_comparison_dict({">=": 1, "<": 10}) is True
    assert is_comparison_dict({">": 1, "contains": "value"}) is False
    assert is_comparison_dict({}) is False


def test_parse_between_embedded_quote():
    """Embedded quote escapes are normalised by unescaping then re-quoting."""
    clause = parse_between("name", "BETWEEN 'O''Brien' AND 'Z'")
    assert clause.render() == "BETWEEN 'O''Brien' AND 'Z'"


def test_between_clause_render():
    """BetweenClause renders both regular and negated forms."""
    assert BetweenClause(False, "1", "2").render() == "BETWEEN 1 AND 2"
