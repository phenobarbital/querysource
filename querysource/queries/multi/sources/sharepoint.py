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

    Alternatively, pass a full file ``url`` and let the component derive
    ``tenant_name``, ``site``, ``directory`` and ``filename`` from it (only the
    auth credentials are still required)::

        {
            "credentials": {
                "client_id": "SHAREPOINT_APP_ID",
                "client_secret": "SHAREPOINT_APP_SECRET",
                "tenant_id": "SHAREPOINT_TENANT_ID"
            },
            "source": {
                "url": "https://<tenant>.sharepoint.com/sites/<site>/Shared Documents/<path>/<file>.xlsx"
            }
        }

    Any explicitly-configured ``site``/``directory``/``filename`` takes
    precedence over the values parsed from ``url``.
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
        # url: full SharePoint file URL. When provided, tenant_name/site/directory/
        # filename are derived from it. Explicitly-configured values still win, so
        # the URL only fills in whatever was left unset.
        url = source.get('url') or options.get('url')
        if url:
            parsed = self._parse_sharepoint_url(url)
            # tenant_name resolves to a placeholder ('SHAREPOINT_TENANT_NAME') when
            # unset, so treat that as empty too.
            if not self._tenant_name or self._tenant_name == 'SHAREPOINT_TENANT_NAME':
                self._tenant_name = parsed['tenant_name']
            self._site = self._site or parsed['site']
            self._directory = self._directory or parsed['directory']
            self._filename = self._filename or parsed['filename']

    @staticmethod
    def _parse_sharepoint_url(url: str) -> dict:
        """Split a full SharePoint file URL into its component parts.

        Handles canonical URLs such as::

            https://<tenant>.sharepoint.com/sites/<site>/Shared Documents/<path>/<file>.xlsx

        and sharing-style URLs that prepend tokens before ``/sites/`` (e.g.
        ``/:x:/r/sites/...``). Percent-encoded characters (``%20``) and query
        strings are handled.

        Returns a dict with ``tenant_name``, ``site``, ``directory`` and
        ``filename``. ``directory`` keeps the document library as its first
        segment (e.g. ``"Shared Documents/Reports/Data"``), matching what
        :meth:`fetch` expects.
        """
        from urllib.parse import urlparse, unquote  # noqa: PLC0415

        parsed = urlparse(url.strip())
        # Host: "<tenant>.sharepoint.com" -> tenant name
        host = parsed.netloc
        tenant_name = host.split('.', 1)[0] if host else ''
        # Path: decode %20 etc. and drop leading/trailing slashes.
        parts = [p for p in unquote(parsed.path).strip('/').split('/') if p]
        # Locate the "/sites/<site>" (or "/teams/<site>") segment; everything
        # after it is library + subfolders + filename. Searching by name also
        # skips sharing-link prefixes like ":x:/r".
        site = ''
        rest = parts
        for i, seg in enumerate(parts):
            if seg.lower() in ('sites', 'teams') and i + 1 < len(parts):
                site = parts[i + 1]
                rest = parts[i + 2:]
                break
        filename = rest[-1] if rest else ''
        directory = '/'.join(rest[:-1]) if len(rest) > 1 else ''
        return {
            'tenant_name': tenant_name,
            'site': site,
            'directory': directory,
            'filename': filename,
        }

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

        if not self._tenant_name or self._tenant_name == 'SHAREPOINT_TENANT_NAME':
            raise ValueError(
                "SharePoint tenant_name must be configured (via credentials or "
                "navconfig SHAREPOINT_TENANT_NAME)."
            )

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
