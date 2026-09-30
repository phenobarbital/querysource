"""FEAT-159 request-path integration: vault → prepare → thread → Graph (mocked)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.auth.identity_tokens import SourceIdentityContext
from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources.graph import GraphDriveSource, StaticTokenCredential


def _credential() -> dict:
    """Build a non-expiring navigator-auth credential fixture."""
    return {
        "access_token": "delegated-access-token",
        "token_type": "Bearer",
        "refresh_token": None,
        "id_token": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "scopes": [],
        "provider_user_id": "42",
    }


def _httpx_manager(content: bytes) -> MagicMock:
    """Return an async ``httpx.AsyncClient`` context manager for CSV bytes."""
    response = MagicMock(content=content)
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    return manager


@pytest.mark.asyncio
async def test_onedrive_delegated_end_to_end_mocked() -> None:
    """Resolve the vault credential before the thread fetches a mocked Graph CSV."""
    events: list[str] = []
    item = MagicMock(id="item-id", additional_data={"@microsoft.graph.downloadUrl": "https://download"})
    graph_client = MagicMock()
    graph_client.me.drive.get = AsyncMock(return_value=SimpleNamespace(id="drive-id"))
    graph_client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.get = AsyncMock(return_value=item)
    graph_service = MagicMock(side_effect=lambda **kwargs: (events.append("thread"), graph_client)[1])
    vault_credential = AsyncMock(side_effect=lambda *args: (events.append("prepare"), _credential())[1])
    context = SourceIdentityContext(user_id=42, session={"session": {"user_id": 42}}, auth=MagicMock())
    query = MultiQS(
        query={"sources": [{"OneDriveSource": {"auth": "delegated", "source": {"filename": "report.csv"}}}]},
        identity_context=context,
    )

    with patch("navigator_auth.identity.store.cached_credential", new=vault_credential), patch(
        "querysource.queries.multi.sources.graph.GraphDriveSource._import_graph",
        return_value=(MagicMock(), graph_service),
    ), patch("httpx.AsyncClient", return_value=_httpx_manager(b"name,value\na,1\n")):
        result, _ = await query.query()

    frame = result
    assert isinstance(frame, pd.DataFrame)
    pd.testing.assert_frame_equal(frame, pd.DataFrame({"name": ["a"], "value": [1]}))
    vault_credential.assert_awaited_once_with(context.session, "onedrive")
    assert events == ["prepare", "thread"]
    assert isinstance(graph_service.call_args.kwargs["credentials"], StaticTokenCredential)
