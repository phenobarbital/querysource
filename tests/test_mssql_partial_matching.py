"""FEAT-180: SQL Server partial-matching operators (Rust and Cython paths)."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import sqlserver as mssqlmod
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond}"
_FAMILIES = [
    ("startswith", "andre", "'andre%'"),
    ("endswith", "andre", "'%andre'"),
    ("contains", "andre", "'%andre%'"),
]
CORPUS = [
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),
    ({"n": {"not_like": "an_re%"}}, "n NOT LIKE 'an_re%'"),
    ({"n": {"ilike": "an_re%"}}, "LOWER(n) LIKE LOWER('an_re%')"),
    ({"n": {"not_ilike": "an_re%"}}, "LOWER(n) NOT LIKE LOWER('an_re%')"),
]
for _name, _val, _lit in _FAMILIES:
    CORPUS += [
        ({"n": {_name: _val}}, f"n LIKE {_lit} ESCAPE '!'"),
        ({"n": {f"not_{_name}": _val}}, f"n NOT LIKE {_lit} ESCAPE '!'"),
        ({"n": {f"i{_name}": _val}}, f"LOWER(n) LIKE LOWER({_lit}) ESCAPE '!'"),
        ({"n": {f"not_i{_name}": _val}}, f"LOWER(n) NOT LIKE LOWER({_lit}) ESCAPE '!'"),
    ]
CORPUS += [
    ({"n": {"startswith": "a[b"}}, "n LIKE 'a![b%' ESCAPE '!'"),
    ({"n": {"icontains": "o'brien"}}, "LOWER(n) LIKE LOWER('%o''brien%') ESCAPE '!'"),
    ({"n": {"contains": "a\\b"}}, "n LIKE '%a\\b%' ESCAPE '!'"),
]


def _rust_supports() -> bool:
    """Return whether the installed Rust extension includes the partial-matching twin."""
    if not mssqlmod.HAS_RUST:
        return False
    try:
        out = mssqlmod._rs.mssql_filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})
    except Exception:  # pylint: disable=W0718
        return False
    return "n LIKE 'andre%' ESCAPE '!'" in out


RUST_AVAILABLE = _rust_supports()
PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(not RUST_AVAILABLE, reason="stale _qs_parsers (TASK-851)"),
    ),
    "cython",
]


def _make_parser(filter_: dict[str, Any]) -> msSQLParser:
    parser = msSQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict[str, Any]) -> str:
    if path == "rust":
        return mssqlmod._rs.mssql_filter_conditions(SQL, filter_, {})
    return await _make_parser(filter_)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1].strip()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_mssql_rendering(path: str, filter_: dict[str, Any], expected: str) -> None:
    """All LIKE-family operators render their SQL Server form."""
    assert _where_body(await _render(path, filter_)) == expected


@pytest.mark.parametrize("operator", ["regex", "iregex", "not_regex", "not_iregex"])
async def test_mssql_regex_raises(operator: str) -> None:
    """SQL Server rejects every regex operator."""
    with pytest.raises(ParserError):
        await _render("cython", {"n": {operator: "^andre"}})


async def test_mssql_other_dicts_unchanged() -> None:
    """Non-partial-matching dicts keep the pre-existing fallthrough rendering."""
    from querysource.types.validators import Entity

    filter_ = {"n": {">=": 5}}
    expected = f"n = {Entity.escapeString(filter_['n'])}"
    assert _where_body(await _render("cython", filter_)) == expected


async def test_mssql_rust_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected Rust failure is logged at WARNING before the Cython fallback renders."""
    class RustFailure:
        """Stand in for a Rust extension that fails unexpectedly."""

        @staticmethod
        def mssql_filter_conditions(*args: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(mssqlmod, "_rs", RustFailure())
    monkeypatch.setattr(mssqlmod, "HAS_RUST", True)
    with caplog.at_level(logging.WARNING, logger="QS.Parser.msSQLParser"):
        rendered = await _make_parser({"n": {"startswith": "andre"}}).filter_conditions(SQL)
    assert _where_body(rendered) == "n LIKE 'andre%' ESCAPE '!'"
    assert "Rust mssql_filter_conditions failed, falling back to Cython: boom" in caplog.text
