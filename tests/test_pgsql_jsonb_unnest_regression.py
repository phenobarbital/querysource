"""FEAT-153 G7 regression: queries without JSONB-unnest syntax render byte-identical SQL.

Expected strings were captured from the pre-FEAT-153 parser (TASK-779) on both
code paths; any drift after the planner is wired in (TASK-784) is a regression.
"""
from __future__ import annotations

from typing import Any

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

WHERE_SQL = "SELECT * FROM t {where_cond}"
TABLE_SQL = "SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}"

# case_id -> (query_raw, attribute overrides, build_query kwargs)
CASES: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
    "scalar_filter": (WHERE_SQL, {"filter": {"status": "'active'"}}, {"querylimit": 10}),
    "group_and_count": (
        WHERE_SQL,
        {"fields": ["store_id", "count(*)"], "grouping": ["store_id"], "ordering": ["store_id DESC"]},
        {},
    ),
    "jsonb_contains": (WHERE_SQL, {"filter": {"attrs": {"@>": {"status": "active"}}}}, {"querylimit": 10}),
    "jsonb_any_of": (
        WHERE_SQL,
        {"filter": {"graduation_details": {"@>|": [[{"course": "Pilates Studio"}], [{"course": "Pilates Mat"}]]}}},
        {},
    ),
    "jsonb_path_text": (WHERE_SQL, {"filter": {"attrs": {"->>": {"status": "active"}}}}, {}),
    "array_cast_field": (WHERE_SQL, {"fields": ["tags::text[]", "id"]}, {}),
    "table_template": (
        TABLE_SQL,
        {"fields": ["a", "b"], "schema": "public", "tablename": "students", "filter": {"licensee": "'Asia'"}},
        {"querylimit": 5, "offset": 10},
    ),
    "ordering_only": (WHERE_SQL, {"ordering": ["created_at DESC"]}, {}),
    "list_in_filter": (WHERE_SQL, {"filter": {"status": ["'a'", "'b'"]}}, {}),
    "negated_key": (WHERE_SQL, {"filter": {"status!": "'closed'"}}, {"querylimit": 3}),
    "comparison_dict": (WHERE_SQL, {"filter": {"amount": {">=": 10}}}, {}),
    "offset_only": (WHERE_SQL, {"ordering": ["id"]}, {"querylimit": 20, "offset": 40}),
}


def _make(case_id: str) -> tuple[pgSQLParser, dict[str, Any]]:
    """Build a parser for ``case_id`` bypassing ``set_options`` (needs Redis)."""
    query, attrs, kwargs = CASES[case_id]
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser, kwargs


async def _render(case_id: str) -> str:
    """Render ``case_id`` through ``build_query`` on the currently selected path."""
    parser, kwargs = _make(case_id)
    return await parser.build_query(**kwargs)


EXPECTED: dict[tuple[str, str], str] = {
    ('array_cast_field', 'cython'): 'SELECT tags::text[], id FROM t ',
    ('array_cast_field', 'rust'): 'SELECT tags::text[], id FROM t ',
    ('comparison_dict', 'cython'): 'SELECT * FROM t  WHERE amount >= 10',
    ('comparison_dict', 'rust'): "SELECT * FROM t  WHERE amount >= '10'",
    ('group_and_count', 'cython'): 'SELECT store_id, count(*) FROM t  GROUP BY store_id ORDER BY store_id DESC',
    ('group_and_count', 'rust'): 'SELECT store_id, count(*) FROM t  GROUP BY store_id ORDER BY store_id DESC',
    ('jsonb_any_of', 'cython'): 'SELECT * FROM t  WHERE (graduation_details @> E\'[\\x7b"course":"Pilates Studio"\\x7d]\'::jsonb OR graduation_details @> E\'[\\x7b"course":"Pilates Mat"\\x7d]\'::jsonb)',
    ('jsonb_any_of', 'rust'): 'SELECT * FROM t  WHERE (graduation_details @> E\'[\\x7b"course":"Pilates Studio"\\x7d]\'::jsonb OR graduation_details @> E\'[\\x7b"course":"Pilates Mat"\\x7d]\'::jsonb)',
    ('jsonb_contains', 'cython'): 'SELECT * FROM t  WHERE attrs @> E\'\\x7b"status":"active"\\x7d\'::jsonb LIMIT 10',
    ('jsonb_contains', 'rust'): 'SELECT * FROM t  WHERE attrs @> E\'\\x7b"status":"active"\\x7d\'::jsonb LIMIT 10',
    ('jsonb_path_text', 'cython'): "SELECT * FROM t  WHERE attrs ->> 'status' = 'active'",
    ('jsonb_path_text', 'rust'): "SELECT * FROM t  WHERE attrs ->> 'status' = 'active'",
    ('list_in_filter', 'cython'): "SELECT * FROM t  WHERE status IN ('a','b')",
    ('list_in_filter', 'rust'): "SELECT * FROM t  WHERE status IN ('a','b')",
    ('negated_key', 'cython'): "SELECT * FROM t  WHERE status != 'closed' LIMIT 3",
    ('negated_key', 'rust'): "SELECT * FROM t  WHERE status != 'closed' LIMIT 3",
    ('offset_only', 'cython'): 'SELECT * FROM t  ORDER BY id LIMIT 20 OFFSET 40',
    ('offset_only', 'rust'): 'SELECT * FROM t  ORDER BY id LIMIT 20 OFFSET 40',
    ('ordering_only', 'cython'): 'SELECT * FROM t  ORDER BY created_at DESC',
    ('ordering_only', 'rust'): 'SELECT * FROM t  ORDER BY created_at DESC',
    ('scalar_filter', 'cython'): "SELECT * FROM t  WHERE status='active' LIMIT 10",
    ('scalar_filter', 'rust'): "SELECT * FROM t  WHERE status='active' LIMIT 10",
    ('table_template', 'cython'): "SELECT a, b FROM public.students  WHERE licensee='Asia'  OFFSET 10 LIMIT 5",
    ('table_template', 'rust'): "SELECT a, b FROM public.students  WHERE licensee='Asia'  OFFSET 10 LIMIT 5",
}


@pytest.mark.parametrize("use_rust", [
    pytest.param(True, marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built")),
    False,
])
@pytest.mark.parametrize("case_id", sorted(CASES))
async def test_non_plan_queries_byte_identical(case_id: str, use_rust: bool, monkeypatch) -> None:
    """Every pre-existing query shape renders exactly the frozen SQL."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    path = "rust" if use_rust else "cython"
    assert await _render(case_id) == EXPECTED[(case_id, path)]
