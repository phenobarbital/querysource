# Copyright (C) 2018-present Jesus Lara
#
# test_elastic_parser.py — Integration tests for Elasticsearch parser functions.
"""Tests for the Elasticsearch parser in qs_parsers Rust extension."""
import pytest

try:
    import qs_parsers
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers not installed")


# ===========================================================================
# es_process_fields
# ===========================================================================

class TestEsProcessFields:
    """Tests for es_process_fields()."""

    def test_simple_fields(self):
        result = qs_parsers.es_process_fields(["name", "email", "status"])
        assert result == ["name", "email", "status"]

    def test_empty_fields(self):
        result = qs_parsers.es_process_fields([])
        assert result is None

    def test_single_field(self):
        result = qs_parsers.es_process_fields(["id"])
        assert result == ["id"]


# ===========================================================================
# es_process_ordering
# ===========================================================================

class TestEsProcessOrdering:
    """Tests for es_process_ordering()."""

    def test_asc_ordering(self):
        result = qs_parsers.es_process_ordering(["name"])
        assert result == [{"name": "asc"}]

    def test_desc_ordering(self):
        result = qs_parsers.es_process_ordering(["-created_at"])
        assert result == [{"created_at": "desc"}]

    def test_mixed_ordering(self):
        result = qs_parsers.es_process_ordering(["name", "-created_at"])
        assert result == [{"name": "asc"}, {"created_at": "desc"}]

    def test_empty_ordering(self):
        result = qs_parsers.es_process_ordering([])
        assert result is None

    def test_multiple_desc(self):
        result = qs_parsers.es_process_ordering(["-a", "-b", "-c"])
        assert result == [{"a": "desc"}, {"b": "desc"}, {"c": "desc"}]


# ===========================================================================
# es_filter_conditions
# ===========================================================================

class TestEsFilterConditions:
    """Tests for es_filter_conditions()."""

    def test_empty_filter(self):
        result = qs_parsers.es_filter_conditions({}, {})
        assert result == {}

    def test_simple_term(self):
        result = qs_parsers.es_filter_conditions(
            {"status": "active"}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("term", {}).get("status") == "active"
            for c in clauses
        )

    def test_integer_term(self):
        result = qs_parsers.es_filter_conditions(
            {"age": 30}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("term", {}).get("age") == 30
            for c in clauses
        )

    def test_boolean_term(self):
        result = qs_parsers.es_filter_conditions(
            {"active": True}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("term", {}).get("active") is True
            for c in clauses
        )

    def test_null_condition(self):
        """null → must_not exists."""
        result = qs_parsers.es_filter_conditions(
            {"email": "null"}, {}
        )
        assert "must_not" in result
        clauses = result["must_not"]
        assert any(
            c.get("exists", {}).get("field") == "email"
            for c in clauses
        )

    def test_not_null_condition(self):
        """!null → filter exists."""
        result = qs_parsers.es_filter_conditions(
            {"email": "!null"}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("exists", {}).get("field") == "email"
            for c in clauses
        )

    def test_negation_string(self):
        """!active → must_not term."""
        result = qs_parsers.es_filter_conditions(
            {"status": "!active"}, {}
        )
        assert "must_not" in result
        clauses = result["must_not"]
        assert any(
            c.get("term", {}).get("status") == "active"
            for c in clauses
        )

    def test_list_in_clause(self):
        """list → terms (IN)."""
        result = qs_parsers.es_filter_conditions(
            {"color": ["red", "blue"]}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            "terms" in c and "color" in c["terms"]
            for c in clauses
        )

    def test_list_not_in_clause(self):
        """list with ! suffix → must_not terms (NOT IN)."""
        result = qs_parsers.es_filter_conditions(
            {"color!": ["red", "blue"]}, {}
        )
        assert "must_not" in result
        clauses = result["must_not"]
        assert any(
            "terms" in c and "color" in c["terms"]
            for c in clauses
        )

    def test_range_gte(self):
        """Dict with >= → range gte."""
        result = qs_parsers.es_filter_conditions(
            {"age": {">=": 18}}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        range_clause = None
        for c in clauses:
            if "range" in c and "age" in c["range"]:
                range_clause = c["range"]["age"]
                break
        assert range_clause is not None
        assert range_clause.get("gte") == 18

    def test_range_lt(self):
        """Dict with < → range lt."""
        result = qs_parsers.es_filter_conditions(
            {"price": {"<": 100}}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        range_clause = None
        for c in clauses:
            if "range" in c and "price" in c["range"]:
                range_clause = c["range"]["price"]
                break
        assert range_clause is not None
        assert range_clause.get("lt") == 100

    def test_not_equal_dict(self):
        """Dict with != → must_not term."""
        result = qs_parsers.es_filter_conditions(
            {"status": {"!=": "deleted"}}, {}
        )
        assert "must_not" in result
        clauses = result["must_not"]
        assert any(
            c.get("term", {}).get("status") == "deleted"
            for c in clauses
        )

    def test_between_condition(self):
        """BETWEEN string → range gte + lte."""
        result = qs_parsers.es_filter_conditions(
            {"created_at": "BETWEEN 2024-01-01 AND 2024-12-31"}, {}
        )
        assert "filter" in result
        clauses = result["filter"]
        range_clause = None
        for c in clauses:
            if "range" in c and "created_at" in c["range"]:
                range_clause = c["range"]["created_at"]
                break
        assert range_clause is not None
        assert range_clause.get("gte") == "2024-01-01"
        assert range_clause.get("lte") == "2024-12-31"

    def test_multiple_conditions(self):
        """Multiple conditions produce correct filter/must_not."""
        result = qs_parsers.es_filter_conditions(
            {"status": "active", "age": 25, "deleted": "null"},
            {}
        )
        # status=active and age=25 should be in filter
        assert "filter" in result
        # deleted=null should be in must_not
        assert "must_not" in result

    def test_none_value(self):
        """None value → must_not exists."""
        result = qs_parsers.es_filter_conditions(
            {"deleted_at": None}, {}
        )
        assert "must_not" in result
        clauses = result["must_not"]
        assert any(
            c.get("exists", {}).get("field") == "deleted_at"
            for c in clauses
        )

    def test_type_conversion_integer(self):
        """String value with integer cond_definition → converted."""
        result = qs_parsers.es_filter_conditions(
            {"age": "25"},
            {"age": "integer"}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("term", {}).get("age") == 25
            for c in clauses
        )

    def test_type_conversion_boolean(self):
        """String value with boolean cond_definition → converted."""
        result = qs_parsers.es_filter_conditions(
            {"active": "true"},
            {"active": "boolean"}
        )
        assert "filter" in result
        clauses = result["filter"]
        assert any(
            c.get("term", {}).get("active") is True
            for c in clauses
        )
