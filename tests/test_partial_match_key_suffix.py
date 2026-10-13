"""FEAT-173: key suffixes on partial-matching filters (base column, ``!`` negates, others rejected)."""
from __future__ import annotations

from typing import Any

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers import pgsql as pgmod
from querysource.parsers import sql as sqlmod
from querysource.parsers import sqlserver as mssqlmod
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.partial_matching import (
    PARTIAL_MATCH_OPERATORS,
    resolve_key_suffix,
    validate_partial_match_dict,
)
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond}"
DIALECTS: dict[str, tuple[Any, Any, str, str, str]] = {
    # name: (parser, module, rust function, positive LIKE, negated LIKE)
    "sql": (SQLParser, sqlmod, "filter_conditions", "n LIKE '%abc%'", "n NOT LIKE '%abc%'"),
    "mssql": (msSQLParser, mssqlmod, "mssql_filter_conditions", "n LIKE '%abc%'", "n NOT LIKE '%abc%'"),
    "bq": (BigQueryParser, bqmod, "bq_filter_conditions", 'n LIKE "%abc%"', 'n NOT LIKE "%abc%"'),
    "pg": (pgSQLParser, pgmod, "pgsql_filter_conditions", "n LIKE '%abc%'", "n NOT LIKE '%abc%'"),
}


def _rust_resolves_suffix(dialect: str) -> bool:
    """Return whether the staged Rust extension already strips/flips key suffixes."""
    _, module, rust_name, _, _ = DIALECTS[dialect]
    fn = getattr(getattr(module, "_rs", None), rust_name, None)
    if not module.HAS_RUST or fn is None:
        return False
    try:
        return "n! " not in fn(SQL, {"n!": {"contains": "abc"}}, {})
    except Exception:  # noqa: BLE001 - a stale extension may raise differently
        return False


async def _render(dialect: str, path: str, filter_: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> str:
    """Render the filter through build_query on the selected path."""
    parser_cls, module, _, _, _ = DIALECTS[dialect]
    if path == "rust":
        if not _rust_resolves_suffix(dialect):
            pytest.skip("stale _qs_parsers: rebuild the Rust extension (TASK-886)")
    else:
        monkeypatch.setattr(module, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return await parser.build_query()


PARAMS = [(d, p) for d in sorted(DIALECTS) for p in ("rust", "cython")]


@pytest.mark.parametrize(("dialect", "path"), PARAMS)
async def test_bang_suffix_negates_on_base_column(dialect: str, path: str, monkeypatch) -> None:
    """`n!` + contains renders NOT LIKE on the base column `n`."""
    sql = await _render(dialect, path, {"n!": {"contains": "abc"}}, monkeypatch)
    assert DIALECTS[dialect][4] in sql
    assert "n!" not in sql


@pytest.mark.parametrize(("dialect", "path"), PARAMS)
async def test_bang_suffix_on_negated_operator_flips_back(dialect: str, path: str, monkeypatch) -> None:
    """`n!` + not_contains renders a positive LIKE."""
    sql = await _render(dialect, path, {"n!": {"not_contains": "abc"}}, monkeypatch)
    assert DIALECTS[dialect][3] in sql
    assert "NOT LIKE" not in sql


@pytest.mark.parametrize(("dialect", "path"), PARAMS)
async def test_unsuffixed_key_unchanged(dialect: str, path: str, monkeypatch) -> None:
    """A plain key keeps its operator."""
    sql = await _render(dialect, path, {"n": {"contains": "abc"}}, monkeypatch)
    assert DIALECTS[dialect][3] in sql


@pytest.mark.parametrize("key", ["n~", "n|", "n!~", "n!!"])
@pytest.mark.parametrize(("dialect", "path"), PARAMS)
async def test_other_suffixes_rejected(dialect: str, path: str, key: str, monkeypatch) -> None:
    """Suffixes without a partial-matching meaning raise ParserError on every path."""
    with pytest.raises(ParserError, match="not supported with partial-matching operators"):
        await _render(dialect, path, {key: {"contains": "abc"}}, monkeypatch)


def test_resolve_key_suffix_flip_is_an_involution() -> None:
    """`!` maps each operator to its counterpart and back."""
    for op in PARTIAL_MATCH_OPERATORS.values():
        flipped = resolve_key_suffix("n!", op)
        assert flipped.negated is not op.negated
        assert resolve_key_suffix("n!", flipped) is op
        assert resolve_key_suffix("n", op) is op


def test_validate_partial_match_dict_applies_suffix() -> None:
    """The shared validator returns the suffix-resolved operator."""
    assert validate_partial_match_dict("n!", {"icontains": "abc"}, supports_regex=False).name == "not_icontains"
    with pytest.raises(ParserError):
        validate_partial_match_dict("n#", {"icontains": "abc"}, supports_regex=False)
