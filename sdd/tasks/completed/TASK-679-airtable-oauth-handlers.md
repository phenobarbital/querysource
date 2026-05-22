# TASK-679: OAuth handler views (`/connect` consent HTML + `/callback` token exchange)

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-675, TASK-678
**Assigned-to**: unassigned

---

## Context

This is the first OAuth callback in the entire querysource package (verified by grep — no `oauth|callback|access_token|refresh_token` matches in `querysource/handlers/` or `querysource/auth/`). It owns the user-facing consent page AND the token-exchange handler.

Implements §3 Module 4 of the spec.

---

## Scope

Create a new package and two view classes:

- `querysource/handlers/integrations/__init__.py` — empty package marker (single line: `"""QuerySource integration HTTP handlers (FEAT-096+)."""`).
- `querysource/handlers/integrations/airtable.py` — contains:

### `AirtableConnectView`
- Inherits `AbstractHandler` (from `querysource/handlers/abstract.py`).
- `async def get(self, request: web.Request) -> web.Response`:
  1. Resolves the current session via `session = await self._get_user_session(request)` (the helper at `querysource/handlers/abstract.py:225`). If `session is None`, return HTTP 503 with body `"navigator-session is not installed; OAuth flow unavailable. Use AIRTABLE_ACCESS_TOKEN instead."`.
  2. Generates `state = secrets.token_urlsafe(32)` and `code_verifier = secrets.token_urlsafe(64)`; computes `code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()`.
  3. Stores `session['airtable_oauth_state'] = {"state": state, "code_verifier": code_verifier}`.
  4. Builds the Airtable authorize URL:
     ```
     https://airtable.com/oauth2/v1/authorize
       ?client_id=<AIRTABLE_CLIENT_ID>
       &redirect_uri=<AIRTABLE_REDIRECT_URI>
       &response_type=code
       &state=<state>
       &code_challenge=<code_challenge>
       &code_challenge_method=S256
       &scope=data.records:read%20data.recordComments:read%20schema.bases:read
     ```
  5. Returns a 200 HTML response containing a centered "Connect to Airtable" anchor pointing to the authorize URL. Keep the HTML ≤ 30 lines, inline `<style>`, no JS.

### `AirtableCallbackView`
- Inherits `AbstractHandler`.
- `async def get(self, request: web.Request) -> web.Response`:
  1. Read `code = request.rel_url.query.get('code')`, `state = request.rel_url.query.get('state')`, `error = request.rel_url.query.get('error')`.
  2. If `error` set → return 400 HTML with `error` + `request.rel_url.query.get('error_description')`.
  3. If `code` or `state` missing → return 400 `"Missing code or state"`.
  4. Resolve session. If `None` → 503 (same message as Connect view).
  5. Read `stored = session.get('airtable_oauth_state')`. If not set OR `stored['state'] != state` → return 400 `"State mismatch (CSRF defense)"`. Always delete `session['airtable_oauth_state']` after read to prevent replay.
  6. POST to `https://airtable.com/oauth2/v1/token` with:
     - Headers: `Authorization: Basic base64(client_id:client_secret)`, `Content-Type: application/x-www-form-urlencoded`.
     - Body params: `grant_type=authorization_code`, `code=<code>`, `redirect_uri=<AIRTABLE_REDIRECT_URI>`, `code_verifier=<stored.code_verifier>`.
  7. On non-2xx → return 400 with body excerpt (truncated to 200 chars).
  8. On success, parse JSON → compute `expires_at_iso = (now_utc + expires_in_seconds).isoformat()` → write `session['airtable'] = {"access_token": ..., "refresh_token": ..., "expires_at": expires_at_iso, "scope": ..., "token_type": ...}`.
  9. Return a 200 HTML "Connected!" confirmation page.

Both views read `AIRTABLE_CLIENT_ID`, `AIRTABLE_CLIENT_SECRET`, and `AIRTABLE_REDIRECT_URI` from `querysource.conf` at request time (not at module import — keeps tests easy via monkeypatch).

Tests in `tests/handlers/test_airtable_oauth.py` per Test Specification.

**NOT in scope**:
- Registering the views into `QuerySource.setup()` — that is `TASK-680`.
- The actual Airtable API calls in `AirtableInterface` for refresh — `TASK-675`. This task does the **initial** code-for-token exchange directly inside the view (not via the Interface), because the view runs before any Interface instance exists.
- Logging out / disconnecting the integration — out of scope; users delete the session key out-of-band if needed.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/integrations/__init__.py` | CREATE | Package marker |
| `querysource/handlers/integrations/airtable.py` | CREATE | `AirtableConnectView` + `AirtableCallbackView` |
| `tests/handlers/test_airtable_oauth.py` | CREATE | Unit tests for both views, including CSRF + 503 paths |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# In querysource/handlers/integrations/airtable.py:
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from querysource.handlers.abstract import AbstractHandler   # verified: querysource/handlers/components.py:18
from querysource import conf                                # verified: settings module
```

### Existing Signatures to Use

```python
# querysource/handlers/abstract.py:225-251 (verified):
class AbstractHandler:
    async def _get_user_session(
        self,
        request: web.Request,
    ) -> Optional[SessionData]: ...
    # Returns None if navigator_session is not installed OR no session exists.
    # Memoizes via request['user_session'].


# querysource/handlers/components.py:24 (verified — handler class pattern):
class ComponentHandler(AbstractHandler):
    async def list_components(self, request: web.Request) -> web.Response: ...
    async def validate_pipeline(self, request: web.Request) -> web.Response: ...


# querysource/conf.py (after TASK-678 lands):
AIRTABLE_CLIENT_ID: str | None
AIRTABLE_CLIENT_SECRET: str | None
AIRTABLE_REDIRECT_URI: str
QS_AIRTABLE_OAUTH_ENABLED: bool


# navigator_session.SessionData is dict-like (verified: handlers/abstract.py:318
# does `session.get(AUTH_SESSION_OBJECT, {})`). Both reads and writes use
# subscript / .get() syntax. There is no `session.put(...)` or `session.set(...)`.
```

### Does NOT Exist

- ~~A `routes` decorator pattern in querysource handlers~~ — routes are registered imperatively inside `QuerySource.setup()`. Do NOT use `@web.middleware` or `web.RouteTableDef()`.
- ~~`querysource.handlers.OAuthBase`~~ — there is no shared OAuth base class. Inherit `AbstractHandler` directly.
- ~~`request.app['oauth_state_store']`~~ — no app-level state store exists; we use the session itself.
- ~~An aiohttp Jinja2 / template engine wired up for handlers~~ — return inline HTML strings via `web.Response(text=..., content_type='text/html')`.
- ~~A built-in PKCE helper anywhere in the codebase~~ — implement inline with stdlib (`secrets`, `hashlib`, `base64`).

### Airtable OAuth2 Reference (cite if Airtable changes their docs)

- Authorize endpoint: `https://airtable.com/oauth2/v1/authorize` (GET — redirect target).
- Token endpoint: `https://airtable.com/oauth2/v1/token` (POST `application/x-www-form-urlencoded`).
- Required scopes for read-only record fetch: `data.records:read` (other scopes are best-effort additions).
- Response: `{ "access_token", "refresh_token", "expires_in" (seconds), "token_type": "Bearer", "scope" }`.

---

## Implementation Notes

### Pattern to Follow

`AirtableConnectView` HTML body (keep it minimal — see spec §7 Patterns):

```python
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
```

For PKCE:

```python
verifier = secrets.token_urlsafe(64)            # 86 chars, well within 43-128 range
digest = hashlib.sha256(verifier.encode()).digest()
challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
```

For URL building, use `urlencode` over the query params dict — never hand-concatenate strings (avoids encoding bugs with the `redirect_uri`).

### Key Constraints

- The leaked PAT (`pat36EoFVW…`) MUST NOT appear in any default or fallback. The Connect view does NOT read `AIRTABLE_ACCESS_TOKEN` at all (PAT is the *fallback* for the Source; OAuth is the *interactive* path).
- `session['airtable_oauth_state']` MUST be deleted in the Callback view as soon as it is read (single-use to prevent replay).
- Both views return early-403/503 with informative `Content-Type: text/plain` bodies rather than letting unhandled exceptions surface as 500s.
- Never log `code`, `code_verifier`, or token values. It is acceptable to log "exchanged code for tokens" + the user identifier at INFO.
- All `aiohttp.ClientSession` usage MUST set an explicit `aiohttp.ClientTimeout(total=30)` (mirror `querysource/queries/multi/sources/smartsheet.py:77`).
- View methods are `async def get(self, request)` to allow registration via `app.router.add_get(path, view.get)`. They are NOT `web.View` subclasses with separate `get`/`post` methods.

### References in Codebase

- `querysource/handlers/components.py:24-...` — closest pattern (handler class with async methods accepting `request`).
- `querysource/handlers/abstract.py:225-251` — `_get_user_session` helper this task relies on.

---

## Acceptance Criteria

- [ ] `querysource/handlers/integrations/airtable.py` contains both `AirtableConnectView` and `AirtableCallbackView`.
- [ ] `AirtableConnectView.get` returns a 200 HTML response with an anchor tag whose `href` contains: `client_id`, `redirect_uri`, `response_type=code`, `state`, `code_challenge`, `code_challenge_method=S256`.
- [ ] `AirtableConnectView.get` writes `session['airtable_oauth_state']` with both `state` and `code_verifier`.
- [ ] When `_get_user_session` returns `None` (mocked), `AirtableConnectView.get` returns HTTP 503.
- [ ] `AirtableCallbackView.get` with `state` matching what `/connect` stored AND a mocked successful token exchange → writes `session['airtable']` with all five keys (`access_token`, `refresh_token`, `expires_at`, `scope`, `token_type`) and returns 200.
- [ ] After Callback handles a request, `session['airtable_oauth_state']` no longer exists (single-use enforcement).
- [ ] `AirtableCallbackView.get` with mismatching `state` → 400 with body containing `"State mismatch"`. Session writeback was NOT performed.
- [ ] `AirtableCallbackView.get` with `error` query param → 400 with body containing the `error` and `error_description`.
- [ ] `AirtableCallbackView.get` with non-2xx from token endpoint → 400; session was NOT updated.
- [ ] Token-exchange request includes the `code_verifier` (PKCE) and HTTP Basic auth header.
- [ ] No log line contains the verbatim `code` or `code_verifier` string (assert via caplog).
- [ ] `pytest tests/handlers/test_airtable_oauth.py -v` passes.
- [ ] `ruff check querysource/handlers/integrations/ tests/handlers/test_airtable_oauth.py` passes.

---

## Test Specification

```python
# tests/handlers/test_airtable_oauth.py
import base64
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse, parse_qs

import pytest
from aiohttp import web
from aioresponses import aioresponses

from querysource.handlers.integrations.airtable import (
    AirtableConnectView,
    AirtableCallbackView,
)


def _make_request(query: dict | None = None, session_data: dict | None = None):
    """Build a minimally-functional MagicMock aiohttp request."""
    req = MagicMock(spec=web.Request)
    if query is None:
        query = {}
    req.rel_url = MagicMock()
    req.rel_url.query = query
    req.get.return_value = None
    # session is set via patched _get_user_session, not on request directly
    req._session_data = session_data if session_data is not None else {}
    return req


@pytest.fixture(autouse=True)
def _patch_conf(monkeypatch):
    monkeypatch.setattr(
        "querysource.handlers.integrations.airtable.conf.AIRTABLE_CLIENT_ID",
        "test-client-id",
    )
    monkeypatch.setattr(
        "querysource.handlers.integrations.airtable.conf.AIRTABLE_CLIENT_SECRET",
        "test-client-secret",
    )
    monkeypatch.setattr(
        "querysource.handlers.integrations.airtable.conf.AIRTABLE_REDIRECT_URI",
        "https://example.com/api/v1/qs/integrations/airtable/callback",
    )


class TestConnectView:
    @pytest.mark.asyncio
    async def test_returns_200_with_authorize_link(self):
        view = AirtableConnectView()
        session_data = {}
        req = _make_request(session_data=session_data)
        with patch.object(
            AirtableConnectView, "_get_user_session",
            AsyncMock(return_value=session_data),
        ):
            resp = await view.get(req)
        assert resp.status == 200
        body = resp.text
        # Verify authorize URL params
        assert "client_id=test-client-id" in body
        assert "code_challenge=" in body
        assert "code_challenge_method=S256" in body
        assert "state=" in body
        assert "response_type=code" in body
        # Session was written
        assert "airtable_oauth_state" in session_data
        assert "state" in session_data["airtable_oauth_state"]
        assert "code_verifier" in session_data["airtable_oauth_state"]

    @pytest.mark.asyncio
    async def test_503_when_session_unavailable(self):
        view = AirtableConnectView()
        req = _make_request()
        with patch.object(
            AirtableConnectView, "_get_user_session",
            AsyncMock(return_value=None),
        ):
            resp = await view.get(req)
        assert resp.status == 503
        assert "navigator-session" in resp.text


class TestCallbackView:
    @pytest.mark.asyncio
    async def test_state_mismatch_400(self):
        view = AirtableCallbackView()
        session = {"airtable_oauth_state": {"state": "expected", "code_verifier": "v"}}
        req = _make_request(
            query={"code": "the-code", "state": "WRONG"},
        )
        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value=session),
        ):
            resp = await view.get(req)
        assert resp.status == 400
        assert "State mismatch" in resp.text
        assert "airtable" not in session

    @pytest.mark.asyncio
    async def test_successful_exchange_writes_session(self):
        view = AirtableCallbackView()
        session = {"airtable_oauth_state": {"state": "s1", "code_verifier": "v1"}}
        req = _make_request(query={"code": "the-code", "state": "s1"})

        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value=session),
        ), aioresponses() as m:
            m.post(
                "https://airtable.com/oauth2/v1/token",
                payload={
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "data.records:read",
                },
            )
            resp = await view.get(req)
        assert resp.status == 200
        assert "airtable" in session
        bundle = session["airtable"]
        assert bundle["access_token"] == "at-1"
        assert bundle["refresh_token"] == "rt-1"
        assert bundle["token_type"] == "Bearer"
        assert "expires_at" in bundle
        # Single-use state: cleaned up after read
        assert "airtable_oauth_state" not in session

    @pytest.mark.asyncio
    async def test_error_query_param_returns_400(self):
        view = AirtableCallbackView()
        req = _make_request(query={
            "error": "access_denied",
            "error_description": "User declined",
        })
        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value={}),
        ):
            resp = await view.get(req)
        assert resp.status == 400
        assert "access_denied" in resp.text
        assert "User declined" in resp.text

    @pytest.mark.asyncio
    async def test_token_endpoint_failure(self):
        view = AirtableCallbackView()
        session = {"airtable_oauth_state": {"state": "s", "code_verifier": "v"}}
        req = _make_request(query={"code": "c", "state": "s"})
        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value=session),
        ), aioresponses() as m:
            m.post(
                "https://airtable.com/oauth2/v1/token",
                status=400,
                payload={"error": "invalid_grant"},
            )
            resp = await view.get(req)
        assert resp.status == 400
        assert "airtable" not in session  # session must NOT be partially written
```

---

## Agent Instructions

1. Confirm `TASK-675` and `TASK-678` are `completed`.
2. Re-verify `AbstractHandler._get_user_session` signature at `querysource/handlers/abstract.py:225`.
3. Create the new package directory and both view files per Scope.
4. Run `pytest tests/handlers/test_airtable_oauth.py -v`.
5. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:

**Completed by**: SDD Worker (Claude Sonnet 4.6)
**Date**: 2026-05-22
**Notes**: Implemented AirtableConnectView and AirtableCallbackView with full PKCE, CSRF protection, token exchange, and session writeback. All 8 tests pass.
**Deviations from spec**: None. Tests added one extra test (test_missing_code_returns_400) beyond the spec's TestCallbackView examples for better coverage.
