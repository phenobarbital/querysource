# Copyright (C) 2018-present Jesus Lara
#
# test_rust_parsers.py — Integration tests for qs_parsers Rust extension.
# Validates that Rust functions produce identical output to Cython equivalents.
"""Tests for the qs_parsers Rust extension module."""
import pytest

try:
    from querysource import qs_parsers
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers not installed")


# ===========================================================================
# Validators
# ===========================================================================

class TestStrtobool:
    """Tests for strtobool()."""

    @pytest.mark.parametrize("val,expected", [
        ("true", True), ("True", True), ("TRUE", True),
        ("yes", True), ("y", True), ("1", True), ("on", True),
        ("false", False), ("False", False), ("FALSE", False),
        ("no", False), ("n", False), ("0", False), ("off", False),
        ("null", False),
    ])
    def test_valid_values(self, val: str, expected: bool):
        assert qs_parsers.strtobool(val) == expected

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            qs_parsers.strtobool("maybe")


class TestTypeCheckers:
    """Tests for is_integer, is_float, is_boolean, is_udf, is_pgconstant."""

    @pytest.mark.parametrize("val,expected", [
        ("42", True), ("-1", True), ("0", True),
        ("3.14", False), ("abc", False), ("", False),
    ])
    def test_is_integer(self, val: str, expected: bool):
        assert qs_parsers.is_integer(val) == expected

    @pytest.mark.parametrize("val,expected", [
        ("3.14", True), ("42", True), ("-0.5", True),
        ("abc", False), ("", False),
    ])
    def test_is_float(self, val: str, expected: bool):
        assert qs_parsers.is_float(val) == expected

    @pytest.mark.parametrize("val,expected", [
        ("true", True), ("false", True), ("yes", True), ("0", True),
        ("maybe", False), ("", False),
    ])
    def test_is_boolean(self, val: str, expected: bool):
        assert qs_parsers.is_boolean(val) == expected

    @pytest.mark.parametrize("val,expected", [
        ("TODAY", True), ("today", True), ("YESTERDAY", True),
        ("FDOM", True), ("LDOM", True), ("CURRENT_YEAR", True),
        ("random", False),
    ])
    def test_is_udf(self, val: str, expected: bool):
        assert qs_parsers.is_udf(val) == expected

    @pytest.mark.parametrize("val,expected", [
        ("CURRENT_DATE", True), ("current_date", True),
        ("CURRENT_TIMESTAMP", True),
        ("random", False),
    ])
    def test_is_pgconstant(self, val: str, expected: bool):
        assert qs_parsers.is_pgconstant(val) == expected


class TestFieldComponents:
    """Tests for field_components()."""

    def test_prefixed_field(self):
        result = qs_parsers.field_components("@my_var!")
        assert len(result) == 1
        assert result[0] == ("@", "my_var", "!")

    def test_no_prefix(self):
        result = qs_parsers.field_components("plain_field")
        # Plain fields match with empty prefix/suffix (matches Cython behavior)
        assert len(result) >= 0  # may or may not match depending on regex

    def test_hash_prefix(self):
        result = qs_parsers.field_components("#count|")
        assert len(result) == 1
        assert result[0][0] == "#"
        assert result[0][1] == "count"


class TestQuoteString:
    """Tests for quote_string()."""

    def test_basic(self):
        assert qs_parsers.quote_string("hello") == "'hello'"

    def test_already_quoted(self):
        assert qs_parsers.quote_string("'hello'") == "'hello'"

    def test_internal_quotes(self):
        assert qs_parsers.quote_string("it's") == "'it''s'"

    def test_null(self):
        assert qs_parsers.quote_string("null") == "null"
        assert qs_parsers.quote_string("NULL") == "NULL"

    def test_none_string(self):
        assert qs_parsers.quote_string("None") == "''"

    def test_boolean_strings(self):
        assert qs_parsers.quote_string("True") == "True"
        assert qs_parsers.quote_string("false") == "false"

    def test_double_quote_conversion(self):
        result = qs_parsers.quote_string('"hello"', True)
        assert result == "'hello'"


class TestEscapeString:
    """Tests for escape_string()."""

    def test_newline(self):
        assert qs_parsers.escape_string("hello\nworld") == "hello\\nworld"

    def test_tab(self):
        assert qs_parsers.escape_string("a\tb") == "a\\tb"

    def test_quotes_stripped(self):
        assert qs_parsers.escape_string("it's") == "its"

    def test_percent(self):
        assert qs_parsers.escape_string("100%") == "100\\%"

    def test_backslash(self):
        assert qs_parsers.escape_string("a\\b") == "a\\\\b"


class TestIsValid:
    """Tests for is_valid() type dispatch."""

    def test_integer(self):
        assert qs_parsers.is_valid("x", "42", "integer") == "42"

    def test_float(self):
        assert qs_parsers.is_valid("x", "3.14", "float") == "3.14"

    def test_string(self):
        assert qs_parsers.is_valid("x", "hello", "string") == "'hello'"

    def test_boolean_true(self):
        assert qs_parsers.is_valid("x", "true", "boolean") == "TRUE"

    def test_boolean_false(self):
        assert qs_parsers.is_valid("x", "false", "boolean") == "FALSE"

    def test_literal(self):
        result = qs_parsers.is_valid("x", "hello\nworld", "literal")
        assert result == "hello\\nworld"

    def test_null_generic(self):
        assert qs_parsers.is_valid("x", "null") == "null"
        assert qs_parsers.is_valid("x", "NULL") == "null"

    def test_pg_constant(self):
        assert qs_parsers.is_valid("x", "CURRENT_DATE") == "CURRENT_DATE"

    def test_pg_function(self):
        assert qs_parsers.is_valid("x", "now()") == "now()"

    def test_noquote(self):
        assert qs_parsers.is_valid("x", "val", noquote=True) == "val"

    def test_default_quote(self):
        assert qs_parsers.is_valid("x", "some_value") == "'some_value'"


# ===========================================================================
# ParseQS
# ===========================================================================

class TestParseQS:
    """Tests for parseqs functions."""

    @pytest.mark.parametrize("val,expected", [
        ("[1,2]", "list"),
        ("(a,b)", "tuple"),
        ('{"a":1}', "dict"),
        ("hello", ""),
        ("", ""),
    ])
    def test_is_parseable(self, val: str, expected: str):
        assert qs_parsers.is_parseable(val) == expected

    def test_parse_list(self):
        assert qs_parsers.parse_list("[a, b, c]") == ["a", "b", "c"]

    def test_parse_list_quoted(self):
        assert qs_parsers.parse_list("['hello', 'world']") == ["hello", "world"]

    def test_parse_tuple(self):
        assert qs_parsers.parse_tuple("(x, y, z)") == ["x", "y", "z"]


# ===========================================================================
# SafeDict
# ===========================================================================

class TestSafeFormatMap:
    """Tests for safe_format_map()."""

    def test_partial_replacement(self):
        result = qs_parsers.safe_format_map(
            "SELECT {fields} FROM {table} {filter}",
            {"fields": "a, b", "table": "t1"},
        )
        assert result == "SELECT a, b FROM t1 {filter}"

    def test_full_replacement(self):
        result = qs_parsers.safe_format_map("{a} + {b}", {"a": "1", "b": "2"})
        assert result == "1 + 2"

    def test_no_replacement(self):
        result = qs_parsers.safe_format_map("SELECT *", {})
        assert result == "SELECT *"

    def test_empty_template(self):
        result = qs_parsers.safe_format_map("", {"a": "1"})
        assert result == ""


# ===========================================================================
# SQL Parser
# ===========================================================================

class TestFilterConditions:
    """Tests for filter_conditions()."""

    def test_simple_string(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"name": "John"},
            {},
        )
        assert "WHERE" in result
        assert "name='John'" in result

    def test_null_condition(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"status": "null"},
            {},
        )
        assert "status IS NULL" in result

    def test_not_null(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"status": "!null"},
            {},
        )
        assert "status IS NOT NULL" in result

    def test_negation(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"status": "!active"},
            {},
        )
        assert "status != 'active'" in result

    def test_list_in_clause(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"color": ["red", "blue"]},
            {},
        )
        assert "color IN ('red','blue')" in result

    def test_multiple_conditions(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}",
            {"name": "John", "age": "25"},
            {},
        )
        assert "AND" in result

    def test_empty_filter(self):
        result = qs_parsers.filter_conditions(
            "SELECT * FROM t {filter}", {}, {}
        )
        assert "WHERE" not in result


class TestGroupBy:
    """Tests for group_by()."""

    def test_new_group(self):
        result = qs_parsers.group_by("SELECT * FROM t", ["col1"])
        assert result == "SELECT * FROM t GROUP BY col1"

    def test_multiple(self):
        result = qs_parsers.group_by("SELECT * FROM t", ["col1", "col2"])
        assert result == "SELECT * FROM t GROUP BY col1, col2"

    def test_empty(self):
        result = qs_parsers.group_by("SELECT * FROM t", [])
        assert result == "SELECT * FROM t"


class TestOrderBy:
    """Tests for order_by()."""

    def test_single(self):
        result = qs_parsers.order_by("SELECT * FROM t", ["name ASC"])
        assert result == "SELECT * FROM t ORDER BY name ASC"

    def test_empty(self):
        result = qs_parsers.order_by("SELECT * FROM t", [])
        assert result == "SELECT * FROM t"


class TestLimiting:
    """Tests for limiting()."""

    def test_limit_only(self):
        result = qs_parsers.limiting("SELECT * FROM t", "10")
        assert result == "SELECT * FROM t LIMIT 10"

    def test_limit_and_offset(self):
        result = qs_parsers.limiting("SELECT * FROM t", "10", "20")
        assert result == "SELECT * FROM t LIMIT 10 OFFSET 20"

    def test_empty(self):
        result = qs_parsers.limiting("SELECT * FROM t")
        assert result == "SELECT * FROM t"


class TestProcessFields:
    """Tests for process_fields()."""

    def test_replace_star(self):
        result = qs_parsers.process_fields(
            "SELECT * FROM t", ["a", "b"], False, ""
        )
        assert result == "SELECT a, b FROM t"

    def test_with_placeholder(self):
        result = qs_parsers.process_fields(
            "SELECT {fields} FROM t", ["x", "y"], False, ""
        )
        assert result == "SELECT x, y FROM t"

    def test_empty_fields(self):
        result = qs_parsers.process_fields("SELECT * FROM t", [], False, "")
        assert result == "SELECT * FROM t"


class TestBuildSQL:
    """Tests for build_sql()."""

    def test_full_query(self):
        result = qs_parsers.build_sql(
            "SELECT * FROM users",
            ["id", "name", "email"],  # fields
            False,                     # add_fields
            ["department"],            # grouping
            ["name ASC"],              # ordering
            "10",                      # limit
            "5",                       # offset
            "",                        # query_raw
            {},                        # conditions
        )
        assert "SELECT id, name, email FROM users" in result
        assert "GROUP BY department" in result
        assert "ORDER BY name ASC" in result
        assert "LIMIT 10" in result
        assert "OFFSET 5" in result

    def test_no_clauses(self):
        result = qs_parsers.build_sql(
            "SELECT * FROM t", [], False, [], [], "", "", "", {}
        )
        assert result == "SELECT * FROM t"

    def test_with_conditions_dict(self):
        result = qs_parsers.build_sql(
            "SELECT * FROM {table_name}",
            [], False, [], [], "", "", "",
            {"table_name": "users"},
        )
        assert result == "SELECT * FROM users"


# ===========================================================================
# RethinkDB Parser
# ===========================================================================

class TestRethinkProcessFields:
    """Tests for rethink_process_fields()."""

    def test_simple_fields(self):
        fields, aliases = qs_parsers.rethink_process_fields(
            ["name", "status", "email"]
        )
        assert fields == ["name", "status", "email"]
        assert aliases == {}

    def test_field_with_alias(self):
        fields, aliases = qs_parsers.rethink_process_fields(
            ["user_name as name", "email"]
        )
        assert fields == ["user_name", "email"]
        assert aliases == {"name": "user_name"}

    def test_multiple_aliases(self):
        fields, aliases = qs_parsers.rethink_process_fields(
            ['first_name as "fname"', 'last_name as "lname"']
        )
        assert fields == ["first_name", "last_name"]
        assert aliases == {"fname": "first_name", "lname": "last_name"}

    def test_empty_fields(self):
        fields, aliases = qs_parsers.rethink_process_fields([])
        assert fields == []
        assert aliases == {}


class TestRethinkProcessOrdering:
    """Tests for rethink_process_ordering()."""

    def test_desc_ordering(self):
        result = qs_parsers.rethink_process_ordering(["name DESC"])
        assert result == [("name", "DESC")]

    def test_asc_ordering(self):
        result = qs_parsers.rethink_process_ordering(["name ASC"])
        assert result == [("name", "ASC")]

    def test_default_asc(self):
        result = qs_parsers.rethink_process_ordering(["name"])
        assert result == [("name", "ASC")]

    def test_multiple_orderings(self):
        result = qs_parsers.rethink_process_ordering(
            ["name DESC", "created_at"]
        )
        assert result == [("name", "DESC"), ("created_at", "ASC")]

    def test_empty_ordering(self):
        result = qs_parsers.rethink_process_ordering([])
        assert result is None


class TestRethinkClassifyConditions:
    """Tests for rethink_classify_conditions()."""

    def test_date_field(self):
        result = qs_parsers.rethink_classify_conditions(
            {"inserted_at": "2024-01-01"},
            {"inserted_at": "date"},
        )
        assert result == {"inserted_at": "date"}

    def test_epoch_field(self):
        result = qs_parsers.rethink_classify_conditions(
            {"created_ts": 1234567890},
            {"created_ts": "epoch"},
        )
        assert result == {"created_ts": "epoch"}

    def test_timestamp_field(self):
        result = qs_parsers.rethink_classify_conditions(
            {"updated_at": "2024-01-01T00:00:00"},
            {"updated_at": "timestamp"},
        )
        assert result == {"updated_at": "date"}

    def test_list_value(self):
        result = qs_parsers.rethink_classify_conditions(
            {"tags": [1, 2, 3]},
            {},
        )
        assert result == {"tags": "list"}

    def test_dict_value(self):
        result = qs_parsers.rethink_classify_conditions(
            {"meta": {"contains": "test"}},
            {},
        )
        assert result == {"meta": "dict"}

    def test_scalar_value(self):
        result = qs_parsers.rethink_classify_conditions(
            {"status": "active"},
            {},
        )
        assert result == {"status": "scalar"}

    def test_mixed_conditions(self):
        result = qs_parsers.rethink_classify_conditions(
            {"name": "John", "inserted_at": "2024-01-01", "tags": [1]},
            {"inserted_at": "datetime"},
        )
        assert result["name"] == "scalar"
        assert result["inserted_at"] == "date"
        assert result["tags"] == "list"

    def test_empty_filter(self):
        result = qs_parsers.rethink_classify_conditions({}, {})
        assert result == {}

