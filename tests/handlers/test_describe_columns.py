"""FEAT-148 TASK-741 — GET /api/v1/queries/{slug}/columns."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from asyncdb.exceptions import DriverError, ProviderError

from querysource.exceptions import ParserError, SlugNotFound
from querysource.handlers.describe import QueryDescribe


class FakeProvider:
    def __init__(self, query="SELECT 1", definition=None, columns=None):
        self._query = query
        self._definition = definition or {}
        self._columns = columns or []

    def get_query(self):
        return self._query

    def get_definition(self):
        return self._definition

    async def describe_columns(self):
        return self._columns


class FakeQS:
    def __init__(self, slug="", conditions=None, request=None, **kwargs):
        self.slug = slug
        self.conditions = conditions
        self.request = request
        self.provider = None
        self.close_called = False

    async def build_provider(self):
        if self.slug == "vanished":
            raise SlugNotFound("vanished")
        if self.slug == "parser_err":
            raise ParserError("parser error")
        if self.slug == "driver_err":
            raise DriverError("driver error")
        if self.provider is None:
            self.provider = FakeProvider()

    def get_source(self):
        return self.provider

    async def close(self):
        self.close_called = True



@pytest.fixture
def handler():
    return QueryDescribe()


@pytest.fixture
def mock_request():
    req = MagicMock(spec=web.Request)
    req.match_info = {"slug": "test-slug"}
    req.app = {}
    return req


@pytest.mark.asyncio
async def test_columns_prepare_typed(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")
    fake_qs.provider = FakeProvider(
        query="SELECT id, name FROM users",
        columns=[{"name": "id", "type": "integer"}, {"name": "name", "type": "string"}]
    )

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock) as mock_p, \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock) as mock_s, \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock) as mock_lv, \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.columns(mock_request)

        assert fake_qs.close_called is True
        mock_jr.assert_called_once()
        resp_data = mock_jr.call_args[0][0]
        assert resp_data["slug"] == "test-slug"
        assert resp_data["columns_source"] == "prepare"
        assert resp_data["columns"] == [{"name": "id", "type": "integer"}, {"name": "name", "type": "string"}]
        assert resp_data["warnings"] == []


@pytest.mark.asyncio
async def test_columns_unresolved_placeholders_declared_fallback(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")
    fake_qs.provider = FakeProvider(
        query="SELECT * FROM users WHERE id = {unresolved_id}",
        definition={"attributes": {"columns": ["id", "name"]}}
    )

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.columns(mock_request)

        assert fake_qs.close_called is True
        resp_data = mock_jr.call_args[0][0]
        assert resp_data["columns_source"] == "declared"
        assert resp_data["columns"] == [{"name": "id", "type": None}, {"name": "name", "type": None}]
        assert any("prepare_skipped: unresolved placeholders" in w for w in resp_data["warnings"])



@pytest.mark.asyncio
async def test_columns_datasource_down_unavailable(handler, mock_request):
    fake_qs = FakeQS(slug="driver_err")

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock) as mock_lv, \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        mock_lv.return_value = {"attributes": {}}  # no declared columns

        await handler.columns(mock_request)

        assert fake_qs.close_called is True
        resp_data = mock_jr.call_args[0][0]
        assert resp_data["columns_source"] == "unavailable"
        assert resp_data["columns"] == []
        assert any("provider_unavailable: DriverError" in w for w in resp_data["warnings"])


@pytest.mark.asyncio
async def test_columns_timeout_declared(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")
    fake_qs.provider = FakeProvider(
        query="SELECT 1",
        definition={"attributes": {"columns": ["col1"]}}
    )

    async def mock_describe_columns():
        raise asyncio.TimeoutError()

    fake_qs.provider.describe_columns = mock_describe_columns

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.columns(mock_request)

        assert fake_qs.close_called is True
        resp_data = mock_jr.call_args[0][0]
        assert resp_data["columns_source"] == "declared"
        assert resp_data["columns"] == [{"name": "col1", "type": None}]
        assert any("prepare_failed: TimeoutError" in w for w in resp_data["warnings"])


@pytest.mark.asyncio
async def test_columns_slug_vanished_404(handler, mock_request):
    fake_qs = FakeQS(slug="vanished")

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}):

        with pytest.raises(web.HTTPNotFound):
            await handler.columns(mock_request)


@pytest.mark.asyncio
async def test_columns_never_returns_sql(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")
    fake_qs.provider = FakeProvider(
        query="SELECT secret_column FROM sensitive_table",
        columns=[{"name": "secret_column", "type": "string"}]
    )

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.columns(mock_request)

        resp_data = mock_jr.call_args[0][0]
        import json
        serialized = json.dumps(resp_data)
        assert "SELECT" not in serialized
        assert "sensitive_table" not in serialized


@pytest.mark.asyncio
async def test_columns_conditions_merge_query_over_body(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")

    def mock_init(slug="", conditions=None, request=None, **kwargs):
        fake_qs.slug = slug
        fake_qs.conditions = conditions
        fake_qs.request = request
        return fake_qs

    with patch("querysource.handlers.describe.QS", side_effect=mock_init), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={"a": 1, "b": 2}), \
         patch.object(QueryDescribe, "query_parameters", return_value={"b": 3, "c": 4}), \
         patch.object(QueryDescribe, "json_response"):

        await handler.columns(mock_request)

        assert fake_qs.conditions == {"a": 1, "b": 3, "c": 4}




@pytest.mark.asyncio
async def test_columns_401_without_principal(handler, mock_request):
    with patch.object(QueryDescribe, "_principal", side_effect=web.HTTPUnauthorized):
        with pytest.raises(web.HTTPUnauthorized):
            await handler.columns(mock_request)


@pytest.mark.asyncio
async def test_columns_close_always_called(handler, mock_request):
    fake_qs = FakeQS(slug="test-slug")

    async def mock_build_provider():
        fake_qs.provider = FakeProvider()
        async def mock_describe_columns():
            raise RuntimeError("unexpected error")
        fake_qs.provider.describe_columns = mock_describe_columns

    fake_qs.build_provider = mock_build_provider

    with patch("querysource.handlers.describe.QS", return_value=fake_qs), \
         patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_store", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "_load_visible", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_data", new_callable=AsyncMock, return_value={}), \
         patch.object(QueryDescribe, "query_parameters", return_value={}), \
         patch.object(QueryDescribe, "json_response"):

        with pytest.raises(RuntimeError):
            await handler.columns(mock_request)

        assert fake_qs.close_called is True

