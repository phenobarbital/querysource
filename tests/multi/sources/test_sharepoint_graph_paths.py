"""Regression coverage for SharePoint's GraphDriveSource integration."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from querysource.queries.multi._introspect import extract_source_schema
from querysource.queries.multi.sources import SharepointSource


def _triples(attrs):
    """Return stable schema fields for snapshot comparison."""
    return [(attr["name"], attr.get("default"), attr.get("required")) for attr in attrs]


def _source(directory: str = "Shared Documents/Gen") -> SharepointSource:
    """Build a SharePoint source with path-mode configuration."""
    return SharepointSource(
        "sharepoint",
        {
            "credentials": {"client_id": "id", "client_secret": "secret", "tenant_id": "tenant", "tenant_name": "contoso", "site": "Roadshows"},
            "source": {"filename": "file.csv", "directory": directory},
        },
        None,
        asyncio.Queue(),
    )


def _drive(name: str, drive_id: str) -> MagicMock:
    """Build a drive with real name and id attributes."""
    drive = MagicMock()
    drive.name = name
    drive.id = drive_id
    return drive


def test_sharepoint_schema_unchanged():
    """SharePoint's public configuration schema remains stable after rebasing."""
    live = extract_source_schema(SharepointSource)["attributes"]
    with open("generated/SharepointSource.json", encoding="utf-8") as snapshot:
        generated = json.load(snapshot)["attributes"]
    assert _triples(live) == _triples(generated)


@pytest.mark.asyncio
async def test_sharepoint_resolve_drive_item_paths():
    """Shared Documents resolves to Documents and an unknown library falls back."""
    source = _source()
    client = MagicMock()
    client.sites.by_site_id.return_value.get = AsyncMock(return_value=MagicMock(id="site-id"))
    documents = _drive("Documents", "documents-id")
    other = _drive("Other", "other-id")
    client.sites.by_site_id.return_value.drives.get = AsyncMock(return_value=MagicMock(value=[documents, other]))
    item = MagicMock(id="item-id")
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=item)

    assert await source._resolve_drive_item(client) is item
    client.drives.by_drive_id.assert_called_once_with("documents-id")
    client.drives.by_drive_id.return_value.items.by_drive_item_id.assert_called_once_with("root:/Gen/file.csv:")

    fallback_source = _source("Unknown/Gen")
    assert await fallback_source._resolve_drive_item(client) is item
    assert client.drives.by_drive_id.call_args_list[-1].args == ("documents-id",)


@pytest.mark.asyncio
async def test_sharepoint_file_not_found_message():
    """Missing drive items retain the established SharePoint error message."""
    source = _source("Reports")
    client = MagicMock()
    client.sites.by_site_id.return_value.get = AsyncMock(return_value=MagicMock(id="site-id"))
    client.sites.by_site_id.return_value.drives.get = AsyncMock(return_value=MagicMock(value=[_drive("Documents", "documents-id")]))
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="not found in SharePoint directory"):
        await source._resolve_drive_item(client)


@pytest.mark.asyncio
async def test_sharepoint_library_not_found_message():
    """Missing libraries retain the established SharePoint error message."""
    source = _source()
    client = MagicMock()
    client.sites.by_site_id.return_value.get = AsyncMock(return_value=MagicMock(id="site-id"))
    client.sites.by_site_id.return_value.drives.get = AsyncMock(return_value=MagicMock(value=[]))

    with pytest.raises(RuntimeError, match="No document library found for site 'Roadshows' matching 'Documents'"):
        await source._resolve_drive_item(client)
