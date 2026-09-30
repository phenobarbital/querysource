"""OneDriveSource — download one CSV/Excel file from Microsoft OneDrive (FEAT-159)."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from aiohttp import web

from ....auth.identity_tokens import DelegatedToken, SourceIdentityContext, resolve_delegated_token
from .graph import GraphDriveSource, StaticTokenCredential


class OneDriveSource(GraphDriveSource):
    """Download one CSV/Excel file from OneDrive.

    Modes: ``auth: app`` (default) with ``user`` + path, or ``url``;
    ``auth: delegated`` (the requesting / run-as user's own drive via a linked
    ``onedrive`` identity) with path or ``url``.
    """

    _extra_name = "onedrive"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        # Wrapped in str(): the docs introspector treats a bare `x = options.get('k')`
        # as a sub-dict alias and would drop the field from the generated schema.
        self._auth_mode = str(options.get('auth', 'app'))
        self._user = str(options.get('user', ''))
        creds = options.get('credentials', {})
        self._client_id = self._with_fallback('client_id', creds.get('client_id', 'ONEDRIVE_APP_ID'), 'SHAREPOINT_APP_ID')
        self._client_secret = self._with_fallback(
            'client_secret', creds.get('client_secret', 'ONEDRIVE_APP_SECRET'), 'SHAREPOINT_APP_SECRET'
        )
        self._tenant_id = self._with_fallback('tenant_id', creds.get('tenant_id', 'ONEDRIVE_TENANT_ID'), 'SHAREPOINT_TENANT_ID')
        self._token: Optional[DelegatedToken] = None
        self._validate_mode()

    def _with_fallback(self, key: str, value: str, fallback_name: str) -> str:
        """Resolve value; when it stays the unresolved ONEDRIVE name, use the fallback."""
        resolved = self.resolve_credential(key, value)
        if resolved == value and value in {'ONEDRIVE_APP_ID', 'ONEDRIVE_APP_SECRET', 'ONEDRIVE_TENANT_ID'}:
            return self.resolve_credential(key, fallback_name)
        return resolved

    def _validate_mode(self) -> None:
        """Raise ``ValueError`` for an invalid OneDrive authentication combination."""
        if self._auth_mode not in {'app', 'delegated'}:
            raise ValueError("OneDrive auth must be 'app' or 'delegated'.")
        if self._auth_mode == 'app' and not self._url and not self._user:
            raise ValueError("OneDrive app mode requires 'user' unless 'url' is configured.")
        if self._auth_mode == 'delegated' and self._user:
            raise ValueError("OneDrive delegated mode does not accept 'user'.")
        if self._url and not self._url.lower().startswith('https://'):
            raise ValueError("OneDrive 'url' must be an https:// share or file URL.")
        if not self._url and not self._filename:
            raise ValueError("OneDrive requires either 'url' or a source 'filename'.")

    async def prepare(self, context: SourceIdentityContext | None) -> None:
        """Delegated only: resolve the user's access token on the caller's loop."""
        if self._auth_mode == 'delegated':
            self._token = await resolve_delegated_token(context)

    async def _credential(self) -> Any:
        if self._auth_mode == 'delegated':
            if self._token is None:
                raise RuntimeError("OneDriveSource delegated mode requires prepare() on the caller loop")
            return StaticTokenCredential(self._token.access_token, self._token.expires_at)
        return await super()._credential()

    async def _resolve_drive_item(self, client: Any) -> Any:
        """Resolve the configured drive and file path through Microsoft Graph."""
        directory = self.resolve_masks(self._directory).strip('/')
        filename = self.resolve_masks(self._filename)
        file_path = f"{directory}/{filename}" if directory else filename
        if self._auth_mode == 'delegated':
            drive = await client.me.drive.get()
        else:
            drive = await client.users.by_user_id(self._user).drive.get()
        drive_id = getattr(drive, 'id', None) if drive is not None else None
        item = None
        if drive_id:
            item = await client.drives.by_drive_id(drive_id).items.by_drive_item_id(f"root:/{file_path}:").get()
        if item is None or not getattr(item, 'id', None):
            raise RuntimeError(f"File '{self._filename}' not found in OneDrive directory '{self._directory}'.")
        return item
