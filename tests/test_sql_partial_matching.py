"""FEAT-180: generic SQL partial-matching operators (Rust and Cython paths)."""
from __future__ import annotations

import logging as stdlib_logging
import sys
import types
from typing import Any

import pytest

# ``navconfig.logging`` probes Logstash during import. Sandboxed runners deny
# sockets, so retain a minimal fallback while using the real module elsewhere.
try:
    import navconfig.logging  # noqa: F401
except Exception:
    fake_navconfig_logging = types.ModuleType("navconfig.logging")
    fake_navconfig_logging.logging = stdlib_logging
    sys.modules["navconfig.logging"] = fake_navconfig_logging

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import sql as sqlmod
from querysource.parsers.sql import SQLParser

SQL = "SELECT * FROM t {where_cond}"
CORPUS = [
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),
    ({"n": {"not_like": "an_re%"}}, "n NOT LIKE 'an_re%'"),
    ({"n": {"ilike": "an_re%"}}, "LOWER(n) LIKE LOWER('an_re%')"),
    ({"n": {"not_ilike": "an_re%"}}, "LOWER(n) NOT LIKE LOWER('an_re%')"),
    ({"n": {"startswith": "andre"}}, "n LIKE 'andre%' ESCAPE '!'"),
    ({"n": {"not_startswith": "andre"}}, "n NOT LIKE 'andre%' ESCAPE '!'"),
    ({"n": {"istartswith": "andre"}}, "LOWER(n) LIKE LOWER('andre%') ESCAPE '!'"),
    ({"n": {"not_istartswith": "andre"}}, "LOWER(n) NOT LIKE LOWER('andre%') ESCAPE '!'"),
    ({"n": {"endswith": "andre"}}, "n LIKE '%andre' ESCAPE '!'"),
    ({"n": {"not_endswith": "andre"}}, "n NOT LIKE '%andre' ESCAPE '!'"),
    ({"n": {"iendswith": "andre"}}, "LOWER(n) LIKE LOWER('%andre') ESCAPE '!'"),
    ({"n": {"not_iendswith": "andre"}}, "LOWER(n) NOT LIKE LOWER('%andre') ESCAPE '!'"),
    ({"n": {"contains": "andre"}}, "n LIKE '%andre%' ESCAPE '!'"),
    ({"n": {"not_contains": "andre"}}, "n NOT LIKE '%andre%' ESCAPE '!'"),
    ({"n": {"icontains": "andre"}}, "LOWER(n) LIKE LOWER('%andre%') ESCAPE '!'"),
    ({"n": {"not_icontains": "andre"}}, "LOWER(n) NOT LIKE LOWER('%andre%') ESCAPE '!'"),
]


def _rust_supports() -> bool:
    """Return whether the installed Rust extension includes this task's twin."""
    if not sqlmod.HAS_RUST:
        return False
    return "n LIKE 'andre%' ESCAPE '!'" in sqlmod._rs.filter_conditions(
        SQL, {"n": {"startswith": "andre"}}, {}
    )


RUST_AVAILABLE = _rust_supports()
PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(not RUST_AVAILABLE, reason="stale _qs_parsers (TASK-851)"),
    ),
    "cython",
]


def _make_parser(filter_: dict[str, Any]) -> SQLParser:
    """Build a SQL parser with its filter state assigned directly."""
    parser = SQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> str:
    """Render one filter through the requested implementation path."""
    if path == "rust":
        return sqlmod._rs.filter_conditions(SQL, filter_, {})
    monkeypatch.setattr(sqlmod, "HAS_RUST", False)
    return await _make_parser(filter_).filter_conditions(SQL)


def _where_body(sql: str) -> str | None:
    """Return text following the WHERE clause, when present."""
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1].strip()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_sql_rendering(
    path: str, filter_: dict[str, Any], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All LIKE-family operators render their generic SQL form."""
    assert _where_body(await _render(path, filter_, monkeypatch)) == expected


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "filter_,expected",
    [
        ({"n": {"startswith": "o'brien"}}, "n LIKE 'o''brien%' ESCAPE '!'"),
        ({"n": {"contains": "50%"}}, "n LIKE '%50!%%' ESCAPE '!'"),
        ({"n": {"contains": "a_b"}}, "n LIKE '%a!_b%' ESCAPE '!'"),
        ({"n": {"endswith": r"a\b"}}, r"n LIKE '%a\\b' ESCAPE '!'"),
        ({"n": {"startswith": "a!b"}}, "n LIKE 'a!!b%' ESCAPE '!'"),
        ({"n": {"startswith": "a[b"}}, "n LIKE 'a![b%' ESCAPE '!'"),
    ],
)
async def test_sql_escaping(
    path: str, filter_: dict[str, Any], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escaped operator families protect wildcard and literal-special operands."""
    assert _where_body(await _render(path, filter_, monkeypatch)) == expected


@pytest.mark.parametrize("operator", ["regex", "iregex", "not_regex", "not_iregex"])
async def test_sql_regex_raises(operator: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic SQL rejects every regex operator through the Cython fallback."""
    with pytest.raises(ParserError):
        await _render("cython", {"n": {operator: "^andre"}}, monkeypatch)


async def test_sql_rust_error_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Rust validation error falls through to Cython's ``ParserError`` validation."""
    class RustFailure:
        """Stand in for a Rust extension that rejects an invalid operand."""

        @staticmethod
        def filter_conditions(*args: object) -> str:
            """Mirror Rust's invalid-operand failure."""
            raise ValueError("invalid partial-matching operand")

    monkeypatch.setattr(sqlmod, "_rs", RustFailure())
    monkeypatch.setattr(sqlmod, "HAS_RUST", True)
    with pytest.raises(ParserError, match="requires a string operand"):
        await _make_parser({"n": {"startswith": 1}}).filter_conditions(SQL)
