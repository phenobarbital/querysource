"""End-to-end dry-run tests for ``MultiQS``: pipeline syntax and parsing, no database.

``MultiQS.query()`` runs for real — payload/slug decoding, source
normalization, preflight, threaded dispatch and the operator/transform
pipeline (Join, GroupBy, Filter, Transform). Only the leaf execution is
replaced: the ``LocalExecutor`` builds each child's ``QueryObject`` and
provider exactly as in production (so the parser renders its SQL), records
the rendered statement and enqueues a canned DataFrame instead of querying.
"""
from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest
import sqlglot
from aiohttp import web

import querysource.queries.multi as multi_module
from querysource import conf
from querysource.exceptions import DriverError, QueryException
from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources.executors import LocalExecutor
from querysource.queries.obj import QueryObject
from querysource.tenants import QueryStore

_STORES = pd.DataFrame({"store_id": [1, 2, 3], "region": ["EMEA", "EMEA", "APAC"]})
_SALES = pd.DataFrame({"store_id": [1, 1, 2, 3], "amount": [10.0, 20.0, 5.0, 7.0]})
# Canned result per child alias (a single-query slug is keyed by its slug).
FRAMES: dict[str, pd.DataFrame] = {"stores": _STORES, "sales": _SALES, "stores_q": _STORES}


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Dry-run every child query; return the SQL rendered per child alias."""
    captured: dict[str, str] = {}

    async def _dry_execute(
        self: LocalExecutor,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
        *,
        store: QueryStore | None = None,
    ) -> None:
        obj = QueryObject(
            name,
            query,
            queue=queue,
            request=request,
            loop=asyncio.get_running_loop(),
            tenant=store.schema if store else None,
        )
        await obj.build_provider()
        if obj._type == "slug":
            sql, error = await obj._qs.dry_run()
            assert error is None
        else:
            sql = obj._qs.query
        captured[name] = sql
        await queue.put({name: FRAMES[name].copy()})

    monkeypatch.setattr(LocalExecutor, "execute", _dry_execute)
    return captured


@pytest.fixture(autouse=True)
def _catalog(definitions) -> None:
    definitions.add(
        query_slug="stores_q",
        provider="db",
        query_raw="SELECT {fields} FROM public.stores {where_cond}",
        fields=["store_id", "region"],
    )
    definitions.add(
        query_slug="sales_q",
        provider="db",
        query_raw="SELECT store_id, amount FROM public.sales {where_cond}",
    )


def assert_valid_sql(statements: dict[str, str]) -> None:
    """Assert every rendered child statement is one valid PostgreSQL statement."""
    for alias, sql in statements.items():
        parsed = sqlglot.parse(sql, read="postgres")
        assert len(parsed) == 1 and parsed[0] is not None, f"{alias}: {sql!r}"


class TestInlinePipeline:
    """``MultiQS(query={...})`` with child slugs and a post-processing pipeline."""

    async def test_join_and_groupby(self, rendered: dict[str, str]) -> None:
        mqs = MultiQS(query={
            "queries": {
                "stores": {"slug": "stores_q", "region": ["EMEA", "APAC"]},
                "sales": {"slug": "sales_q", "where_cond": {"amount": {">": 1}}},
            },
            "Join": [{"type": "inner", "left": "stores", "right": "sales", "using": ["store_id"]}],
            "GroupBy": {"by": ["region"], "columns": {"amount": "sum"}},
        })
        result, options = await mqs.query()

        assert rendered == {
            "stores": "SELECT store_id, region FROM public.stores  WHERE region IN ('EMEA','APAC')",
            "sales": "SELECT store_id, amount FROM public.sales  WHERE amount > '1'",
        }
        assert_valid_sql(rendered)
        totals = dict(zip(result["region"], result["amount_sum"], strict=True))
        assert totals == {"EMEA": 35.0, "APAC": 7.0}
        assert "Join" not in options

    async def test_parent_conditions_reach_children(self, rendered: dict[str, str]) -> None:
        mqs = MultiQS(
            query={"queries": {"stores": {"slug": "stores_q"}}},
            conditions={"stores": {"region": "EMEA"}},
        )
        await mqs.query()
        assert rendered == {
            "stores": "SELECT store_id, region FROM public.stores  WHERE region='EMEA'",
        }

    async def test_raw_inline_child_is_passed_through(self, rendered: dict[str, str]) -> None:
        mqs = MultiQS(query={"queries": {"stores": {"query": "SELECT 1", "driver": "pg"}}})
        result, _ = await mqs.query()
        assert rendered == {"stores": "SELECT 1"}
        assert list(result.columns) == ["store_id", "region"]


class TestSlugPipeline:
    """``MultiQS(slug=...)``: the pipeline JSON lives in the stored ``query_raw``."""

    async def test_stored_pipeline(self, definitions, rendered: dict[str, str]) -> None:
        definitions.add(
            query_slug="sales_pipeline",
            provider="db",
            query_raw=json.dumps({
                "queries": {"stores": {"slug": "stores_q"}, "sales": {"slug": "sales_q"}},
                "Join": [{"type": "left", "left": "stores", "right": "sales", "using": ["store_id"]}],
                "Filter": {"conditions": [{"column": "amount", "expression": ">", "value": 6}]},
                "Transform": [{"tOrder": {"columns": ["amount"], "ascending": False}}],
            }),
        )
        result, _ = await MultiQS(slug="sales_pipeline").query()

        assert rendered == {
            "stores": "SELECT store_id, region FROM public.stores ",
            "sales": "SELECT store_id, amount FROM public.sales ",
        }
        assert_valid_sql(rendered)
        assert list(result["amount"]) == [20.0, 10.0, 7.0]
        assert definitions.requested[0] == "sales_pipeline"

    async def test_single_query_slug_falls_back(self, rendered: dict[str, str]) -> None:
        """A plain-SQL slug is wrapped as a one-child pipeline keyed by its slug."""
        result, _ = await MultiQS(slug="stores_q", conditions={"region": "APAC"}).query()
        assert rendered == {
            "stores_q": "SELECT store_id, region FROM public.stores  WHERE region='APAC'",
        }
        assert len(result) == 3


class TestPayloadValidation:
    """Malformed payloads fail before any child query is dispatched."""

    def test_empty_payload_is_rejected(self) -> None:
        with pytest.raises(DriverError, match="all empty"):
            MultiQS(query={})

    def test_dict_sources_are_normalized(self) -> None:
        mqs = MultiQS(query={"sources": {"sheet": {"type": "SmartSheetSource", "source.file_id": {"file_id": 1}}}})
        assert mqs._sources == [{"SmartSheetSource": {"source": {"file_id": 1}}}]

    async def test_child_without_slug_or_query(self, rendered: dict[str, str]) -> None:
        with pytest.raises(DriverError, match="missing a 'slug' key"):
            await MultiQS(query={"queries": {"x": {"foo": 1}}}).query()
        assert rendered == {}

    async def test_unknown_child_slug(self, rendered: dict[str, str]) -> None:
        with pytest.raises(QueryException, match="Preflight policy check failed"):
            await MultiQS(query={"queries": {"x": {"slug": "nope"}}}).query()
        assert rendered == {}

    async def test_unknown_source_type(self) -> None:
        with pytest.raises(DriverError, match="Unknown source type: 'NotASource'"):
            await MultiQS(query={"sources": {"s": {"type": "NotASource"}}}).query()

    async def test_invalid_remote_worker_port(self, rendered: dict[str, str]) -> None:
        mqs = MultiQS(query={"queries": {"stores": {"slug": "stores_q", "remote": True, "worker": "qw:abc"}}})
        with pytest.raises(DriverError, match="port must be an integer"):
            await mqs.query()
        assert rendered == {}

    async def test_remote_without_worker(self, monkeypatch: pytest.MonkeyPatch, rendered: dict[str, str]) -> None:
        monkeypatch.setattr(multi_module, "QWORKER_HOST", None)
        mqs = MultiQS(query={"queries": {"stores": {"slug": "stores_q", "remote": True}}})
        with pytest.raises(DriverError, match="no worker address"):
            await mqs.query()
        assert rendered == {}

    async def test_too_many_sources(self, monkeypatch: pytest.MonkeyPatch, rendered: dict[str, str]) -> None:
        monkeypatch.setattr(conf, "MULTIQS_MAX_SOURCES_PER_REQUEST", 1)
        mqs = MultiQS(query={"queries": {"stores": {"slug": "stores_q"}, "sales": {"slug": "sales_q"}}})
        with pytest.raises(QueryException, match="Too many MultiQS sources"):
            await mqs.query()
        assert rendered == {}
