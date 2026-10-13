"""FEAT-167 TASK-878 — WHERE value hardening, end-to-end through set_options()/build_query()."""
from __future__ import annotations

import pytest

import querysource.parsers.pgsql as pgsql
from querysource.models import QueryObject
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.cql import CQLParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.qs_parsers import _qs_parsers as rs
from querysource.types.validators import Entity

SQL = "SELECT * FROM public.t {where_cond}"


class _Connection:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Redis:
    async def connection(self) -> _Connection:
        return _Connection()


async def where(parser_cls, flt: dict) -> str:
    """Render ``flt`` through the real pipeline and return the WHERE fragment."""
    parser = parser_cls(definition=None, conditions=QueryObject(filter=flt), query=SQL)
    parser._redis = _Redis()
    await parser.set_options()
    return (await parser.build_query()).split("WHERE", 1)[1].strip()


@pytest.fixture(params=["pg-rust", "pg-cython", "sql", "cql"])
def sq_parser(request, monkeypatch):
    """Single-quote dialects; pgsql in both engines."""
    if request.param == "pg-cython":
        monkeypatch.setattr(pgsql, "HAS_RUST", False)
    return {
        "pg-rust": pgSQLParser,
        "pg-cython": pgSQLParser,
        "sql": SQLParser,
        "cql": CQLParser,
    }[request.param]


def _is_cql(parser_cls) -> bool:
    return parser_cls is CQLParser


async def test_config_key_is_literal(sq_parser) -> None:  # AC1
    assert "'DBPWD'" in await where(sq_parser, {"username": "DBPWD"})
    assert "'12345678'" not in await where(sq_parser, {"username": "DBPWD"})


async def test_function_name_is_literal(sq_parser) -> None:  # AC2
    assert "'midnight'" in await where(sq_parser, {"created": "midnight"})


async def test_parenthesis_is_literal(sq_parser) -> None:  # AC3
    assert "'pg_sleep(5)'" in await where(sq_parser, {"name": "pg_sleep(5)"})
    if not _is_cql(sq_parser):
        out = await where(sq_parser, {"amount": {">": "x) OR (1=1"}})
        assert "amount > 'x) OR (1=1'" in out


async def test_apostrophe_roundtrip(sq_parser) -> None:  # AC4
    assert "'O''Brien'" in await where(sq_parser, {"name": "O'Brien"})
    assert "'O''''Brien'" not in await where(sq_parser, {"name": "O'Brien"})
    assert "'OBrien'" not in await where(sq_parser, {"name": "O'Brien"})
    assert "IN ('O''Brien','x')" in await where(sq_parser, {"name": ["O'Brien", "x"]})
    if not _is_cql(sq_parser):
        out = await where(sq_parser, {"name": {">": "O'Brien"}})
        assert "name > 'O''Brien'" in out
        neg = await where(sq_parser, {"name": "!O'Brien"})
        assert "!= 'O''Brien'" in neg  # a real negation, not equality with "!O'Brien"
        assert "O''''Brien" not in neg
        assert "'!" not in neg


async def test_bigquery_renders() -> None:  # AC3/AC4 BigQuery
    assert await where(BigQueryParser, {"name": "O'Brien"}) == 'name="O\'Brien"'
    assert (
        await where(BigQueryParser, {"amount": {">": "x) OR (1=1"}})
        == 'amount > "x) OR (1=1"'
    )


@pytest.mark.parametrize("raw", ["O'Brien", "plain", "it''s", "50%", "x) OR (1=1"])
def test_rust_cython_quote_parity(raw: str) -> None:  # AC4 both engines
    assert rs.quote_string(raw, True) == Entity.quoteString(raw)
    assert rs.quote_string(Entity.quoteString(raw), True) == Entity.quoteString(raw)


@pytest.mark.parametrize("parser_cls", [pgSQLParser, SQLParser, CQLParser])
async def test_negation_of_hostile_value_is_one_literal(parser_cls) -> None:
    """`!` + anything (spaces, quotes, parens) stays a single quoted operand."""
    out = await where(parser_cls, {"name": "!x' OR 1=1 --"})
    assert "!= 'x'' OR 1=1 --'" in out


async def test_generic_sql_doubles_backslash() -> None:  # MySQL-safe policy (sql builder)
    out = await where(SQLParser, {"name": "\\' OR 1=1 -- "})
    assert out == "name='\\\\'' OR 1=1 -- '"


async def test_pgsql_keeps_backslash_literal() -> None:  # standard_conforming_strings
    out = await where(pgSQLParser, {"name": "a\\b"})
    assert out == "name='a\\b'"


async def test_bigquery_backslash_cannot_close_literal() -> None:
    out = await where(BigQueryParser, {"name": '\\" OR 1=1 --'})
    assert out == 'name="\\\\\\" OR 1=1 --"'


HOSTILE = [
    "x' OR 1=1 --",
    '"x\' OR 1=1 --"',
    "\\' OR 1=1 -- ",
    "'",
    "''",
    "'a'b'",
    "it''s",
    "50%",
    "O'Brien",
]


@pytest.mark.parametrize("raw", HOSTILE)
def test_is_valid_never_returns_unescaped_quote(raw: str) -> None:
    """Cython is_valid output, when quoted, is a well-formed literal."""
    from querysource.types.validators import is_valid, unquote_literal

    out = is_valid("k", raw)
    if isinstance(out, str) and out.startswith("'"):
        assert unquote_literal(out) is not None, (raw, out)


@pytest.mark.parametrize("raw", HOSTILE)
def test_cython_rust_is_valid_parity(raw: str) -> None:
    from querysource.types.validators import is_valid

    assert rs.is_valid("k", raw, None, False) == is_valid("k", raw), raw
