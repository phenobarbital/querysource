"""FEAT-180: PostgreSQL partial-matching operators — Rust and Cython builders agree."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"


def _rust_supports_partial_match() -> bool:
    """Return whether the installed Rust extension includes this feature."""
    if not pgsql.HAS_RUST:
        return False
    rendered = pgsql._rs.pgsql_filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})
    return "n LIKE 'andre%'" in rendered


RUST_AVAILABLE = _rust_supports_partial_match()
PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(
            not RUST_AVAILABLE,
            reason="stale _qs_parsers: run `make build-rust && make stage-rust` (TASK-851)",
        ),
    ),
    "cython",
]

CORPUS = [
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),
    ({"n": {"not_like": "an_re%"}}, "n NOT LIKE 'an_re%'"),
    ({"n": {"ilike": "andre"}}, "n ILIKE 'andre'"),
    ({"n": {"not_ilike": "andre"}}, "n NOT ILIKE 'andre'"),
    ({"full_name": {"startswith": "andre"}}, "full_name LIKE 'andre%'"),
    ({"n": {"not_startswith": "o'brien"}}, "n NOT LIKE 'o''brien%'"),
    ({"n": {"istartswith": "andre"}}, "n ILIKE 'andre%'"),
    ({"n": {"not_istartswith": "andre"}}, "n NOT ILIKE 'andre%'"),
    ({"n": {"endswith": "01"}}, "n LIKE '%01'"),
    ({"n": {"not_endswith": "01"}}, "n NOT LIKE '%01'"),
    ({"n": {"iendswith": "a_b"}}, "n ILIKE E'%a\\\\_b'"),
    ({"n": {"not_iendswith": "a\\b"}}, "n NOT ILIKE E'%a\\\\\\\\b'"),
    ({"n": {"contains": "50%"}}, "n LIKE E'%50\\\\%%'"),
    ({"n": {"not_contains": "abc"}}, "n NOT LIKE '%abc%'"),
    ({"n": {"icontains": "pilates"}}, "n ILIKE '%pilates%'"),
    ({"n": {"not_icontains": "{json}"}}, "n NOT ILIKE E'%\\x7bjson\\x7d%'"),
    ({"n": {"regex": "^an.*e$"}}, "n ~ '^an.*e$'"),
    ({"n": {"not_regex": "^an"}}, "n !~ '^an'"),
    ({"n": {"iregex": "^an"}}, "n ~* '^an'"),
    ({"n": {"not_iregex": "^an"}}, "n !~* '^an'"),
]


def _make_parser(filter_: dict) -> pgSQLParser:
    """Build a parser wired directly to the Cython filtering path."""
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict) -> str:
    """Render a filter through the selected implementation path."""
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(SQL, filter_, {})
    return await _make_parser(filter_)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    """Return the rendered WHERE condition, if one exists."""
    return sql.split(" WHERE ", 1)[1].strip() if " WHERE " in sql else None


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_pg_rendering(path: str, filter_: dict, expected: str) -> None:
    """Every table operator renders its PostgreSQL form."""
    assert _where_body(await _render(path, filter_)) == expected


async def test_pg_regex_flag_true() -> None:
    """PostgreSQL accepts the regex family during pre-dispatch validation."""
    parser = _make_parser({})
    assert parser.supports_regex_filter is True
    await parser.set_where({"n": {"regex": "^a"}}, None)


async def test_pg_cython_revalidates() -> None:
    """A direct filter_options-style operand is validated by the Cython builder."""
    parser = _make_parser({"n": {"contains": "ab"}})
    with pytest.raises(ParserError, match="requires at least 3 characters"):
        await parser._filter_conditions_cy(SQL)


async def test_pg_legacy_ilike_and_suffix_untouched() -> None:
    """FEAT-152 ILIKE and the legacy field suffix retain their old rendering."""
    assert _where_body(await _render("cython", {"city": {"ILIKE": "'%san%'"}})) == "city ILIKE '%san%'"
    assert _where_body(await _render("cython", {"name~": "'ab'"})) == "name ILIKE '''ab%'"
