"""End-to-end dry-run tests for ``QS``: syntax and parsing, no database.

Each test builds a real ``QS`` and calls ``dry_run()``, which resolves the
query (inline or by slug), constructs the provider, runs the dialect parser
(``set_options`` + ``build_query``) and returns the rendered statement without
executing it. Only Redis and the definition repository are faked (see
``conftest.py``); every rendered statement is also checked to be a single,
syntactically valid PostgreSQL statement.
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlglot

from querysource.exceptions import EmptySentence
from querysource.queries.qs import QS
from querysource.tenant_errors import TenantError


def assert_valid_sql(sql: str) -> None:
    """Assert ``sql`` parses as exactly one PostgreSQL statement."""
    statements = sqlglot.parse(sql, read="postgres")
    assert len(statements) == 1, f"expected one statement, got {len(statements)}: {sql!r}"
    assert statements[0] is not None


async def dry_run(**kwargs: Any) -> str:
    """Build a ``QS`` from ``kwargs``, dry-run it and return the rendered SQL."""
    qs = QS(**kwargs)
    sql, error = await qs.dry_run()
    assert error is None
    assert isinstance(sql, str)
    assert_valid_sql(sql)
    return sql


class TestInlineQuery:
    """``QS(query=..., driver='db')``: the parser renders every placeholder."""

    @pytest.mark.parametrize("query,conditions,expected", [
        pytest.param(
            "SELECT {fields} FROM public.users {where_cond}",
            {"fields": ["user_id", "email"], "where_cond": {"user_id": 1, "status": "active"}},
            "SELECT user_id, email FROM public.users  WHERE user_id='1' AND status='active'",
            id="fields-and-where",
        ),
        pytest.param(
            "SELECT * FROM t {where_cond}",
            {"where_cond": {"amount": {">=": 100}, "name": "!null", "d": ["2024-01-01", "2024-01-31"]}},
            "SELECT * FROM t  WHERE amount >= '100' AND name IS NOT NULL"
            " AND d IN ('2024-01-01','2024-01-31')",
            id="operators-null-and-in",
        ),
        pytest.param(
            "SELECT * FROM t {where_cond}",
            {"filter": {"a": 1, "b": [1, 2]}, "querylimit": 10, "ordering": ["a DESC"]},
            "SELECT * FROM t  WHERE a='1' AND b IN ('1','2') ORDER BY a DESC LIMIT 10",
            id="filter-ordering-limit",
        ),
        pytest.param(
            "SELECT * FROM t {where_cond}",
            {"querylimit": 5, "_offset": 10, "ordering": ["id"]},
            "SELECT * FROM t  ORDER BY id LIMIT 5 OFFSET 10",
            id="pagination-without-filters",
        ),
        pytest.param(
            "SELECT {fields} FROM sales {where_cond} {group_by}",
            {"fields": ["region", "sum(amount) as total"], "group_by": ["region"],
             "where_cond": {"year": 2024}},
            "SELECT region, sum(amount) as total FROM sales  WHERE year='2024'  GROUP BY region",
            id="group-by",
        ),
    ])
    async def test_renders_placeholders(self, query: str, conditions: dict, expected: str) -> None:
        assert await dry_run(query=query, driver="db", conditions=conditions) == expected

    async def test_query_without_placeholders_is_untouched(self) -> None:
        """Brace literals survive when the parser is not needed."""
        sql = "SELECT '{a,b}'::text[] AS arr"
        assert await dry_run(query=sql, driver="db") == sql

    async def test_conditions_are_not_mutated(self) -> None:
        conditions = {"fields": ["id"], "where_cond": {"id": 7}}
        snapshot = {"fields": ["id"], "where_cond": {"id": 7}}
        await dry_run(query="SELECT {fields} FROM t {where_cond}", driver="db", conditions=conditions)
        assert conditions == snapshot


class TestSanitization:
    """User-supplied values can never break out of the rendered statement."""

    async def test_quote_injection_stays_inside_literal(self) -> None:
        sql = await dry_run(
            query="SELECT * FROM t {where_cond}",
            driver="db",
            conditions={"where_cond": {"name": "x'; DROP TABLE t; --"}},
        )
        assert sql == "SELECT * FROM t  WHERE name='x; DROP TABLE t; --'"
        assert "DROP" not in [token.text.upper() for token in sqlglot.tokenize(sql)
                              if token.token_type.name != "STRING"]

    async def test_unsafe_identifier_is_dropped(self) -> None:
        sql = await dry_run(
            query="SELECT * FROM t {where_cond}",
            driver="db",
            conditions={"where_cond": {"a;drop": 1, "ok": 2}},
        )
        assert sql == "SELECT * FROM t  WHERE ok='2'"


class TestSlugQuery:
    """``QS(slug=...)``: definition lookup + parser, served from memory."""

    @pytest.fixture(autouse=True)
    def _catalog(self, definitions) -> None:
        definitions.add(
            query_slug="sales_by_region",
            provider="db",
            query_raw="SELECT {fields} FROM public.sales {where_cond}",
            fields=["region", "amount"],
            conditions={"year": 2024},
            ordering=["region"],
        )
        definitions.add(
            query_slug="raw_orders",
            provider="db",
            is_raw=True,
            query_raw="SELECT * FROM public.orders WHERE store_id = {store_id}",
        )

    async def test_definition_drives_rendering(self, definitions) -> None:
        sql = await dry_run(slug="sales_by_region")
        assert sql == "SELECT region, amount FROM public.sales  WHERE year='2024' ORDER BY region"
        assert definitions.requested == ["sales_by_region"]

    async def test_request_conditions_override_definition(self) -> None:
        sql = await dry_run(slug="sales_by_region", conditions={"region": "EMEA", "year": 2025})
        assert sql == (
            "SELECT region, amount FROM public.sales"
            "  WHERE year='2025' AND region='EMEA' ORDER BY region"
        )

    async def test_raw_slug_is_dispatched_verbatim(self) -> None:
        """``is_raw`` definitions bypass the parser: conditions are never interpolated."""
        qs = QS(slug="raw_orders", conditions={"store_id": "1 OR 1=1"})
        sql, error = await qs.dry_run()
        assert error is None
        assert sql == "SELECT * FROM public.orders WHERE store_id = {store_id}"

    async def test_unknown_slug_raises(self) -> None:
        with pytest.raises(TenantError) as exc:
            await QS(slug="does_not_exist").dry_run()
        assert exc.value.error_code == "query_not_found"


def test_empty_request_is_rejected() -> None:
    with pytest.raises(EmptySentence):
        QS()
