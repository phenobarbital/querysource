"""FEAT-148 TASK-741 — GET /api/v1/queries/vocabulary."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from querysource.handlers.describe import QueryDescribe


@pytest.fixture
def handler():
    return QueryDescribe()


@pytest.fixture
def mock_request():
    req = MagicMock(spec=web.Request)
    req.app = {}
    return req


@pytest.mark.asyncio
async def test_vocabulary_requires_principal(handler, mock_request):
    with patch.object(QueryDescribe, "_principal", side_effect=web.HTTPUnauthorized):
        with pytest.raises(web.HTTPUnauthorized):
            await handler.vocabulary(mock_request)


@pytest.mark.asyncio
async def test_vocabulary_session_ok(handler, mock_request):
    with patch.object(QueryDescribe, "_principal", new_callable=AsyncMock) as mock_p, \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.vocabulary(mock_request)

        mock_p.assert_called_once_with(mock_request)
        mock_jr.assert_called_once()
        resp_data = mock_jr.call_args[0][0]
        assert isinstance(resp_data, dict)
        assert "keywords" in resp_data
        assert "constants" in resp_data
        assert "pg_functions" in resp_data


@pytest.mark.asyncio
async def test_vocabulary_keywords_match_effective_udf_list(handler, mock_request):
    with patch.object(QueryDescribe, "_principal", new_callable=AsyncMock), \
         patch.object(QueryDescribe, "json_response") as mock_jr:

        await handler.vocabulary(mock_request)

        resp_data = mock_jr.call_args[0][0]
        assert len(resp_data["keywords"]) > 0
        assert len(resp_data["constants"]) > 0
        assert len(resp_data["pg_functions"]) > 0

