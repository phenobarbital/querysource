"""Shared Microsoft Graph helpers for MultiQS drive sources (FEAT-159)."""
from __future__ import annotations

import platform
import threading
import time
import asyncio
from abc import abstractmethod
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, Optional

import pandas as pd
from aiohttp import web
from azure.core.credentials import AccessToken

from .base import ThreadSource
from .file import excel_based

_PATCH_LOCK = threading.Lock()
_PATCH_DEPTH: int = 0
_ORIGINAL_VERSION = None


@contextmanager
def kiota_platform_version_patch() -> Iterator[None]:
    """Strip platform.version()'s trailing space for the kiota User-Agent.

    kiota builds its User-Agent from ``platform.version()``, which carries a
    trailing space on Linux that httpx rejects. Reference-counted under a lock:
    the first entry installs the wrapper, the last exit restores the original
    in ``finally`` — never leaked, never restored early under concurrency.
    """
    global _PATCH_DEPTH, _ORIGINAL_VERSION  # noqa: PLW0603
    with _PATCH_LOCK:
        if _PATCH_DEPTH == 0:
            _ORIGINAL_VERSION = platform.version
            platform.version = lambda: _ORIGINAL_VERSION().strip()
        _PATCH_DEPTH += 1
    try:
        yield
    finally:
        with _PATCH_LOCK:
            _PATCH_DEPTH -= 1
            if _PATCH_DEPTH == 0:
                platform.version = _ORIGINAL_VERSION
                _ORIGINAL_VERSION = None


class StaticTokenCredential:
    """AsyncTokenCredential adapter over an already-resolved delegated access token."""

    def __init__(self, access_token: str, expires_at: Optional[datetime]) -> None:
        self._access_token = access_token
        self._expires_at = expires_at

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        """Return the token; unknown expiry → now + 300 seconds (epoch)."""
        expires_on = int(self._expires_at.timestamp()) if self._expires_at else int(time.time()) + 300
        return AccessToken(self._access_token, expires_on)

    async def close(self) -> None:
        """No-op — nothing to release; never treated as an owned Azure credential."""

    async def __aenter__(self) -> StaticTokenCredential:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


class GraphDriveSource(ThreadSource):
    """Download one CSV/Excel file through Microsoft Graph and return a DataFrame."""

    _extra_name: str = "sharepoint"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        source = options.get('source', {})
        self._filename: str = source.get('filename', '')
        self._directory: str = source.get('directory', '')
        self._sheet_name = source.get('sheet_name', 0)
        self._pd_args: dict = source.get('pd_args', {})
        self._url: str = source.get('url', '') or options.get('url', '') or ''
        _source_masks = source.get('masks', {})
        if _source_masks:
            self._masks = {**self._masks, **_source_masks}
        self._tenant_id: str = ''
        self._client_id: str = ''
        self._client_secret: str = ''

    @staticmethod
    def _encode_share_url(url: str) -> str:
        """Encode a SharePoint URL into a Graph ``shares`` id.

        Per https://learn.microsoft.com/graph/api/shares-get the share id is
        ``"u!"`` followed by the unpadded base64url encoding of the UTF-8 URL.
        Works for canonical and sharing URLs alike — Graph resolves the share
        itself, so no client-side path parsing (and no subsite ambiguity) is
        involved.
        """
        import base64  # noqa: PLC0415

        b64 = base64.urlsafe_b64encode(url.strip().encode('utf-8')).decode('ascii')
        return 'u!' + b64.rstrip('=')

    def _parse_file_content(self, content: bytes) -> pd.DataFrame:
        """Parse raw bytes as Excel or CSV depending on the filename extension."""
        buf = BytesIO(content)
        suffix = Path(self._filename).suffix.lower()
        # Determine MIME from extension for excel_based check
        ext_to_mime = {
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
            '.xlsb': 'application/vnd.ms-excel.sheet.binary.macroEnabled.12',
        }
        mime = ext_to_mime.get(suffix, 'text/csv')
        if mime in excel_based:
            engine = 'xlrd' if suffix == '.xls' else 'openpyxl'
            df = pd.read_excel(
                buf,
                sheet_name=self._sheet_name,
                na_values=["NULL", "TBD"],
                na_filter=True,
                engine=engine,
                keep_default_na=False,
                **self._pd_args,
            )
        else:
            df = pd.read_csv(
                buf,
                na_values=["NULL", "TBD"],
                na_filter=True,
                keep_default_na=False,
                **self._pd_args,
            )
        df = df.infer_objects()
        # A spreadsheet can yield non-string column names (date/number header
        # cells, or blank/merged headers -> NaN). Those break JSON output
        # ("Dict key must be str") and DB writes, so coerce any non-str header.
        if isinstance(df, pd.DataFrame):
            df.columns = [c if isinstance(c, str) else str(c) for c in df.columns]
        return df

    def _import_graph(self) -> tuple[type, type]:
        """Return (ClientSecretCredential[aio], GraphServiceClient) or raise ImportError naming the extra."""
        try:
            from azure.identity.aio import ClientSecretCredential  # noqa: PLC0415
            from msgraph import GraphServiceClient  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                f"Install msgraph-sdk and azure-identity for {self._extra_name.title()} support: "
                f"pip install querysource[{self._extra_name}]"
            ) from exc
        return ClientSecretCredential, GraphServiceClient

    async def _credential(self) -> Any:
        """Return the app-only credential configured on this source."""
        ClientSecretCredential, _ = self._import_graph()
        return ClientSecretCredential(self._tenant_id, self._client_id, self._client_secret)

    def _owns_credential(self, credential: Any) -> bool:
        """Return whether the credential needs releasing after a fetch."""
        return not isinstance(credential, StaticTokenCredential)

    async def _resolve_shared_item(self, client: Any) -> Any:
        """Resolve a Graph drive item from the configured sharing URL."""
        shares_id = self._encode_share_url(self._url)
        item = await client.shares.by_shared_drive_item_id(shares_id).drive_item.get()
        if item is None or not getattr(item, 'id', None):
            raise RuntimeError(f"Could not resolve SharePoint file from url: {self._url}")
        if not self._filename:
            self._filename = getattr(item, 'name', '') or 'download'
        return item

    @abstractmethod
    async def _resolve_drive_item(self, client: Any) -> Any:
        """Resolve a configured directory and filename to a Graph drive item."""

    async def _download(self, item: Any) -> bytes:
        """Download an item using its Graph-provided preauthenticated URL."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Install httpx for SharePoint file download support: "
                "pip install httpx"
            ) from exc
        download_url = item.additional_data.get('@microsoft.graph.downloadUrl') if item.additional_data else None
        if not download_url:
            raise RuntimeError(f"Could not obtain download URL for file '{self._filename}'.")
        async with httpx.AsyncClient(timeout=600.0) as http_client:
            response = await http_client.get(download_url)
            response.raise_for_status()
            return response.content

    async def fetch(self) -> pd.DataFrame:
        """Fetch, download, and parse the configured Graph drive item."""
        _, GraphServiceClient = self._import_graph()
        credential = await self._credential()
        try:
            with kiota_platform_version_patch():
                client = GraphServiceClient(
                    credentials=credential,
                    scopes=["https://graph.microsoft.com/.default"],
                )
                item = await self._resolve_shared_item(client) if self._url else await self._resolve_drive_item(client)
                content = await self._download(item)
                return self._parse_file_content(content)
        finally:
            if self._owns_credential(credential):
                await credential.close()
