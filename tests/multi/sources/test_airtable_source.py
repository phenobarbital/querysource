"""Tests for AirtableSource — MultiQuery source component (TASK-676)."""
import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from querysource.queries.multi.sources.airtable import AirtableSource
from querysource.interfaces.airtable import AirtableReauthRequired


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.get.return_value = None
    return req


@pytest.fixture
def queue():
    return asyncio.Queue()


def _opts_url():
    return {
        "credentials": {"access_token": "AIRTABLE_ACCESS_TOKEN"},
        "source": {"url": "https://airtable.com/app1/tbl1/viw1"},
    }


def _opts_explicit():
    return {
        "source": {"base_id": "app1", "table": "tbl1"},
    }


class TestInit:
    def test_url_parsing(self, mock_request, queue):
        s = AirtableSource("src", _opts_url(), mock_request, queue)
        assert s._base_id == "app1"
        assert s._table == "tbl1"
        assert s._view == "viw1"

    def test_explicit_ids(self, mock_request, queue):
        s = AirtableSource("src", _opts_explicit(), mock_request, queue)
        assert s._base_id == "app1"
        assert s._table == "tbl1"
        assert s._view is None


class TestFetchAuth:
    @pytest.mark.asyncio
    async def test_session_first(self, mock_request, queue, monkeypatch):
        # Session present → OAuth path
        async def fake_get_session(request, new=False):
            return {"airtable": {
                "access_token": "oauth-abc",
                "refresh_token": "refresh-xyz",
                "expires_at": None,
                "scope": "data.records:read",
            }}
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )

        s = AirtableSource("src", _opts_url(), mock_request, queue)

        captured: dict = {}

        class _FakeIface:
            BASE_URL = "x"

            def __init__(self, *, tokens, is_oauth, client_id=None,
                         client_secret=None, persist_tokens=None, timeout_seconds=30):
                captured["is_oauth"] = is_oauth
                captured["access_token"] = tokens.access_token

            async def list_records(self, *a, **k):
                return [{"fields": {"Name": "Alice"}}]

        with patch(
            "querysource.queries.multi.sources.airtable.AirtableInterface",
            _FakeIface,
        ):
            df = await s.fetch()
        assert captured["is_oauth"] is True
        assert captured["access_token"] == "oauth-abc"
        assert isinstance(df, pd.DataFrame)
        assert df.iloc[0]["Name"] == "Alice"

    @pytest.mark.asyncio
    async def test_pat_fallback(self, mock_request, queue, monkeypatch):
        # No session → PAT path.
        # Mock resolve_credential to return a known PAT since navconfig may
        # read the real AIRTABLE_ACCESS_TOKEN from the .env file in tests.
        async def fake_get_session(request, new=False):
            return None
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )

        s = AirtableSource("src", _opts_url(), mock_request, queue)
        # Patch resolve_credential to return a deterministic PAT value
        monkeypatch.setattr(s, "resolve_credential", lambda key, val: "pat-resolved-123")

        captured: dict = {}

        class _FakeIface:
            def __init__(self, *, tokens, is_oauth, **kwargs):
                captured["is_oauth"] = is_oauth
                captured["access_token"] = tokens.access_token

            async def list_records(self, *a, **k):
                return []

        with patch(
            "querysource.queries.multi.sources.airtable.AirtableInterface",
            _FakeIface,
        ):
            df = await s.fetch()
        assert captured["is_oauth"] is False
        assert captured["access_token"] == "pat-resolved-123"
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0  # empty result is still a DataFrame, never None

    @pytest.mark.asyncio
    async def test_no_creds_raises(self, mock_request, queue, monkeypatch):
        # Simulate no session AND no PAT by making resolve_credential return
        # the unresolved literal (what happens when navconfig has no value).
        async def fake_get_session(request, new=False):
            return None
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )

        s = AirtableSource("src", _opts_url(), mock_request, queue)
        # Force resolve_credential to return the unresolved env-var name
        # (this is what ThreadSource.resolve_credential returns when the var
        # is missing from navconfig — see base.py:54-61).
        monkeypatch.setattr(s, "resolve_credential", lambda key, val: val)

        with pytest.raises(RuntimeError, match="AIRTABLE_ACCESS_TOKEN"):
            await s.fetch()


class TestFetchEmptyAndError:
    @pytest.mark.asyncio
    async def test_empty_records_returns_empty_df(self, mock_request, queue, monkeypatch):
        async def fake_get_session(request, new=False):
            return None
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )

        s = AirtableSource("src", _opts_url(), mock_request, queue)
        # Ensure a deterministic PAT is resolved
        monkeypatch.setattr(s, "resolve_credential", lambda key, val: "pat-x")

        class _FakeIface:
            def __init__(self, **kwargs): ...

            async def list_records(self, *a, **k):
                return []

        with patch(
            "querysource.queries.multi.sources.airtable.AirtableInterface",
            _FakeIface,
        ):
            df = await s.fetch()
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    @pytest.mark.asyncio
    async def test_reauth_required_propagates(self, mock_request, queue, monkeypatch):
        async def fake_get_session(request, new=False):
            return {"airtable": {"access_token": "tok", "refresh_token": None}}
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )

        s = AirtableSource("src", _opts_url(), mock_request, queue)

        class _FakeIface:
            def __init__(self, **kwargs): ...

            async def list_records(self, *a, **k):
                raise AirtableReauthRequired("test")

        with patch(
            "querysource.queries.multi.sources.airtable.AirtableInterface",
            _FakeIface,
        ):
            with pytest.raises(AirtableReauthRequired):
                await s.fetch()

    @pytest.mark.asyncio
    async def test_missing_url_and_ids_raises(self, mock_request, queue):
        s = AirtableSource("src", {"source": {}}, mock_request, queue)
        with pytest.raises(ValueError, match="source.url"):
            await s.fetch()
