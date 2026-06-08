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
from querysource.outputs.destinations.abstract import AbstractDestination


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
        self._tenant_name: str = resolved.get("tenant_name", "")
        # site_id can be provided directly to skip Graph site resolution
        self._site_id: str = resolved.get("site_id", "")

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

        # The msgraph/kiota SDK uses platform.version() to build the User-Agent
        # header. On Linux the string has a trailing space which aiohttp rejects
        # as an illegal header value. Patch it before the client is constructed.
        import platform as _platform
        _orig_version = _platform.version
        _platform.version = lambda: _orig_version().strip()

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
        """Return the Graph site-id for :attr:`_site`.

        Resolution order:
        1. ``site_id`` credential provided directly → return as-is (no API call).
        2. Hostname-based path: ``{tenant_name}.sharepoint.com:/sites/{site}:``
        3. Fallback path: ``root:/sites/{site}:``
        4. Search by display name via ``$search``.
        """
        # Fast path: site_id already known
        if self._site_id:
            return self._site_id

        # Primary lookup: hostname-qualified path (most reliable)
        if self._tenant_name:
            try:
                site_path = f"{self._tenant_name}.sharepoint.com:/sites/{self._site}:"
                site = await graph_client.sites.by_site_id(site_path).get()
                if site and site.id:
                    return site.id
            except Exception:
                pass

        # Secondary lookup: root-relative path
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

    def _parse_directory_path(self, directory: str) -> tuple:
        """Split *directory* into ``(library_name, path_within_library)``.

        When *directory* contains at least two segments, the first segment is
        the document-library name and the rest is the folder path inside that
        library.  When only one segment is given it is treated as a subfolder
        inside the default ``"Documents"`` library (single-segment paths are
        rarely library names, and this matches the common convention).
        ``"Shared Documents"`` is normalised to ``"Documents"``.

        Examples::

            "Shared Documents/Reports/2026" → ("Documents", "Reports/2026")
            "Documents/Tests"               → ("Documents", "Tests")
            "Tests"                         → ("Documents", "Tests")
            "troc/Project Management"       → ("troc", "Project Management")
        """
        if not directory:
            return "Documents", ""

        directory = directory.replace("\\", "/").strip().strip("/")
        if not directory:
            return "Documents", ""

        parts = directory.split("/")

        if len(parts) == 1:
            # Single segment → always treat as subfolder of default library
            return "Documents", parts[0]

        library_name = parts[0]
        path_within = "/".join(parts[1:])

        if library_name.lower() == "shared documents":
            library_name = "Documents"

        return library_name, path_within

    async def _resolve_drive(
        self, graph_client, site_id: str, library_name: str = "Documents"
    ) -> str:
        """Return the drive-id for *library_name* inside *site_id*.

        Performs a case-insensitive search across all document libraries.
        If the requested library is not found, logs the available names and
        falls back to the first library named "Documents" / "Shared Documents",
        or the first library overall.
        """
        try:
            drives = await graph_client.sites.by_site_id(site_id).drives.get()
            if not drives or not drives.value:
                raise OutputError(
                    f"ToSharepoint: no document libraries found for site '{site_id}'."
                )

            # Case-insensitive exact match
            for drive in drives.value:
                if drive.name.lower() == library_name.lower():
                    return drive.id

            # Log available libraries and fall back to default
            available = [d.name for d in drives.value]
            self.logger.warning(
                "ToSharepoint: library '%s' not found. Available: %s. Using default.",
                library_name,
                available,
            )
            for drive in drives.value:
                if drive.name.lower() in ("documents", "shared documents"):
                    return drive.id
            return drives.value[0].id

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
        library_name, path_within = self._parse_directory_path(self._directory)
        drive_id = await self._resolve_drive(graph_client, site_id, library_name)
        parent_id = await self._ensure_folder(graph_client, drive_id, path_within)

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
