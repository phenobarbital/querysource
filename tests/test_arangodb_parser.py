# Copyright (C) 2018-present Jesus Lara
#
# test_arangodb_parser.py — Tests for ArangoDB AQL parser (Rust + Cython).
"""Tests for ArangoDB AQL parser functions."""
import pytest

try:
    from querysource import qs_parsers
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers not installed")


# ===========================================================================
# aql_filter_conditions
# ===========================================================================


class TestAqlFilterConditions:
    """Tests for aql_filter_conditions()."""

    def test_simple_equality(self):
        result = qs_parsers.aql_filter_conditions(
            {"name": "John"}, {}
        )
        assert len(result) == 1
        assert 'doc.name == "John"' in result[0]

    def test_integer_equality(self):
        result = qs_parsers.aql_filter_conditions(
            {"age": 30}, {}
        )
        assert len(result) == 1
        assert "doc.age == 30" in result[0]

    def test_boolean_equality(self):
        result = qs_parsers.aql_filter_conditions(
            {"active": True}, {}
        )
        assert len(result) == 1
        assert "doc.active == true" in result[0]

    def test_null_check(self):
        result = qs_parsers.aql_filter_conditions(
            {"email": "null"}, {}
        )
        assert len(result) == 1
        assert "doc.email == null" in result[0]

    def test_not_null_check(self):
        result = qs_parsers.aql_filter_conditions(
            {"email": "!null"}, {}
        )
        assert len(result) == 1
        assert "doc.email != null" in result[0]

    def test_negation(self):
        result = qs_parsers.aql_filter_conditions(
            {"status": "!active"}, {}
        )
        assert len(result) == 1
        assert 'doc.status != "active"' in result[0]

    def test_operator_dict(self):
        result = qs_parsers.aql_filter_conditions(
            {"age": {">=": 18}}, {}
        )
        assert len(result) == 1
        assert "doc.age >= 18" in result[0]

    def test_in_list(self):
        result = qs_parsers.aql_filter_conditions(
            {"status": ["active", "pending"]}, {}
        )
        assert len(result) == 1
        assert "doc.status IN" in result[0]
        assert '"active"' in result[0]
        assert '"pending"' in result[0]

    def test_not_in_list(self):
        result = qs_parsers.aql_filter_conditions(
            {"status!": ["deleted"]}, {}
        )
        assert len(result) == 1
        assert "NOT IN" in result[0]

    def test_empty_filter(self):
        result = qs_parsers.aql_filter_conditions({}, {})
        assert len(result) == 0

    def test_multiple_conditions(self):
        result = qs_parsers.aql_filter_conditions(
            {"name": "John", "age": 30}, {}
        )
        assert len(result) == 2

    def test_custom_doc_var(self):
        result = qs_parsers.aql_filter_conditions(
            {"name": "John"}, {}, "u"
        )
        assert len(result) == 1
        assert 'u.name == "John"' in result[0]

    def test_type_conversion(self):
        result = qs_parsers.aql_filter_conditions(
            {"age": "25"}, {"age": "integer"}
        )
        assert len(result) == 1
        assert "doc.age == 25" in result[0]

    def test_none_value(self):
        result = qs_parsers.aql_filter_conditions(
            {"deleted": None}, {}
        )
        assert len(result) == 1
        assert "doc.deleted == null" in result[0]

    def test_like_pattern(self):
        result = qs_parsers.aql_filter_conditions(
            {"name": "%john%"}, {}
        )
        assert len(result) == 1
        assert "LIKE(doc.name" in result[0]

    def test_between(self):
        result = qs_parsers.aql_filter_conditions(
            {"created_at": "BETWEEN 2024-01-01 AND 2024-12-31"}, {}
        )
        assert len(result) == 1
        assert "doc.created_at >=" in result[0]
        assert "doc.created_at <=" in result[0]


# ===========================================================================
# aql_process_fields
# ===========================================================================


class TestAqlProcessFields:
    """Tests for aql_process_fields()."""

    def test_empty_fields(self):
        result = qs_parsers.aql_process_fields([])
        assert result == "RETURN doc"

    def test_single_field(self):
        result = qs_parsers.aql_process_fields(["name"])
        assert "name: doc.name" in result
        assert result.startswith("RETURN {")

    def test_multiple_fields(self):
        result = qs_parsers.aql_process_fields(["name", "email", "age"])
        assert "name: doc.name" in result
        assert "email: doc.email" in result
        assert "age: doc.age" in result

    def test_alias_field(self):
        result = qs_parsers.aql_process_fields(["username as name"])
        assert "name: doc.username" in result

    def test_custom_doc_var(self):
        result = qs_parsers.aql_process_fields(["name"], "u")
        assert "name: u.name" in result


# ===========================================================================
# aql_process_ordering
# ===========================================================================


class TestAqlProcessOrdering:
    """Tests for aql_process_ordering()."""

    def test_empty_ordering(self):
        result = qs_parsers.aql_process_ordering([])
        assert result == ""

    def test_single_asc(self):
        result = qs_parsers.aql_process_ordering(["name"])
        assert result == "SORT doc.name ASC"

    def test_single_desc_prefix(self):
        result = qs_parsers.aql_process_ordering(["-created_at"])
        assert result == "SORT doc.created_at DESC"

    def test_explicit_desc(self):
        result = qs_parsers.aql_process_ordering(["age DESC"])
        assert result == "SORT doc.age DESC"

    def test_multiple_ordering(self):
        result = qs_parsers.aql_process_ordering(["name ASC", "-created_at"])
        assert "doc.name ASC" in result
        assert "doc.created_at DESC" in result

    def test_custom_doc_var(self):
        result = qs_parsers.aql_process_ordering(["name"], "u")
        assert result == "SORT u.name ASC"


# ===========================================================================
# aql_build_query
# ===========================================================================


class TestAqlBuildQuery:
    """Tests for aql_build_query()."""

    def test_simple_collection_query(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
        )
        assert "FOR doc IN users" in result
        assert "RETURN doc" in result

    def test_query_with_filter(self):
        result = qs_parsers.aql_build_query(
            "users",
            {"status": "active"},
            {},
            [],
            [],
            [],
        )
        assert "FOR doc IN users" in result
        assert "FILTER" in result
        assert 'doc.status == "active"' in result

    def test_query_with_fields(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            ["name", "email"],
            [],
            [],
        )
        assert "FOR doc IN users" in result
        assert "RETURN {" in result
        assert "name: doc.name" in result
        assert "email: doc.email" in result

    def test_query_with_ordering(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            ["name ASC"],
            [],
        )
        assert "SORT doc.name ASC" in result

    def test_query_with_limit(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            limit=10,
        )
        assert "LIMIT 10" in result

    def test_query_with_offset(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            limit=10,
            offset=20,
        )
        assert "LIMIT 20, 10" in result

    def test_query_with_grouping(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            ["department"],
        )
        assert "COLLECT department = doc.department" in result

    def test_full_query(self):
        result = qs_parsers.aql_build_query(
            "users",
            {"status": "active", "age": {">=": 18}},
            {},
            ["name", "email"],
            ["name ASC"],
            [],
            limit=10,
            offset=5,
        )
        assert "FOR doc IN users" in result
        assert "FILTER" in result
        assert "SORT" in result
        assert "LIMIT 5, 10" in result
        assert "RETURN {" in result

    def test_graph_traversal(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            graph={
                "direction": "OUTBOUND",
                "start_vertex": "users/12345",
                "edge_collection": "follows",
                "min_depth": 1,
                "max_depth": 3,
            },
        )
        assert "FOR v, e, p IN 1..3 OUTBOUND" in result
        assert "'users/12345'" in result
        assert "follows" in result
        assert "RETURN v" in result

    def test_graph_with_filter(self):
        result = qs_parsers.aql_build_query(
            "users",
            {"active": True},
            {},
            [],
            [],
            [],
            graph={
                "direction": "OUTBOUND",
                "start_vertex": "users/12345",
                "edge_collection": "follows",
                "min_depth": 1,
                "max_depth": 2,
            },
        )
        assert "FOR v, e, p IN" in result
        assert "FILTER v.active == true" in result

    def test_graph_with_fields(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            ["name", "email"],
            [],
            [],
            graph={
                "direction": "INBOUND",
                "start_vertex": "users/999",
                "edge_collection": "follows",
            },
        )
        assert "INBOUND" in result
        assert "RETURN {" in result
        assert "name: v.name" in result

    def test_search_query(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            search={
                "view": "users_view",
                "analyzer": "text_en",
                "fields": {"name": "John"},
            },
        )
        assert "FOR doc IN users_view" in result
        assert "SEARCH ANALYZER" in result
        assert '"text_en"' in result
        assert 'doc.name == "John"' in result

    def test_search_with_phrase(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            search={
                "view": "users_view",
                "phrase": {"bio": "software engineer"},
            },
        )
        assert "FOR doc IN users_view" in result
        assert "SEARCH" in result
        assert 'PHRASE(doc.bio, "software engineer")' in result

    def test_custom_doc_var(self):
        result = qs_parsers.aql_build_query(
            "users",
            {},
            {},
            [],
            [],
            [],
            doc_var="u",
        )
        assert "FOR u IN users" in result
        assert "RETURN u" in result


# ===========================================================================
# Integration: ArangoDB Live Tests
# ===========================================================================


class TestArangoDBLive:
    """Live tests against a running ArangoDB instance.

    Requires ArangoDB running at localhost:8529 with:
    - username: root
    - password: 12345678
    - database: navigator
    """

    @pytest.fixture(autouse=True)
    def check_arangodb(self):
        """Skip if ArangoDB is not reachable."""
        try:
            from asyncdb.drivers.arangodb import arangodb as ArangoDB  # noqa: F401
        except ImportError:
            pytest.skip("asyncdb ArangoDB driver not installed")

    def test_execute_simple_query(self):
        """Test generating and executing a simple AQL query."""
        import asyncio
        from asyncdb.drivers.arangodb import arangodb as ArangoDB

        async def _run():
            params = {
                'host': 'localhost',
                'port': 8529,
                'username': 'root',
                'password': '12345678',
                'database': 'navigator',
            }
            db = ArangoDB(params=params)
            async with await db.connection() as conn:
                query = qs_parsers.aql_build_query(
                    "_graphs",
                    {},
                    {},
                    [],
                    [],
                    [],
                    limit=5,
                )
                result = await conn.query(query)
                assert isinstance(result, list)

        try:
            asyncio.run(_run())
        except Exception as e:
            pytest.skip(f"ArangoDB not reachable: {e}")

    def test_execute_filtered_query(self):
        """Test generating and executing a filtered AQL query."""
        import asyncio
        from asyncdb.drivers.arangodb import arangodb as ArangoDB

        async def _run():
            params = {
                'host': 'localhost',
                'port': 8529,
                'username': 'root',
                'password': '12345678',
                'database': 'navigator',
            }
            db = ArangoDB(params=params)
            async with await db.connection() as conn:
                query = qs_parsers.aql_build_query(
                    "_graphs",
                    {},
                    {},
                    [],
                    [],
                    [],
                    limit=3,
                )
                result = await conn.query(query)
                assert isinstance(result, list)

        try:
            asyncio.run(_run())
        except Exception as e:
            pytest.skip(f"ArangoDB not reachable: {e}")

