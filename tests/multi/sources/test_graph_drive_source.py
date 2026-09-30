"""Tests for the shared Microsoft Graph drive source template."""
import asyncio
import builtins
import platform
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.queries.multi.sources.graph import GraphDriveSource, StaticTokenCredential


class _Probe(GraphDriveSource):
    """Concrete GraphDriveSource used to exercise the shared template."""

    async def _resolve_drive_item(self, client):
        """Return the test item for path-mode requests."""
        return self._item


def _probe(**source):
    """Build a probe source with its test queue."""
    return _Probe("p", {"source": source}, None, asyncio.Queue())


def _httpx_client(content: bytes):
    """Create an async httpx client context manager returning ``content``."""
    response = MagicMock(content=content)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    return response, client, manager


def _item(name: str):
    """Return a minimal Graph drive item with a downloadable URL."""
    item = MagicMock(id="item-id", additional_data={"@microsoft.graph.downloadUrl": "https://download"})
    item.name = name  # MagicMock(name=...) names the mock itself, not the attribute
    return item


def test_encode_share_url_unpadded():
    """Share identifiers are URL-safe base64 without trailing padding."""
    assert _probe(filename="x.csv")._encode_share_url("https://a/b").startswith("u!")


def test_graph_import_error_names_extra():
    """Missing optional Graph dependencies name the relevant package extra."""
    source = _probe(filename="x.csv")
    real_import = builtins.__import__

    def missing_graph(name, *args, **kwargs):
        if name in {"msgraph", "azure.identity.aio"}:
            raise ImportError("missing graph dependency")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=missing_graph), pytest.raises(ImportError) as exc_info:
        source._import_graph()

    assert "msgraph-sdk" in str(exc_info.value)
    assert "querysource[sharepoint]" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_closes_only_owned_credentials(monkeypatch):
    """Static delegated credentials stay open while owned app credentials close."""
    source = _probe(filename="x.csv")
    source._item = _item("x.csv")
    _, _, manager = _httpx_client(b"a,b\n1,2\n")
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: manager)
    graph_client = MagicMock()
    graph_client_type = MagicMock(return_value=graph_client)
    monkeypatch.setattr(source, "_import_graph", lambda: (MagicMock(), graph_client_type))

    delegated = StaticTokenCredential("token", None)
    delegated.close = AsyncMock()
    monkeypatch.setattr(source, "_credential", AsyncMock(return_value=delegated))
    await source.fetch()
    delegated.close.assert_not_awaited()

    owned = MagicMock()
    owned.close = AsyncMock()
    monkeypatch.setattr(source, "_credential", AsyncMock(return_value=owned))
    await source.fetch()
    owned.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_mode, suffix", [(False, ".csv"), (True, ".xlsx")])
async def test_fetch_roundtrip_with_mocked_graph_and_httpx(monkeypatch, url_mode, suffix):
    """Path and share URL Graph modes download and parse CSV or Excel data."""
    filename = f"report{suffix}"
    source = _probe(**({"url": "https://share/link"} if url_mode else {"filename": filename}))
    source._item = _item(filename)
    frame = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    if suffix == ".csv":
        content = frame.to_csv(index=False).encode()
    else:
        buffer = BytesIO()
        frame.to_excel(buffer, index=False)
        content = buffer.getvalue()
    _, client, manager = _httpx_client(content)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: manager)
    graph_client = MagicMock()
    graph_client_type = MagicMock(return_value=graph_client)
    monkeypatch.setattr(source, "_import_graph", lambda: (MagicMock(), graph_client_type))
    credential = MagicMock()
    credential.close = AsyncMock()
    monkeypatch.setattr(source, "_credential", AsyncMock(return_value=credential))
    if url_mode:
        graph_client.shares.by_shared_drive_item_id.return_value.drive_item.get = AsyncMock(return_value=source._item)

    before = platform.version
    actual = await source.fetch()

    pd.testing.assert_frame_equal(actual, frame)
    assert platform.version is before
    client.get.assert_awaited_once_with("https://download")
    credential.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_restores_platform_version_after_error(monkeypatch):
    """The scoped kiota patch restores platform.version when a download fails."""
    source = _probe(filename="x.csv")
    source._item = _item("x.csv")
    graph_client_type = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(source, "_import_graph", lambda: (MagicMock(), graph_client_type))
    credential = MagicMock()
    credential.close = AsyncMock()
    monkeypatch.setattr(source, "_credential", AsyncMock(return_value=credential))
    monkeypatch.setattr(source, "_download", AsyncMock(side_effect=RuntimeError("download failed")))

    before = platform.version
    with pytest.raises(RuntimeError, match="download failed"):
        await source.fetch()

    assert platform.version is before
    credential.close.assert_awaited_once()
