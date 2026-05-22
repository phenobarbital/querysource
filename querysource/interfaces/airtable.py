"""AirtableInterface — all Airtable HTTP API interactions for QuerySource.

This module is the single point of contact with the Airtable REST API.
Read methods are fully implemented; write methods are stubs that raise
``NotImplementedError`` so the public surface is stable when a future
feature adds write support (see FEAT-096 §1 Non-Goals).

Dependencies: ``aiohttp`` (existing project dep), stdlib only.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import aiohttp


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(slots=True)  # requires Python 3.10+
class AirtableTokens:
    """OAuth2 token bundle stored in ``session['airtable']``.

    Args:
        access_token: The current bearer token (OAuth2 or PAT).
        refresh_token: Refresh token for OAuth2; ``None`` for PAT auth.
        expires_at: Absolute UTC expiry time; ``None`` when unknown.
        scope: Space-separated OAuth2 scopes granted; ``None`` for PAT.
        token_type: Always ``"Bearer"`` per Airtable's OAuth2 spec.
    """

    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]
    scope: Optional[str]
    token_type: str = "Bearer"


class AirtableReauthRequired(RuntimeError):
    """Raised when the session OAuth token is expired and refresh failed.

    The frontend / CLI is expected to catch this and prompt the user to
    re-run the ``/api/v1/qs/integrations/airtable/connect`` flow.
    """


# Callback type: the Source layer wires this so the Interface can persist
# freshly-refreshed tokens back into the session without knowing about sessions.
TokenPersistFn = Callable[[AirtableTokens], Awaitable[None]]


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _compute_expires_at(expires_in: Optional[int]) -> Optional[datetime]:
    """Convert an ``expires_in`` seconds value to an absolute UTC datetime.

    Args:
        expires_in: Seconds until the token expires, or ``None``.

    Returns:
        An aware UTC ``datetime`` offset from *now*, or ``None``.
    """
    if not expires_in:
        return None
    return datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))


# ---------------------------------------------------------------------------
# AirtableInterface
# ---------------------------------------------------------------------------

class AirtableInterface:
    """Encapsulates ALL Airtable HTTP API interactions.

    Read methods are implemented; write methods are stubs that raise
    ``NotImplementedError`` so the public surface is stable when a future
    feature adds write support.

    One instance per source invocation — do NOT share across requests.

    Args:
        tokens: Current bearer-token bundle.
        is_oauth: ``True`` for OAuth2 user tokens; ``False`` for PAT.
        client_id: OAuth2 client ID (required for token refresh).
        client_secret: OAuth2 client secret (required for token refresh).
        persist_tokens: Async callback invoked after a successful token
            refresh so the caller can persist the new bundle.
        timeout_seconds: Total request timeout passed to ``aiohttp``.
    """

    BASE_URL: str = "https://api.airtable.com/v0"
    OAUTH_TOKEN_URL: str = "https://airtable.com/oauth2/v1/token"
    OAUTH_AUTHORIZE_URL: str = "https://airtable.com/oauth2/v1/authorize"

    def __init__(
        self,
        tokens: AirtableTokens,
        is_oauth: bool,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        persist_tokens: Optional[TokenPersistFn] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._tokens = tokens
        self._is_oauth = is_oauth
        self._client_id = client_id
        self._client_secret = client_secret
        self._persist_tokens = persist_tokens
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._logger = logging.getLogger(__name__)

    # ── URL parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def parse_url(url: str) -> tuple[str, str, Optional[str]]:
        """Parse an Airtable base URL into its constituent identifiers.

        Accepts ``https://airtable.com/<baseId>/<tableId>[/<viewId>]`` with
        optional query strings and trailing slashes; both are silently ignored.

        Args:
            url: Full Airtable web URL.

        Returns:
            A ``(base_id, table_id, view_id_or_None)`` tuple.

        Raises:
            ValueError: When *url* is not a valid Airtable URL or is missing
                the required base/table path segments.
        """
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or parsed.netloc != "airtable.com":
            raise ValueError(f"Not an Airtable URL: {url!r}")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError(f"Airtable URL missing base/table segment: {url!r}")
        base_id = parts[0]
        table_id = parts[1]
        view_id = parts[2] if len(parts) >= 3 else None
        return base_id, table_id, view_id

    # ── Auth helpers ─────────────────────────────────────────────────────────

    def _is_token_expired(self) -> bool:
        """Return ``True`` when the current token is expired (or about to expire).

        Adds a 30-second buffer so a token expiring imminently is treated as
        already expired, avoiding 401 errors on in-flight requests.

        Returns:
            ``True`` when ``expires_at`` is known and the token has expired
            (within the 30-second buffer), ``False`` otherwise.
        """
        if self._tokens is None or self._tokens.expires_at is None:
            return False
        return datetime.now(tz=timezone.utc) >= self._tokens.expires_at - timedelta(seconds=30)

    def _auth_headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header dict for the current token."""
        return {"Authorization": f"Bearer {self._tokens.access_token}"}

    # ── Private HTTP helpers ─────────────────────────────────────────────────

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> dict:
        """Check status and deserialise a response to a dict.

        Args:
            resp: An open ``aiohttp.ClientResponse``.

        Returns:
            The parsed JSON body as a dict.

        Raises:
            RuntimeError: On HTTP 429 (rate limit).
            aiohttp.ClientResponseError: On other non-2xx statuses.
        """
        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After", "unknown")
            raise RuntimeError(
                f"Airtable API rate limit exceeded (HTTP 429). Retry-After: {retry_after}s."
            )
        resp.raise_for_status()
        return await resp.json()

    async def _refresh_tokens(self, session: aiohttp.ClientSession) -> None:
        """Exchange the stored refresh token for a new token pair.

        Updates ``self._tokens`` in-place and calls ``self._persist_tokens``
        if a callback was provided.  Airtable rotates refresh tokens on every
        exchange — the new refresh token always overwrites the old one.

        Args:
            session: An open ``aiohttp.ClientSession`` to reuse.

        Raises:
            AirtableReauthRequired: When any prerequisite is missing or the
                token-endpoint returns a non-2xx response.
        """
        if not (self._client_id and self._client_secret and self._tokens.refresh_token):
            raise AirtableReauthRequired(
                "Cannot refresh: missing client_id, client_secret, or refresh_token."
            )
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._tokens.refresh_token,
        }
        async with session.post(self.OAUTH_TOKEN_URL, headers=headers, data=data) as resp:
            if resp.status >= 400:
                body = await resp.text()
                self._logger.warning(
                    "Airtable refresh failed (%s): %s", resp.status, body[:200]
                )
                raise AirtableReauthRequired(f"Refresh exchange failed: HTTP {resp.status}")
            payload = await resp.json()

        new_tokens = AirtableTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token") or self._tokens.refresh_token,
            expires_at=_compute_expires_at(payload.get("expires_in")),
            scope=payload.get("scope") or self._tokens.scope,
            token_type=payload.get("token_type", "Bearer"),
        )
        self._tokens = new_tokens
        if self._persist_tokens is not None:
            await self._persist_tokens(new_tokens)

    async def _request_with_refresh(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict:
        """Issue a request; on 401 (OAuth) try one refresh + retry.

        PAT auth: propagates 401 as ``RuntimeError``.
        OAuth auth: attempts one refresh; on second 401 or refresh failure
        raises ``AirtableReauthRequired``.

        Args:
            session: Open ``aiohttp.ClientSession`` to reuse.
            method: HTTP method string (``"GET"``, ``"POST"``, …).
            url: Full request URL.
            **kwargs: Forwarded to ``session.request``.

        Returns:
            Parsed JSON response body.
        """
        # Proactively refresh before the request if the token is already expired.
        if self._is_oauth and self._is_token_expired():
            await self._refresh_tokens(session)

        headers = kwargs.pop("headers", {})
        headers.update(self._auth_headers())
        async with session.request(method, url, headers=headers, **kwargs) as resp:
            if resp.status == 401:
                body = await resp.text()
                if self._is_oauth and self._tokens.refresh_token:
                    await self._refresh_tokens(session)
                    headers = self._auth_headers()
                    async with session.request(method, url, headers=headers, **kwargs) as resp2:
                        if resp2.status == 401:
                            raise AirtableReauthRequired(
                                "OAuth refresh succeeded but Airtable still returned 401 "
                                "— reconnect required."
                            )
                        return await self._handle_response(resp2)
                if self._is_oauth:
                    raise AirtableReauthRequired(
                        "Session OAuth token expired and no refresh token available "
                        "— reconnect required."
                    )
                # PAT case
                raise RuntimeError(
                    f"AirtableInterface: 401 Unauthorized (PAT auth). Body: {body[:200]}"
                )
            return await self._handle_response(resp)

    # ── READ ─────────────────────────────────────────────────────────────────

    async def list_records(
        self,
        base_id: str,
        table: str,
        view: Optional[str] = None,
        *,
        filter_by_formula: Optional[str] = None,
        max_records: Optional[int] = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Paginate over Airtable's records endpoint and return all records.

        Handles offset-based pagination and 401-refresh transparently.

        Args:
            base_id: The Airtable base ID (e.g. ``"appXXX"``).
            table: Table ID or table name (e.g. ``"tblYYY"`` or ``"My Table"``).
            view: Optional view ID or name to restrict the result set.
            filter_by_formula: An Airtable formula string to filter records.
            max_records: Hard upper-bound on the number of records returned.
                ``None`` means no limit.
            page_size: Records per page (capped at 100 per Airtable's limit).

        Returns:
            List of raw Airtable record dicts:
            ``[{"id": "rec…", "fields": {…}, "createdTime": "…"}, …]``.

        Raises:
            ValueError: When *base_id* or *table* are empty.
            AirtableReauthRequired: When OAuth refresh fails.
            RuntimeError: On PAT 401 or HTTP 429 rate limit.
        """
        if not base_id or not table:
            raise ValueError(
                "AirtableInterface.list_records: 'base_id' and 'table' are required."
            )

        url = f"{self.BASE_URL}/{base_id}/{table}"
        effective_page_size = min(page_size, 100)
        accumulated: list[dict] = []
        offset: Optional[str] = None

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            while True:
                params: dict[str, Any] = {"pageSize": effective_page_size}
                if view:
                    params["view"] = view
                if filter_by_formula:
                    params["filterByFormula"] = filter_by_formula
                if max_records:
                    params["maxRecords"] = max_records
                if offset:
                    params["offset"] = offset

                data = await self._request_with_refresh(
                    session, "GET", url, params=params
                )
                accumulated.extend(data.get("records", []))

                if max_records and len(accumulated) >= max_records:
                    accumulated = accumulated[:max_records]
                    break

                offset = data.get("offset")
                if not offset:
                    break

        return accumulated

    # ── WRITE (stubs — deferred to future feature) ────────────────────────────

    async def create_records(
        self, base_id: str, table: str, records: list[dict]
    ) -> list[dict]:
        """Stub — Airtable write support is deferred.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Airtable write support is deferred — see FEAT-096 §1 Non-Goals"
        )

    async def update_records(
        self, base_id: str, table: str, records: list[dict]
    ) -> list[dict]:
        """Stub — Airtable write support is deferred.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Airtable write support is deferred — see FEAT-096 §1 Non-Goals"
        )

    async def delete_records(
        self, base_id: str, table: str, record_ids: list[str]
    ) -> list[dict]:
        """Stub — Airtable write support is deferred.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Airtable write support is deferred — see FEAT-096 §1 Non-Goals"
        )

    async def create_table(self, base_id: str, schema: dict) -> dict:
        """Stub — Airtable write support is deferred.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Airtable write support is deferred — see FEAT-096 §1 Non-Goals"
        )
