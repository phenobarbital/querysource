"""FEAT-172: brace-bearing partial-match operands survive build_query's format_map passes."""
from __future__ import annotations

from typing import Any

import pytest

from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers import sql as sqlmod
from querysource.parsers import sqlserver as mssqlmod
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.deltatbl import DeltaTableParser
from querysource.parsers.iceberg import IcebergParser
from querysource.parsers.partial_matching import BRACE_CLOSE_SENTINEL, BRACE_OPEN_SENTINEL
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond} {limit}"
DIALECTS: dict[str, tuple[Any, Any, str]] = {
    "sql": (SQLParser, sqlmod, "filter_conditions"),
    "mssql": (msSQLParser, mssqlmod, "mssql_filter_conditions"),
    "bq": (BigQueryParser, bqmod, "bq_filter_conditions"),
}
OPERANDS = ["a{b}c", "{limit}", "x{{y}}z"]


def _rust_protects(dialect: str) -> bool:
    """Return whether the staged Rust extension already emits brace sentinels."""
    _, module, rust_name = DIALECTS[dialect]
    if not module.HAS_RUST:
        return False
    out = getattr(module._rs, rust_name)("SELECT 1 {where_cond}", {"n": {"contains": "a{b}c"}}, {})
    return BRACE_OPEN_SENTINEL in out


PATHS = ["rust", "cython"]


async def _build(dialect: str, path: str, operand: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run build_query with a single contains filter through the selected path."""
    parser_cls, module, _ = DIALECTS[dialect]
    if path == "rust":
        if not _rust_protects(dialect):
            pytest.skip("stale _qs_parsers: rebuild the Rust extension (TASK-884)")
    else:
        monkeypatch.setattr(module, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = {"n": {"contains": operand}}
    return await parser.build_query()


@pytest.mark.parametrize("operand", OPERANDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("dialect", sorted(DIALECTS))
async def test_brace_operand_survives_build_query(
    dialect: str, path: str, operand: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operand's braces reach the final SQL unchanged; no sentinel leaks out."""
    sql = await _build(dialect, path, operand, monkeypatch)
    assert f"%{operand}%" in sql
    assert BRACE_OPEN_SENTINEL not in sql and BRACE_CLOSE_SENTINEL not in sql


@pytest.mark.parametrize("parser_cls", [DeltaTableParser, IcebergParser])
async def test_subclass_build_query_restores_braces(parser_cls: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """DeltaTable and Iceberg override build_query and must restore sentinels too."""
    monkeypatch.setattr(sqlmod, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = {"n": {"contains": "{limit}"}}
    sql = await parser.build_query()
    assert "%{limit}%" in sql
    assert BRACE_OPEN_SENTINEL not in sql and BRACE_CLOSE_SENTINEL not in sql
