# TASK-674: AirtableInterface — foundation (tokens, exceptions, URL parser, write stubs)

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This is the foundation task for FEAT-096. It introduces `querysource/interfaces/airtable.py` with the data model + URL parser + exception type + write-method stubs that every downstream task (`TASK-675` read methods, `TASK-676` AirtableSource, `TASK-679` OAuth handlers) depends on. Read methods land in `TASK-675`.

Implements §2 Architectural Design → Data Models + New Public Interfaces and §3 Module 1 of the spec.

---

## Scope

- Create `querysource/interfaces/airtable.py` containing:
  - `AirtableTokens` (frozen-ish dataclass with `slots=True`): `access_token`, `refresh_token: Optional[str]`, `expires_at: Optional[datetime]`, `scope: Optional[str]`, `token_type: str = "Bearer"`.
  - `AirtableReauthRequired(RuntimeError)` — typed exception with a docstring referencing the OAuth reconnect flow.
  - `TokenPersistFn` type alias: `Callable[[AirtableTokens], Awaitable[None]]`.
  - `AirtableInterface` class shell — `__init__` accepts `(tokens, is_oauth, client_id=None, client_secret=None, persist_tokens=None, timeout_seconds=30)`. Initializes `self._tokens`, `self._is_oauth`, `self._client_id`, `self._client_secret`, `self._persist_tokens`, `self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)`, and `self._logger = logging.getLogger(__name__)`. **Does not** create an `aiohttp.ClientSession` yet — that lives in `TASK-675`'s `list_records`.
  - Class-level constants: `BASE_URL = "https://api.airtable.com/v0"`, `OAUTH_TOKEN_URL = "https://airtable.com/oauth2/v1/token"`, `OAUTH_AUTHORIZE_URL = "https://airtable.com/oauth2/v1/authorize"`.
  - `@staticmethod parse_url(url: str) -> tuple[str, str, Optional[str]]` — parses `https://airtable.com/<baseId>/<tableId>[/<viewId>]` and returns `(base_id, table_id, view_id_or_None)`. Raises `ValueError` for malformed URLs (missing scheme, wrong host, missing table segment). Accept query strings; ignore them. Accept trailing slash.
  - Write-method stubs (signatures only, body = `raise NotImplementedError(...)`):
    - `async def create_records(self, base_id: str, table: str, records: list[dict]) -> list[dict]`
    - `async def update_records(self, base_id: str, table: str, records: list[dict]) -> list[dict]`
    - `async def delete_records(self, base_id: str, table: str, record_ids: list[str]) -> list[dict]`
    - `async def create_table(self, base_id: str, schema: dict) -> dict`
    - Each must raise `NotImplementedError("Airtable write support is deferred — see FEAT-096 §1 Non-Goals")`.
- Create `tests/interfaces/test_airtable_interface.py` covering the parser and constructor.

**NOT in scope**:
- `list_records` and pagination / 401-refresh logic — that is **TASK-675**.
- Any actual HTTP I/O — no `aiohttp.ClientSession.get` calls in this task.
- Modifying `querysource/queries/multi/sources/` — that is **TASK-676**.
- Modifying `querysource/conf.py` or `querysource/services.py` — those are **TASK-678** / **TASK-680**.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/airtable.py` | CREATE | `AirtableTokens`, `AirtableReauthRequired`, `TokenPersistFn`, `AirtableInterface` shell, `parse_url`, write stubs |
| `tests/interfaces/test_airtable_interface.py` | CREATE | Unit tests for `parse_url`, constructor, and `NotImplementedError` on write stubs |

---

## Codebase Contract (Anti-Hallucination)

> All references verified during FEAT-096 research phase — see `sdd/state/FEAT-096/findings/F001..F009`.

### Verified Imports

```python
# Standard library
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Awaitable, Callable
from urllib.parse import urlparse

# Third-party (already project deps)
import aiohttp                                           # verified: querysource/queries/multi/sources/smartsheet.py:11
from navconfig.logging import logging                    # verified: querysource/queries/multi/sources/base.py uses navconfig
```

### Existing Signatures to Use

```python
# querysource/interfaces/ — directory exists with these files:
#   connections.py, credentials.py, databases/, http.py, __init__.py,
#   playwright_service.py, queries.py, selenium_service.py
# Verified: `ls querysource/interfaces/`. New `airtable.py` sits alongside.

# querysource/interfaces/__init__.py exists but is small — DO NOT re-export
# AirtableInterface from here in this task (avoid coupling; future caller
# code imports the full path `from querysource.interfaces.airtable import ...`).

# aiohttp.ClientTimeout (used as constant timeout for future HTTP calls):
import aiohttp
aiohttp.ClientTimeout(total=30)                          # documented pattern at querysource/queries/multi/sources/smartsheet.py:77
```

### Does NOT Exist

- ~~`querysource/interfaces/__init__.py::AirtableInterface`~~ — not re-exported here; importers must use the full path.
- ~~`querysource.exceptions.AirtableReauthRequired`~~ — define it locally in `querysource/interfaces/airtable.py`. Do NOT add to a global exceptions module.
- ~~`pyairtable`, `airtable-python-wrapper`, or any Airtable SDK~~ — not a project dep; do not import.
- ~~`requests` or `httpx` for Airtable~~ — use `aiohttp` only.
- ~~`pydantic.BaseModel` for `AirtableTokens`~~ — the project pattern for source-side data objects (see `querysource/auth/credentials.py:17` `ResolvedCredentials`) uses `@dataclass(slots=True)`. Match that style.
- ~~A `vault` module~~ — no such concept exists; tokens flow through `AirtableTokens` and (in later tasks) `navigator_session`.

---

## Implementation Notes

### Pattern to Follow

Match the style of `querysource/auth/credentials.py:17-38` (the `ResolvedCredentials` dataclass) for `AirtableTokens`:

```python
# querysource/auth/credentials.py:17 (reference)
@dataclass(slots=True)
class ResolvedCredentials:
    """Connection parameters resolved by CredentialResolver.

    Args:
        host: ...
    """
    host: str
    port: int
    user: str
    password: str
    database: str
    source: str
```

URL parser — be strict about scheme and host but lenient about query string and trailing slash:

```python
@staticmethod
def parse_url(url: str) -> tuple[str, str, Optional[str]]:
    """Parse 'https://airtable.com/<base>/<table>[/<view>][?...][/]' → (base, table, view)."""
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
```

### Key Constraints

- Use `logging.getLogger(__name__)` for `self._logger`. Never `print()`.
- `parse_url` is a `@staticmethod` — no `self`. Allows callers like `AirtableSource` to use it before instantiation.
- Write stubs must reference FEAT-096 in their `NotImplementedError` message so future readers find this spec.
- `AirtableTokens.expires_at` is `Optional[datetime]` — Airtable's response gives `expires_in` (seconds); conversion to absolute UTC `datetime` happens in the OAuth handler (`TASK-679`), not here.
- Do **not** add `expires_at` validation / freshness logic in this task. That belongs in `TASK-675` (read methods, on 401 path).

### References in Codebase

- `querysource/queries/multi/sources/smartsheet.py:18-92` — concrete Bearer-token + aiohttp pattern this Interface will eventually use (next task).
- `querysource/queries/multi/sources/sharepoint.py:113-128` — lazy-import pattern; **not needed here** because `aiohttp` is always available.
- `querysource/auth/credentials.py:17-38` — dataclass-with-slots style to match.

---

## Acceptance Criteria

- [ ] `querysource/interfaces/airtable.py` exists and contains: `AirtableTokens`, `AirtableReauthRequired`, `TokenPersistFn`, `AirtableInterface` (with constants + `__init__` + `parse_url` + 4 write stubs).
- [ ] `from querysource.interfaces.airtable import AirtableInterface, AirtableTokens, AirtableReauthRequired` works.
- [ ] `AirtableInterface.parse_url("https://airtable.com/app1/tbl1/viw1")` returns `("app1", "tbl1", "viw1")`.
- [ ] `AirtableInterface.parse_url("https://airtable.com/app1/tbl1")` returns `("app1", "tbl1", None)`.
- [ ] `AirtableInterface.parse_url("https://airtable.com/app1/tbl1/")` returns `("app1", "tbl1", None)` (trailing slash tolerated).
- [ ] `AirtableInterface.parse_url("https://airtable.com/app1/tbl1?ignored=1")` returns `("app1", "tbl1", None)`.
- [ ] `AirtableInterface.parse_url("https://example.com/app1/tbl1")` raises `ValueError`.
- [ ] `AirtableInterface.parse_url("https://airtable.com/onlybase")` raises `ValueError`.
- [ ] All four write stubs (`create_records`, `update_records`, `delete_records`, `create_table`) raise `NotImplementedError` whose message contains the substring `"FEAT-096"`.
- [ ] `pytest tests/interfaces/test_airtable_interface.py -v` passes.
- [ ] `ruff check querysource/interfaces/airtable.py tests/interfaces/test_airtable_interface.py` passes.
- [ ] The leaked token (`pat36EoFVW…`) appears **nowhere** in any file (`grep -r "pat36EoFVW" .` returns nothing committed).

---

## Test Specification

```python
# tests/interfaces/test_airtable_interface.py
import pytest

from querysource.interfaces.airtable import (
    AirtableInterface,
    AirtableTokens,
    AirtableReauthRequired,
)


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
```

---

## Agent Instructions

1. Read the spec (`sdd/specs/multi-threadsource-airtable.spec.md`) §2 Data Models, §6 Codebase Contract, and §7 Implementation Notes.
2. Verify the Codebase Contract above is still accurate (`grep -n "ResolvedCredentials" querysource/auth/credentials.py`; `ls querysource/interfaces/`).
3. Implement per Scope. Do not import or call any HTTP machinery — that is `TASK-675`.
4. Run `pytest tests/interfaces/test_airtable_interface.py -v` and confirm green.
5. Run `ruff check querysource/interfaces/airtable.py tests/interfaces/test_airtable_interface.py`.
6. Move this file to `sdd/tasks/completed/TASK-674-airtable-interface-foundation.md`.
7. Update `sdd/tasks/index/multi-threadsource-airtable.json` → `tasks[0].status = "completed"`, `completed_at = <ISO>`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:

**Completed by**: SDD Worker (Claude Sonnet 4.6)
**Date**: 2026-05-22
**Notes**: Implemented as specified. AirtableInterface foundation (tokens, exception, URL parser, write stubs) and read methods (list_records, _refresh_tokens, _request_with_refresh, _auth_headers) combined into single commit since both tasks modify the same file and TASK-675 depends directly on TASK-674.
**Deviations from spec**: Tests use `re.compile()` regex patterns instead of bare URL strings in `aioresponses` mock registrations, because aioresponses 0.7.8 matches the full URL including query parameters. The regex approach (`re.compile(r"https://api\.airtable\.com/v0/appX/tblY.*")`) is functionally equivalent and recommended for this version.
