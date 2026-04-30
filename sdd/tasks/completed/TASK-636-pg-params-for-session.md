# TASK-636: Postgres `params_for(session)` hook + `credential_prefix`

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-628, TASK-635
**Assigned-to**: unassigned

---

## Context

Implements **Module 10** of the spec. Adds:

1. A `credential_prefix` class attribute to each Postgres-flavoured
   datasource (`postgresDriver` → `"PG"`; `pg_adminDriver` → `"DB"`). This
   is what `CredentialResolver` uses to construct env-var lookup keys —
   resolved Open Question Q2.
2. A new `params_for(session, app=None)` method on `pgDriver` (the shared
   base) that consults `app['credential_resolver']` and returns either the
   resolved per-user/profile params or falls back to the existing
   `params()` output.

The existing `params()` method is **unchanged** — legacy callers continue
to work. Only call sites that opt into per-user resolution (TASK-637)
invoke `params_for(session)`.

---

## Scope

- Add a `credential_prefix: str` class attribute to:
  - `pgDriver` (in `pg.py`) — set to `"PG"` as the default.
  - `postgresDriver` (in `postgres.py`) — explicit `"PG"` (matches default).
  - `pg_adminDriver` (in `pg_admin.py`) — `"DB"`.
- Add `params_for(self, session, app=None) -> dict` method to `pgDriver`:
  - If `app is None` or `app.get('credential_resolver') is None`: return
    `self.params()` (no PBAC ⇒ legacy behaviour).
  - Read the optional `credential_profile` from the session's policy
    attributes, if present (see Implementation Notes).
  - Call `app['credential_resolver'].resolve(prefix=self.credential_prefix,
    session=session, credential_profile=...)`.
  - On `ResolvedCredentials` returned: return a dict matching the shape of
    `params()` (`host`, `port`, `username`, `password`, `database`).
  - On `None`: fall back to `self.params()`.

**NOT in scope**: changing `params()` itself (it stays untouched);
extending the `credential_prefix` to non-Postgres drivers (Postgres-only
in v1 per resolved Q1); driver factory plumbing (TASK-637).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/datasources/drivers/pg.py` | MODIFY | Add `credential_prefix = "PG"` class attr; add `params_for(session, app)` method. |
| `querysource/datasources/drivers/postgres.py` | MODIFY | Add explicit `credential_prefix = "PG"`. |
| `querysource/datasources/drivers/pg_admin.py` | MODIFY | Add `credential_prefix = "DB"`. |
| `tests/datasources/test_pg_params_for.py` | CREATE | Unit tests for `params_for`. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.auth.credentials import CredentialResolver, ResolvedCredentials
# (Imported by tests; pg.py itself only types — does not import nav-auth.)
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/pg.py:17
class pgDriver(SQLDriver):
    # 66 lines total.
    def params(self) -> dict:                                         # line 42
        return {
            "host": self.host, "port": self.port,
            "username": self.user, "password": self.password,
            "database": self.database,
        }

# Resolver signature (TASK-628):
class CredentialResolver:
    def resolve(
        self,
        prefix: str,
        session,
        credential_profile: Optional[str] = None,
    ) -> Optional[ResolvedCredentials]:
        ...

@dataclass(slots=True)
class ResolvedCredentials:
    host: str; port: int; user: str; password: str; database: str; source: str
```

### Does NOT Exist

- ~~`credential_profile` as a top-level session field~~ — it's an attribute
  on the policy decision, not on the session itself. The most reliable
  source is `request['user_session']['policy_attributes']['credential_profile']`
  but that contract isn't established in v1. **Safe v1 approach**: pull
  `credential_profile` from the session's `userinfo` directly if the
  operator has chosen to surface it there; otherwise pass `None` to the
  resolver. Document the decision.
- ~~`params_for` on non-`pgDriver` classes~~ — Postgres only in v1.
  Other SQL drivers continue using `params()`.
- ~~`SQLDriver.params_for`~~ — do not lift the method into the parent base
  in this task. Keep it on `pgDriver` until the multi-driver rollout
  follow-up spec.

---

## Implementation Notes

### `pg.py` changes

```python
class pgDriver(SQLDriver):
    # ... existing fields ...

    # FEAT-091: env-var prefix for the credential resolver. Subclasses can
    # override (e.g. pg_adminDriver sets this to "DB").
    credential_prefix: str = "PG"

    def params(self) -> dict:                                          # unchanged
        return {
            "host": self.host, "port": self.port,
            "username": self.user, "password": self.password,
            "database": self.database,
        }

    def params_for(self, session, app=None) -> dict:
        """Resolve connection params using the per-user credential resolver.

        Falls back to ``self.params()`` when:
          - PBAC is disabled (app['credential_resolver'] absent), or
          - The resolver returns ``None`` (no override matches).

        v1 scope: Postgres only. Other drivers continue using ``params()``.
        """
        if app is None:
            return self.params()
        resolver = app.get('credential_resolver')
        if resolver is None:
            return self.params()

        # Optional credential_profile: look in the session userinfo.
        # If the operator hasn't set this attribute, the resolver will
        # skip the profile tier and proceed to the default tier.
        credential_profile = None
        try:
            if session and hasattr(session, 'get'):
                userinfo = session.get('user', {}) or session.get('userinfo', {}) or {}
                if isinstance(userinfo, dict):
                    credential_profile = userinfo.get('credential_profile')
        except Exception:
            credential_profile = None

        resolved = resolver.resolve(
            prefix=self.credential_prefix,
            session=session,
            credential_profile=credential_profile,
        )
        if resolved is None:
            return self.params()
        return {
            "host":     resolved.host,
            "port":     resolved.port,
            "username": resolved.user,
            "password": resolved.password,
            "database": resolved.database,
        }
```

### `postgres.py` change (explicit override)

```python
class postgresDriver(pgDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    defaults: str = asyncpg_url
    credential_prefix: str = "PG"   # ← NEW (explicit)
```

### `pg_admin.py` change

```python
class pg_adminDriver(pgDriver):
    driver: str = 'pg_admin'
    name: str = 'pg_admin'
    defaults: str = default_dsn
    credential_prefix: str = "DB"   # ← NEW
```

### Key Constraints

- **`params()` must remain unchanged.** Legacy callers (queue workers, the
  `default_sources()` enumeration) call it without a session and expect the
  current behaviour. Verify by `grep -rn 'driver.params()\|\.params()' querysource/`.
- **`params_for` is the new entry point.** TASK-637 plumbs `app` and
  `session` into the call sites that should opt in.
- **Username key in `userinfo`**: the resolver itself extracts `username`
  from the session — `params_for` only forwards the session blob.
- **Never raise** from `params_for`. On any unexpected exception in the
  resolver/session-extraction path, log and fall back to `params()`.

### References in Codebase

- `querysource/datasources/drivers/pg.py` (66 lines) — read end-to-end.
- `querysource/datasources/drivers/postgres.py` (29 lines).
- `querysource/datasources/drivers/pg_admin.py` (created by TASK-635).
- `querysource/auth/credentials.py` (created by TASK-628) — resolver API.

---

## Acceptance Criteria

- [ ] `pgDriver.credential_prefix == "PG"` (verifiable via class access).
- [ ] `postgresDriver.credential_prefix == "PG"`.
- [ ] `pg_adminDriver.credential_prefix == "DB"`.
- [ ] Existing `params()` method on `pgDriver` is unchanged — verifiable via
      diff (only addition, no edit to lines 42-49).
- [ ] `params_for(session=None, app=None)` returns the same dict as
      `params()` — bit-for-bit equal.
- [ ] `params_for(session, app)` with no `app['credential_resolver']`
      returns the same as `params()`.
- [ ] `params_for(session, app)` with a resolver returning
      `ResolvedCredentials` returns the resolved values mapped to
      `host/port/username/password/database` keys.
- [ ] `params_for(session, app)` swallows resolver exceptions and falls
      back to `params()`.
- [ ] No regressions: `pytest tests/ -x -q` clean.

---

## Test Specification

```python
# tests/datasources/test_pg_params_for.py
import pytest
from unittest.mock import MagicMock
from querysource.auth.credentials import ResolvedCredentials
from querysource.datasources.drivers.pg import pgDriver


@pytest.fixture
def driver():
    d = pgDriver(
        dsn="postgres://u:p@h:5432/db",
        host="h", port=5432, database="db", user="u", password="p",
    )
    return d


class TestParamsFor:
    def test_no_app_falls_back_to_params(self, driver):
        assert driver.params_for(session={"username": "x"}, app=None) == driver.params()

    def test_no_resolver_falls_back(self, driver):
        app = {}  # no 'credential_resolver'
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()

    def test_resolver_none_falls_back(self, driver):
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=None)
        app = {"credential_resolver": resolver}
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()
        resolver.resolve.assert_called_once_with(
            prefix="PG", session={"username": "x"}, credential_profile=None,
        )

    def test_resolver_returns_creds(self, driver):
        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=ResolvedCredentials(
            host="resolved-host", port=6543, user="ru", password="rp",
            database="rd", source="user-override",
        ))
        app = {"credential_resolver": resolver}
        result = driver.params_for(session={"username": "x"}, app=app)
        assert result == {
            "host": "resolved-host", "port": 6543,
            "username": "ru", "password": "rp", "database": "rd",
        }

    def test_resolver_exception_falls_back(self, driver):
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=RuntimeError("boom"))
        app = {"credential_resolver": resolver}
        # Should fall back, not raise:
        assert driver.params_for(session={"username": "x"}, app=app) == driver.params()


class TestCredentialPrefix:
    def test_pg_default(self):
        from querysource.datasources.drivers.pg import pgDriver
        assert pgDriver.credential_prefix == "PG"

    def test_postgres(self):
        from querysource.datasources.drivers.postgres import postgresDriver
        assert postgresDriver.credential_prefix == "PG"

    def test_pg_admin(self):
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        assert pg_adminDriver.credential_prefix == "DB"
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 10) + 6 + 7.
2. Re-read `querysource/datasources/drivers/pg.py` (66 lines) end-to-end.
3. Verify TASK-628 (`CredentialResolver`) and TASK-635 (`pg_admin.py`) are
   in `tasks/done/` before starting.
4. Add the `credential_prefix` class attr to all three classes.
5. Add `params_for` to `pgDriver`. **Do not modify `params()`.**
6. Write the unit tests.
7. Run `pytest tests/ -x -q`.
8. Move task to `done/` and update index.

---

## Completion Note

**Completed by**: Claude (SDD Worker)
**Date**: 2026-04-30
**Notes**: All 10 tests pass. `params()` method unchanged. `credential_prefix` added to all three classes. Resolver exception path wrapped in its own try/except (separate from session extraction) for cleaner fallback semantics.
**`credential_profile` extraction path used**: `session.get('user', {}).get('credential_profile')` with fallback to `session.get('userinfo', {}).get('credential_profile')` — passed as `None` if absent.

**Deviations from spec**: Added extra try/except around `resolver.resolve()` call (separate from session-extraction block) to ensure resolver exceptions also fall back cleanly. Spec said "never raise from params_for" so this is a tightening, not a deviation.
