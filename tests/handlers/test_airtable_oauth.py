"""Tests for Airtable OAuth2 handler views (TASK-679)."""
from unittest.mock import AsyncMock, MagicMock, patch

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
        # Verify authorize URL params are present in the HTML
        assert "client_id=test-client-id" in body
        assert "code_challenge=" in body
        assert "code_challenge_method=S256" in body
        assert "state=" in body
        assert "response_type=code" in body
        # Session was written with state and code_verifier
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
        # Session was NOT written with airtable token bundle
        assert "airtable" not in session
        # State was consumed (deleted) even on mismatch
        assert "airtable_oauth_state" not in session

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
        # Session must NOT be partially written
        assert "airtable" not in session

    @pytest.mark.asyncio
    async def test_503_when_session_unavailable(self):
        view = AirtableCallbackView()
        req = _make_request(query={"code": "c", "state": "s"})
        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value=None),
        ):
            resp = await view.get(req)
        assert resp.status == 503
        assert "navigator-session" in resp.text

    @pytest.mark.asyncio
    async def test_missing_code_returns_400(self):
        view = AirtableCallbackView()
        req = _make_request(query={"state": "s"})  # no code
        with patch.object(
            AirtableCallbackView, "_get_user_session",
            AsyncMock(return_value={}),
        ):
            resp = await view.get(req)
        assert resp.status == 400
        assert "Missing" in resp.text
