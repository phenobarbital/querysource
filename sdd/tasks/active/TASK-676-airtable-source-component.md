# TASK-676: AirtableSource — MultiQuery Source component

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-675
**Assigned-to**: unassigned

---

## Context

This task creates the actual MultiQuery `Source` that user pipelines reference. It is intentionally **thin**: it parses options, picks the right auth credentials, instantiates `AirtableInterface`, and converts the returned records into a DataFrame. All HTTP and pagination complexity lives in the Interface.

Implements §3 Module 2 of the spec.

---

## Scope

- Create `querysource/queries/multi/sources/airtable.py` containing `AirtableSource(ThreadSource)`.
- Constructor (matching `ThreadSource` signature exactly):
  - `def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None`
  - Call `super().__init__(name, options, request, queue)`.
  - Read `creds = options.get('credentials', {})` and `source = options.get('source', {})`.
  - Resolve `source` identifiers:
    - If `source.get('url')` is set → `AirtableInterface.parse_url(source['url'])` → `(self._base_id, self._table, self._view)`.
    - Else → `self._base_id = source.get('base_id') or self.resolve_credential('base_id', 'AIRTABLE_BASE_ID')`; `self._table = source.get('table')`; `self._view = source.get('view')`.
  - Store API-side filter knobs: `self._filter_by_formula = source.get('filter_by_formula')`, `self._max_records = source.get('max_records')`, `self._page_size = source.get('page_size', 100)`.
  - Store `self._creds = creds` for later auth resolution in `fetch()` (DO NOT resolve PAT in `__init__` — wait until `fetch()` so the session check happens at fetch-time).
- `async def fetch(self) -> pd.DataFrame`:
  1. Validate `self._base_id` and `self._table` are truthy; else raise `ValueError("AirtableSource: 'source.url' or 'source.base_id'+'source.table' is required.")`.
  2. Try to resolve user-session OAuth tokens by calling a new helper `await self._resolve_session_tokens()` (see below).
  3. If session tokens returned → build `interface = AirtableInterface(tokens=session_tokens, is_oauth=True, client_id=..., client_secret=..., persist_tokens=self._make_session_writeback())`.
  4. Else → resolve PAT via `pat = self.resolve_credential('access_token', self._creds.get('access_token', 'AIRTABLE_ACCESS_TOKEN'))`; if `pat` is falsy or equals the literal `"AIRTABLE_ACCESS_TOKEN"` (i.e. unresolved env var) → raise `RuntimeError("AirtableSource: no credentials available — provide a session OAuth token or set AIRTABLE_ACCESS_TOKEN")`.
  5. Build `interface = AirtableInterface(tokens=AirtableTokens(access_token=pat, refresh_token=None, expires_at=None, scope=None), is_oauth=False)`.
  6. Call `records = await interface.list_records(self._base_id, self._table, self._view, filter_by_formula=self._filter_by_formula, max_records=self._max_records, page_size=self._page_size)`.
  7. Build DataFrame: `df = pd.DataFrame.from_records([r.get("fields", {}) for r in records]); df = df.infer_objects(); return df`.
  8. **Empty result must still be a DataFrame**: `pd.DataFrame.from_records([])` already returns an empty DataFrame — verify, do not special-case to `None`.
- Helper `async def _resolve_session_tokens(self) -> Optional[AirtableTokens]`:
  - Lazy-import `from navigator_session import get_session` inside the method (mirroring the defensive pattern at `querysource/handlers/abstract.py:9` but inlined here because `ThreadSource` is not a handler).
  - Try `session = await get_session(self._request, new=False)`. On `RuntimeError` → log a debug message and return `None`.
  - If `session is None` → return `None`.
  - Read `bundle = session.get('airtable')`. If falsy → return `None`.
  - Construct and return `AirtableTokens(access_token=bundle['access_token'], refresh_token=bundle.get('refresh_token'), expires_at=_parse_dt(bundle.get('expires_at')), scope=bundle.get('scope'), token_type=bundle.get('token_type', 'Bearer'))`.
- Helper `def _make_session_writeback(self) -> TokenPersistFn`:
  - Returns an `async def writeback(new_tokens: AirtableTokens) -> None` closure that calls `get_session(self._request, new=False)` and writes `session['airtable'] = {...new_tokens as dict, expires_at iso8601...}`.
  - On `RuntimeError`, log a warning and silently swallow (refresh still succeeded; we just couldn't persist).
- Tests in `tests/multi/sources/test_airtable_source.py` per Test Specification.

**NOT in scope**:
- Registering `AirtableSource` in `SOURCE_REGISTRY` — that is `TASK-677`.
- The OAuth callback handler — that is `TASK-679`.
- Field-type normalization (linked records, attachments) — open question `Q-impl-2` in spec §8. For this task, just pass the `fields` dict through unchanged.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/airtable.py` | CREATE | `AirtableSource(ThreadSource)` |
| `tests/multi/sources/test_airtable_source.py` | CREATE | Unit tests for init, auth selection, fetch, empty-result, errors |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# In querysource/queries/multi/sources/airtable.py:
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from aiohttp import web

from .base import ThreadSource                            # verified: querysource/queries/multi/sources/base.py:11
from ...interfaces.airtable import (                      # verified: created by TASK-674/675
    AirtableInterface,
    AirtableTokens,
    AirtableReauthRequired,
    TokenPersistFn,
)
# `navigator_session` is lazy-imported inside _resolve_session_tokens to match
# the defensive pattern at querysource/handlers/abstract.py:9, where it is
# treated as optional infrastructure.
```

### Existing Signatures to Use

```python
# querysource/queries/multi/sources/base.py:11-116 (verified)
class ThreadSource(threading.Thread, ABC):
    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...                                        # line 22-35

    def resolve_credential(self, key: str, value: str) -> str: ...   # line 37-62

    @abstractmethod
    async def fetch(self) -> pd.DataFrame: ...            # line 74-88


# querysource/queries/multi/sources/smartsheet.py:42-55 (verified) — closest analogue
class SmartSheetSource(ThreadSource):
    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._api_key = self.resolve_credential(
            'api_key', creds.get('api_key', 'SMARTSHEET_API_KEY')
        )


# navigator_session — verified at querysource/handlers/abstract.py:9
from navigator_session import get_session, SessionData
# get_session signature: async def get_session(request, *, new: bool = False) -> SessionData
```

### Does NOT Exist

- ~~A `BaseSource` or `AbstractSource` other than `ThreadSource`~~ — only `ThreadSource`.
- ~~`querysource/queries/multi/sources/__init__.py::register_source(...)`~~ — sources are added by hand-editing `SOURCE_REGISTRY` (see `TASK-677`).
- ~~`ThreadSource.session`, `ThreadSource.user`, `ThreadSource.app`~~ — none of these exist. The only request handle is `self._request`.
- ~~`ThreadSource.resolve_credential` returning `None`~~ — it returns either the resolved value OR the original string. Code that compares `resolve_credential(...) == "AIRTABLE_ACCESS_TOKEN"` correctly detects the "unresolved" case.
- ~~A built-in session writeback helper anywhere in querysource~~ — `_make_session_writeback` is brand new and must be implemented from scratch.

---

## Implementation Notes

### Pattern to Follow

The constructor mirrors `SmartSheetSource.__init__` exactly in structure (see `querysource/queries/multi/sources/smartsheet.py:42-55`). The difference is that we capture credentials but defer PAT resolution to `fetch()` so the auth-precedence rule "session first" is fully expressed at fetch-time.

The `_resolve_session_tokens` helper inlines a small version of `_get_user_session` from `querysource/handlers/abstract.py:225-251`. Do NOT add a memoization key on `self._request` here — fetch is called once per source instantiation.

`expires_at` (de)serialization: store as ISO 8601 strings in the session dict; parse via `datetime.fromisoformat(s)` on read, emit via `dt.isoformat()` on write.

### Key Constraints

- Constructor signature is FIXED. Do not add `**kwargs`. Do not reorder.
- `fetch()` MUST return a `pd.DataFrame`, never `None`. Use `pd.DataFrame.from_records([])` for empty.
- Never read `os.environ` directly. Use `self.resolve_credential`.
- `_make_session_writeback` must close over `self._request` ONLY (not over mutable state). The callback is invoked from `AirtableInterface._refresh_tokens`, possibly outside this method's stack frame.
- Log at INFO when falling back to PAT; log at DEBUG when using session OAuth. Never log token values.
- The `AirtableReauthRequired` exception is propagated unchanged — `fetch()` does not catch it. `ThreadSource.run` will capture it on `self.exc`.

### References in Codebase

- `querysource/queries/multi/sources/smartsheet.py:18-92` — structural template; copy the constructor shape and adapt.
- `querysource/queries/multi/sources/sharepoint.py:48-73` — example of multi-key credential resolution via `resolve_credential` (use only the pattern, not the SharePoint deps).
- `querysource/handlers/abstract.py:225-251` — reference behavior for `get_session` lookup; the defensive `try / except RuntimeError` is mandatory.

---

## Acceptance Criteria

- [ ] `AirtableSource(ThreadSource)` class exists at `querysource/queries/multi/sources/airtable.py`.
- [ ] Constructor signature matches `ThreadSource.__init__` exactly (`name`, `options`, `request`, `queue`).
- [ ] `options['source']['url'] = "https://airtable.com/app1/tbl1/viw1"` populates `self._base_id`, `self._table`, `self._view` correctly.
- [ ] `options['source'] = {'base_id': 'app1', 'table': 'tbl1'}` (no URL) populates the same fields; `self._view` is `None`.
- [ ] Missing both `url` AND `base_id`+`table` → `fetch()` raises `ValueError` containing `"source.url"`.
- [ ] When the request session has `session['airtable']` set, `fetch()` builds an `AirtableInterface` with `is_oauth=True` and `persist_tokens` set; the PAT env var is NOT read in this path.
- [ ] When no session is present, `fetch()` resolves `AIRTABLE_ACCESS_TOKEN` via `resolve_credential` and instantiates `AirtableInterface` with `is_oauth=False`.
- [ ] When neither session nor a usable PAT is available, `fetch()` raises `RuntimeError` with a message naming both `AIRTABLE_ACCESS_TOKEN` and "session OAuth token".
- [ ] `fetch()` returns a `pd.DataFrame` whose rows are the Airtable `fields` dicts (one row per record).
- [ ] An empty record list yields an empty DataFrame (`len(df) == 0`), NEVER `None`.
- [ ] `AirtableReauthRequired` raised inside `list_records` propagates out of `fetch()` unchanged (verified with mock).
- [ ] `_make_session_writeback` writeback is invoked when the Interface refreshes tokens (mocked); the session dict is updated with the new bundle.
- [ ] `pytest tests/multi/sources/test_airtable_source.py -v` passes.
- [ ] `ruff check querysource/queries/multi/sources/airtable.py tests/multi/sources/test_airtable_source.py` passes.

---

## Test Specification

```python
# tests/multi/sources/test_airtable_source.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.queries.multi.sources.airtable import AirtableSource
from querysource.interfaces.airtable import (
    AirtableTokens,
    AirtableReauthRequired,
)


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
            session = {"airtable": {
                "access_token": "oauth-abc",
                "refresh_token": "refresh-xyz",
                "expires_at": None,
                "scope": "data.records:read",
            }}
            return session
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
        # No session → PAT path
        async def fake_get_session(request, new=False):
            return None
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )
        monkeypatch.setenv("AIRTABLE_ACCESS_TOKEN", "pat-resolved-123")

        s = AirtableSource("src", _opts_url(), mock_request, queue)

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
        async def fake_get_session(request, new=False):
            return None
        monkeypatch.setattr(
            "navigator_session.get_session", fake_get_session,
        )
        monkeypatch.delenv("AIRTABLE_ACCESS_TOKEN", raising=False)

        s = AirtableSource("src", _opts_url(), mock_request, queue)
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
        monkeypatch.setenv("AIRTABLE_ACCESS_TOKEN", "pat-x")

        s = AirtableSource("src", _opts_url(), mock_request, queue)

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
```

---

## Agent Instructions

1. Confirm TASK-674 and TASK-675 are `completed`.
2. Re-verify `ThreadSource` constructor at `querysource/queries/multi/sources/base.py:22-35`.
3. Implement per Scope. Keep `fetch()` body ≤ 50 LoC.
4. Run `pytest tests/multi/sources/test_airtable_source.py -v`.
5. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:
