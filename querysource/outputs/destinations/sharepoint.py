"""
ToSharepoint Destination.

Uploads a pandas DataFrame as an Excel (.xlsx) or CSV (.csv) file to a
SharePoint document library using the Microsoft Graph SDK.

Authentication uses the OAuth2 Client Credentials flow
(``ClientSecretCredential``) — app-only, no user interaction required.

Upload strategy:
- Files ≤ 4 MB: single PUT request.
- Files > 4 MB: resumable upload session with 10 MB chunks.
"""
import io
import asyncio
from pathlib import PurePosixPath
from typing import Union
import pandas as pd
from querysource.exceptions import OutputError
from .abstract import AbstractDestination


# Upload size thresholds (bytes)
_SMALL_FILE_THRESHOLD = 4 * 1024 * 1024   # 4 MB
_CHUNK_SIZE = 10 * 1024 * 1024             # 10 MB


class ToSharepoint(AbstractDestination):
    """
    Upload a DataFrame as an Excel or CSV file to a SharePoint document library.

    YAML configuration example::

        Output:
          - ToSharepoint:
              credentials:
                client_id: SHAREPOINT_APP_ID
                client_secret: SHAREPOINT_APP_SECRET
                tenant_id: SHAREPOINT_TENANT_ID
                site: Roadshows
              destination:
                filename: "2025 Events Master Schedule.xlsx"
                directory: "Shared Documents/General/Schedule"

    The ``site`` key inside ``credentials`` is the SharePoint *site name*
    (not the full URL).  All credential values may be literal strings or
    navconfig variable names (ALL_CAPS_SNAKE_CASE) that are resolved at
    runtime.

    The output format is inferred from the filename extension:
    - ``.xlsx`` → Excel (via *openpyxl*)
    - ``.csv``  → CSV (UTF-8)
    Any other extension defaults to CSV.
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        super().__init__(data, **kwargs)

        raw_creds: dict = kwargs.get("credentials", {}) or {}
        dest_cfg: dict = kwargs.get("destination", {}) or {}

        # Resolve navconfig variables in credentials
        resolved = self.resolve_credentials(raw_creds)

        self._client_id: str = resolved.get("client_id", "")
        self._client_secret: str = resolved.get("client_secret", "")
        self._tenant_id: str = resolved.get("tenant_id", "")
        self._site: str = resolved.get("site", "")

        # Destination config
        self._filename: str = dest_cfg.get("filename", "output.xlsx")
        self._directory: str = dest_cfg.get("directory", "Shared Documents").strip("/")

    # ------------------------------------------------------------------
    # DataFrame ↔ bytes conversion
    # ------------------------------------------------------------------

    def _convert_dataframe(self, df: pd.DataFrame, filename: str) -> bytes:
        """
        Convert a DataFrame to file bytes based on *filename* extension.

        :param df: Source DataFrame.
        :param filename: Target filename — extension determines format.
        :returns: Raw bytes ready for upload.
        :raises OutputError: If the DataFrame cannot be serialised.
        """
        ext = PurePosixPath(filename.lower()).suffix
        try:
            if ext in (".xlsx", ".xls"):
                buf = io.BytesIO()
                df.to_excel(buf, index=False, engine="openpyxl")
                return buf.getvalue()
            else:
                # Default to CSV
                return df.to_csv(index=False).encode("utf-8")
        except Exception as err:
            raise OutputError(
                f"ToSharepoint: failed to convert DataFrame to {ext!r}: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    def _build_graph_client(self):
        """
        Build and return a :class:`msgraph.GraphServiceClient` authenticated
        with ``ClientSecretCredential`` (client-credentials flow).

        :raises OutputError: If required credential fields are missing.
        """
        try:
            from azure.identity import ClientSecretCredential
            from msgraph import GraphServiceClient
        except ImportError as exc:
            raise OutputError(
                "ToSharepoint requires 'azure-identity' and 'msgraph-sdk'. "
                "Install them with: pip install azure-identity msgraph-sdk"
            ) from exc

        if not all([self._tenant_id, self._client_id, self._client_secret]):
            raise OutputError(
                "ToSharepoint: 'tenant_id', 'client_id', and 'client_secret' "
                "are required in the credentials config."
            )

        credential = ClientSecretCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        scopes = ["https://graph.microsoft.com/.default"]
        return GraphServiceClient(credential, scopes=scopes)

    # ------------------------------------------------------------------
    # SharePoint Graph API helpers
    # ------------------------------------------------------------------

    async def _resolve_site_id(self, graph_client) -> str:
        """Return the Graph site-id for :attr:`_site`."""
        # Primary lookup: tenant:path format
        try:
            site_path = f"root:/sites/{self._site}:"
            site = await graph_client.sites.by_site_id(site_path).get()
            if site and site.id:
                return site.id
        except Exception:
            pass

        # Fallback: search by display name using $search query parameter
        try:
            from msgraph.generated.sites.sites_request_builder import SitesRequestBuilder
            from kiota_abstractions.base_request_configuration import RequestConfiguration

            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters(
                search=self._site
            )
            config = RequestConfiguration(query_parameters=query_params)
            result = await graph_client.sites.get(request_configuration=config)
            if result and result.value:
                return result.value[0].id
        except Exception:
            pass

        raise OutputError(
            f"ToSharepoint: could not resolve SharePoint site '{self._site}'. "
            f"Verify the site name and credentials."
        )

    async def _get_drive_id(self, graph_client, site_id: str) -> str:
        """Return the document library drive-id for *site_id*."""
        try:
            drives = await graph_client.sites.by_site_id(site_id).drives.get()
            if drives and drives.value:
                # Prefer a drive named "Documents" or "Shared Documents"
                for drive in drives.value:
                    if drive.name.lower() in ("documents", "shared documents"):
                        return drive.id
                return drives.value[0].id
            raise OutputError(
                f"ToSharepoint: no document libraries found for site '{site_id}'."
            )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"ToSharepoint: failed to resolve document library: {err}"
            ) from err

    async def _ensure_folder(
        self,
        graph_client,
        drive_id: str,
        folder_path: str,
    ) -> str:
        """
        Ensure *folder_path* exists inside *drive_id*.  Create missing
        intermediate directories if needed.

        :returns: The folder item-id of the deepest folder.
        """
        from msgraph.generated.models.drive_item import DriveItem
        from msgraph.generated.models.folder import Folder

        if not folder_path:
            root = await graph_client.drives.by_drive_id(drive_id).root.get()
            return root.id

        # Try direct path lookup first
        try:
            item = await (
                graph_client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(f"root:/{folder_path}:")
                .get()
            )
            if item:
                return item.id
        except Exception:
            pass

        # Create recursively
        root = await graph_client.drives.by_drive_id(drive_id).root.get()
        parent_id = root.id

        for segment in [s for s in folder_path.split("/") if s]:
            # Check if this segment already exists
            children = await (
                graph_client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(parent_id)
                .children.get()
            )
            existing = None
            if children and children.value:
                for child in children.value:
                    if child.name == segment and child.folder:
                        existing = child
                        break
            if existing:
                parent_id = existing.id
                continue

            # Create the folder segment
            new_folder = DriveItem()
            new_folder.name = segment
            new_folder.folder = Folder()
            new_folder.additional_data = {
                "@microsoft.graph.conflictBehavior": "replace"
            }
            created = await (
                graph_client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(parent_id)
                .children.post(new_folder)
            )
            parent_id = created.id
            self.logger.info("Created SharePoint folder: %s", segment)

        return parent_id

    async def _upload_bytes_small(
        self,
        graph_client,
        drive_id: str,
        parent_id: str,
        filename: str,
        content: bytes,
    ) -> None:
        """Upload *content* as a single PUT request (≤ 4 MB)."""
        from urllib.parse import quote
        encoded_name = quote(filename)
        request_path = f"{parent_id}:/{encoded_name}:"
        await (
            graph_client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(request_path)
            .content.put(content)
        )

    async def _upload_bytes_large(
        self,
        graph_client,
        drive_id: str,
        parent_id: str,
        filename: str,
        content: bytes,
    ) -> None:
        """Upload *content* via a resumable upload session (> 4 MB)."""
        import aiohttp
        from urllib.parse import quote
        from msgraph.generated.drives.item.items.item.create_upload_session.create_upload_session_post_request_body import (  # noqa: E501
            CreateUploadSessionPostRequestBody,
        )
        from msgraph.generated.models.drive_item_uploadable_properties import DriveItemUploadableProperties

        encoded_name = quote(filename)
        body = CreateUploadSessionPostRequestBody()
        body.item = DriveItemUploadableProperties()
        body.item.additional_data = {"@microsoft.graph.conflictBehavior": "replace"}

        session = await (
            graph_client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(f"{parent_id}:/{encoded_name}:/")
            .create_upload_session.post(body)
        )

        upload_url = session.upload_url
        total = len(content)
        uploaded = 0

        async with aiohttp.ClientSession() as http:
            while uploaded < total:
                chunk = content[uploaded: uploaded + _CHUNK_SIZE]
                start = uploaded
                end = start + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total}",
                }
                async with http.put(upload_url, headers=headers, data=chunk) as resp:
                    if resp.status in (200, 201):
                        self.logger.info(
                            "ToSharepoint: large file upload complete: %s", filename
                        )
                        return
                    elif resp.status == 202:
                        uploaded = end + 1
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            await asyncio.sleep(int(retry_after))
                    else:
                        err_text = await resp.text()
                        raise OutputError(
                            f"ToSharepoint: chunk upload failed "
                            f"({resp.status}): {err_text}"
                        )

        raise OutputError(
            "ToSharepoint: upload session closed without a completion response."
        )

    # ------------------------------------------------------------------
    # Core upload orchestration
    # ------------------------------------------------------------------

    async def _upload_to_sharepoint(
        self,
        content: bytes,
        filename: str,
    ) -> None:
        """
        Authenticate, resolve the target folder, and upload *content*.

        :param content: File bytes to upload.
        :param filename: Target filename on SharePoint.
        """
        graph_client = self._build_graph_client()

        site_id = await self._resolve_site_id(graph_client)
        drive_id = await self._get_drive_id(graph_client, site_id)

        # Parse directory: first segment is the library name, rest is the path
        parts = self._directory.split("/", 1)
        library_part = parts[0]
        path_part = parts[1] if len(parts) > 1 else ""

        # Find the specific library drive if name was given
        if library_part:
            try:
                drives = await graph_client.sites.by_site_id(site_id).drives.get()
                for drive in (drives.value or []):
                    if drive.name.lower() == library_part.lower() or (
                        library_part.lower() == "shared documents"
                        and drive.name.lower() == "documents"
                    ):
                        drive_id = drive.id
                        break
                else:
                    path_part = self._directory  # fallback: treat whole dir as path
            except Exception:
                path_part = self._directory

        parent_id = await self._ensure_folder(graph_client, drive_id, path_part)

        if len(content) <= _SMALL_FILE_THRESHOLD:
            self.logger.info(
                "ToSharepoint: small-file upload (%d bytes) → %s/%s",
                len(content),
                self._directory,
                filename,
            )
            await self._upload_bytes_small(
                graph_client, drive_id, parent_id, filename, content
            )
        else:
            self.logger.info(
                "ToSharepoint: large-file upload (%d bytes) → %s/%s",
                len(content),
                self._directory,
                filename,
            )
            await self._upload_bytes_large(
                graph_client, drive_id, parent_id, filename, content
            )

    # ------------------------------------------------------------------
    # AbstractDestination interface
    # ------------------------------------------------------------------

    async def run(self) -> Union[dict, pd.DataFrame]:
        """
        Convert :attr:`data` to a file and upload it to SharePoint.

        Handles both a single :class:`~pandas.DataFrame` and a ``dict``
        of DataFrames (each DataFrame is uploaded as a separate file with
        the dict key prepended to the filename).

        :returns: Original :attr:`data` (pass-through).
        :raises OutputError: On authentication or upload failure.
        """
        try:
            if isinstance(self.data, dict):
                for key, df in self.data.items():
                    if not isinstance(df, pd.DataFrame):
                        continue
                    stem = PurePosixPath(self._filename).stem
                    suffix = PurePosixPath(self._filename).suffix
                    target_name = f"{stem}_{key}{suffix}"
                    content = self._convert_dataframe(df, target_name)
                    await self._upload_to_sharepoint(content, target_name)
            else:
                content = self._convert_dataframe(self.data, self._filename)
                await self._upload_to_sharepoint(content, self._filename)
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"ToSharepoint: unexpected error during upload: {err}"
            ) from err

        return self.data
