# TASK-805: Delegated identity resolver (`querysource/auth/identity_tokens.py`)

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (G3, G4). This module resolves a delegated OneDrive access
token for either a request user (session vault) or a scheduler run-as user
(`auth.user_identities` through navigator-auth's `IdentityProvider`). It
performs **no** refresh-grant logic of its own: navigator-auth refreshes and
persists rotation.

---

## Scope

- Create `identity_tokens.py` with `ONEDRIVE_PROVIDER`, `IDENTITY_LINK_PATH`,
  `DelegatedIdentityError`, `DelegatedToken`, `SourceIdentityContext` (with
  `from_request`/`for_scheduler`), `user_id_from_session`,
  `identity_provider(auth)` and `resolve_delegated_token`.
- Import navigator-auth symbols **lazily inside functions**. On `ImportError`,
  raise `DelegatedIdentityError(reason="auth_unavailable")`.
- Write unit tests with fake session, vault and IdP.

**NOT in scope**: calling this from MultiQS or sources (TASK-806 / TASK-807).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/identity_tokens.py` | CREATE | resolver module |
| `tests/auth/test_identity_tokens.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..exceptions import QueryException                                   # verified: querysource/exceptions.py:6
# lazy, inside functions (navigator-auth 0.28.2 installed):
from navigator_auth.conf import AUTH_SESSION_OBJECT, IDENTITY_REFRESH_LEEWAY  # "session", 120
from navigator_auth.identity.store import cached_credential, cache_credential
from navigator_auth.identity.types import TokenResponse
from navigator_auth.exceptions import UserNotFound, ConfigError, AuthException
```

### Existing Signatures to Use
```python
# navigator_auth (site-packages 0.28.2)
async def cached_credential(session: Any, provider: str) -> Optional[dict]        # vault key "identity:{provider}"
async def cache_credential(session: Any, provider: str, credential: dict) -> None
class TokenResponse:
    def is_expiring(self, leeway: int = 0) -> bool
    @classmethod
    def from_credential(cls, data: dict) -> "TokenResponse"                      # credential dict keys: access_token, token_type,
                                                                               #   refresh_token, id_token, expires_at (ISO), scopes, provider_user_id
class IdentityProvider:
    async def get_user_identity_credential(self, user_id, provider: str, *, auto_refresh: bool = True) -> dict
        # raises UserNotFound (no linked identity), ConfigError (expired w/o refresh token, backend disabled)
# AuthHandler: app["auth"]; private self._idp (auth.py:136); public `identity_provider` only from navigator-auth 0.29.0 (FEAT-100)
# session shapes: session.get(AUTH_SESSION_OBJECT, {}) → {"user_id": 42, ...}  OR  session.get("user_id")
```

### Does NOT Exist
- ~~`querysource.auth.identity_tokens`~~ before this task.
- ~~`AuthHandler.identity_provider`~~ in 0.28.2: use `getattr(auth, "identity_provider", None) or getattr(auth, "_idp", None)`.
- ~~`CredentialResolver._extract_username` for user ids~~: it prefers usernames (`auth/credentials.py:78-93`). Do not reuse it.
- ~~Any refresh-token grant code in querysource~~: must not be added.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/identity_tokens.py", "action": "CREATE"},
    {"path": "tests/auth/test_identity_tokens.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/exceptions.py#QueryException"]
}
```

---

## Implementation Notes

### Key Constraints
- Resolution order: (1) if `context.session`, use `cached_credential`, and
  return the token unless `TokenResponse.from_credential(c).is_expiring(IDENTITY_REFRESH_LEEWAY)`;
  (2) `identity_provider(auth).get_user_identity_credential(user_id, provider,
  auto_refresh=True)`; (3) if `context.session`, call `cache_credential(session,
  provider, credential)`, best effort.
- Error mapping: no `context` or `user_id` → `no_user`; no auth or IdP →
  `auth_unavailable`; `UserNotFound` → `not_linked`; `ConfigError`/`AuthException`
  → `refresh_failed`. Every error carries
  `link_url = IDENTITY_LINK_PATH.format(provider=provider)`.
- Never log tokens. Log `user_id`, `provider` and `reason` only.

---

## Implementation Blueprint

### Steps (in order)
1. Create the module below — *why*: TASK-806, 807 and 812 import these exact names.
2. Complete the FILL INs — *why*: the resolution order is spec §2's contract.
3. Write the tests.

### `querysource/auth/identity_tokens.py` (CREATE)
```python
"""Delegated identity-token resolution for MultiQS sources (FEAT-159)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from aiohttp import web

from ..exceptions import QueryException  # verified: querysource/exceptions.py:6

logger = logging.getLogger(__name__)

ONEDRIVE_PROVIDER: str = "onedrive"
IDENTITY_LINK_PATH: str = "/api/v1/user/identities/link/{provider}"


class DelegatedIdentityError(QueryException):
    """No usable linked identity for a delegated source."""

    def __init__(self, message: str, *, provider: str, user_id: Optional[int], reason: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.user_id = user_id
        self.reason = reason  # no_user | auth_unavailable | not_linked | refresh_failed
        self.link_url = IDENTITY_LINK_PATH.format(provider=provider)


@dataclass(frozen=True)
class DelegatedToken:
    access_token: str
    expires_at: Optional[datetime]


def user_id_from_session(session: Any) -> Optional[int]:
    """Numeric user id from session[AUTH_SESSION_OBJECT]['user_id'] or session['user_id']."""
    # FILL IN: lazy AUTH_SESSION_OBJECT import (fallback "session"); int() coercion; None on failure/absence


def identity_provider(auth: Any) -> Any | None:
    """auth.identity_provider (navigator-auth ≥0.29.0) else auth._idp, else None."""
    if auth is None:
        return None
    return getattr(auth, "identity_provider", None) or getattr(auth, "_idp", None)


@dataclass(frozen=True)
class SourceIdentityContext:
    user_id: Optional[int]
    session: Any | None = None
    auth: Any | None = None
    origin: str = "request"

    @classmethod
    def from_request(cls, request: web.Request, session: Any) -> "SourceIdentityContext":
        return cls(user_id=user_id_from_session(session), session=session,
                   auth=request.app.get("auth") if request is not None else None, origin="request")

    @classmethod
    def for_scheduler(cls, run_as_user_id: Optional[int], auth: Any) -> "SourceIdentityContext":
        return cls(user_id=run_as_user_id, session=None, auth=auth, origin="scheduler")


async def resolve_delegated_token(
    context: SourceIdentityContext | None, provider: str = ONEDRIVE_PROVIDER
) -> DelegatedToken:
    """Resolve an access token on the caller's loop (vault → IdP → re-cache)."""
    # FILL IN: order + error mapping per Key Constraints; parse expires_at ISO → aware datetime
```
**Why this shape**: the frozen context crosses from the handler or scheduler
into MultiQS unchanged. The lazy imports keep querysource importable on older
navigator-auth versions.

### FILL IN checklist
- [ ] `user_id_from_session`: both session shapes, numeric only.
- [ ] `resolve_delegated_token`: order, error reasons, best-effort re-cache, no token logging.

---

## Acceptance Criteria

- [ ] A vault hit (not expiring) returns without calling the IdP.
- [ ] An expiring or missing vault entry → the IdP is called with `auto_refresh=True` → re-cached when a session exists.
- [ ] A sessionless (scheduler) context uses the IdP only.
- [ ] Each failure maps to its `reason`, and `link_url == "/api/v1/user/identities/link/onedrive"`.
- [ ] `ruff check querysource/auth/identity_tokens.py` is clean.

## Validation Commands

- `pytest tests/auth/test_identity_tokens.py -q`

---

## Test Specification

```python
# tests/auth/test_identity_tokens.py
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.auth.identity_tokens import (
    DelegatedIdentityError, SourceIdentityContext, resolve_delegated_token, user_id_from_session,
)


def _cred(minutes: int) -> dict:
    exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    return {"access_token": "tok", "token_type": "Bearer", "refresh_token": None,
            "id_token": None, "expires_at": exp, "scopes": [], "provider_user_id": "x"}


def test_user_id_from_session_numeric_only():
    assert user_id_from_session({"session": {"user_id": 42, "username": "bob"}}) == 42
    assert user_id_from_session({"user_id": "7"}) == 7
    assert user_id_from_session({"username": "bob"}) is None


async def test_resolve_token_vault_hit():
    ...  # FILL IN: patch cached_credential → _cred(30); IdP mock not awaited


async def test_resolve_token_vault_expiring_falls_back():
    ...  # FILL IN: cached _cred(1) → idp.get_user_identity_credential → cache_credential awaited


async def test_resolve_token_sessionless_uses_idp():
    ...  # FILL IN: SourceIdentityContext.for_scheduler(42, auth) → idp called (42, "onedrive", auto_refresh=True)


async def test_resolve_token_errors():
    ...  # FILL IN: None ctx→no_user; auth None→auth_unavailable; UserNotFound→not_linked; ConfigError→refresh_failed
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-805 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
