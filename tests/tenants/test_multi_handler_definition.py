"""FEAT-151: QueryHandler definition-aware columns and validate-only dry-run."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.handlers.multi import QueryHandler
from querysource.models import QueryModel
from querysource.tenant_errors import TenantError
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore

MULTI_RAW = json.dumps({"queries": {"a": {"slug": "child_a"}, "b": {"slug": "child_b", "tenant": None},
                                    "r": {"query": "SELECT 1"}}, "files": {}})


def _store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(database_namespace="localhost:5432/qs", schema=schema, table="queries",
                      contract="tenant", columns=frozenset({"query_slug"}))


def _loaded(query_raw: str = MULTI_RAW, columns: list | None = None) -> LoadedDefinition:
    runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi",
                         query_raw=query_raw, columns_definition=columns or [])
    return LoadedDefinition(identity=QueryIdentity(store=_store(), slug="parent"), runtime=runtime, revision="r1")


def _make_handler():
    """Construct a QueryHandler instance without invoking __init__."""
    h = QueryHandler.__new__(QueryHandler)
    h.logger = MagicMock()
    h._loop = None
    h._json = MagicMock()
    h._json.dumps = MagicMock(return_value="{}")
    return h


def _make_request(app=None, match_info=None, method="GET", query=None, storage=None):
    """Minimal mock request with a storage dict behind request['key']/.get()."""
    req = MagicMock(spec=web.Request)
    req.app = app or {}
    req.headers = {}
    req.match_info = match_info or {}
    req.method = method
    req.query = query or {}
    store = storage or {}
    req.get = MagicMock(side_effect=lambda key, default=None: store.get(key, default))
    return req


class _FakeRegistry:
    def resolve(self, tenant):
        return _store(tenant or "public")


class _FakeRepo:
    def __init__(self, missing_slugs=None):
        self.missing_slugs = missing_slugs or set()

    async def get(self, identity):
        if identity.slug in self.missing_slugs:
            raise TenantError("not found", error_code="query_not_found")
        return _loaded()


# --- columns() -------------------------------------------------------------

@pytest.mark.asyncio
async def test_columns_head_multi_headers():
    h = _make_handler()
    definition = _loaded(columns=["a", "b"])
    req = _make_request(method="HEAD", storage={"qs_definition": definition})

    resp = await h.columns(req)

    assert resp.status == 204
    assert resp.headers["X-Columns"] == repr(["a", "b"])
    assert resp.headers["X-Slug"] == "parent"
    assert "X-Message" not in resp.headers


@pytest.mark.asyncio
async def test_columns_head_multi_empty_message():
    h = _make_handler()
    definition = _loaded(columns=[])
    req = _make_request(method="HEAD", storage={"qs_definition": definition})

    resp = await h.columns(req)

    assert resp.status == 204
    assert resp.headers["X-Message"] == "No Columns found"


@pytest.mark.asyncio
async def test_columns_patch_multi_list_or_204():
    h = _make_handler()
    definition = _loaded(columns=["a"])
    req = _make_request(method="PATCH", storage={"qs_definition": definition})

    resp = await h.columns(req)
    assert resp.status == 200

    h2 = _make_handler()
    definition_empty = _loaded(columns=[])
    req2 = _make_request(method="PATCH", storage={"qs_definition": definition_empty})
    with pytest.raises(web.HTTPNoContent) as exc_info:
        await h2.columns(req2)
    assert exc_info.value.headers["X-Message"] == "No Columns available"


@pytest.mark.asyncio
async def test_columns_without_definition_legacy_204():
    h = _make_handler()
    req = _make_request(method="HEAD")

    with pytest.raises(web.HTTPNoContent) as exc_info:
        await h.columns(req)
    assert exc_info.value.headers["X-Message"] == "No Columns available"


# --- test_slug() -------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_dry_run_reports_children():
    h = _make_handler()
    definition = _loaded()
    registry = _FakeRegistry()
    repo = _FakeRepo()
    req = _make_request(
        app={"qs_tenant_registry": registry, "qs_definition_repository": repo},
        match_info={"slug": "parent"},
        storage={"qs_definition": definition, "qs_tenant": "tenant1"},
    )

    resp = await h.test_slug(req)
    body = json.loads(resp.body)

    assert body["kind"] == "multi"
    assert body["works"] is True
    children_by_alias = {c["alias"]: c for c in body["children"]}
    assert children_by_alias["a"]["tenant"] == "tenant1"
    assert children_by_alias["a"]["store"] == "tenant1.queries"
    assert children_by_alias["a"]["exists"] is True
    assert children_by_alias["a"]["allowed"] is None  # no 'security' key in app
    assert children_by_alias["b"]["tenant"] is None  # explicit-null override
    assert children_by_alias["r"]["kind"] == "raw"
    assert children_by_alias["r"]["exists"] is None


@pytest.mark.asyncio
async def test_multi_dry_run_missing_child_marks_not_works():
    h = _make_handler()
    definition = _loaded()
    registry = _FakeRegistry()
    repo = _FakeRepo(missing_slugs={"child_a"})
    req = _make_request(
        app={"qs_tenant_registry": registry, "qs_definition_repository": repo},
        match_info={"slug": "parent"},
        storage={"qs_definition": definition, "qs_tenant": "tenant1"},
    )

    resp = await h.test_slug(req)
    body = json.loads(resp.body)

    assert body["works"] is False
    children_by_alias = {c["alias"]: c for c in body["children"]}
    assert children_by_alias["a"]["exists"] is False
    assert children_by_alias["a"]["error"] == "query_not_found"


@pytest.mark.asyncio
async def test_multi_dry_run_non_multi_payload_warns():
    h = _make_handler()
    definition = _loaded(query_raw="SELECT 1")
    req = _make_request(
        app={},
        match_info={"slug": "parent"},
        storage={"qs_definition": definition, "qs_tenant": "tenant1"},
    )

    resp = await h.test_slug(req)
    body = json.loads(resp.body)

    assert body["works"] is True
    assert body["children"] == []
    assert len(body["warnings"]) == 1
    assert "fall back to single-query mode" in body["warnings"][0]


@pytest.mark.asyncio
async def test_multi_dry_run_requires_definition():
    h = _make_handler()
    req = _make_request(match_info={"slug": "parent"})

    with pytest.raises(web.HTTPBadRequest):
        await h.test_slug(req)


# --- forwarding to MultiQS --------------------------------------------------

@pytest.mark.asyncio
async def test_query_forwards_definition_to_multiqs(monkeypatch):
    import querysource.handlers.multi as multi_module

    class _StopSentinel(Exception):
        pass

    captured = {}

    class _FakeMultiQS:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def query(self):
            raise _StopSentinel()

    monkeypatch.setattr(multi_module, "MultiQS", _FakeMultiQS)

    h = _make_handler()
    h._preflight_multiquery = AsyncMock()
    h._preflight_multiquery_owned = AsyncMock()
    h.json_data = AsyncMock(return_value={})

    sentinel_definition = object()
    req = _make_request(
        app={},
        match_info={"slug": "parent"},
        storage={"qs_definition": sentinel_definition, "qs_tenant": "tenant1"},
    )

    # query()'s final `except Exception as ex: raise self.Except(...)` wraps
    # any unrecognized exception (including our _StopSentinel) into an
    # HTTPInternalServerError — the AsyncMock still records the call and its
    # kwargs before raising, which is all this test needs to prove forwarding.
    with pytest.raises(web.HTTPException):
        await h.query(req)

    assert captured.get("definition") is sentinel_definition
    assert captured.get("tenant") == "tenant1"
