"""Delegated identity-token resolution for MultiQS sources (FEAT-159)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from aiohttp import web

from ..exceptions import QueryException

logger = logging.getLogger(__name__)

ONEDRIVE_PROVIDER: str = "onedrive"
IDENTITY_LINK_PATH: str = "/api/v1/user/identities/link/{provider}"


class DelegatedIdentityError(QueryException):
    """No usable linked identity for a delegated source."""

    default_code: int = 409
    code: int = 409

    def __init__(self, message: str, *, provider: str, user_id: Optional[int], reason: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.user_id = user_id
        self.reason = reason
        self.link_url = IDENTITY_LINK_PATH.format(provider=provider)


@dataclass(frozen=True)
class DelegatedToken:
    """Resolved delegated access token and its optional expiry."""

    access_token: str
    expires_at: Optional[datetime]


def user_id_from_session(session: Any) -> Optional[int]:
    """Numeric user id from session[AUTH_SESSION_OBJECT]['user_id'] or session['user_id']."""
    try:
        from navigator_auth.conf import AUTH_SESSION_OBJECT
    except ImportError:
        AUTH_SESSION_OBJECT = "session"

    if session is None or not hasattr(session, "get"):
        return None
    try:
        session_data = session.get(AUTH_SESSION_OBJECT, {}) or {}
        value = session_data.get("user_id") if hasattr(session_data, "get") else None
        if value is None:
            value = session.get("user_id")
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError, AttributeError):
        return None


def identity_provider(auth: Any) -> Any | None:
    """auth.identity_provider (navigator-auth >=0.29.0) else auth._idp, else None."""
    if auth is None:
        return None
    return getattr(auth, "identity_provider", None) or getattr(auth, "_idp", None)


@dataclass(frozen=True)
class SourceIdentityContext:
    """Identity inputs carried from a request or scheduler invocation."""

    user_id: Optional[int]
    session: Any | None = None
    auth: Any | None = None
    origin: str = "request"

    @classmethod
    def from_request(cls, request: web.Request, session: Any) -> SourceIdentityContext:
        return cls(
            user_id=user_id_from_session(session),
            session=session,
            auth=request.app.get("auth") if request is not None else None,
            origin="request",
        )

    @classmethod
    def for_scheduler(cls, run_as_user_id: Optional[int], auth: Any) -> SourceIdentityContext:
        return cls(user_id=run_as_user_id, session=None, auth=auth, origin="scheduler")


def _identity_error(provider: str, user_id: Optional[int], reason: str, message: str) -> DelegatedIdentityError:
    """Build the stable public error shape for delegated identity failures."""
    logger.warning("Delegated identity resolution failed: user_id=%s provider=%s reason=%s", user_id, provider, reason)
    return DelegatedIdentityError(message, provider=provider, user_id=user_id, reason=reason)


async def resolve_delegated_token(
    context: SourceIdentityContext | None, provider: str = ONEDRIVE_PROVIDER
) -> DelegatedToken:
    """Resolve an access token on the caller's loop (vault -> IdP -> re-cache)."""
    user_id = context.user_id if context is not None else None
    if user_id is None:
        raise _identity_error(provider, user_id, "no_user", "A user identity is required.")

    try:
        from navigator_auth.conf import IDENTITY_REFRESH_LEEWAY
        from navigator_auth.exceptions import AuthException, ConfigError, UserNotFound
        from navigator_auth.identity.store import cache_credential, cached_credential
        from navigator_auth.identity.types import TokenResponse
    except ImportError as exc:
        raise _identity_error(provider, user_id, "auth_unavailable", "Identity authentication is unavailable.") from exc

    if context.session is not None:
        credential = await cached_credential(context.session, provider)
        if credential:
            try:
                token = TokenResponse.from_credential(credential)
                if not token.is_expiring(IDENTITY_REFRESH_LEEWAY):
                    expires_at = token.expires_at
                    if expires_at is not None and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    return DelegatedToken(access_token=token.access_token, expires_at=expires_at)
            except (KeyError, TypeError, ValueError):
                pass

    idp = identity_provider(context.auth)
    if idp is None:
        raise _identity_error(provider, user_id, "auth_unavailable", "Identity authentication is unavailable.")
    try:
        credential = await idp.get_user_identity_credential(user_id, provider, auto_refresh=True)
    except UserNotFound as exc:
        raise _identity_error(provider, user_id, "not_linked", "No linked identity was found.") from exc
    except (ConfigError, AuthException) as exc:
        raise _identity_error(provider, user_id, "refresh_failed", "The linked identity could not be refreshed.") from exc

    try:
        token = TokenResponse.from_credential(credential)
    except (KeyError, TypeError, ValueError) as exc:
        raise _identity_error(provider, user_id, "refresh_failed", "The linked identity returned an invalid token.") from exc

    if context.session is not None:
        try:
            await cache_credential(context.session, provider, credential)
        except Exception:
            logger.warning("Delegated identity cache failed: user_id=%s provider=%s reason=%s", user_id, provider, "refresh_failed")

    expires_at = token.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return DelegatedToken(access_token=token.access_token, expires_at=expires_at)
