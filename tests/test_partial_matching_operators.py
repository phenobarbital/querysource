"""Tests for the partial-matching operator table and its shared helpers."""
import pytest

from querysource.exceptions import ParserError
from querysource.parsers.partial_matching import (
    PARTIAL_MATCH_OPERATORS,
    bq_like_literal,
    build_like_pattern,
    like_escape,
    like_escape_bang,
    mssql_like_literal,
    sql_like_literal,
    validate_partial_match,
    validate_partial_match_dict,
)

EXPECTED = [
    "like", "not_like", "ilike", "not_ilike", "startswith", "not_startswith",
    "istartswith", "not_istartswith", "endswith", "not_endswith", "iendswith",
    "not_iendswith", "contains", "not_contains", "icontains", "not_icontains",
    "regex", "not_regex", "iregex", "not_iregex",
]


def test_table_has_twenty_lowercase_names_in_order():
    """The Python table preserves the Rust parity order."""
    assert list(PARTIAL_MATCH_OPERATORS) == EXPECTED


@pytest.mark.parametrize("op", ["contains", "icontains", "not_contains", "not_icontains"])
@pytest.mark.parametrize("value", ["a", "ab"])
def test_contains_min_length(op, value):
    """Contains-family operators require three raw characters."""
    with pytest.raises(ParserError, match="requires at least 3 characters"):
        validate_partial_match("n", op, value, supports_regex=True)


def test_escape_helpers_cover_dialect_metacharacters():
    """LIKE escapers preserve literal matching semantics for each dialect family."""
    assert like_escape("a\\b%c_d") == "a\\\\b\\%c\\_d"
    assert like_escape_bang("a!%_[b") == "a!!!%!_![b"


def test_literal_helpers_quote_patterns_without_entity_side_effects():
    """Dedicated literal helpers retain LIKE escape characters."""
    assert sql_like_literal("o'b\\") == "'o''b\\\\'"
    assert mssql_like_literal("o'b\\") == "'o''b\\'"
    assert bq_like_literal('a"\\%') == '"a\\"\\\\%"'


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("startswith", "v\\%\\_" + "%"),
        ("endswith", "%" + "v\\%\\_"),
        ("contains", "%v\\%\\_%"),
        ("like", "v%_"),
        ("ilike", "v%_"),
        ("regex", "v%_"),
    ],
)
def test_build_like_pattern_uses_table_escape_ownership(op, expected):
    """Only raw-value families are escaped before their configured wildcards."""
    assert build_like_pattern(PARTIAL_MATCH_OPERATORS[op], "v%_", escaper=like_escape) == expected


@pytest.mark.parametrize("value", [1, None, [], {}])
def test_validator_rejects_non_string_operands(value):
    """Every table entry requires a string operand."""
    with pytest.raises(ParserError, match="startswith on 'name' requires a string operand"):
        validate_partial_match("name", "startswith", value, supports_regex=True)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "empty regex pattern"),
        ("a" * 201, "regex pattern too long \(201 > 200 chars\)"),
        ("(a+)+", "regex pattern has a nested quantifier"),
    ],
)
def test_regex_validator_applies_safety_bounds(value, message):
    """Regex input observes the shared residual safety policy."""
    with pytest.raises(ParserError, match=message):
        validate_partial_match("name", "regex", value, supports_regex=True)


def test_regex_validator_rejects_unsupported_parser_before_pattern_checks():
    """Unsupported parsers reject all regex operators first."""
    with pytest.raises(ParserError, match="regex operators are not supported by this query parser"):
        validate_partial_match("name", "regex", "", supports_regex=False)


def test_validator_allows_valid_contains_and_short_startswith():
    """The min-length guard applies only to the contains family."""
    assert validate_partial_match("name", "contains", "abc", supports_regex=True).name == "contains"
    assert validate_partial_match("name", "startswith", "a", supports_regex=True).name == "startswith"


@pytest.mark.parametrize("value", [{">=": 1}, {"@>": {"kind": "book"}}])
def test_dict_validator_ignores_non_table_operators(value):
    """Existing dict operators continue through their current parser path."""
    assert validate_partial_match_dict("name", value, supports_regex=True) is None


@pytest.mark.parametrize(
    "value",
    [{"startswith": "a", "endswith": "z"}, {"contains": "abc", ">=": 1}],
)
def test_dict_validator_rejects_table_operator_with_other_keys(value):
    """Partial-matching dicts must contain exactly one operator."""
    with pytest.raises(
        ParserError,
        match="one operator per field: 'name' combines a partial-matching operator with other keys",
    ):
        validate_partial_match_dict("name", value, supports_regex=True)
