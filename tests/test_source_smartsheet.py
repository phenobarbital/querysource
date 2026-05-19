"""Unit tests for SourceSmartSheet (TASK-648)."""
import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.queries.multi.sources.base import ThreadSource
from querysource.queries.multi.sources.smartsheet import SourceSmartSheet


def _make_excel_bytes() -> bytes:
    """Build a minimal Excel workbook as bytes for testing."""
    df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


class TestSourceSmartSheet:
    def test_inherits_thread_source(self):
        assert issubclass(SourceSmartSheet, ThreadSource)

    def test_parses_config(self):
        options = {"source": {"file_id": 12345}}
        source = SourceSmartSheet("ss_test", options, None, asyncio.Queue())
        assert source._file_id == 12345

    def test_explicit_api_key(self):
        options = {
            "credentials": {"api_key": "my-literal-key"},
            "source": {"file_id": 99},
        }
        source = SourceSmartSheet("ss", options, None, asyncio.Queue())
        assert source._api_key == "my-literal-key"

    def test_default_credential_resolution(self):
        """Without credentials, should attempt to resolve SMARTSHEET_API_KEY."""
        options = {"source": {"file_id": 12345}}
        source = SourceSmartSheet("ss_test", options, None, asyncio.Queue())
        # Must be a string (either resolved or the literal env var name as fallback)
        assert isinstance(source._api_key, str)
        assert len(source._api_key) > 0

    def test_missing_file_id_raises(self):
        options = {}
        source = SourceSmartSheet("ss", options, None, asyncio.Queue())

        async def _run():
            with pytest.raises(ValueError, match="file_id"):
                await source.fetch()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()

    def test_fetch_returns_dataframe(self):
        """fetch() with mocked HTTP response should return a DataFrame."""
        options = {
            "credentials": {"api_key": "test-key"},
            "source": {"file_id": 42},
        }
        source = SourceSmartSheet("ss", options, None, asyncio.Queue())
        excel_content = _make_excel_bytes()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=excel_content)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def _run():
            with patch('aiohttp.ClientSession', return_value=mock_session):
                return await source.fetch()

        loop = asyncio.new_event_loop()
        df = loop.run_until_complete(_run())
        loop.close()
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]

    def test_rate_limit_raises(self):
        """HTTP 429 should raise RuntimeError."""
        options = {"credentials": {"api_key": "k"}, "source": {"file_id": 1}}
        source = SourceSmartSheet("ss", options, None, asyncio.Queue())

        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def _run():
            with patch('aiohttp.ClientSession', return_value=mock_session):
                with pytest.raises(RuntimeError, match="rate limit"):
                    await source.fetch()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()
