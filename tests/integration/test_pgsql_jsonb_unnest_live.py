"""FEAT-153 AC12: JSONB-array aggregation executed against a real PostgreSQL (opt-in).

Runs only when ``QS_TEST_POSTGRES_DSN`` points at an isolated test database.
Uses a TEMP table, so nothing persists after the connection closes.
"""
from __future__ import annotations

import os

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

POSTGRES_DSN = os.environ.get("QS_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not POSTGRES_DSN, reason="QS_TEST_POSTGRES_DSN not set")

QUERY = "SELECT * FROM jsonb_unnest_students {where_cond}"
GD = "graduation_details"
DDL = (
    "CREATE TEMP TABLE jsonb_unnest_students "
    "(student_uid text, licensee text, graduation_details jsonb)"
)
ROWS = [
    ("s1", "Asia",
     '[{"course":"Pilates Studio","category":"Comprehensive","course_date":"2025-09-19"}]'),
    ("s2", "Asia",
     '[{"course":"Pilates Studio","category":"Comprehensive","course_date":"2024-03-01"},'
     '{"course":"Pilates Mat","category":"Mat","course_date":"2025-01-10"}]'),
    ("s3", "Europe",
     '[{"course":"Pilates Mat","category":"Mat","course_date":"2025-05-05"}]'),
    ("s4", "Asia", "[]"),
    ("s5", "Asia", None),
    ("s6", "Asia", '{"not":"array"}'),
    ("s7", "Asia",
     '[{"course":"Pilates Studio","course_date":"not-a-date","level":5}]'),
]

RUST_MARK = pytest.mark.skipif(
    not (pgsql.HAS_RUST and hasattr(getattr(pgsql, "_rs", None), "pgsql_unnest_plan")),
    reason="no Rust planner",
)
USE_RUST = [pytest.param(True, marks=RUST_MARK), False]


def _insert_sql() -> str:
    values = ", ".join(
        f"('{uid}', '{licensee}', " + ("NULL" if doc is None else f"'{doc}'::jsonb") + ")"
        for uid, licensee, doc in ROWS
    )
    return f"INSERT INTO jsonb_unnest_students VALUES {values}"


@pytest.fixture
async def conn():
    from asyncdb import AsyncDB

    db = AsyncDB("pg", dsn=POSTGRES_DSN)
    connection = await db.connection()
    try:
        _, error = await connection.execute(DDL)
        assert not error, error
        _, error = await connection.execute(_insert_sql())
        assert not error, error
        yield connection
    finally:
        await connection.close()


def _make(**attrs) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=QUERY), query=QUERY)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser


async def _run(conn, parser: pgSQLParser):
    result, error = await conn.query(await parser.build_query())
    assert not error, error
    return result


def _by(result, key: str, value: str) -> dict:
    return {r[key]: r[value] for r in result}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_graduates_per_course(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", "count(distinct student_uid) as graduates"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'"},
        attributes={"jsonb_unnest": {"safe_cast": True}},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "graduates") == {"Pilates Studio": 3, "Pilates Mat": 1}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_element_filter_only_counts_matching_elements(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", "count(distinct student_uid) as graduates"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'", f"{GD}[].course": "'Pilates Studio'"},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "graduates") == {"Pilates Studio": 3}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_row_filter_keeps_other_diplomas(conn, use_rust, monkeypatch):
    """A row-level ``@>`` filter keeps every element of the matching rows (documented pitfall)."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", "count(distinct student_uid) as graduates"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'", GD: {"@>": [{"course": "Pilates Studio"}]}},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "graduates") == {"Pilates Studio": 3, "Pilates Mat": 1}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_having_filters_groups(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", "count(distinct student_uid) as graduates"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'"},
        having={"graduates": {">": 1}},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "graduates") == {"Pilates Studio": 3}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_year_bucket_with_safe_cast(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"year({GD}[].course_date)", "count(*) as n"],
        grouping=[f"year({GD}[].course_date)"],
        filter={"licensee": "'Asia'"},
        attributes={"jsonb_unnest": {"safe_cast": True}},
    )
    result = await _run(conn, parser)
    buckets = {(str(r["year_course_date"]) if r["year_course_date"] else None): r["n"] for r in result}
    assert buckets == {"2025-01-01": 2, "2024-01-01": 1, None: 1}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_empty_include_yields_null_group(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", f"count({GD}[].course) as n"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'"},
        attributes={"jsonb_unnest": {"columns": {GD: {"empty": "include"}}}},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "n") == {"Pilates Studio": 3, "Pilates Mat": 1, None: 0}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_empty_exclude_has_no_null_group(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course", "count(*) as n"],
        grouping=[f"{GD}[].course"],
        filter={"licensee": "'Asia'"},
    )
    result = await _run(conn, parser)
    assert _by(result, "course", "n") == {"Pilates Studio": 3, "Pilates Mat": 1}


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_cast_without_safe_cast_fails_on_dirty_data(conn, use_rust, monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    parser = _make(
        fields=[f"{GD}[].course_date::date", "count(*) as n"],
        grouping=[f"{GD}[].course_date::date"],
        filter={"licensee": "'Asia'"},
    )
    sql = await parser.build_query()
    try:
        _, error = await conn.query(sql)
    except Exception as exc:  # noqa: BLE001 - the driver may raise instead of returning the error
        error = exc
    assert error, "an invalid date must fail the query when safe_cast is off"


@pytest.mark.parametrize("use_rust", USE_RUST)
async def test_prefilter_is_type_sensitive_opt_in(conn, use_rust, monkeypatch):
    """Why ``prefilter`` is opt-in: ``@>`` does not match the JSON number 5 with the string "5"."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)

    def _parser(config: dict) -> pgSQLParser:
        return _make(
            fields=[f"{GD}[].level", "count(*) as n"],
            grouping=[f"{GD}[].level"],
            filter={"licensee": "'Asia'", f"{GD}[].level": "'5'"},
            attributes={"jsonb_unnest": config},
        )

    without = await _run(conn, _parser({}))
    assert _by(without, "level", "n") == {"5": 1}
    with_prefilter = await _run(conn, _parser({"columns": {GD: {"prefilter": True}}}))
    assert list(with_prefilter) == []
