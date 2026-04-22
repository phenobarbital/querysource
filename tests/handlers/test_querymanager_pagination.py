"""Unit + integration tests for the FEAT-090 slug list pagination feature.

Covers:
    * Spec §4 "Unit Tests" table — pagination parameter validation and pure
      SQL-builder behaviour (no DB, no aiohttp).
    * Spec §4 "Integration Tests" table — end-to-end HTTP contract using
      aiohttp's in-process test server and a fake ``qs_connection`` pool
      (see ``tests/handlers/conftest.py``).

Integration tests intentionally use a fake pg connection rather than a live
Postgres: the aim here is to exercise ``QueryManager.get`` routing + the
pagination envelope, not to re-test asyncdb. Real DB coverage belongs to a
separate smoke / staging test suite.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from querysource.handlers._pagination import (
    DEFAULT_PAGE_SIZE,
    FILTERABLE_COLUMNS,
    MAX_PAGE_SIZE,
    SEARCHABLE_COLUMNS,
    SORTABLE_COLUMNS,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    build_count_sql,
    build_order_by,
    build_page_sql,
    build_where_clause,
)


# ---------------------------------------------------------------------------
# Unit tests — PaginationParams
# ---------------------------------------------------------------------------


class TestPaginationParams:
    """Validation behaviour of :class:`PaginationParams`."""

    def test_pagination_params_defaults(self) -> None:
        p = PaginationParams()
        assert p.page == 1
        assert p.page_size == DEFAULT_PAGE_SIZE == 50
        assert p.sort_field == "updated_at"
        assert p.sort_direction == "desc"
        assert p.search is None
        assert p.fields is None

    def test_pagination_params_clamps_page_size(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page_size=MAX_PAGE_SIZE + 1)
        with pytest.raises(ValidationError):
            PaginationParams(page_size=9999)

    def test_pagination_params_rejects_negative_or_zero_page(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=0)
        with pytest.raises(ValidationError):
            PaginationParams(page=-1)

    def test_pagination_params_rejects_unknown_sort_field(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(sort_field="password")
        with pytest.raises(ValidationError):
            PaginationParams(sort_field="evil_col")

    def test_pagination_params_rejects_unknown_field_in_projection(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(fields=["query_slug", "bogus"])

    def test_pagination_params_parses_sort_string(self) -> None:
        p = PaginationParams.from_query_string(
            {"sort": "query_slug:asc"}
        )
        assert p.sort_field == "query_slug"
        assert p.sort_direction == "asc"

    def test_pagination_params_parses_sort_without_direction(self) -> None:
        p = PaginationParams.from_query_string({"sort": "created_at"})
        assert p.sort_field == "created_at"
        assert p.sort_direction == "desc"  # default

    def test_pagination_params_rejects_bad_sort_direction(self) -> None:
        with pytest.raises(ValueError):
            PaginationParams.from_query_string(
                {"sort": "query_slug:sideways"}
            )

    def test_from_query_string_parses_csv_fields(self) -> None:
        p = PaginationParams.from_query_string(
            {"fields": "query_slug, description, provider"}
        )
        assert p.fields == ["query_slug", "description", "provider"]

    def test_from_query_string_rejects_bad_csv_field(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            PaginationParams.from_query_string(
                {"fields": "query_slug,not_a_real_col"}
            )

    def test_offset_math(self) -> None:
        assert PaginationParams(page=1, page_size=50).offset == 0
        assert PaginationParams(page=2, page_size=50).offset == 50
        assert PaginationParams(page=3, page_size=50).offset == 100
        assert PaginationParams(page=5, page_size=10).offset == 40


# ---------------------------------------------------------------------------
# Unit tests — SQL builders
# ---------------------------------------------------------------------------


class TestSqlBuilders:
    """Pure-function behaviour of :func:`build_*` helpers."""

    def test_build_where_clause_empty(self) -> None:
        assert build_where_clause(PaginationParams(), {}) == ""

    def test_build_where_clause_search_only(self) -> None:
        where = build_where_clause(
            PaginationParams(search="sales"), {}
        )
        assert "ILIKE" in where.upper()
        # Matches each searchable column
        for col in SEARCHABLE_COLUMNS:
            assert f'"{col}"' in where

    def test_build_where_clause_extra_filters(self) -> None:
        where = build_where_clause(
            PaginationParams(), {"provider": "db"}
        )
        assert '"provider"' in where
        assert "'db'" in where  # quoted

    def test_build_where_clause_rejects_injection(self) -> None:
        where = build_where_clause(
            PaginationParams(),
            {"query_slug; DROP TABLE": "x"},
        )
        # Unknown key is dropped — no SQL fragment produced.
        assert "DROP TABLE" not in where
        assert where == ""

    def test_build_where_clause_escapes_search_quotes(self) -> None:
        # Search value with a single quote must be escaped — the quote
        # doubling pattern guarantees no SQL break-out.
        where = build_where_clause(
            PaginationParams(search="O'Brien"), {}
        )
        assert "'%O''Brien%'" in where
        # The LIKE literal is not split by the user's quote
        assert where.count("'%") == len(
            [c for c in SEARCHABLE_COLUMNS if c in FILTERABLE_COLUMNS]
        )

    def test_build_order_by_allowlist_and_direction(self) -> None:
        asc = build_order_by(
            PaginationParams(sort_field="query_slug", sort_direction="asc")
        )
        assert asc == 'ORDER BY "query_slug" ASC'
        desc = build_order_by(
            PaginationParams(sort_field="updated_at", sort_direction="desc")
        )
        assert desc == 'ORDER BY "updated_at" DESC'

    def test_build_order_by_defensive_reject(self) -> None:
        params = PaginationParams()
        # Smuggle an unsafe value past the model validator (only possible
        # via direct attribute mutation) and confirm the builder still
        # refuses it.
        object.__setattr__(params, "sort_field", "password")
        with pytest.raises(ValueError):
            build_order_by(params)

    def test_build_count_sql_structure(self) -> None:
        sql = build_count_sql("troc", "queries", "")
        assert sql == 'SELECT COUNT(*) FROM "troc"."queries"'
        sql_w = build_count_sql(
            "troc", "queries", 'WHERE "provider" = \'db\''
        )
        assert "COUNT(*)" in sql_w
        assert '"troc"."queries"' in sql_w
        assert 'WHERE "provider"' in sql_w

    def test_build_count_sql_rejects_bad_identifier(self) -> None:
        with pytest.raises(ValueError):
            build_count_sql("troc; DROP TABLE", "queries", "")
        with pytest.raises(ValueError):
            build_count_sql("troc", "1queries", "")

    def test_build_page_sql_applies_limit_offset(self) -> None:
        sql = build_page_sql(
            schema="troc",
            table="queries",
            fields=["query_slug", "description"],
            where="",
            order_by='ORDER BY "updated_at" DESC',
            limit=50,
            offset=100,
        )
        assert "LIMIT 50" in sql
        assert "OFFSET 100" in sql
        assert '"troc"."queries"' in sql
        assert '"query_slug"' in sql
        assert '"description"' in sql
        assert 'ORDER BY "updated_at" DESC' in sql

    def test_build_page_sql_rejects_bad_projection(self) -> None:
        with pytest.raises(ValueError):
            build_page_sql(
                "troc", "queries",
                ["query_slug", "password"],
                "", "", 10, 0,
            )

    def test_build_page_sql_empty_projection_is_star(self) -> None:
        sql = build_page_sql("troc", "queries", [], "", "", 10, 0)
        assert "SELECT *" in sql
        assert "LIMIT 10" in sql

    def test_build_page_sql_rejects_negative_limit_offset(self) -> None:
        with pytest.raises(ValueError):
            build_page_sql("troc", "queries", [], "", "", -1, 0)
        with pytest.raises(ValueError):
            build_page_sql("troc", "queries", [], "", "", 10, -5)


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class TestPaginatedResponse:
    def test_envelope_round_trip(self) -> None:
        meta = PaginationMeta(
            page=2, page_size=10, total=53, total_pages=6
        )
        resp = PaginatedResponse(
            data=[{"query_slug": "x"}], meta=meta
        )
        dumped = resp.model_dump()
        assert dumped["data"] == [{"query_slug": "x"}]
        assert dumped["meta"]["total"] == 53
        assert dumped["meta"]["total_pages"] == 6


# ---------------------------------------------------------------------------
# Integration tests — HTTP contract
# ---------------------------------------------------------------------------


def _rows(slugs: list[dict], start: int, n: int) -> list[dict]:
    """Return a slice of the seeded data — simulates a DB LIMIT/OFFSET."""
    return slugs[start : start + n]


@pytest.mark.asyncio
class TestQueryManagerListPagination:
    """HTTP-level tests for the list-pagination branch of ``QueryManager.get``."""

    async def test_default_pagination_returns_envelope(
        self,
        test_client,
        fake_qs_connection,
        seeded_query_slugs,
    ) -> None:
        fake_qs_connection.fetchval_handler = len(seeded_query_slugs)
        fake_qs_connection.fetch_handler = lambda _sql: _rows(
            seeded_query_slugs, 0, DEFAULT_PAGE_SIZE
        )

        resp = await test_client.get("/api/v1/management/queries")
        assert resp.status == 200
        body = await resp.json()
        assert "data" in body and "meta" in body
        assert len(body["data"]) <= DEFAULT_PAGE_SIZE
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == DEFAULT_PAGE_SIZE
        assert body["meta"]["total"] == len(seeded_query_slugs)
        assert resp.headers["X-Total-Count"] == str(len(seeded_query_slugs))
        assert resp.headers["X-Page"] == "1"
        assert resp.headers["X-Page-Size"] == str(DEFAULT_PAGE_SIZE)
        assert resp.headers["X-Total-Pages"] == str(body["meta"]["total_pages"])

    async def test_second_page_returns_expected_slice(
        self,
        test_client,
        fake_qs_connection,
        seeded_query_slugs,
    ) -> None:
        fake_qs_connection.fetchval_handler = len(seeded_query_slugs)
        fake_qs_connection.fetch_handler = lambda _sql: _rows(
            seeded_query_slugs, 10, 10
        )

        resp = await test_client.get(
            "/api/v1/management/queries?page=2&page_size=10"
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["meta"]["page"] == 2
        assert body["meta"]["page_size"] == 10
        assert len(body["data"]) == 10
        # Verify the LIMIT/OFFSET produced by the builder.
        calls = [c for c in fake_qs_connection.calls if c[0] == "fetch"]
        assert calls, "expected at least one fetch() call"
        sql = calls[-1][1]
        assert "LIMIT 10" in sql
        assert "OFFSET 10" in sql

    async def test_sort_asc_propagates_to_sql(
        self,
        test_client,
        fake_qs_connection,
        seeded_query_slugs,
    ) -> None:
        fake_qs_connection.fetchval_handler = 3
        fake_qs_connection.fetch_handler = lambda _sql: seeded_query_slugs[:3]

        resp = await test_client.get(
            "/api/v1/management/queries?sort=query_slug:asc&page_size=3"
        )
        assert resp.status == 200
        calls = [c for c in fake_qs_connection.calls if c[0] == "fetch"]
        sql = calls[-1][1]
        assert 'ORDER BY "query_slug" ASC' in sql

    async def test_search_applies_ilike(
        self,
        test_client,
        fake_qs_connection,
        seeded_query_slugs,
    ) -> None:
        fake_qs_connection.fetchval_handler = 12
        fake_qs_connection.fetch_handler = lambda _sql: [
            r for r in seeded_query_slugs
            if "fixture_slug_01" in r["query_slug"]
        ][:12]

        resp = await test_client.get(
            "/api/v1/management/queries?search=fixture_slug_01"
        )
        assert resp.status == 200
        body = await resp.json()
        # All returned rows match
        assert all(
            "fixture_slug" in r["query_slug"] for r in body["data"]
        )
        calls = [c for c in fake_qs_connection.calls if c[0] == "fetch"]
        assert calls
        assert "ILIKE" in calls[-1][1].upper()

    async def test_invalid_page_size_returns_400(
        self,
        test_client,
        fake_qs_connection,
    ) -> None:
        resp = await test_client.get(
            "/api/v1/management/queries?page_size=9999"
        )
        assert resp.status == 400
        # No DB work should have happened.
        assert all(
            c[0] not in ("fetch", "fetchval")
            for c in fake_qs_connection.calls
        )

    async def test_unknown_sort_field_returns_400(
        self,
        test_client,
        fake_qs_connection,
    ) -> None:
        resp = await test_client.get(
            "/api/v1/management/queries?sort=password:desc"
        )
        assert resp.status == 400
        assert all(
            c[0] not in ("fetch", "fetchval")
            for c in fake_qs_connection.calls
        )

    async def test_empty_result_returns_204_with_headers(
        self,
        test_client,
        fake_qs_connection,
    ) -> None:
        fake_qs_connection.fetchval_handler = 0
        fake_qs_connection.fetch_handler = []
        resp = await test_client.get(
            "/api/v1/management/queries?search=__no_such_slug__"
        )
        assert resp.status == 204
        assert resp.headers.get("X-Total-Count") == "0"

    async def test_single_slug_branch_untouched(
        self,
        test_client,
        fake_qs_connection,
    ) -> None:
        """Requesting a specific slug must NOT engage _paginate_list.

        Because the fake pool is never asked for a slug, we expect the
        request to raise inside QueryModel.get (which goes through its own
        connection config). The important assertion here is that the
        pagination path is not exercised (no COUNT / paged SELECT issued).
        """
        # The single-slug path calls QueryModel.get which talks to the
        # real asyncdb layer — we simply verify that no pagination SQL
        # ran through our fake pool.
        try:
            await test_client.get(
                "/api/v1/management/queries/some_known_slug",
                timeout=2,
            )
        except Exception:
            # Expected: no live DB is attached, so the single-slug path
            # will fail. That is fine — we only care about routing.
            pass
        # Neither COUNT(*) nor a LIMIT/OFFSET query should have executed
        # against the fake pool.
        saw_count = any(
            "COUNT(*)" in sql for _, sql in fake_qs_connection.calls
        )
        saw_limit = any(
            "LIMIT" in sql for _, sql in fake_qs_connection.calls
        )
        assert not saw_count
        assert not saw_limit

    async def test_meta_branch_returns_schema(
        self,
        test_client,
        fake_qs_connection,
    ) -> None:
        resp = await test_client.get(
            "/api/v1/management/queries:meta"
        )
        # The meta branch should return 200 with the JSON schema of the
        # model — it must not acquire the pagination pool at all.
        assert resp.status == 200
        body = await resp.json()
        # Whatever shape QueryModel.schema returns, it must NOT be the
        # paginated envelope.
        assert "meta" not in body or "page" not in body.get("meta", {})
        # No SQL was issued.
        assert not [
            c for c in fake_qs_connection.calls if c[0] == "fetch"
        ]
