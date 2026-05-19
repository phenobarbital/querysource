"""Unit tests for SourceTable (TASK-650)."""
import asyncio

import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.table import (
    DRIVER_ALIASES,
    SQL_IDENTIFIER_RE,
    SourceTable,
)


class TestSourceTable:
    def test_inherits_thread_source(self):
        assert issubclass(SourceTable, ThreadSource)

    def test_driver_normalization_postgresql(self):
        options = {"driver": "postgresql", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "pg"

    def test_driver_normalization_postgres(self):
        options = {"driver": "postgres", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "pg"

    def test_driver_normalization_bq(self):
        options = {"driver": "bq", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "bigquery"

    def test_driver_normalization_mariadb(self):
        options = {"driver": "mariadb", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "mysql"

    def test_driver_passthrough_for_unknown(self):
        options = {"driver": "mongo", "table": "items"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "mongo"

    def test_invalid_table_name_raises(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            SourceTable(
                "test",
                {"driver": "pg", "table": "'; DROP TABLE--"},
                None,
                asyncio.Queue(),
            )

    def test_invalid_schema_name_raises(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            SourceTable(
                "test",
                {"driver": "pg", "table": "t", "schema": "1bad"},
                None,
                asyncio.Queue(),
            )

    def test_valid_identifiers_pass(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "my_table", "schema": "public"},
            None,
            asyncio.Queue(),
        )
        assert source._table == "my_table"
        assert source._schema == "public"

    def test_build_where_empty(self):
        source = SourceTable("test", {"driver": "pg", "table": "t"}, None, asyncio.Queue())
        assert source._build_where() == ""

    def test_build_where_bool_true(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"active": True}},
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "active = true" in where
        assert where.startswith(" WHERE ")

    def test_build_where_bool_false(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"deleted": False}},
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "deleted = false" in where

    def test_build_where_integer(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"id": 42}},
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "id = 42" in where

    def test_build_where_float(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"score": 9.5}},
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "score = 9.5" in where

    def test_build_where_string_escaping(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"name": "O'Brien"}},
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "O''Brien" in where

    def test_build_where_null(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "t", "filter": {"deleted_at": None}},
            None,
            asyncio.Queue(),
        )
        assert "IS NULL" in source._build_where()

    def test_build_where_multiple_filters(self):
        source = SourceTable(
            "test",
            {
                "driver": "pg",
                "table": "t",
                "filter": {"active": True, "type": "store"},
            },
            None,
            asyncio.Queue(),
        )
        where = source._build_where()
        assert "active = true" in where
        assert "type = 'store'" in where
        assert "AND" in where

    def test_build_where_invalid_column_raises(self):
        source = SourceTable("test", {"driver": "pg", "table": "t"}, None, asyncio.Queue())
        source._filter = {"bad;column": "value"}
        with pytest.raises(ValueError, match="Invalid column name"):
            source._build_where()

    def test_sql_identifier_regex_valid(self):
        assert SQL_IDENTIFIER_RE.match("valid_name")
        assert SQL_IDENTIFIER_RE.match("_private")
        assert SQL_IDENTIFIER_RE.match("CamelCase")
        assert SQL_IDENTIFIER_RE.match("col123")

    def test_sql_identifier_regex_invalid(self):
        assert not SQL_IDENTIFIER_RE.match("123bad")
        assert not SQL_IDENTIFIER_RE.match("no spaces")
        assert not SQL_IDENTIFIER_RE.match("semi;colon")
        assert not SQL_IDENTIFIER_RE.match("dash-name")
        assert not SQL_IDENTIFIER_RE.match("")

    def test_driver_aliases_contains_expected_keys(self):
        assert "postgresql" in DRIVER_ALIASES
        assert "postgres" in DRIVER_ALIASES
        assert "bq" in DRIVER_ALIASES
        assert "mariadb" in DRIVER_ALIASES

    def test_schema_table_ref_with_schema(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "stores", "schema": "troc"},
            None,
            asyncio.Queue(),
        )
        table_ref = f"{source._schema}.{source._table}" if source._schema else source._table
        assert table_ref == "troc.stores"

    def test_schema_table_ref_without_schema(self):
        source = SourceTable(
            "test",
            {"driver": "pg", "table": "stores"},
            None,
            asyncio.Queue(),
        )
        table_ref = f"{source._schema}.{source._table}" if source._schema else source._table
        assert table_ref == "stores"
