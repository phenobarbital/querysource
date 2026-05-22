"""Airtable OAuth2 integration HTTP handlers.

Implements the OAuth2 consent and callback flow for Airtable:

- :class:`AirtableConnectView` — ``GET /api/v1/qs/integrations/airtable/connect``
  Generates a PKCE ``code_verifier``/``code_challenge`` pair, stores them in
  the session, and returns a minimal HTML consent page with an Airtable
  authorize URL.

- :class:`AirtableCallbackView` — ``GET /api/v1/qs/integrations/airtable/callback``
  Validates the ``state`` CSRF token, exchanges the authorization code for
  tokens, and persists ``{access_token, refresh_token, expires_at, scope,
  token_type}`` into ``session['airtable']``.

Both views read conf constants (``AIRTABLE_CLIENT_ID`` etc.) at *request time*
(not module import time) so they are easy to override in tests via monkeypatch.

References
----------
- Spec: ``sdd/specs/multi-threadsource-airtable.spec.md`` §2, §3 Module 4.
- Airtable OAuth2: https://airtable.com/developers/web/api/oauth-reference
"""
import base64
import hashlib
import html as html_lib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from querysource.handlers.abstract import AbstractHandler
from querysource import conf


# ---------------------------------------------------------------------------
# Shared HTML templates
# ---------------------------------------------------------------------------

_CONNECT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connect to Airtable — QuerySource</title>
  <style>
    body {{ font-family: sans-serif; display: flex; align-items: center;
            justify-content: center; height: 100vh; margin: 0;
            background: #f7f7f7; }}
    .card {{ background: white; padding: 2rem 3rem; border-radius: 8px;
             box-shadow: 0 2px 16px rgba(0,0,0,0.08); text-align: center; }}
    a.btn {{ display: inline-block; padding: .75rem 1.25rem; margin-top: 1rem;
             background: #ffbb00; color: #222; text-decoration: none;
             border-radius: 4px; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Connect to Airtable</h1>
    <p>Authorize QuerySource to read records from your Airtable bases.</p>
    <a class="btn" href="{authorize_url}">Connect</a>
  </div>
</body>
</html>
"""

_SESSION_UNAVAILABLE = (
    "navigator-session is not installed; "
    "OAuth flow unavailable. Use AIRTABLE_ACCESS_TOKEN instead."
)

_CONNECTED_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connected — QuerySource</title>
</head>
<body>
  <h1>Connected!</h1>
  <p>Your Airtable account is now linked to QuerySource. You may close this window.</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class AirtableConnectView(AbstractHandler):
    """Serve the Airtable OAuth2 consent page.

    ``GET /api/v1/qs/integrations/airtable/connect``

    Generates a PKCE code verifier, stores it alongside a CSRF state token in
    the session, and returns an HTML page with a "Connect" link pointing at
    Airtable's authorize endpoint.
    """

    async def get(self, request: web.Request) -> web.Response:
        """Handle the consent-page request.

        Args:
            request: Incoming aiohttp request.

        Returns:
            200 HTML consent page, or 503 when ``navigator_session`` is absent
            or OAuth credentials are not configured.
        """
        if not conf.AIRTABLE_CLIENT_ID or not conf.AIRTABLE_CLIENT_SECRET:
            return web.Response(
                status=503,
                text="AIRTABLE_CLIENT_ID and AIRTABLE_CLIENT_SECRET must be configured.",
                content_type="text/plain",
            )

        session = await self._get_user_session(request)
        if session is None:
            return web.Response(
                status=503,
                text=_SESSION_UNAVAILABLE,
                content_type="text/plain",
            )

        # ── PKCE + CSRF state generation ────────────────────────────────────
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

        session['airtable_oauth_state'] = {
            "state": state,
            "code_verifier": code_verifier,
        }

        # ── Build Airtable authorize URL ─────────────────────────────────────
        params = {
            "client_id": conf.AIRTABLE_CLIENT_ID,
            "redirect_uri": conf.AIRTABLE_REDIRECT_URI,
            "response_type": "code",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": conf.AIRTABLE_OAUTH_SCOPES,
        }
        authorize_url = f"https://airtable.com/oauth2/v1/authorize?{urlencode(params)}"

        html = _CONNECT_HTML.format(authorize_url=html_lib.escape(authorize_url))
        return web.Response(
            status=200,
            text=html,
            content_type="text/html",
        )


class AirtableCallbackView(AbstractHandler):
    """Handle the Airtable OAuth2 redirect callback.

    ``GET /api/v1/qs/integrations/airtable/callback``

    Validates the ``state`` CSRF token stored by :class:`AirtableConnectView`,
    exchanges the authorization code for tokens, and writes them into
    ``session['airtable']``.
    """

    async def get(self, request: web.Request) -> web.Response:
        """Handle the OAuth2 redirect from Airtable.

        Args:
            request: Incoming aiohttp request with ``code``, ``state``, and
                optionally ``error`` / ``error_description`` query params.

        Returns:
            200 HTML on success; 400 on CSRF mismatch / token exchange failure;
            503 when ``navigator_session`` is absent or OAuth credentials are
            not configured.
        """
        if not conf.AIRTABLE_CLIENT_ID or not conf.AIRTABLE_CLIENT_SECRET:
            return web.Response(
                status=503,
                text="AIRTABLE_CLIENT_ID and AIRTABLE_CLIENT_SECRET must be configured.",
                content_type="text/plain",
            )

        query = request.rel_url.query
        error = query.get('error')
        code = query.get('code')
        state = query.get('state')

        # ── OAuth2 provider error ────────────────────────────────────────────
        if error:
            error_description = query.get('error_description', '')
            return web.Response(
                status=400,
                text=f"Airtable authorization error: {error}. {error_description}",
                content_type="text/plain",
            )

        if not code or not state:
            return web.Response(
                status=400,
                text="Missing code or state",
                content_type="text/plain",
            )

        session = await self._get_user_session(request)
        if session is None:
            return web.Response(
                status=503,
                text=_SESSION_UNAVAILABLE,
                content_type="text/plain",
            )

        # ── CSRF validation (single-use state) ──────────────────────────────
        stored = session.get('airtable_oauth_state')
        # Always delete state immediately after read to prevent replay
        if 'airtable_oauth_state' in session:
            del session['airtable_oauth_state']

        if not stored or stored.get('state') != state:
            return web.Response(
                status=400,
                text="State mismatch (CSRF defense)",
                content_type="text/plain",
            )

        code_verifier = stored['code_verifier']

        # ── Token exchange ───────────────────────────────────────────────────
        basic = base64.b64encode(
            f"{conf.AIRTABLE_CLIENT_ID}:{conf.AIRTABLE_CLIENT_SECRET}".encode()
        ).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": conf.AIRTABLE_REDIRECT_URI,
            "code_verifier": code_verifier,
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(
                "https://airtable.com/oauth2/v1/token",
                headers=headers,
                data=data,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    return web.Response(
                        status=400,
                        text=f"Token exchange failed (HTTP {resp.status}): {body[:200]}",
                        content_type="text/plain",
                    )
                payload = await resp.json()

        # ── Validate token payload ───────────────────────────────────────────
        access_token = payload.get("access_token")
        if not access_token:
            return web.Response(
                status=400,
                text=f"Token endpoint returned no access_token. Body: {str(payload)[:200]}",
                content_type="text/plain",
            )

        # ── Compute expires_at and persist tokens ────────────────────────────
        expires_in = payload.get("expires_in")
        expires_at_iso: str | None = None
        if expires_in:
            expires_at_iso = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
            ).isoformat()

        session['airtable'] = {
            "access_token": access_token,
            "refresh_token": payload.get("refresh_token"),
            "expires_at": expires_at_iso,
            "scope": payload.get("scope"),
            "token_type": payload.get("token_type", "Bearer"),
        }

        return web.Response(
            status=200,
            text=_CONNECTED_HTML,
            content_type="text/html",
        )
