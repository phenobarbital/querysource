"""Tests for AirtableInterface — foundation (TASK-674) and read methods (TASK-675).

Note on aioresponses URL matching: aioresponses 0.7.x matches the full URL
including query-string parameters.  To avoid enumerating all possible param
combinations in every mock registration we use ``re.compile`` patterns that
match any query string on the target base URL.  This is the recommended
workaround for aioresponses < 0.8.
"""
import re

import pytest
from aioresponses import aioresponses

from querysource.interfaces.airtable import (
    AirtableInterface,
    AirtableReauthRequired,
    AirtableTokens,
)


# ---------------------------------------------------------------------------
# TASK-674: URL parser, constructor, tokens, write stubs
# ---------------------------------------------------------------------------


class TestParseUrl:
    def test_full_url_with_view(self):
        assert AirtableInterface.parse_url(
            "https://airtable.com/appABC/tblDEF/viwGHI"
        ) == ("appABC", "tblDEF", "viwGHI")

    def test_no_view(self):
        assert AirtableInterface.parse_url(
            "https://airtable.com/appABC/tblDEF"
        ) == ("appABC", "tblDEF", None)

    def test_trailing_slash(self):
        assert AirtableInterface.parse_url(
            "https://airtable.com/appABC/tblDEF/"
        ) == ("appABC", "tblDEF", None)

    def test_query_string_ignored(self):
        assert AirtableInterface.parse_url(
            "https://airtable.com/appABC/tblDEF?x=1"
        ) == ("appABC", "tblDEF", None)

    def test_wrong_host_raises(self):
        with pytest.raises(ValueError, match="Not an Airtable URL"):
            AirtableInterface.parse_url("https://example.com/app1/tbl1")

    def test_missing_table_raises(self):
        with pytest.raises(ValueError, match="missing base/table segment"):
            AirtableInterface.parse_url("https://airtable.com/onlybase")


class TestAirtableTokens:
    def test_construct(self):
        t = AirtableTokens(
            access_token="abc",
            refresh_token="xyz",
            expires_at=None,
            scope="data.records:read",
        )
        assert t.token_type == "Bearer"
        assert t.access_token == "abc"


class TestWriteStubs:
    @pytest.mark.asyncio
    async def test_create_records_raises(self):
        iface = AirtableInterface(
            tokens=AirtableTokens("t", None, None, None),
            is_oauth=False,
        )
        with pytest.raises(NotImplementedError, match="FEAT-096"):
            await iface.create_records("appX", "tblY", records=[{"fields": {}}])

    @pytest.mark.asyncio
    async def test_update_records_raises(self):
        iface = AirtableInterface(
            tokens=AirtableTokens("t", None, None, None),
            is_oauth=False,
        )
        with pytest.raises(NotImplementedError, match="FEAT-096"):
            await iface.update_records("appX", "tblY", records=[])

    @pytest.mark.asyncio
    async def test_delete_records_raises(self):
        iface = AirtableInterface(
            tokens=AirtableTokens("t", None, None, None),
            is_oauth=False,
        )
        with pytest.raises(NotImplementedError, match="FEAT-096"):
            await iface.delete_records("appX", "tblY", record_ids=[])

    @pytest.mark.asyncio
    async def test_create_table_raises(self):
        iface = AirtableInterface(
            tokens=AirtableTokens("t", None, None, None),
            is_oauth=False,
        )
        with pytest.raises(NotImplementedError, match="FEAT-096"):
            await iface.create_table("appX", schema={})


class TestAirtableReauthRequired:
    def test_inherits_runtime_error(self):
        assert issubclass(AirtableReauthRequired, RuntimeError)


# ---------------------------------------------------------------------------
# TASK-675: list_records, pagination, 401-refresh
# ---------------------------------------------------------------------------


@pytest.fixture
def pat_iface():
    return AirtableInterface(
        tokens=AirtableTokens("pat-abc", None, None, None),
        is_oauth=False,
    )


@pytest.fixture
def oauth_iface_with_persist():
    persisted: list[AirtableTokens] = []

    async def persist(t: AirtableTokens) -> None:
        persisted.append(t)

    iface = AirtableInterface(
        tokens=AirtableTokens("oauth-old", "refresh-old", None, "data.records:read"),
        is_oauth=True,
        client_id="cli",
        client_secret="sec",
        persist_tokens=persist,
    )
    return iface, persisted


class TestListRecordsPagination:
    @pytest.mark.asyncio
    async def test_three_page_pagination(self, pat_iface):
        # Use regex pattern because aioresponses 0.7.x matches the full URL with params
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, payload={"records": [{"id": "r1", "fields": {"n": 1}}], "offset": "o1"})
            m.get(url_pattern, payload={"records": [{"id": "r2", "fields": {"n": 2}}], "offset": "o2"})
            m.get(url_pattern, payload={"records": [{"id": "r3", "fields": {"n": 3}}]})
            records = await pat_iface.list_records("appX", "tblY")
        assert [r["id"] for r in records] == ["r1", "r2", "r3"]

    @pytest.mark.asyncio
    async def test_max_records_truncates(self, pat_iface):
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, payload={"records": [{"id": f"r{i}", "fields": {}} for i in range(10)]})
            records = await pat_iface.list_records("appX", "tblY", max_records=3)
        assert len(records) == 3


class TestRefresh401:
    @pytest.mark.asyncio
    async def test_oauth_refresh_success(self, oauth_iface_with_persist):
        iface, persisted = oauth_iface_with_persist
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, status=401, payload={"error": "invalid_token"})
            m.post(iface.OAUTH_TOKEN_URL, payload={
                "access_token": "oauth-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "data.records:read",
            })
            m.get(url_pattern, payload={"records": []})
            records = await iface.list_records("appX", "tblY")
        assert records == []
        assert iface._tokens.access_token == "oauth-new"
        assert iface._tokens.refresh_token == "refresh-new"
        assert len(persisted) == 1
        assert persisted[0].access_token == "oauth-new"

    @pytest.mark.asyncio
    async def test_oauth_refresh_failure_raises_reauth(self, oauth_iface_with_persist):
        iface, persisted = oauth_iface_with_persist
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, status=401)
            m.post(iface.OAUTH_TOKEN_URL, status=400, payload={"error": "invalid_grant"})
            with pytest.raises(AirtableReauthRequired):
                await iface.list_records("appX", "tblY")
        assert persisted == []

    @pytest.mark.asyncio
    async def test_oauth_no_refresh_token_raises_reauth(self):
        iface = AirtableInterface(
            tokens=AirtableTokens("oauth-old", None, None, None),  # no refresh_token
            is_oauth=True,
            client_id="cli",
            client_secret="sec",
        )
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, status=401)
            with pytest.raises(AirtableReauthRequired):
                await iface.list_records("appX", "tblY")

    @pytest.mark.asyncio
    async def test_pat_401_raises_runtime_error_not_reauth(self, pat_iface):
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, status=401, payload={"error": "x"})
            with pytest.raises(RuntimeError) as exc:
                await pat_iface.list_records("appX", "tblY")
        assert not isinstance(exc.value, AirtableReauthRequired)

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit(self, pat_iface):
        url_pattern = re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")
        with aioresponses() as m:
            m.get(url_pattern, status=429)
            with pytest.raises(RuntimeError, match="rate limit"):
                await pat_iface.list_records("appX", "tblY")
