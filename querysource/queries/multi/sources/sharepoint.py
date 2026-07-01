"""SharepointSource — download a single file from a SharePoint document library.

Downloads a single Excel or CSV file from a SharePoint site's document library
via the Microsoft Graph API and returns it as a pandas DataFrame.

Optional dependencies: ``msgraph-sdk``, ``azure-identity``, ``httpx``.
Install with: ``pip install querysource[sharepoint]``
"""
import asyncio
from io import BytesIO
from pathlib import Path

import pandas as pd
from aiohttp import web

from .base import ThreadSource
from .file import excel_based


class SharepointSource(ThreadSource):
    """Download a single file from a SharePoint document library.

    Authenticates via Microsoft Graph client credentials (client_id,
    client_secret, tenant_id) and downloads the specified file from the
    given SharePoint site and directory path.

    Credentials may be specified as literal values or as navconfig variable
    names (all-uppercase with underscores), in which case they are resolved
    at runtime via navconfig.

    Configuration dict shape::

        {
            "credentials": {
                "client_id": "SHAREPOINT_APP_ID",        # navconfig var or literal
                "client_secret": "SHAREPOINT_APP_SECRET",
                "tenant_id": "SHAREPOINT_TENANT_ID",
                "tenant_name": "mytenant",               # optional, defaults to "sharepoint"
                "site": "Roadshows"
            },
            "source": {
                "filename": "2025 Events Master Schedule.xlsx",
                "directory": "Shared Documents/General/Schedule"
            }
        }

    Alternatively, pass the full file ``url`` (copied straight from the
    browser/Share dialog — sharing links and subsites included) and the file is
    resolved directly via the Microsoft Graph ``/shares`` endpoint. Only the
    auth credentials are required; ``tenant_name``/``site``/``directory`` are
    not needed::

        {
            "credentials": {
                "client_id": "SHAREPOINT_APP_ID",
                "client_secret": "SHAREPOINT_APP_SECRET",
                "tenant_id": "SHAREPOINT_TENANT_ID"
            },
            "source": {
                "url": "https://<tenant>.sharepoint.com/:x:/r/sites/<site>/<subsite>/<path>/<file>.xlsx?..."
            }
        }

    ``sheet_name`` and ``pd_args`` still apply when reading the downloaded file.

    For daily-rotated files addressed via ``site``/``directory``/``filename``
    (not ``url``), declare ``masks`` and reference them in ``directory``/
    ``filename``; they are resolved at fetch time::

        "source": {
            "filename": "extract_{filedate}.csv",
            "masks": {"{filedate}": ["today", {"mask": "%Y%m%d"}]}
        }

    See :meth:`ThreadSource.resolve_masks` for the available functions.
    """

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._client_id = self.resolve_credential(
            'client_id', creds.get('client_id', 'SHAREPOINT_APP_ID')
        )
        self._client_secret = self.resolve_credential(
            'client_secret', creds.get('client_secret', 'SHAREPOINT_APP_SECRET')
        )
        self._tenant_id = self.resolve_credential(
            'tenant_id', creds.get('tenant_id', 'SHAREPOINT_TENANT_ID')
        )
        # tenant_name / tenant_host: used to build the SharePoint host URL.
        # Accepts either a bare tenant name ("trocglobal") or a full hostname
        # ("trocglobal.sharepoint.com") via SHAREPOINT_TENANT_HOST.
        _tenant_raw = (
            creds.get('tenant_name', '')
            or creds.get('tenant_host', '')
            or self.resolve_credential('tenant_name', 'SHAREPOINT_TENANT_NAME')
            or self.resolve_credential('tenant_host', 'SHAREPOINT_TENANT_HOST')
        )
        # Strip .sharepoint.com suffix if the full hostname was provided
        self._tenant_name = _tenant_raw.replace('.sharepoint.com', '').strip()
        self._site = creds.get('site', '')
        source = options.get('source', {})
        self._filename: str = source.get('filename', '')
        self._directory: str = source.get('directory', '')
        # sheet_name: int (0-based index) or str (sheet name). None reads all sheets.
        self._sheet_name = source.get('sheet_name', 0)
        # pd_args: extra kwargs forwarded to pd.read_excel / pd.read_csv (e.g. skiprows, header, usecols).
        self._pd_args: dict = source.get('pd_args', {})
        # url: full SharePoint file URL, including sharing links such as
        # ".../:x:/r/sites/<site>/<subsite>/<path>/<file>.xlsx?d=...&web=1".
        # When set, the file is resolved directly via the Microsoft Graph
        # /shares endpoint (server-side), so tenant_name/site/directory do NOT
        # need to be configured and subsites work transparently.
        self._url: str = source.get('url', '') or options.get('url', '') or ''
        # masks may also be declared inside the "source" block.
        # Use an explicit default so schema introspection marks it optional.
        _source_masks = source.get('masks', {})
        if _source_masks:
            self._masks = {**self._masks, **_source_masks}

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

    async def fetch(self) -> pd.DataFrame:
        """Download the file from SharePoint and return it as a DataFrame.

        Raises:
            ImportError: If ``msgraph-sdk`` or ``azure-identity`` are not installed.
            RuntimeError: If the file cannot be found or downloaded.
        """
        try:
            from azure.identity.aio import ClientSecretCredential  # noqa: PLC0415
            from msgraph import GraphServiceClient  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Install msgraph-sdk and azure-identity for SharePoint support: "
                "pip install querysource[sharepoint]"
            ) from exc

        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Install httpx for SharePoint file download support: "
                "pip install httpx"
            ) from exc

        # The msgraph/kiota SDK uses platform.version() to build the User-Agent
        # header. On Linux the string has a trailing space which httpx rejects
        # as an illegal header value. Patch it before the client is constructed.
        import platform as _platform  # noqa: PLC0415
        _orig_version = _platform.version
        _platform.version = lambda: _orig_version().strip()

        credential = ClientSecretCredential(
            self._tenant_id,
            self._client_id,
            self._client_secret,
        )
        try:
            scopes = ["https://graph.microsoft.com/.default"]
            client = GraphServiceClient(credentials=credential, scopes=scopes)

            if self._url:
                # Resolve the file directly from its (sharing) URL via /shares.
                # Graph handles subsites, document libraries, folders and
                # sharing-link tokens server-side — nothing is parsed here.
                shares_id = self._encode_share_url(self._url)
                item = await (
                    client.shares.by_shared_drive_item_id(shares_id)
                    .drive_item.get()
                )
                if item is None or not getattr(item, 'id', None):
                    raise RuntimeError(
                        f"Could not resolve SharePoint file from url: {self._url}"
                    )
                # Use the resolved item name so extension detection works.
                if not self._filename:
                    self._filename = getattr(item, 'name', '') or 'download'
            else:
                if not self._tenant_name or self._tenant_name == 'SHAREPOINT_TENANT_NAME':
                    raise ValueError(
                        "SharePoint tenant_name must be configured (via credentials "
                        "or navconfig SHAREPOINT_TENANT_NAME), or pass a full 'url'."
                    )
                # Resolve config masks, e.g. {"{filedate}": ["today", {"mask": "%Y%m%d"}]}
                # turns "extract_{filedate}.csv" -> "extract_20260629.csv".
                self._directory = self.resolve_masks(self._directory)
                self._filename = self.resolve_masks(self._filename)
                # Resolve the site ID
                site_host = f"{self._tenant_name}.sharepoint.com" if self._tenant_name else None
                if site_host and self._site:
                    site = await client.sites.by_site_id(
                        f"{site_host}:/sites/{self._site}:"
                    ).get()
                else:
                    raise RuntimeError(
                        "SharePoint tenant_name and site must be specified to locate the site."
                    )

                site_id = site.id

                # Get drives (document libraries) for the site
                drives_response = await client.sites.by_site_id(site_id).drives.get()
                drives = drives_response.value if drives_response else []

                # Parse directory into (library_name, subfolder).
                # Single segment (e.g. "Tests") → Documents library + subfolder "Tests".
                # Multi-segment (e.g. "Documents/Tests") → first segment is library,
                # rest is subfolder.  "Shared Documents" normalised to "Documents".
                _dir = (self._directory or '').replace('\\', '/').strip().strip('/')
                if not _dir:
                    library_name, subfolder = 'Documents', ''
                else:
                    _parts = _dir.split('/')
                    if len(_parts) == 1:
                        library_name, subfolder = 'Documents', _parts[0]
                    else:
                        library_name = _parts[0]
                        subfolder = '/'.join(_parts[1:])
                        if library_name.lower() == 'shared documents':
                            library_name = 'Documents'

                # Find the matching drive (case-insensitive)
                drive = None
                for d in drives:
                    if d.name and d.name.lower() == library_name.lower():
                        drive = d
                        break
                if drive is None and drives:
                    drive = drives[0]  # fallback to first drive

                if drive is None:
                    raise RuntimeError(
                        f"No document library found for site '{self._site}' "
                        f"matching '{library_name}'."
                    )

                drive_id = drive.id

                # Navigate to the subfolder and find the file
                file_path = f"{subfolder.rstrip('/')}/{self._filename}" if subfolder else self._filename
                item = await (
                    client.drives.by_drive_id(drive_id)
                    .items.by_drive_item_id(f"root:/{file_path}:")
                    .get()
                )

                if item is None or not hasattr(item, 'id'):
                    raise RuntimeError(
                        f"File '{self._filename}' not found in SharePoint directory "
                        f"'{self._directory}'."
                    )

            # Get the download URL from the item's additional data
            download_url = (
                item.additional_data.get('@microsoft.graph.downloadUrl')
                if item.additional_data
                else None
            )
            if not download_url:
                raise RuntimeError(
                    f"Could not obtain download URL for file '{self._filename}'."
                )

            # Download the file content using httpx.
            # Large files (e.g. 100MB+) can take several minutes — use a long timeout.
            async with httpx.AsyncClient(timeout=600.0) as http_client:
                response = await http_client.get(download_url)
                response.raise_for_status()
                content = response.content

            return self._parse_file_content(content)
        finally:
            await credential.close()
