"""FEAT-168: builders splice only canonical BETWEEN, even without pre-processing.

``AbstractParser._where_element`` validates BETWEEN strings during
``set_options()``. These tests skip that step on purpose: they hand each
builder a raw ``filter`` dict and assert the builder itself refuses anything
outside the canonical grammar (defence in depth, ledger issue:fc81723e95ac).
"""
from __future__ import annotations

from typing import Any

import pytest

from querysource.parsers import bigquery, pgsql, sql, sqlserver
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM public.t {where_cond}"
DIALECTS = [
    pytest.param(SQLParser, sql, id="sql"),
    pytest.param(msSQLParser, sqlserver, id="sqlserver"),
    pytest.param(BigQueryParser, bigquery, id="bigquery"),
    pytest.param(pgSQLParser, pgsql, id="pgsql"),
]
PATHS = ["rust", "cython"]
HOSTILE = [
    "BETWEEN 1 AND 2 OR 1=1",
    "NOT BETWEEN 1 AND 2) OR (1=1",
    "BETWEEN 1 AND 2; DROP TABLE t",
    "BETWEEN x AND y",
    "BETWEEN 'a\\' AND 'b'",
]


async def build(
    path: str, parser_cls: type, module: Any, filter_: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run only the builder: set ``parser.filter`` directly, skipping ``set_options()``."""
    if path == "rust" and not module.HAS_RUST:
        pytest.skip("Rust extension not available")
    if path == "cython":
        monkeypatch.setattr(module, "HAS_RUST", False)
    parser = parser_cls(definition=None, conditions={}, query=SQL)
    parser.filter = dict(filter_)
    return await parser.filter_conditions(SQL)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("parser_cls", "module"), DIALECTS)
@pytest.mark.parametrize("value", HOSTILE)
async def test_builder_drops_noncanonical_between(
    path: str, parser_cls: type, module: Any, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BETWEEN-prefixed value outside the canonical grammar never reaches the SQL."""
    rendered = await build(path, parser_cls, module, {"amount": value, "status": "active"}, monkeypatch)
    assert "BETWEEN" not in rendered
    assert "1=1" not in rendered
    assert "DROP" not in rendered
    assert "status" in rendered  # the other filter entries are still rendered


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("parser_cls", "module"), DIALECTS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BETWEEN 100 AND 500", "(amount BETWEEN 100 AND 500)"),
        ("NOT BETWEEN '2025-01-01' AND '2025-12-31'", "(amount NOT BETWEEN '2025-01-01' AND '2025-12-31')"),
    ],
)
async def test_builder_keeps_canonical_between(
    path: str, parser_cls: type, module: Any, value: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canonical clause is spliced unchanged on every dialect and path."""
    rendered = await build(path, parser_cls, module, {"amount": value}, monkeypatch)
    assert expected in rendered
