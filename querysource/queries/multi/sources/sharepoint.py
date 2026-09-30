"""SharepointSource — download a single file from a SharePoint document library.

Downloads a single Excel or CSV file from a SharePoint site's document library
via the Microsoft Graph API and returns it as a pandas DataFrame.

Optional dependencies: ``msgraph-sdk``, ``azure-identity``, ``httpx``.
Install with: ``pip install querysource[sharepoint]``
"""
import asyncio
from typing import Any

from aiohttp import web

from .graph import GraphDriveSource


class SharepointSource(GraphDriveSource):
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
    async def _resolve_drive_item(self, client: Any) -> Any:
        """Resolve the configured SharePoint site, library, and file to a drive item."""
        if not self._tenant_name or self._tenant_name == 'SHAREPOINT_TENANT_NAME':
            raise ValueError(
                "SharePoint tenant_name must be configured (via credentials "
                "or navconfig SHAREPOINT_TENANT_NAME), or pass a full 'url'."
            )
        self._directory = self.resolve_masks(self._directory)
        self._filename = self.resolve_masks(self._filename)
        site_host = f"{self._tenant_name}.sharepoint.com" if self._tenant_name else None
        if site_host and self._site:
            site = await client.sites.by_site_id(f"{site_host}:/sites/{self._site}:").get()
        else:
            raise RuntimeError("SharePoint tenant_name and site must be specified to locate the site.")

        site_id = site.id
        drives_response = await client.sites.by_site_id(site_id).drives.get()
        drives = drives_response.value if drives_response else []

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

        drive = None
        for d in drives:
            if d.name and d.name.lower() == library_name.lower():
                drive = d
                break
        if drive is None and drives:
            drive = drives[0]

        if drive is None:
            raise RuntimeError(
                f"No document library found for site '{self._site}' matching '{library_name}'."
            )

        drive_id = drive.id
        file_path = f"{subfolder.rstrip('/')}/{self._filename}" if subfolder else self._filename
        item = await client.drives.by_drive_id(drive_id).items.by_drive_item_id(f"root:/{file_path}:").get()

        if item is None or not hasattr(item, 'id'):
            raise RuntimeError(
                f"File '{self._filename}' not found in SharePoint directory '{self._directory}'."
            )
        return item
