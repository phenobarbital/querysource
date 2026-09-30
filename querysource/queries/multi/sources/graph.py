"""Shared Microsoft Graph helpers for MultiQS drive sources (FEAT-159)."""
from __future__ import annotations

import platform
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from azure.core.credentials import AccessToken

_PATCH_LOCK = threading.Lock()
_PATCH_DEPTH: int = 0
_ORIGINAL_VERSION = None


@contextmanager
def kiota_platform_version_patch() -> Iterator[None]:
    """Strip platform.version()'s trailing space for the kiota User-Agent.

    kiota builds its User-Agent from ``platform.version()``, which carries a
    trailing space on Linux that httpx rejects. Reference-counted under a lock:
    the first entry installs the wrapper, the last exit restores the original
    in ``finally`` — never leaked, never restored early under concurrency.
    """
    global _PATCH_DEPTH, _ORIGINAL_VERSION  # noqa: PLW0603
    with _PATCH_LOCK:
        if _PATCH_DEPTH == 0:
            _ORIGINAL_VERSION = platform.version
            platform.version = lambda: _ORIGINAL_VERSION().strip()
        _PATCH_DEPTH += 1
    try:
        yield
    finally:
        with _PATCH_LOCK:
            _PATCH_DEPTH -= 1
            if _PATCH_DEPTH == 0:
                platform.version = _ORIGINAL_VERSION
                _ORIGINAL_VERSION = None


class StaticTokenCredential:
    """AsyncTokenCredential adapter over an already-resolved delegated access token."""

    def __init__(self, access_token: str, expires_at: Optional[datetime]) -> None:
        self._access_token = access_token
        self._expires_at = expires_at

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        """Return the token; unknown expiry → now + 300 seconds (epoch)."""
        expires_on = int(self._expires_at.timestamp()) if self._expires_at else int(time.time()) + 300
        return AccessToken(self._access_token, expires_on)

    async def close(self) -> None:
        """No-op — nothing to release; never treated as an owned Azure credential."""

    async def __aenter__(self) -> StaticTokenCredential:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
