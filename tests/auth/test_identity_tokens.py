from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.auth.identity_tokens import (
    DelegatedIdentityError,
    SourceIdentityContext,
    resolve_delegated_token,
    user_id_from_session,
)


def _cred(minutes: int) -> dict:
    exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    return {
        "access_token": "tok",
        "token_type": "Bearer",
        "refresh_token": None,
        "id_token": None,
        "expires_at": exp,
        "scopes": [],
        "provider_user_id": "x",
    }


def test_user_id_from_session_numeric_only():
    assert user_id_from_session({"session": {"user_id": 42, "username": "bob"}}) == 42
    assert user_id_from_session({"user_id": "7"}) == 7
    assert user_id_from_session({"username": "bob"}) is None


@pytest.mark.asyncio
async def test_resolve_token_vault_hit():
    idp = SimpleNamespace(get_user_identity_credential=AsyncMock())
    auth = SimpleNamespace(_idp=idp)
    context = SourceIdentityContext(user_id=42, session={}, auth=auth)
    with patch("navigator_auth.identity.store.cached_credential", new=AsyncMock(return_value=_cred(30))), patch(
        "navigator_auth.identity.store.cache_credential", new=AsyncMock()
    ):
        token = await resolve_delegated_token(context)
    assert token.access_token == "tok"
    idp.get_user_identity_credential.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_token_vault_expiring_falls_back():
    idp = MagicMock()
    idp.get_user_identity_credential = AsyncMock(return_value=_cred(30))
    context = SourceIdentityContext(user_id=42, session={}, auth=SimpleNamespace(_idp=idp))
    with patch("navigator_auth.identity.store.cached_credential", new=AsyncMock(return_value=_cred(1))), patch(
        "navigator_auth.identity.store.cache_credential", new=AsyncMock()
    ) as cache:
        token = await resolve_delegated_token(context)
    assert token.access_token == "tok"
    idp.get_user_identity_credential.assert_awaited_once_with(42, "onedrive", auto_refresh=True)
    cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_token_sessionless_uses_idp():
    idp = MagicMock()
    idp.get_user_identity_credential = AsyncMock(return_value=_cred(30))
    context = SourceIdentityContext.for_scheduler(42, SimpleNamespace(_idp=idp))
    token = await resolve_delegated_token(context)
    assert token.access_token == "tok"
    idp.get_user_identity_credential.assert_awaited_once_with(42, "onedrive", auto_refresh=True)


@pytest.mark.asyncio
async def test_resolve_token_errors():
    from navigator_auth.exceptions import ConfigError, UserNotFound

    for context, reason in [(None, "no_user"), (SourceIdentityContext(42, auth=None), "auth_unavailable")]:
        with pytest.raises(DelegatedIdentityError) as error:
            await resolve_delegated_token(context)
        assert error.value.reason == reason
        assert error.value.link_url == "/api/v1/user/identities/link/onedrive"

    for exception, reason in [(UserNotFound(), "not_linked"), (ConfigError(), "refresh_failed")]:
        idp = MagicMock()
        idp.get_user_identity_credential = AsyncMock(side_effect=exception)
        context = SourceIdentityContext.for_scheduler(42, SimpleNamespace(_idp=idp))
        with pytest.raises(DelegatedIdentityError) as error:
            await resolve_delegated_token(context)
        assert error.value.reason == reason
        assert error.value.link_url == "/api/v1/user/identities/link/onedrive"
