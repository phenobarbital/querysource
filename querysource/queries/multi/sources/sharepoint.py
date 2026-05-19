"""SourceSharepoint — download a single file from a SharePoint document library.

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


class SourceSharepoint(ThreadSource):
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
        # tenant_name is used to build the SharePoint host URL
        self._tenant_name = creds.get('tenant_name', '') or self.resolve_credential(
            'tenant_name', 'SHAREPOINT_TENANT_NAME'
        )
        self._site = creds.get('site', '')
        source = options.get('source', {})
        self._filename: str = source.get('filename', '')
        self._directory: str = source.get('directory', '')

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
                na_values=["NULL", "TBD"],
                na_filter=True,
                engine=engine,
                keep_default_na=False,
            )
        else:
            df = pd.read_csv(
                buf,
                na_values=["NULL", "TBD"],
                na_filter=True,
                keep_default_na=False,
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
                    f"{site_host}:/sites/{self._site}"
                ).get()
            else:
                raise RuntimeError(
                    "SharePoint tenant_name and site must be specified to locate the site."
                )

            site_id = site.id

            # Get drives (document libraries) for the site
            drives_response = await client.sites.by_site_id(site_id).drives.get()
            drives = drives_response.value if drives_response else []

            # Parse the directory: split on "/" to get library name and subfolder
            parts = self._directory.split('/', 1) if self._directory else []
            library_name = parts[0] if parts else 'Documents'
            subfolder = parts[1] if len(parts) > 1 else ''

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
            if subfolder:
                path_encoded = subfolder.rstrip('/')
                item = await (
                    client.drives.by_drive_id(drive_id)
                    .items.by_drive_item_id(f"root:/{path_encoded}/{self._filename}:")
                    .get()
                )
            else:
                item = await (
                    client.drives.by_drive_id(drive_id)
                    .items.by_drive_item_id(f"root:/{self._filename}:")
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

            # Download the file content using httpx
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.get(download_url)
                response.raise_for_status()
                content = response.content

            return self._parse_file_content(content)
        finally:
            await credential.close()
