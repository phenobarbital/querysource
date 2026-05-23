---
type: feature
base_branch: dev
---

# Feature Specification: Multi-Query ThreadSource — Airtable

**Feature ID**: FEAT-096
**Date**: 2026-05-22
**Author**: Jesus Lara
**Status**: approved
**Target version**: 6.1.0

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource has zero integration with Airtable today (verified: repo-wide grep for `airtable` returns only documentation files). Customers' Airtable bases are a common data source for MultiQuery pipelines, but users currently have to manually export to CSV/S3 to feed querysource — an unnecessary two-step process.

The freshly-landed FEAT-093 workstream introduced `ThreadSource` and a clean source registry. An Airtable source slots naturally into that framework. Two auth modes must be supported:

1. **End-user OAuth2** — interactive users who want to "connect their Airtable account".
2. **Server-wide Personal Access Token** — service-account use, batch pipelines, and CI/CD jobs running without a user session.

This is also the **first OAuth callback** in the entire querysource package — there is no existing template to copy (verified: no matches for `oauth|callback|access_token|refresh_token` under `querysource/handlers/` or `querysource/auth/`).

### Goals

- Add a new `AirtableSource(ThreadSource)` MultiQuery source that fetches all records from an Airtable table and returns them as a pandas DataFrame.
- Create a reusable `AirtableInterface` class that encapsulates the entire Airtable API surface (read methods used now; write/schema-modification methods stubbed for a future feature).
- Accept either a **full Airtable URL** (`https://airtable.com/<baseId>/<tableId>/<viewId?>`) or explicit `(base_id, table_id, view_id?)` triples as the source identifier.
- Implement dual auth: prefer per-user OAuth2 token from `navigator_session`, fall back to global `AIRTABLE_ACCESS_TOKEN` PAT, raise on neither.
- Register two new aiohttp routes inside `QuerySource.setup()`:
  - `GET /api/v1/qs/integrations/airtable/connect` — serves a self-contained minimal HTML consent page that redirects to Airtable's OAuth2 authorize URL.
  - `GET /api/v1/qs/integrations/airtable/callback` — receives the OAuth2 redirect, exchanges the code for tokens, and writes `{access_token, refresh_token, expires_at, scope}` into `session['airtable']`.
- Gate the OAuth-related plumbing behind a new env flag `QS_AIRTABLE_OAUTH_ENABLED` (default `False`) so existing deployments are unaffected.
- Implement transparent OAuth refresh-on-401 inside `AirtableInterface`; if refresh ultimately fails, raise a typed `AirtableReauthRequired` exception.

### Non-Goals (explicitly out of scope)

- **Writing records, creating tables, or modifying Airtable schemas via the Source.** The Interface exposes method stubs for these (signatures only — `NotImplementedError`) so that a future feature can add them without touching the Source layer; the AirtableSource itself does not call them. *(Per Phase 0 Q&A clarification.)*
- **Per-user PAT via env-var-by-username (FEAT-091 `CredentialResolver` style).** Only a single global `AIRTABLE_ACCESS_TOKEN` env var is supported as the PAT fallback. *(Per proposal U1.)*
- **Persistent token storage outside `navigator_session`.** No new database table for tokens. *(Per proposal scope.)*
- **Multi-workspace selection UI.** A single Airtable base per source declaration.
- **A frontend single-page application for the consent flow.** QuerySource serves the consent page itself (a minimal inline HTML template); a richer UI is a separate concern. *(Per proposal U2.)*
- **Migrating `querysource/auth/credentials.py::CredentialResolver` to handle OAuth tokens.** That class targets database-driver credentials (HOST/PORT/USER/PASSWORD/DATABASE); OAuth tokens use a separate code path.
- **Streaming/incremental pagination.** Records are fully buffered into a DataFrame; large tables (>50k rows) are an explicit caveat.

---

## 2. Architectural Design

### Overview

Implement `AirtableSource` as a thin `ThreadSource` subclass that:

1. **Parses** `options['source']` — either a full Airtable URL or an explicit `(base_id, table_id, view_id?)` triple — into normalized identifiers.
2. **Selects auth** in this order:
   - Read the request session via `navigator_session.get_session(self._request, new=False)`. If a `session['airtable']` entry exists, use the stored OAuth2 access token.
   - Else fall back to `self.resolve_credential('access_token', creds.get('access_token', 'AIRTABLE_ACCESS_TOKEN'))` — i.e. the server-wide PAT via navconfig.
   - Else raise `RuntimeError("AirtableSource: no credentials available — provide a session OAuth token or set AIRTABLE_ACCESS_TOKEN")`.
3. **Delegates** the actual HTTP work to a freshly-instantiated `AirtableInterface(token=..., is_oauth=bool)`.
4. **Calls** `await interface.list_records(base_id, table_id, view_id)` which transparently handles pagination and 401-refresh.
5. **Converts** the list of record dicts to a pandas DataFrame via `pd.DataFrame.from_records([r["fields"] for r in records]).infer_objects()` — Airtable returns each record as `{"id": "...", "fields": {...}, "createdTime": "..."}`; field normalization rules are documented in §7.

The `AirtableInterface` class is the **single point of contact with the Airtable API**:

- Owns the `aiohttp.ClientSession` lifecycle (one session per source invocation).
- Builds `Authorization: Bearer <token>` headers from either an OAuth access token or a PAT.
- Loops over `offset`-based pagination until the API returns no `offset` field.
- On HTTP 401: if `is_oauth=True` and a `refresh_token` is available, attempts a token-refresh exchange and persists the new tokens back into the session via a caller-supplied callback. On refresh failure, raises `AirtableReauthRequired`. On PAT auth, propagates the 401 as `RuntimeError`.
- Exposes (but does not implement) write stubs: `create_records`, `update_records`, `delete_records`, `create_table` — each raises `NotImplementedError("Deferred to a future feature; see FEAT-096 §1 Non-Goals")`. These exist so the public surface is stable when write support is added later.

OAuth callback wiring lives entirely in two new aiohttp handler classes inside a new `querysource/handlers/integrations/airtable.py` module — registered conditionally inside `QuerySource.setup()` based on `QS_AIRTABLE_OAUTH_ENABLED`.

### Component Diagram

```
                                    ┌──────────────────────────┐
                                    │  Airtable OAuth2 server   │
                                    │  airtable.com/oauth2/...  │
                                    └────────────┬──────────────┘
                                                 │ redirect (code)
                                                 ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                  QuerySource.setup() routes                   │
  │                                                                │
  │  GET /api/v1/qs/integrations/airtable/connect  ─→ Consent HTML │
  │  GET /api/v1/qs/integrations/airtable/callback ─→ token exchg  │
  │                                                                │
  └─────────────────────────────┬─────────────────────────────────┘
                                │ writes session['airtable']
                                ▼
                       ┌─────────────────────┐
                       │ navigator_session   │
                       │   SessionData       │
                       └─────────┬───────────┘
                                 │ read on every fetch
                                 ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  AirtableSource(ThreadSource)                                   │
  │    .fetch() ─→ parse URL/IDs ─→ select auth ─→ Interface        │
  └─────────────────────────────┬──────────────────────────────────┘
                                │ delegates everything
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  AirtableInterface                                              │
  │    .list_records()  paginated, refresh-on-401, returns dicts    │
  │    .create_records() / .update_records() / .create_table()      │
  │       └── stubs (NotImplementedError, signatures stable)        │
  └─────────────────────────────┬──────────────────────────────────┘
                                │ aiohttp.ClientSession
                                ▼
                       https://api.airtable.com/v0
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `ThreadSource` (`querysource/queries/multi/sources/base.py:11`) | **inherits** | `AirtableSource(ThreadSource)` — accepts `(name, options, request, queue)` and implements `async def fetch() -> pd.DataFrame`. |
| `SOURCE_REGISTRY` (`querysource/queries/multi/sources/__init__.py:22`) | **modifies** | Adds entry `"AirtableSource": AirtableSource`. |
| `QuerySource.setup` (`querysource/services.py:97`) | **modifies** | Conditionally registers two new routes when `QS_AIRTABLE_OAUTH_ENABLED` is true. |
| `navigator_session.get_session` (`querysource/handlers/abstract.py:9`) | **uses** | Read session in Source + callback handler; write session in callback handler. |
| `AbstractHandler` (`querysource/handlers/abstract.py`) | **inherits** | New `AirtableConnectView` and `AirtableCallbackView` follow the same handler-class pattern used by `ComponentHandler` (`querysource/handlers/components.py:24`). |
| `navconfig.config` (used throughout `conf.py`) | **uses** | Read `AIRTABLE_*` env vars + `QS_AIRTABLE_OAUTH_ENABLED` via the existing `config.get` / `config.getboolean` pattern. |
| `ThreadSource.resolve_credential` (`querysource/queries/multi/sources/base.py:37`) | **uses** | Resolves PAT env-var values via navconfig. |

### Data Models

```python
# querysource/interfaces/airtable.py

from typing import Optional, Awaitable, Callable
from datetime import datetime
from dataclasses import dataclass


@dataclass(slots=True)
class AirtableTokens:
    """OAuth2 token bundle stored in session['airtable']."""

    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]       # UTC
    scope: Optional[str]
    token_type: str = "Bearer"


class AirtableReauthRequired(RuntimeError):
    """Raised when the session OAuth token is expired and refresh failed.

    The frontend / CLI is expected to catch this and prompt the user to
    re-run the /api/v1/qs/integrations/airtable/connect flow.
    """


# Callback signature used by AirtableInterface to persist refreshed tokens
# back into the session (the Source layer wires this).
TokenPersistFn = Callable[[AirtableTokens], Awaitable[None]]
```

### New Public Interfaces

```python
# querysource/queries/multi/sources/airtable.py

class AirtableSource(ThreadSource):
    """Fetch all records from an Airtable table and return as a DataFrame.

    Configuration dict shape::

        {
            "credentials": {
                # Optional explicit overrides; otherwise resolved from env vars.
                "access_token": "AIRTABLE_ACCESS_TOKEN",
                "client_id":    "AIRTABLE_CLIENT_ID",
                "client_secret":"AIRTABLE_CLIENT_SECRET",
            },
            "source": {
                # EITHER a full Airtable URL:
                "url": "https://airtable.com/appXXX/tblYYY/viwZZZ",
                # OR explicit identifiers:
                "base_id":  "appXXX",
                "table":    "tblYYY",     # accepts table_id or table_name
                "view":     "viwZZZ",     # optional
                # Optional API-side filters:
                "filter_by_formula": "AND(...)",
                "max_records": 5000,
                "page_size":  100,
            }
        }
    """

    BASE_URL: str = "https://api.airtable.com/v0"

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...

    async def fetch(self) -> pd.DataFrame: ...


# querysource/interfaces/airtable.py

class AirtableInterface:
    """Encapsulates ALL Airtable HTTP API interactions.

    Read methods are implemented now; write methods are stubs that raise
    NotImplementedError so the public surface is stable when a future
    feature adds write support.
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
    ) -> None: ...

    # --- URL parsing -----------------------------------------------------
    @staticmethod
    def parse_url(url: str) -> tuple[str, str, Optional[str]]:
        """Parse 'https://airtable.com/<base>/<table>[/<view>]' → (base, table, view)."""
        ...

    # --- READ ------------------------------------------------------------
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
        """Paginate over /v0/{base}/{table} and return all records.

        Handles offset-based pagination and 401-refresh transparently.
        Raises AirtableReauthRequired if OAuth refresh fails.
        """
        ...

    # --- WRITE (stubs — deferred) ----------------------------------------
    async def create_records(self, base_id: str, table: str,
                             records: list[dict]) -> list[dict]:
        raise NotImplementedError("Airtable write support is deferred — see FEAT-096 §1 Non-Goals")

    async def update_records(self, base_id: str, table: str,
                             records: list[dict]) -> list[dict]:
        raise NotImplementedError("Airtable write support is deferred — see FEAT-096 §1 Non-Goals")

    async def delete_records(self, base_id: str, table: str,
                             record_ids: list[str]) -> list[dict]:
        raise NotImplementedError("Airtable write support is deferred — see FEAT-096 §1 Non-Goals")

    async def create_table(self, base_id: str, schema: dict) -> dict:
        raise NotImplementedError("Airtable write support is deferred — see FEAT-096 §1 Non-Goals")


# querysource/handlers/integrations/airtable.py

class AirtableConnectView(AbstractHandler):
    """GET /api/v1/qs/integrations/airtable/connect — serves consent HTML."""
    async def get(self, request: web.Request) -> web.Response: ...


class AirtableCallbackView(AbstractHandler):
    """GET /api/v1/qs/integrations/airtable/callback — exchanges code for tokens."""
    async def get(self, request: web.Request) -> web.Response: ...
```

---

## 3. Module Breakdown

### Module 1: `AirtableInterface` (foundation)
- **Path**: `querysource/interfaces/airtable.py`
- **Responsibility**: All Airtable HTTP API logic — URL parsing, paginated record fetch, OAuth refresh-on-401 retry, write-method stubs. Uses raw `aiohttp.ClientSession` (no SDK dependency).
- **Depends on**: `aiohttp` (existing project dep), `navconfig.logging` (existing).
- **Tests**: `tests/interfaces/test_airtable_interface.py` — mock aiohttp transport, verify pagination, refresh, error paths.

### Module 2: `AirtableSource` (MultiQuery source)
- **Path**: `querysource/queries/multi/sources/airtable.py`
- **Responsibility**: Thin `ThreadSource` subclass. Parses config, selects auth (session OAuth → PAT), instantiates `AirtableInterface`, converts records to DataFrame.
- **Depends on**: Module 1, `ThreadSource`, `navigator_session.get_session`.
- **Tests**: `tests/multi/sources/test_airtable_source.py` — unit tests for URL parsing, auth selection, DataFrame conversion; integration test against a recorded API fixture.

### Module 3: Source registry update
- **Path**: `querysource/queries/multi/sources/__init__.py` (modify)
- **Responsibility**: Add the import and the `SOURCE_REGISTRY` entry.
- **Depends on**: Module 2.
- **Tests**: existing registry test (if any) extended; otherwise covered by Module 2 integration test.

### Module 4: OAuth handler views
- **Path**: `querysource/handlers/integrations/__init__.py` (new package marker), `querysource/handlers/integrations/airtable.py` (new file with two view classes).
- **Responsibility**: `AirtableConnectView` (renders consent HTML + builds Airtable authorize URL), `AirtableCallbackView` (exchanges code for tokens, persists to session).
- **Depends on**: `AbstractHandler`, `navigator_session.get_session`, `AirtableInterface`.
- **Tests**: `tests/handlers/test_airtable_oauth.py` — mock token-exchange call, verify session writeback, verify `state` CSRF parameter handling.

### Module 5: Configuration + route registration
- **Path**: `querysource/conf.py` (modify — add 6 new env constants), `querysource/services.py::QuerySource.setup` (modify — conditional route registration).
- **Responsibility**: Expose `AIRTABLE_*` env vars and `QS_AIRTABLE_OAUTH_ENABLED`; register routes when flag is on.
- **Depends on**: Module 4.
- **Tests**: `tests/test_querysource_setup.py` — verify routes register/don't-register based on flag.

### Module 6: Documentation + example
- **Path**: `docs/sources/airtable.md` (new) and a short example in `examples/`.
- **Responsibility**: Document the YAML/JSON shape, env vars, and OAuth setup steps.
- **Depends on**: all prior modules.
- **Tests**: N/A (doc only).

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_parse_url_full` | 1 | `parse_url("https://airtable.com/app1/tbl1/viw1")` returns `("app1", "tbl1", "viw1")`. |
| `test_parse_url_no_view` | 1 | `parse_url("https://airtable.com/app1/tbl1")` returns `("app1", "tbl1", None)`. |
| `test_parse_url_invalid_raises` | 1 | Malformed URL raises `ValueError`. |
| `test_list_records_pagination` | 1 | Mock 3-page response with `offset` chained; verify all records concatenated, no duplicates. |
| `test_list_records_oauth_refresh_on_401` | 1 | First call returns 401, refresh exchange returns new tokens, retry returns 200; `persist_tokens` callback was invoked. |
| `test_list_records_oauth_refresh_failure_raises_reauth` | 1 | First 401 + refresh exchange returns 400 → `AirtableReauthRequired`. |
| `test_list_records_pat_401_propagates` | 1 | PAT auth with 401 raises `RuntimeError`, NOT `AirtableReauthRequired`. |
| `test_write_stubs_raise_not_implemented` | 1 | `create_records`, `update_records`, `delete_records`, `create_table` each raise `NotImplementedError`. |
| `test_source_init_parses_url` | 2 | `AirtableSource` accepts `options['source']['url']` and extracts base/table/view. |
| `test_source_init_parses_explicit_ids` | 2 | Accepts explicit `base_id` + `table` + `view` keys. |
| `test_source_auth_session_first` | 2 | Mock request with `session['airtable']` populated → uses OAuth token; PAT env var is not read. |
| `test_source_auth_pat_fallback` | 2 | No session present → uses `AIRTABLE_ACCESS_TOKEN` resolved via `resolve_credential`. |
| `test_source_auth_neither_raises` | 2 | No session AND no PAT → raises `RuntimeError`. |
| `test_source_fetch_returns_dataframe` | 2 | Mock `AirtableInterface.list_records` to return 3 records → `fetch()` returns a 3-row DataFrame. |
| `test_source_fetch_empty_table_returns_empty_dataframe` | 2 | Empty result → empty (zero-row) DataFrame, **never `None`**. |
| `test_registry_entry_exists` | 3 | `SOURCE_REGISTRY["AirtableSource"]` resolves to the class. |
| `test_connect_view_redirect_url_well_formed` | 4 | Issued redirect URL includes `client_id`, `redirect_uri`, `response_type=code`, `state`, `code_challenge` (PKCE). |
| `test_callback_view_writes_session` | 4 | Mock token exchange → callback writes `session['airtable']` with all expected keys. |
| `test_callback_view_state_csrf_mismatch_rejects` | 4 | If returned `state` does not match the value the handler stored → 400. |
| `test_setup_routes_off_by_default` | 5 | With `QS_AIRTABLE_OAUTH_ENABLED=False`, neither `/connect` nor `/callback` is registered. |
| `test_setup_routes_on_when_flag_set` | 5 | With flag true, both routes resolve via `app.router`. |

### Integration Tests

| Test | Description |
|---|---|
| `test_e2e_pat_fetch` | End-to-end: instantiate `AirtableSource` with PAT, call `fetch()` against a recorded VCR cassette of `api.airtable.com`, assert DataFrame shape + types. |
| `test_e2e_multiquery_pipeline` | A MultiQuery YAML referencing `AirtableSource` runs through the full pipeline (join with another source, applies filter, emits output). |
| `test_oauth_flow_roundtrip` | Simulate `/connect` → external auth (mocked) → `/callback` → session contains tokens → subsequent `AirtableSource.fetch` uses the session token. |

### Test Data / Fixtures

```python
# tests/multi/sources/conftest.py

@pytest.fixture
def airtable_pat_options():
    return {
        "credentials": {"access_token": "AIRTABLE_ACCESS_TOKEN"},
        "source": {"url": "https://airtable.com/appTEST/tblTEST/viwTEST"},
    }

@pytest.fixture
def airtable_session_request(make_aiohttp_request_with_session):
    request = make_aiohttp_request_with_session(
        session_data={"airtable": {
            "access_token": "oauth-abc",
            "refresh_token": "refresh-xyz",
            "expires_at": "2099-01-01T00:00:00Z",
            "scope": "data.records:read",
        }}
    )
    return request

@pytest.fixture
def airtable_api_records_page():
    return {
        "records": [
            {"id": "rec1", "fields": {"Name": "Alice", "Age": 30}, "createdTime": "2024-01-01T00:00:00.000Z"},
            {"id": "rec2", "fields": {"Name": "Bob",   "Age": 25}, "createdTime": "2024-01-02T00:00:00.000Z"},
        ],
        "offset": None,
    }
```

---

## 5. Acceptance Criteria

This feature is complete when ALL of the following are true:

- [ ] All unit tests pass (`pytest tests/interfaces/test_airtable_interface.py tests/multi/sources/test_airtable_source.py tests/handlers/test_airtable_oauth.py -v`).
- [ ] All integration tests pass (`pytest tests/integration/test_airtable_multiquery.py -v`).
- [ ] `AirtableSource` is registered in `SOURCE_REGISTRY` in `querysource/queries/multi/sources/__init__.py` and exported via `__all__`.
- [ ] `AirtableSource.fetch()` returns a `pandas.DataFrame` — never `None`, never raises for an empty table (empty DataFrame instead).
- [ ] `AirtableSource` accepts either `options['source']['url']` OR explicit `base_id`/`table`/`view` keys; both forms work end-to-end against the recorded fixture.
- [ ] Auth precedence is **session-first, PAT-fallback, raise on neither**: when `session['airtable']` is present its `access_token` is used; otherwise `AIRTABLE_ACCESS_TOKEN` env var is resolved via `self.resolve_credential`; otherwise `RuntimeError` is raised with a clear message.
- [ ] On HTTP 401 with OAuth auth: `AirtableInterface` attempts exactly **one** refresh exchange, persists new tokens via the `persist_tokens` callback, and retries the original request. On refresh failure (or missing refresh_token), it raises `AirtableReauthRequired`.
- [ ] On HTTP 401 with PAT auth: `AirtableInterface` raises `RuntimeError` (never `AirtableReauthRequired`).
- [ ] `AirtableInterface.create_records`, `update_records`, `delete_records`, `create_table` exist with stable signatures and each raise `NotImplementedError` referencing this spec.
- [ ] `QuerySource.setup()` registers `GET /api/v1/qs/integrations/airtable/connect` and `GET /api/v1/qs/integrations/airtable/callback` **only when** `QS_AIRTABLE_OAUTH_ENABLED=True`. Default (`False`) leaves both routes absent.
- [ ] `/connect` returns a 200 HTML response with a "Connect to Airtable" link whose `href` is a properly-formed Airtable OAuth2 authorize URL containing `client_id`, `redirect_uri`, `response_type=code`, `state`, and a PKCE `code_challenge` + `code_challenge_method=S256`.
- [ ] `/callback` validates the `state` value against the one stored at `/connect` time (CSRF defense); mismatch yields HTTP 400. On valid `state`, it exchanges the authorization code for tokens and writes `{access_token, refresh_token, expires_at, scope, token_type}` to `session['airtable']`.
- [ ] If `navigator_session` is not installed in a given deployment, `/connect` and `/callback` return HTTP 503 with a clear message; `AirtableSource` still works via PAT (mirroring the defensive logging pattern at `querysource/handlers/abstract.py:248`).
- [ ] The token leaked in the original prompt (`pat36EoFVW…`) appears **nowhere** in the implementation, fixtures, or tests. Every reference uses the env-var name `AIRTABLE_ACCESS_TOKEN` (verified via repo-wide grep).
- [ ] No breaking changes to existing public API (existing sources, `ThreadSource`, `SOURCE_REGISTRY` consumers continue to work).
- [ ] Documentation updated: new `docs/sources/airtable.md` covers YAML shape, env vars, PAT-vs-OAuth setup, and the OAuth callback registration steps.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Every entry below was verified by reading the actual file during research
> phase (sdd/state/FEAT-096/findings/F001–F009). Implementation agents MUST
> NOT reference imports, attributes, or methods not listed here without first
> verifying they exist via `grep` or `read`.

### Verified Imports

```python
# Multi-query source base — querysource/queries/multi/sources/base.py:1-8
import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd
from aiohttp import web

from .base import ThreadSource    # verified: __init__.py:1 re-exports it

# Source registry — querysource/queries/multi/sources/__init__.py:22
from querysource.queries.multi.sources import SOURCE_REGISTRY

# Session retrieval — handlers/abstract.py:9 — confirmed working in production
from navigator_session import get_session, SessionData

# AbstractHandler — querysource/handlers/abstract.py:9, used by ComponentHandler:24
from querysource.handlers.abstract import AbstractHandler

# navconfig — used throughout conf.py
from navconfig import config
from navconfig.logging import logging

# aiohttp app type — querysource/services.py:13
from aiohttp import web
```

### Existing Class Signatures

```python
# querysource/queries/multi/sources/base.py:11-116
class ThreadSource(threading.Thread, ABC):
    """Abstract base class for all MultiQuery source threads."""

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None: ...                                       # line 22-35

    def resolve_credential(self, key: str, value: str) -> str:
        """Resolve via navconfig if value looks like an env-var name."""
        # line 37-62 — uppercase + underscore → navconfig.config.get(value)

    @property
    def slug(self) -> str: ...                           # line 64-72

    @abstractmethod
    async def fetch(self) -> pd.DataFrame: ...           # line 74-88

    def run(self) -> None: ...                           # line 90-116


# querysource/queries/multi/sources/__init__.py:22-27
SOURCE_REGISTRY: dict = {
    "SharepointSource": SharepointSource,
    "SmartSheetSource": SmartSheetSource,
    "S3Source": S3Source,
    "TableSource": TableSource,
}


# querysource/services.py:49 (Singleton metaclass)
class QuerySource(metaclass=Singleton):

    def setup(self, app: web.Application) -> web.Application:
        # line 97-310 — wires every route via self.app.router.add_get/add_post
        # NOTE: there is NO method named `configure()` — the prompt called
        # it `configure()`, but the real method is `setup()`. This spec
        # uses the correct name throughout.


# querysource/handlers/abstract.py:225-251
class AbstractHandler:
    async def _get_user_session(
        self,
        request: web.Request,
    ) -> Optional[SessionData]:
        """Cached lookup of navigator_session.get_session(request, new=False).

        Caches on request['user_session']. Returns None if navigator_session
        is not installed (logs error)."""


# querysource/handlers/components.py:24 — example handler-class pattern
class ComponentHandler(AbstractHandler):
    async def list_components(self, request: web.Request) -> web.Response: ...
    async def validate_pipeline(self, request: web.Request) -> web.Response: ...


# Closest analogues (read these BEFORE implementing AirtableSource):
#   - querysource/queries/multi/sources/smartsheet.py:18-92  (Bearer token + aiohttp.ClientSession)
#   - querysource/queries/multi/sources/sharepoint.py:20-225 (credentials + lazy import + optional extras)
#   - querysource/queries/multi/sources/s3.py:24-165         (resolve_credential pattern for multiple keys)
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `AirtableSource.__init__` | `ThreadSource.__init__` | `super().__init__(name, options, request, queue)` | `querysource/queries/multi/sources/base.py:22-35` |
| `AirtableSource.fetch` | `pd.DataFrame.from_records` + `infer_objects()` | function call | `querysource/queries/multi/sources/smartsheet.py:90-91` (precedent) |
| `AirtableSource` (auth path) | `navigator_session.get_session(request, new=False)` | awaited call | `querysource/handlers/abstract.py:246` (precedent for usage pattern) |
| `SOURCE_REGISTRY` | new entry `"AirtableSource"` | dict mutation in `__init__.py` | `querysource/queries/multi/sources/__init__.py:22-27` |
| `AirtableConnectView` / `AirtableCallbackView` | `AbstractHandler` | inheritance | `querysource/handlers/components.py:24` (precedent) |
| `QuerySource.setup` | adds 2 routes via `self.app.router.add_get(...)` | function call | `querysource/services.py:218-227` (precedent: ComponentHandler registration) |
| `conf.py` env reads | `config.get('VAR', fallback=...)`, `config.getboolean('QS_AIRTABLE_OAUTH_ENABLED', fallback=False)` | function call | `querysource/conf.py:24-66` (precedent throughout) |

### Does NOT Exist (Anti-Hallucination)

- ~~`QuerySource.configure()`~~ — there is no `configure` method on `QuerySource`. The route-registration entrypoint is `QuerySource.setup(app)` at `querysource/services.py:97`. The original prompt used the wrong method name; implementations MUST target `setup()`.
- ~~`querysource/integrations/`~~ — this package does not exist. New integrations go under `querysource/handlers/integrations/` (a directory we create as part of this feature).
- ~~Any pre-existing OAuth callback handler~~ — repo-wide grep for `oauth|callback|access_token|refresh_token` under `querysource/handlers/` and `querysource/auth/` returns zero matches. There is no template to copy. Build from scratch.
- ~~A "vault" abstraction in querysource~~ — zero matches for `vault|Vault` anywhere. The "session vault" mentioned in the original prompt maps to a single key inside `navigator_session.SessionData`: `session['airtable']`. Do not invent a separate `SecretsVault` class.
- ~~`querysource/auth/credentials.py::CredentialResolver` for OAuth~~ — that class resolves database HOST/PORT/USER/PASSWORD/DATABASE (FEAT-091). It is **not** suitable for OAuth tokens; do not extend it. OAuth tokens flow through `AirtableInterface` + session storage.
- ~~`pyairtable` SDK~~ — not a project dependency, and we will not add it. Use raw `aiohttp.ClientSession` (per Q-impl-1).
- ~~`requests` / `httpx` for the Airtable API~~ — `httpx` is used elsewhere (e.g. SharePoint download) but Airtable MUST use `aiohttp.ClientSession` to match the SmartSheet pattern and avoid a second sync/async story.
- ~~`session.set(...)` or `session.put(...)` methods~~ — `SessionData` is dict-like; writes use `session[key] = value`.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Constructor signature is fixed**: `(name: str, options: dict, request: web.Request, queue: asyncio.Queue)`. Always call `super().__init__(name, options, request, queue)`. Do not introduce a different signature.
- **Resolve env-var-looking credentials via `self.resolve_credential(key, value)`** — never call `os.environ` or `config.get` directly inside the Source. The base class handles the uppercase-underscore heuristic.
- **Use raw `aiohttp.ClientSession` with `aiohttp.ClientTimeout(total=30)`** for all Airtable HTTP calls. Mirror the pattern at `querysource/queries/multi/sources/smartsheet.py:77-87`. Do NOT use `httpx`, `requests`, or `pyairtable`.
- **All Airtable API logic lives in `AirtableInterface`.** `AirtableSource.fetch` should be ~30-50 lines: parse, select auth, instantiate Interface, await `list_records`, build DataFrame, return.
- **Empty result → empty DataFrame, never `None`.** Construct `pd.DataFrame()` when `records == []`. Mirror behavior of every other source (SmartSheet/SharePoint/S3 all `return df`).
- **Lazy/optional imports**: not required for raw aiohttp (already a project dep), so the heavy-import pattern from `sharepoint.py:113-128` is NOT needed here. Skip the optional-extras section in `pyproject.toml` unless we add a real SDK.
- **Field normalization**: Airtable returns each record as `{"id": "...", "fields": {...}, "createdTime": "..."}`. The DataFrame is built from `[r["fields"] for r in records]`. Linked-record arrays, attachment arrays, lookup arrays, and formula returns are left as-is in the cell (pandas will infer `object` dtype). A future feature can add a normalization pass — see Open Question Q-impl-2 in §8.
- **OAuth2 with PKCE**: Airtable's OAuth2 requires PKCE. The `/connect` view generates a `code_verifier` (random 43-128 chars), stores it in `session['airtable_oauth_state'] = {state, code_verifier}`, and emits `code_challenge = base64url(sha256(code_verifier))`. The `/callback` view reads back the stored values and passes `code_verifier` to the token-exchange POST. Reference: <https://airtable.com/developers/web/api/oauth-reference>.
- **State / CSRF**: `/connect` generates `state = secrets.token_urlsafe(32)`, stores it in `session['airtable_oauth_state']`. `/callback` rejects any request whose `state` query param does not match the stored value with HTTP 400.
- **Logging**: every Source and Interface method uses `self.logger` (or `self._logger`) — never `print()`. Follow the existing pattern at `querysource/queries/multi/sources/base.py:35`.

### Known Risks / Gotchas

- **No precedent for writing OAuth tokens into `navigator_session`.** This spec defines the schema for `session['airtable']`. Treat that schema (the `AirtableTokens` dataclass) as a stable contract — any future feature that touches this key must update this spec.
- **`navigator_session` may be uninstalled in some deployments.** Both views and the Source must degrade gracefully (Source falls back to PAT; views return 503 with a clear message). Mirror the defensive logging at `querysource/handlers/abstract.py:248`.
- **Airtable API rate limit: 5 req/sec per base.** Long tables (many pagination pages) will hit this. Mitigation in this feature: explicit 429 handling that raises `RuntimeError` with the response body. A retry/back-off layer is a follow-up.
- **Large tables fully buffered in memory.** A 100k-row Airtable table can easily exceed 100MB in a DataFrame. The proposal log emits a warning above 100MB (mirror `querysource/queries/multi/sources/s3.py:_SIZE_WARNING_BYTES`). True streaming is a follow-up.
- **Self-served consent HTML is a UI concern inside an API package.** Keep it minimal: a single `<html>` document with a styled "Connect to Airtable" anchor tag. ≤30 lines. No JavaScript, no external CSS.
- **Refresh-token rotation.** Airtable rotates refresh tokens on every refresh; the new `refresh_token` must overwrite the previous one in the session. The `persist_tokens` callback must always be passed a complete `AirtableTokens` instance (not a partial update).
- **`AIRTABLE_REDIRECT_URI` must match the value registered with Airtable's OAuth2 app**, otherwise the token exchange fails with `invalid_grant`. Document this in `docs/sources/airtable.md`.
- **The token leaked in the prompt (`pat36EoFVW…`) is in chat-transcript scope only.** Verify via grep that it does NOT appear in any committed file at PR review time.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `aiohttp` | already required | All Airtable HTTP calls (raw `ClientSession`). |
| `pandas` | already required | DataFrame construction. |
| `navconfig` | already required | Env-var resolution for `AIRTABLE_*` and `QS_AIRTABLE_OAUTH_ENABLED`. |
| `navigator-session` | already required (used in handlers/abstract.py:9) | OAuth token storage. |
| `secrets` (stdlib) | — | CSRF `state` + PKCE `code_verifier` generation. |
| `hashlib` (stdlib) | — | PKCE `code_challenge` (SHA-256). |
| `base64` (stdlib) | — | PKCE `code_challenge` encoding (URL-safe). |

**No new third-party dependencies.** Per Q-impl-1, `pyairtable` is explicitly rejected for this feature.

---

## Worktree Strategy

- **Default isolation unit**: `per-spec` (a single worktree for the whole feature).
- **Why**: the modules are ordered (Module 1 → 2 → 3 → 4 → 5 → 6) and each subsequent task builds on the previous. Module 4 (OAuth handlers) uses `AirtableInterface` (Module 1) directly; running them in separate worktrees would create merge friction.
- **Worktree path**: `.claude/worktrees/feat-096-multi-threadsource-airtable/`
- **Branch**: `feat-096-multi-threadsource-airtable`
- **Base**: `dev` (per frontmatter `type: feature` / `base_branch: dev`)
- **Parallelizable tasks**: none in this feature. Module 4 conceptually could run in parallel with Module 2 once Module 1 is in, but the test fixtures are shared and the marginal speed-up is small.
- **Cross-feature dependencies**: none. FEAT-093 (which introduced `ThreadSource`) is already merged.

---

## 8. Open Questions

> Resolved items use `[x]` and carry the answer from the proposal / Q&A.
> Unresolved items use `[ ]` and block either implementation or a downstream
> decision — task decomposition should treat each `[ ]` as a question to
> close before code is written.

### Resolved (carried forward)

- [x] **U1 — PAT fallback scope.** *Resolved in proposal Phase 5 Q&A*: Global server-wide `AIRTABLE_ACCESS_TOKEN` only. No per-user PAT env-var convention (FEAT-091 style not adopted).
- [x] **U2 — Consent-page ownership.** *Resolved in proposal Phase 5 Q&A*: QuerySource serves a minimal HTML consent page at `/api/v1/qs/integrations/airtable/connect`. The feature is self-contained; no frontend dependency.
- [x] **U3 — Token-refresh failure UX.** *Resolved in proposal Phase 5 Q&A*: `AirtableInterface` raises a typed `AirtableReauthRequired` exception when refresh fails or no refresh token is present. No silent PAT fallback in this case.
- [x] **Q-impl-1 — HTTP client choice.** *Resolved in spec Phase 3 Q&A*: Raw `aiohttp.ClientSession`, no new dependency. Mirrors SmartSheetSource pattern.
- [x] **Q-impl-3 — `AirtableInterface` file location.** *Resolved (proposal recommendation)*: `querysource/interfaces/airtable.py`, alongside `http.py` / `credentials.py`.

### Unresolved (decide before / during implementation)

- [ ] **Q-impl-2 — Airtable field-type normalization rules.** *Owner: implementer (Module 1)*. Open questions to answer before the spec is marked `approved`:
  - Linked records: keep array of record IDs (default) or expand to inner objects via `?expand[]=Field`?
  - Attachments: keep the JSON array (with `url`, `filename`, `type`, `size`) or flatten to a comma-joined `url` string?
  - Formula fields: pandas-`object` is fine for v1; document caveat.
  - Date/datetime: trust pandas `infer_objects()` (object dtype) or proactively convert via `pd.to_datetime`?
  Recommended default (to be confirmed): keep linked records and attachments as-is (object dtype), do NOT proactively coerce dates. Document the behavior in `docs/sources/airtable.md` and revisit only if user reports surface concrete pain.
- [x] **Q-runtime — Token persistence across process restarts.** *Owner: deployment*. `navigator_session` may be backed by Redis / DB / cookie depending on deployment. Confirm the chosen backend persists across restarts (Redis: yes; client-side cookie: depends on size — the OAuth bundle is ~1KB which is fine). If a deployment uses an in-memory session backend, users must reconnect on every restart — document this caveat: navigator-session is backed by database.
- [x] **Q-rate-limit — Retry/back-off layer.** *Owner: follow-up FEAT*. This spec raises `RuntimeError` on HTTP 429. A bounded-retry/jittered-back-off layer is a natural follow-up; not in scope here.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-22 | Jesus Lara | Initial draft scaffolded by `/sdd-spec FEAT-096` from `sdd/proposals/feat-096-multi-threadsource-airtable.proposal.md` + `sdd/state/FEAT-096/` findings. Carries U1–U3 + Q-impl-1 as resolved; Q-impl-2 / Q-runtime / Q-rate-limit remain open. |
