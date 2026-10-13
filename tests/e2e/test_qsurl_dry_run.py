"""qsurl end-to-end on the dry-run harness: split -> QS (real dialect parsers, FEAT-152).

Every test builds the IR dict directly -- the stable contract ``parse()``
produces (spec §2 Data Models) -- instead of calling ``parse()``, so the
composition this module exists to prove (the real ``translate.split``,
TASK-771, against the real PostgreSQL/Cassandra dialect parsers via a real
``QS.dry_run()`` and a real ``residual.apply``) is exercised independently of
the grammar, which has its own tests.

The ILIKE pushdown tests force ``monkeypatch.setattr(pgsql, "HAS_RUST", False)``
to pin the Cython ``pgSQLParser.filter_conditions`` path (TASK-769), so that
builder stays covered even when the Rust extension is installed and would
otherwise be tried first.
"""
from __future__ import annotations

import pytest
import sqlglot

import querysource.parsers.pgsql as pgsql
from querysource.providers.cassandra import cassandraProvider
from querysource.providers.pg import pgProvider
from querysource.qsurl import QSUrlError
from querysource.qsurl.translate import split
from querysource.queries.qs import QS

PG_PROVIDER = "pg"  # confirmed (scratch probe): resolves to pgProvider via
# QueryConnection.get_provider -> load_provider("pg") -> querysource.providers.pg.pgProvider


@pytest.fixture(autouse=True)
def _catalog(definitions) -> None:
    definitions.add(
        query_slug="hisense_stores",
        provider=PG_PROVIDER,
        query_raw="SELECT {fields} FROM public.stores {where_cond}",
    )


def _ir(**over) -> dict:
    ir = {
        "slug": "hisense_stores",
        "fields": [],
        "filter": None,
        "sort": [],
        "limit": None,
        "offset": None,
        "distinct": False,
        "requires": [],
    }
    ir.update(over)
    return ir


async def _render(ir: dict, capabilities: frozenset[str], residual_scan: bool) -> tuple[str, object]:
    conditions, plan = split(ir, capabilities, residual_scan=residual_scan)
    sql, error = await QS(slug=ir["slug"], conditions=conditions, residual=plan).dry_run()
    assert error is None
    assert len(sqlglot.parse(sql, read="postgres")) == 1
    return sql, plan


async def test_pushdown_only_pg_sql():
    ir = _ir(
        fields=["store_id", "name"],
        filter={
            "and": [
                {"column": "state_code", "expression": "==", "value": "CA"},
                {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"},
            ]
        },
        sort=[{"column": "name", "order": "desc"}],
        limit=50,
        requires=["select", "filter", "sort", "limit"],
    )
    sql, plan = await _render(ir, pgProvider.capabilities, pgProvider.residual_scan)
    assert plan.is_empty()
    assert sql == (
        "SELECT store_id, name FROM public.stores  WHERE state_code='CA'"
        " AND opened >= '2024-01-01' ORDER BY name DESC LIMIT 50"
    )


async def test_text_match_pushdown_pg(monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    ir = _ir(
        filter={"and": [{"column": "city", "expression": "contains", "value": "san"}]},
        requires=["select", "filter", "text_match"],
    )
    sql, plan = await _render(ir, pgProvider.capabilities, pgProvider.residual_scan)
    assert plan.is_empty()
    assert "city LIKE '%san%'" in sql


async def test_text_match_pushdown_pg_with_internal_quote(monkeypatch):
    """FEAT-152 code review: a genuine internal apostrophe must survive the
    real is_valid()-then-pg_literal() round trip through set_options()/
    set_where() (abstract.pyx) without double-quoting or under-escaping —
    the earlier ``test_ilike_strips_prequoted_value`` unit test only proved
    the wrapper-stripping mechanics with a quote-free value; this proves the
    full pipeline against a value that itself needs SQL-literal escaping.
    """
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    ir = _ir(
        filter={"and": [{"column": "name", "expression": "contains", "value": "o'brien"}]},
        requires=["select", "filter", "text_match"],
    )
    sql, plan = await _render(ir, pgProvider.capabilities, pgProvider.residual_scan)
    assert plan.is_empty()
    assert "name LIKE '%o''brien%'" in sql


async def test_text_match_icontains_pg(monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    ir = _ir(
        filter={"and": [{"column": "city", "expression": "icontains", "value": "san"}]},
        requires=["select", "filter", "text_match"],
    )
    sql, plan = await _render(ir, pgProvider.capabilities, pgProvider.residual_scan)
    assert plan.is_empty()
    assert "city ILIKE '%san%'" in sql


async def test_or_filter_is_residual_on_pg():
    ir = _ir(
        filter={
            "or": [
                {"column": "city", "expression": "==", "value": "San Francisco"},
                {"column": "city", "expression": "==", "value": "Austin"},
            ]
        },
        requires=["select", "filter", "or"],
    )
    conditions, plan = split(ir, pgProvider.capabilities, residual_scan=pgProvider.residual_scan)
    assert "filter" not in conditions
    assert not plan.is_empty()
    sql, error = await QS(slug=ir["slug"], conditions=conditions).dry_run()
    assert error is None
    assert "WHERE" not in sql

    rows = [
        {"city": "San Francisco"},
        {"city": "Austin"},
        {"city": "Houston"},
    ]
    result = QS(slug=ir["slug"], conditions=conditions, residual=plan)._apply_residual(rows)
    assert result == [{"city": "San Francisco"}, {"city": "Austin"}]


def test_cassandra_residual_only_is_cost():
    ir = _ir(
        filter={"and": [{"column": "city", "expression": "contains", "value": "san"}]},
        requires=["select", "filter", "text_match"],
    )
    with pytest.raises(QSUrlError) as exc:
        split(ir, cassandraProvider.capabilities, residual_scan=cassandraProvider.residual_scan)
    assert exc.value.kind == "cost"
