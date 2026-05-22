# TASK-675: AirtableInterface — read methods (list_records, pagination, refresh-on-401)

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-674
**Assigned-to**: unassigned

---

## Context

`AirtableInterface` is the single point of contact with the Airtable API. This task adds the **read** half (`list_records`) plus the transparent refresh-on-401 retry that makes the OAuth path work. Downstream tasks `TASK-676` (`AirtableSource`) and `TASK-679` (OAuth handlers) depend on this.

Implements §2 Architectural Design and §5 Acceptance Criteria of the spec (the items covering pagination, 401-refresh, and PAT-vs-OAuth behavior split).

---

## Scope

Modify `querysource/interfaces/airtable.py` (file already exists from `TASK-674`):

- Add `async def list_records(...) -> list[dict]` per the signature in spec §2 New Public Interfaces.
- Add private helper `async def _request_with_refresh(self, session, method, url, **kwargs) -> dict` that:
  1. Issues the request with the current bearer token.
  2. On HTTP 401 + `is_oauth=True` + `refresh_token` present: calls `await self._refresh_tokens(session)`, then retries the original request **exactly once**.
  3. On HTTP 401 + PAT (`is_oauth=False`): raises `RuntimeError("AirtableInterface: 401 Unauthorized — check AIRTABLE_ACCESS_TOKEN")`.
  4. On HTTP 401 after refresh, or no refresh_token to begin with: raises `AirtableReauthRequired("OAuth refresh failed — user must reconnect")`.
  5. On HTTP 429: raises `RuntimeError("Airtable API rate limit exceeded (HTTP 429).")`.
  6. Other non-2xx: calls `resp.raise_for_status()`.
  7. Returns `await resp.json()`.
- Add private helper `async def _refresh_tokens(self, session) -> None` that POSTs to `OAUTH_TOKEN_URL` with `grant_type=refresh_token` + `refresh_token` + `client_id` + `client_secret` (HTTP Basic auth header per Airtable docs), parses the JSON response into a new `AirtableTokens`, replaces `self._tokens`, and `await self._persist_tokens(new_tokens)` if the callback is set. On non-2xx, raises `AirtableReauthRequired`.
- `list_records` itself:
  1. Validates `base_id` and `table` are non-empty.
  2. Builds the URL `f"{self.BASE_URL}/{base_id}/{table}"`.
  3. In a single `aiohttp.ClientSession(timeout=self._timeout)` async context manager, loops:
     - Builds `params = {"pageSize": page_size}` (capped at 100 per Airtable's max).
     - Adds `params["view"] = view` if set.
     - Adds `params["filterByFormula"] = filter_by_formula` if set.
     - Adds `params["maxRecords"] = max_records` if set.
     - On iteration > 0: `params["offset"] = offset`.
     - Calls `await self._request_with_refresh(session, "GET", url, params=params, headers=self._auth_headers())`.
     - Appends `data.get("records", [])` to the accumulator.
     - If `data.get("offset")` is set, loop with that offset; else break.
     - If `max_records` is set and accumulator length ≥ `max_records`, break and slice to `max_records`.
  4. Returns the accumulated list of record dicts (each record is the raw Airtable shape: `{"id": ..., "fields": {...}, "createdTime": ...}`).
- Add private helper `def _auth_headers(self) -> dict[str, str]` that returns `{"Authorization": f"Bearer {self._tokens.access_token}"}`.

Extend `tests/interfaces/test_airtable_interface.py` with the new test classes listed in Test Specification below — mock `aiohttp` via the existing `aioresponses` pattern (already a project dep if used elsewhere; otherwise use the equivalent `aiohttp.test_utils` or `pytest-aiohttp` fixtures).

**NOT in scope**:
- Write methods — already stubbed in `TASK-674`.
- Field normalization / DataFrame construction — that is `TASK-676`.
- Persisting refreshed tokens back into `navigator_session` — `AirtableInterface` only calls the `persist_tokens` callback. Wiring that callback to the session is in `TASK-676` (Source) and `TASK-679` (OAuth handler).
- Retry/back-off on 429 — out of scope per spec §8 Open Questions Q-rate-limit. Just raise.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/airtable.py` | MODIFY | Add `list_records`, `_request_with_refresh`, `_refresh_tokens`, `_auth_headers` |
| `tests/interfaces/test_airtable_interface.py` | MODIFY | Add `TestListRecords`, `TestRefresh401` test classes |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# Add to existing imports in querysource/interfaces/airtable.py
import asyncio
import base64
from typing import Any
import aiohttp                                           # already imported by TASK-674
```

### Existing Signatures to Use

```python
# querysource/interfaces/airtable.py (created by TASK-674):
class AirtableInterface:
    BASE_URL: str
    OAUTH_TOKEN_URL: str
    OAUTH_AUTHORIZE_URL: str

    def __init__(
        self,
        tokens: AirtableTokens,
        is_oauth: bool,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        persist_tokens: Optional[TokenPersistFn] = None,
        timeout_seconds: int = 30,
    ) -> None: ...

    @staticmethod
    def parse_url(url: str) -> tuple[str, str, Optional[str]]: ...

# AirtableReauthRequired (from TASK-674) — raise on refresh failure


# querysource/queries/multi/sources/smartsheet.py:77-87 — pattern for ClientSession + status handling:
timeout = aiohttp.ClientTimeout(total=30)
async with aiohttp.ClientSession(timeout=timeout) as session:
    async with session.get(url, headers=headers) as resp:
        if resp.status == 429:
            raise RuntimeError("SmartSheet API rate limit exceeded (HTTP 429).")
        if resp.status == 401:
            raise RuntimeError(...)
        resp.raise_for_status()
        content = await resp.read()
```

### Does NOT Exist

- ~~`aiohttp.ClientSession.fetch(...)`~~ — use `session.get(...)` / `session.post(...)` as context managers.
- ~~A pre-built retry/refresh decorator anywhere in querysource~~ — `grep "backoff\|retry_on" querysource/interfaces/` shows usage in `http.py` only (web-scraping context); do not reuse. Implement inline.
- ~~`querysource.exceptions.RateLimitError`~~ — no such typed exception today; raise `RuntimeError` per the SmartSheet precedent.
- ~~Airtable's "v1" or "v2" API path~~ — the live REST endpoint is `https://api.airtable.com/v0`. No other major version exists at time of spec.

### Airtable API Reference (verified externally; cite in PR description if you change)

- Records list: `GET https://api.airtable.com/v0/{baseId}/{tableIdOrName}` — query params: `view`, `pageSize` (max 100), `offset`, `filterByFormula`, `maxRecords`, `fields[]`, `sort[]`.
- Response shape: `{ "records": [{"id": "rec...", "fields": {...}, "createdTime": "..."}], "offset": "..." (optional) }`.
- 401 means token is invalid/expired. Refresh-token endpoint: `POST https://airtable.com/oauth2/v1/token` with `grant_type=refresh_token`, HTTP Basic auth header `Authorization: Basic base64(client_id:client_secret)`.
- Refresh response: `{ "access_token": "...", "refresh_token": "...", "expires_in": 3600, "token_type": "Bearer", "scope": "..." }`. **Airtable rotates the refresh token on every refresh** — always overwrite.

---

## Implementation Notes

### Pattern to Follow

Use the SmartSheetSource HTTP pattern (`querysource/queries/multi/sources/smartsheet.py:77-87`) for status handling. Wrap the request in a tiny helper to make the retry-once logic readable:

```python
async def _request_with_refresh(
    self,
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict:
    """Issue request; on 401 (OAuth) try one refresh + retry. Returns JSON."""
    headers = kwargs.pop("headers", {})
    headers.update(self._auth_headers())
    async with session.request(method, url, headers=headers, **kwargs) as resp:
        if resp.status == 401:
            body = await resp.text()
            if self._is_oauth and self._tokens.refresh_token:
                await self._refresh_tokens(session)
                headers = self._auth_headers()  # rebuild with new token
                async with session.request(method, url, headers=headers, **kwargs) as resp2:
                    if resp2.status == 401:
                        raise AirtableReauthRequired(
                            "OAuth refresh succeeded but Airtable still returned 401 — reconnect required."
                        )
                    return await self._handle_response(resp2)
            if self._is_oauth:
                # OAuth but no refresh_token — cannot recover
                raise AirtableReauthRequired(
                    "Session OAuth token expired and no refresh token available — reconnect required."
                )
            # PAT case
            raise RuntimeError(
                f"AirtableInterface: 401 Unauthorized (PAT auth). Body: {body[:200]}"
            )
        return await self._handle_response(resp)
```

Refresh helper — HTTP Basic auth for client credentials (Airtable docs say `client_secret` MUST be in the Authorization header, not the form body, when present):

```python
async def _refresh_tokens(self, session: aiohttp.ClientSession) -> None:
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
            self._logger.warning("Airtable refresh failed (%s): %s", resp.status, body[:200])
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
```

Provide a small module-level helper:

```python
def _compute_expires_at(expires_in: Optional[int]) -> Optional[datetime]:
    if not expires_in:
        return None
    from datetime import datetime, timezone, timedelta
    return datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
```

### Key Constraints

- **Exactly one retry on 401** when OAuth is active. Never an infinite loop.
- Refresh tokens MUST be overwritten (Airtable rotates them). Use `payload.get("refresh_token") or self._tokens.refresh_token` — Airtable may or may not return one; keep the old as a fallback.
- The `persist_tokens` callback is `async` — always `await` it.
- `page_size` upper bound is 100 (Airtable hard limit). Defensively cap with `min(page_size, 100)`.
- Use `session.request(method, url, ...)` rather than `session.get`/`session.post` so the same helper handles both GET (record list) and POST (refresh).

### References in Codebase

- `querysource/queries/multi/sources/smartsheet.py:77-92` — the canonical 401 / 429 / `raise_for_status()` pattern.
- `querysource/queries/multi/sources/sharepoint.py:113-128` — example of lazy imports; not needed here (aiohttp is always available).

---

## Acceptance Criteria

- [ ] `AirtableInterface.list_records` is implemented and returns a `list[dict]`.
- [ ] Pagination works: a 3-page mocked response returns the concatenated records in order, no duplicates, no records lost.
- [ ] `max_records` truncates the result to exactly that many records (no more, no less).
- [ ] OAuth + 401 + refresh succeeds: `persist_tokens` callback was awaited exactly once; the retry returned the original endpoint's data.
- [ ] OAuth + 401 + refresh fails (HTTP 400 from token endpoint): raises `AirtableReauthRequired`.
- [ ] OAuth + 401 + no refresh_token: raises `AirtableReauthRequired` immediately (no refresh attempt).
- [ ] PAT + 401: raises `RuntimeError`, never `AirtableReauthRequired`.
- [ ] 429 on data fetch: raises `RuntimeError` containing the substring `"rate limit"`.
- [ ] `_refresh_tokens` builds an HTTP Basic auth header (verifiable in mock) and POSTs `application/x-www-form-urlencoded` body.
- [ ] After a successful refresh, `self._tokens.access_token` is the new token from the mocked response.
- [ ] `pytest tests/interfaces/test_airtable_interface.py -v` passes (all tests from `TASK-674` plus the new ones).
- [ ] `ruff check querysource/interfaces/airtable.py tests/interfaces/test_airtable_interface.py` passes.

---

## Test Specification

```python
# tests/interfaces/test_airtable_interface.py (extend file from TASK-674)

import pytest
from aioresponses import aioresponses   # add as a test-only dep if not already present

from querysource.interfaces.airtable import (
    AirtableInterface,
    AirtableTokens,
    AirtableReauthRequired,
)


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
        with aioresponses() as m:
            url = f"{pat_iface.BASE_URL}/appX/tblY"
            m.get(url, payload={"records": [{"id": "r1", "fields": {"n": 1}}], "offset": "o1"})
            m.get(url, payload={"records": [{"id": "r2", "fields": {"n": 2}}], "offset": "o2"})
            m.get(url, payload={"records": [{"id": "r3", "fields": {"n": 3}}]})
            records = await pat_iface.list_records("appX", "tblY")
        assert [r["id"] for r in records] == ["r1", "r2", "r3"]

    @pytest.mark.asyncio
    async def test_max_records_truncates(self, pat_iface):
        with aioresponses() as m:
            url = f"{pat_iface.BASE_URL}/appX/tblY"
            m.get(url, payload={"records": [{"id": f"r{i}", "fields": {}} for i in range(10)]})
            records = await pat_iface.list_records("appX", "tblY", max_records=3)
        assert len(records) == 3


class TestRefresh401:
    @pytest.mark.asyncio
    async def test_oauth_refresh_success(self, oauth_iface_with_persist):
        iface, persisted = oauth_iface_with_persist
        with aioresponses() as m:
            data_url = f"{iface.BASE_URL}/appX/tblY"
            m.get(data_url, status=401, payload={"error": "invalid_token"})
            m.post(iface.OAUTH_TOKEN_URL, payload={
                "access_token": "oauth-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "data.records:read",
            })
            m.get(data_url, payload={"records": []})
            records = await iface.list_records("appX", "tblY")
        assert records == []
        assert iface._tokens.access_token == "oauth-new"
        assert iface._tokens.refresh_token == "refresh-new"
        assert len(persisted) == 1
        assert persisted[0].access_token == "oauth-new"

    @pytest.mark.asyncio
    async def test_oauth_refresh_failure_raises_reauth(self, oauth_iface_with_persist):
        iface, persisted = oauth_iface_with_persist
        with aioresponses() as m:
            m.get(f"{iface.BASE_URL}/appX/tblY", status=401)
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
        with aioresponses() as m:
            m.get(f"{iface.BASE_URL}/appX/tblY", status=401)
            with pytest.raises(AirtableReauthRequired):
                await iface.list_records("appX", "tblY")

    @pytest.mark.asyncio
    async def test_pat_401_raises_runtime_error_not_reauth(self, pat_iface):
        with aioresponses() as m:
            m.get(f"{pat_iface.BASE_URL}/appX/tblY", status=401, payload={"error": "x"})
            with pytest.raises(RuntimeError) as exc:
                await pat_iface.list_records("appX", "tblY")
        assert not isinstance(exc.value, AirtableReauthRequired)

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit(self, pat_iface):
        with aioresponses() as m:
            m.get(f"{pat_iface.BASE_URL}/appX/tblY", status=429)
            with pytest.raises(RuntimeError, match="rate limit"):
                await pat_iface.list_records("appX", "tblY")
```

---

## Agent Instructions

1. Confirm `TASK-674` is `completed` in `sdd/tasks/index/multi-threadsource-airtable.json`.
2. Verify the AirtableInterface shell is in place at `querysource/interfaces/airtable.py` (constants, `__init__`, `parse_url`, write stubs).
3. Re-verify the SmartSheet pattern at `querysource/queries/multi/sources/smartsheet.py:77-92` is unchanged (`grep -n "rate limit" querysource/queries/multi/sources/smartsheet.py`).
4. If `aioresponses` is not in dev deps, add it to the test-only group of `pyproject.toml` (`[project.optional-dependencies] test` or equivalent).
5. Implement per Scope. Keep `list_records` body ≤ 50 LoC; push complexity into the two helpers.
6. Run the full file: `pytest tests/interfaces/test_airtable_interface.py -v`.
7. Move task to `sdd/tasks/completed/` and update the index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:

**Completed by**: SDD Worker (Claude Sonnet 4.6)
**Date**: 2026-05-22
**Notes**: Implemented list_records with pagination, 401-refresh, and PAT vs OAuth error handling. All tests pass.
**Deviations from spec**: Same aioresponses regex pattern workaround as TASK-674.
