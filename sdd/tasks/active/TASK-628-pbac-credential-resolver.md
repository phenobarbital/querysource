# TASK-628: Per-user credential resolver

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-627
**Assigned-to**: unassigned

---

## Context

Implements **Module 3** of the spec. Builds the `CredentialResolver` that
the Postgres driver (TASK-636) consults at connection-params build time.
Resolution order is per-user env vars → policy `credential_profile`
attribute → datasource default. Group-tier env-var lookup is **explicitly
out of scope** (see spec §6 — "Does NOT Exist"). The resolver runs entirely
without `navigator-auth` imports — it only needs the session dict.

This task is independent of the PBAC bootstrap (TASK-629) and can be
implemented in parallel.

---

## Scope

- Implement `querysource/auth/credentials.py` with:
  - `ResolvedCredentials` dataclass (`@dataclass(slots=True)`).
  - `CredentialResolver` class with:
    - `__init__(self, logger: logging.Logger | None = None)`
    - `resolve(self, prefix: str, session: SessionData | None,
        credential_profile: str | None = None) -> ResolvedCredentials | None`
    - `@staticmethod sanitize(value: str) -> str`
- Three-tier lookup with **partial-set fall-through**:
  1. Per-user: `<PREFIX>_<SANITIZED_USERNAME>_HOST/PORT/USER/PASSWORD/DATABASE`
  2. Profile-from-policy: `<PREFIX>_<SANITIZED_PROFILE>_HOST/PORT/USER/PASSWORD/DATABASE`
  3. Datasource default: `<PREFIX>_HOST/PORT/USER/PASSWORD/DATABASE`
- Sanitization: `value.upper().replace(".", "_").replace("-", "_").replace("@", "_")`.
- Partial sets fall through to next tier; warn once per (tier, missing-key) per
  process via the resolver's logger.
- Re-export `CredentialResolver`, `ResolvedCredentials` from
  `querysource/auth/__init__.py`.

**NOT in scope**: registering the resolver instance on the aiohttp app (that's
TASK-629); calling the resolver from the Postgres driver (TASK-636); any
group-tier lookup; any policy evaluation logic.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/credentials.py` | CREATE | `ResolvedCredentials` + `CredentialResolver`. |
| `querysource/auth/__init__.py` | MODIFY | Re-export `CredentialResolver`, `ResolvedCredentials`. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import os
import logging
from dataclasses import dataclass
from typing import Optional
# Type-only — only for type hints; never instantiated by the resolver itself:
from navigator_session import SessionData   # querysource/interfaces/queries.py:18 (already in use)
```

### Existing Signatures to Use

```python
# querysource/conf.py — env-var read pattern (NOTE: resolver uses os.environ
# directly because the lookup keys are dynamic per-user/per-profile, not
# pre-declared in conf.py).

# Session attribute contract (read from userinfo dict; spec §2 Data Models):
#   "username": str         → primary key for user-tier lookup
#   "user_id":  str | int   → fallback if "username" missing
#   "service":  bool        → if True, prefer user_id over username
```

### Does NOT Exist

- ~~`PG_<GROUP>_*` lookup~~ — group-tier env-var lookup is **explicitly out of
  scope**. Group precedence is handled inside the policy engine.
- ~~`querysource.auth.credentials`~~ — module does not exist; this task creates it.
- ~~`navigator_session.SessionData.username` attribute access~~ — `SessionData`
  is dict-like; access via `session.get("username")` or
  `session[AUTH_SESSION_OBJECT]["username"]` depending on layout. Use
  defensive `.get()` chains.
- ~~`os.environ.get_typed`~~ — not a real function. Use `os.environ.get`
  + manual int conversion for `_PORT`.

---

## Implementation Notes

### `ResolvedCredentials` shape

```python
@dataclass(slots=True)
class ResolvedCredentials:
    host: str
    port: int
    user: str
    password: str
    database: str
    source: str   # "user-override" | "profile:<name>" | "default:<prefix>"
```

### Resolution algorithm (sketch — agent must implement)

```python
def resolve(self, prefix, session, credential_profile=None):
    # 1) Per-user
    if session is not None:
        username = self._extract_username(session)
        if username:
            creds = self._lookup(prefix, self.sanitize(username))
            if creds is not None:
                return self._build("user-override", creds)
    # 2) Profile from policy
    if credential_profile:
        creds = self._lookup(prefix, self.sanitize(credential_profile))
        if creds is not None:
            return self._build(f"profile:{credential_profile}", creds)
    # 3) Default
    creds = self._lookup(prefix, None)   # uses just <PREFIX>_HOST etc.
    if creds is not None:
        return self._build(f"default:{prefix}", creds)
    return None
```

`_lookup(prefix, segment)` constructs the keys:
- `f"{prefix}_HOST"` if `segment is None` else `f"{prefix}_{segment}_HOST"`
- Same pattern for `_PORT`, `_USER`, `_PASSWORD`, `_DATABASE`.
- Returns `None` if any of the **5** keys is missing (partial sets fall
  through). Log a single warning at `logger.warning` level on partial sets,
  using a `frozenset((tier, missing_keys))` cache to dedupe.

### Username extraction from session

```python
def _extract_username(self, session) -> str | None:
    if session is None:
        return None
    # navigator-session SessionData: dict-like
    if hasattr(session, 'get'):
        username = session.get("username") or session.get("user_id")
        if username:
            return str(username)
    return None
```

### Sanitization

```python
@staticmethod
def sanitize(value: str) -> str:
    """Canonicalize a username/profile for env-var lookup.

    Rules: uppercase, '.', '-', '@' → '_'. Other chars pass through
    (operators are responsible for safe inputs).
    """
    return value.upper().replace(".", "_").replace("-", "_").replace("@", "_")
```

### `__init__.py` update

```python
from querysource.auth.credentials import CredentialResolver, ResolvedCredentials

__all__ = ("CredentialResolver", "ResolvedCredentials", "logger")
```

### Key Constraints

- Use `os.environ.get` directly — do NOT round-trip through `navconfig` for
  these dynamic keys (the resolver runs at request time, not import time).
- Port must be coerced to `int` before returning; `os.environ.get` returns
  `str`. Catch `ValueError` and treat the whole tier as missing.
- The resolver MUST NOT raise on missing creds — return `None` so the
  driver can fall back to its existing `params()` method.
- All deduped warnings must be `logger.warning(...)` — never `print`.

### References in Codebase

- `querysource/conf.py:38-42` — `PG_*` env-var pattern for the **default**
  tier the resolver mirrors.
- `querysource/interfaces/queries.py:165-178` — existing `user_session`
  helper; useful reference for session-shape assumptions.

---

## Acceptance Criteria

- [ ] `from querysource.auth import CredentialResolver, ResolvedCredentials` succeeds.
- [ ] All five env vars set for `PG_JOHN_*` → `resolve("PG", {"username": "john"})`
      returns `ResolvedCredentials(source="user-override", ...)`.
- [ ] Only `PG_JOHN_HOST` set (other 4 missing) → returns `None` from per-user
      tier and falls through to next; warning logged once.
- [ ] `resolve("PG", session_without_user_keys)` with `PG_TIER1_*` set + 
      `credential_profile="tier1"` → returns
      `ResolvedCredentials(source="profile:tier1", ...)`.
- [ ] No env vars set → returns `None`.
- [ ] `sanitize("john.doe@acme.com")` → `"JOHN_DOE_ACME_COM"`.
- [ ] `sanitize("Some-User")` → `"SOME_USER"`.
- [ ] No regressions: `pytest tests/ -x -q` clean.

---

## Test Specification

Write the unit tests **inline with this task** in `tests/auth/test_credentials.py`
(matching the package layout the spec proposes). Don't wait for TASK-641 —
it covers a broader scope.

```python
# tests/auth/test_credentials.py
import pytest
from querysource.auth import CredentialResolver, ResolvedCredentials


@pytest.fixture
def resolver():
    return CredentialResolver()


class TestSanitize:
    def test_email(self):
        assert CredentialResolver.sanitize("john.doe@acme.com") == "JOHN_DOE_ACME_COM"

    def test_dashes(self):
        assert CredentialResolver.sanitize("Some-User") == "SOME_USER"

    def test_already_clean(self):
        assert CredentialResolver.sanitize("ALICE") == "ALICE"


class TestResolve:
    def test_user_override_full(self, resolver, monkeypatch):
        for k, v in {"PG_JOHN_HOST": "h", "PG_JOHN_PORT": "5432",
                     "PG_JOHN_USER": "u", "PG_JOHN_PASSWORD": "p",
                     "PG_JOHN_DATABASE": "d"}.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", {"username": "john"})
        assert result is not None
        assert result.source == "user-override"
        assert result.host == "h" and result.port == 5432

    def test_user_override_partial_falls_through(self, resolver, monkeypatch):
        monkeypatch.setenv("PG_JANE_HOST", "h")  # only one of five
        for k, v in {"PG_HOST": "default-h", "PG_PORT": "5432",
                     "PG_USER": "u", "PG_PASSWORD": "p",
                     "PG_DATABASE": "d"}.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", {"username": "jane"})
        assert result is not None
        assert result.source.startswith("default:")

    def test_profile_from_policy(self, resolver, monkeypatch):
        for k, v in {"PG_TIER1_HOST": "h", "PG_TIER1_PORT": "5432",
                     "PG_TIER1_USER": "u", "PG_TIER1_PASSWORD": "p",
                     "PG_TIER1_DATABASE": "d"}.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", session=None, credential_profile="tier1")
        assert result.source == "profile:tier1"

    def test_default_tier(self, resolver, monkeypatch):
        for k, v in {"PG_HOST": "h", "PG_PORT": "5432",
                     "PG_USER": "u", "PG_PASSWORD": "p",
                     "PG_DATABASE": "d"}.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", session={})
        assert result.source == "default:PG"

    def test_no_creds_returns_none(self, resolver, monkeypatch):
        for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        assert resolver.resolve("PG", session=None) is None

    def test_invalid_port_falls_through(self, resolver, monkeypatch):
        for k, v in {"PG_BOB_HOST": "h", "PG_BOB_PORT": "not-a-number",
                     "PG_BOB_USER": "u", "PG_BOB_PASSWORD": "p",
                     "PG_BOB_DATABASE": "d"}.items():
            monkeypatch.setenv(k, v)
        # Should fall through, not raise
        result = resolver.resolve("PG", {"username": "bob"})
        # Result depends on whether default tier is set — assert no exception
        assert result is None or result.source.startswith("default:")
```

---

## Agent Instructions

1. Read spec at `sdd/specs/pbac-support.spec.md` (sections 2, 6, 7) for context.
2. Verify the Codebase Contract — confirm `navigator_session.SessionData`
   import path is unchanged.
3. Implement `querysource/auth/credentials.py` per the algorithm sketch.
4. Update `querysource/auth/__init__.py` to re-export.
5. Write the unit tests at `tests/auth/test_credentials.py`.
6. Run `pytest tests/auth/test_credentials.py -v` until green.
7. Run full suite `pytest tests/ -x -q` to verify no regressions.
8. Move this file to `sdd/tasks/done/`.
9. Update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
