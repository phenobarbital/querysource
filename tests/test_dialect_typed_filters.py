"""FEAT-165: typed cond_definition columns filtered explicitly (Cython path)."""
from __future__ import annotations

import logging as _stdlib_logging
import sys
import types

try:
    import navconfig.logging  # noqa: F401
except Exception:
    _fake = types.ModuleType("navconfig.logging")
    _fake.logging = _stdlib_logging
    sys.modules["navconfig.logging"] = _fake

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM public.t {where_cond}"


class _Connection:
    """Minimal async context manager for parser preprocessing tests."""

    async def __aenter__(self) -> None:
        """Return the no-op connection."""
        return None

    async def __aexit__(self, *args: object) -> None:
        """Close the no-op connection."""
        return None


class _Redis:
    """Minimal Redis facade that supplies the no-op test connection."""

    async def connection(self) -> _Connection:
        """Return a no-op asynchronous context manager."""
        return _Connection()


async def prepare(conditions: dict, monkeypatch, query: str = SQL) -> pgSQLParser:
    """Run the Cython parser preprocessing pipeline."""
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    parser = pgSQLParser(definition=None, conditions=QueryObject(**conditions), query=query)
    parser.logger.notice = parser.logger.info
    parser._redis = _Redis()
    await parser.set_options()
    return parser


async def render(conditions: dict, monkeypatch, query: str = SQL) -> str:
    """Render a query through the Cython parser pipeline."""
    parser = await prepare(conditions, monkeypatch, query)
    return await parser.build_query()


async def test_typed_array_scalar(monkeypatch):
    """An explicit scalar array filter reaches the PostgreSQL builder raw."""
    sql = await render({"filter": {"tags": "vip"}, "cond_definition": {"tags": "array"}}, monkeypatch)
    assert "'vip'::character varying = ANY(tags)" in sql


async def test_typed_array_list_uses_containment(monkeypatch):
    """An explicit array list stays raw for PostgreSQL builder handling."""
    parser = await prepare({"filter": {"tags": ["vip", "staff"]}, "cond_definition": {"tags": "array"}}, monkeypatch)
    assert parser.filter == {"tags": ["vip", "staff"]}


async def test_typed_ranges_and_date_list(monkeypatch):
    """Range formats and date lists remain raw for the builder."""
    parser = await prepare(
        {
            "filter": {
                "score": "5",
                "period": "2025-01-01",
                "created": ["2025-01-01", "2025-12-31"],
            },
            "cond_definition": {"score": "numrange", "period": "daterange", "created": "date"},
        },
        monkeypatch,
    )
    assert parser.filter == {
        "score": "5",
        "period": "2025-01-01",
        "created": ["2025-01-01", "2025-12-31"],
    }


async def test_negated_date_list_uses_not_between(monkeypatch):
    """A suffixed date key remains raw for builder-side NOT BETWEEN handling."""
    parser = await prepare(
        {"filter": {"created!": ["2025-01-01", "2025-12-31"]}, "cond_definition": {"created": "date"}},
        monkeypatch,
    )
    assert parser.filter == {"created!": ["2025-01-01", "2025-12-31"]}


async def test_placeholder_typed_filter_keeps_existing_substitution(monkeypatch):
    """A typed key used by the template remains a placeholder."""
    sql = await render(
        {"filter": {"since": "2025-01-01"}, "cond_definition": {"since": "date"}},
        monkeypatch,
        "SELECT * FROM public.t WHERE created_at >= {since} {and_cond}",
    )
    assert "created_at >= '2025-01-01'" in sql


async def test_flat_typed_key_still_uses_placeholder_routing(monkeypatch):
    """A declared non-filter key keeps the existing placeholder routing."""
    sql = await render(
        {"tags": ["vip"], "cond_definition": {"tags": "array"}},
        monkeypatch,
        "SELECT * FROM public.t WHERE tags = {tags} {and_cond}",
    )
    assert "tags =" in sql


async def test_flat_array_string_does_not_raise(monkeypatch):
    """A plain declared array string is safely dropped by preprocessing."""
    sql = await render({"tags": "vip", "cond_definition": {"tags": "array"}}, monkeypatch)
    assert sql == "SELECT * FROM public.t "
