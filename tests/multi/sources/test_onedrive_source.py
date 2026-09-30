"""Tests for OneDrive Graph source modes and credential handling."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.auth.identity_tokens import DelegatedToken
from querysource.queries.multi.sources.graph import StaticTokenCredential
from querysource.queries.multi.sources.onedrive import OneDriveSource


def _src(**opts):
    """Build a OneDrive source for a test configuration."""
    return OneDriveSource("od", opts, None, asyncio.Queue())


@pytest.mark.parametrize(
    "opts",
    [
        {"auth": "bogus", "source": {"filename": "f.csv"}, "user": "u"},
        {"auth": "app", "source": {"filename": "f.csv"}},
        {"auth": "delegated", "user": "u", "source": {"filename": "f.csv"}},
        {"auth": "app", "user": "u", "source": {}},
    ],
)
def test_onedrive_mode_matrix_invalid(opts):
    """Invalid authentication and path combinations fail at construction."""
    with pytest.raises(ValueError):
        _src(**opts)


def test_onedrive_valid_modes_construct():
    """App and delegated modes accept both path and sharing URL forms."""
    _src(auth="app", user="u", source={"filename": "f.csv"})
    _src(auth="app", source={"url": "https://share/link"})
    _src(auth="delegated", source={"filename": "f.csv"})
    _src(auth="delegated", source={"url": "https://share/link"})


def test_onedrive_credential_fallback():
    """Unresolved OneDrive names fall back to the SharePoint credential names."""
    source = _src(auth="app", user="u", source={"filename": "f.csv"})

    def resolve(key, value):
        return {"SHAREPOINT_APP_ID": "sp-id", "SHAREPOINT_APP_SECRET": "sp-secret", "SHAREPOINT_TENANT_ID": "sp-tenant"}.get(
            value, value
        )

    with patch.object(source, "resolve_credential", side_effect=resolve):
        source._client_id = source._with_fallback("client_id", "ONEDRIVE_APP_ID", "SHAREPOINT_APP_ID")
        source._client_secret = source._with_fallback("client_secret", "ONEDRIVE_APP_SECRET", "SHAREPOINT_APP_SECRET")
        source._tenant_id = source._with_fallback("tenant_id", "ONEDRIVE_TENANT_ID", "SHAREPOINT_TENANT_ID")

    assert (source._client_id, source._client_secret, source._tenant_id) == ("sp-id", "sp-secret", "sp-tenant")


@pytest.mark.asyncio
async def test_onedrive_app_user_drive_path():
    """App mode resolves a user's drive and the root-relative file path."""
    source = _src(auth="app", user="u", source={"directory": "/dir/", "filename": "f.csv"})
    client = MagicMock()
    client.users.by_user_id.return_value.drive.get = AsyncMock(return_value=MagicMock(id="D"))
    item = MagicMock(id="item")
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=item)

    assert await source._resolve_drive_item(client) is item
    client.users.by_user_id.assert_called_once_with("u")
    client.drives.by_drive_id.return_value.items.by_drive_item_id.assert_called_once_with("root:/dir/f.csv:")


@pytest.mark.asyncio
async def test_onedrive_delegated_me_drive():
    """Delegated mode prepares a static credential and resolves the user's drive."""
    source = _src(auth="delegated", source={"filename": "f.csv"})
    token = DelegatedToken("access", datetime.now(timezone.utc))
    client = MagicMock()
    client.me.drive.get = AsyncMock(return_value=MagicMock(id="D"))
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=MagicMock(id="item"))
    with patch("querysource.queries.multi.sources.onedrive.resolve_delegated_token", AsyncMock(return_value=token)) as resolver:
        await source.prepare(MagicMock())
    resolver.assert_awaited_once()
    assert isinstance(await source._credential(), StaticTokenCredential)
    await source._resolve_drive_item(client)
    client.me.drive.get.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_onedrive_delegated_without_prepare_raises():
    """Delegated credential creation requires caller-loop preparation."""
    source = _src(auth="delegated", source={"filename": "f.csv"})
    with pytest.raises(RuntimeError, match="requires prepare\(\)"):
        await source._credential()


@pytest.mark.asyncio
async def test_onedrive_missing_item_message():
    """Path mode reports the configured OneDrive path when Graph finds nothing."""
    source = _src(auth="app", user="u", source={"directory": "dir", "filename": "f.csv"})
    client = MagicMock()
    client.users.by_user_id.return_value.drive.get = AsyncMock(return_value=MagicMock(id="D"))
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="File 'f.csv' not found in OneDrive directory 'dir'"):
        await source._resolve_drive_item(client)
