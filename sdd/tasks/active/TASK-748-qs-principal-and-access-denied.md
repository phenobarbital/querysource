# TASK-748: QSPrincipal identity type and QueryAccessDenied exception

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 1**. Library callers need a public, ai-parrot-independent
identity to pass as `QS(..., principal=...)`. This task adds that type, including the
sessionless-authz form resolved in §8 Q2, plus the denial exception that every later
task raises. Nothing in this task evaluates policies.

---

## Scope

- Create `querysource/auth/principal.py` with the frozen dataclass `QSPrincipal`: validation, `for_authz()`, `is_authz`, `to_userinfo()`, `log_fields()`.
- Add `QueryAccessDenied(QueryException)` to `querysource/exceptions.py`, with code 404 and a generic default message.
- Write `tests/auth/test_principal.py`.

**NOT in scope**: exporting from `querysource/auth/__init__.py` (TASK-750 owns that file); any evaluator, EvalContext or QS change.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/principal.py` | CREATE | `QSPrincipal` dataclass |
| `querysource/exceptions.py` | MODIFY | add `QueryAccessDenied` after `QueryNotFound` |
| `tests/auth/test_principal.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryException   # verified: querysource/exceptions.py:6
```

### Existing Signatures to Use
```python
# querysource/exceptions.py:6
class QueryException(Exception):
    code: int = 0
    def __init__(self, message: str, code: int = 0, **kwargs): ...   # sets .message, .code (int), .stacktrace

# querysource/exceptions.py:52-55 — pattern to mirror
class QueryNotFound(QueryException):
    def __init__(self, message: str = None):
        super().__init__(message, code=404)

# querysource/handlers/abstract.py:382-386 — the handler's synthetic sessionless identity (must match exactly)
authz_userinfo = {
    'username': f'authz:{backend}',
    'groups': ['authorized', backend],
    'roles': [],
}
```

### Does NOT Exist
- ~~`querysource.auth.principal`~~: created by this task.
- ~~`QueryAccessDenied`~~: created by this task.
- ~~`QSPrincipal.org_id` / `QSPrincipal.client_id`~~: dropped by the spec (§1 Non-Goals). Do not add them.
- Do not confuse `QSPrincipal` with ~~`slug_visibility.Principal`~~, which is a different, describe-only, request-derived type (`querysource/auth/slug_visibility.py:36`). Do not import or modify it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/principal.py", "action": "CREATE"},
    {"path": "querysource/exceptions.py", "action": "MODIFY"},
    {"path": "tests/auth/test_principal.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#QueryException",
    "sym:querysource/exceptions.py#QueryNotFound"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Stdlib only (`dataclasses`, `typing`). **No navigator-auth import**, because this module is imported whether or not PBAC is enabled.
- The dataclass is `frozen=True`. Normalize sequences in `__post_init__` with `object.__setattr__`.
- `tenant_id` and `channel` must **never** appear in `to_userinfo()`, per spec §2 item 1 and FEAT-147 L235.
- The authz form's `to_userinfo()` must equal the handler dict byte-for-byte: keys `username`, `groups`, `roles` only, as lists.

---

## Implementation Blueprint

### Steps (in order)
1. Create `querysource/auth/principal.py` from the block below — *why*: it fixes the public type every later task imports.
2. Fill in `__post_init__` validation — *why*: a blank identity must never evaluate as anonymous (spec §2 item 1), and an authz principal must not carry extra privileges (resolved Q2).
3. Add `QueryAccessDenied` after `QueryNotFound` — *why*: the spec fixes code 404 and a message that never names a policy.
4. Write the tests from the Test Specification, then run the Validation Commands.

### `querysource/auth/principal.py` (CREATE)
```python
"""Public caller identity for programmatic (request-less) PBAC enforcement (FEAT-150)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_AUTHZ_GROUP = "authorized"


def _as_tuple(values: Iterable[Any] | None) -> tuple[str, ...]:
    """Normalize a str/sequence/None into a tuple of str (a bare str is one item)."""
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(v) for v in values)


@dataclass(frozen=True)
class QSPrincipal:
    """Identity of the user a library caller acts on behalf of.

    Only identity/claims used by PBAC policies. ``tenant_id`` and ``channel``
    are informational (logs); they never select a store and never enter the
    evaluation userinfo. Raises ValueError when ``user_id`` is empty/blank, or
    when ``authz_backend`` is set and the other claims differ from the
    for_authz() shape. Sequences passed for groups/roles/programs are
    normalized to tuples of str.
    """

    user_id: str
    username: str | None = None
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    superuser: bool = False
    tenant_id: str | None = None
    channel: str = "library"
    authz_backend: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", _as_tuple(self.groups))
        object.__setattr__(self, "roles", _as_tuple(self.roles))
        object.__setattr__(self, "programs", _as_tuple(self.programs))
        # FILL IN: raise ValueError when user_id is None/blank after str().strip() — bounded by spec §2 item 1
        # FILL IN: when authz_backend is set, raise ValueError unless user_id == username == f"authz:{backend}",
        #          groups == ("authorized", backend), roles == programs == () and superuser is False
        #          — bounded by resolved §8 Q2 (no extra privileges on the authz form)

    @classmethod
    def for_authz(
        cls, backend: str, *, tenant_id: str | None = None, channel: str = "library"
    ) -> "QSPrincipal":
        """Sessionless-authz identity identical to the handler's synthetic one.

        Mirrors ``handlers/abstract.py:382-386``: user_id = username =
        ``authz:<backend>``, groups ``("authorized", backend)``, no roles,
        programs or superuser. Raises ValueError on a blank backend.
        """
        # FILL IN: validate backend (str(backend).strip() non-empty) and build cls(...) with the exact shape above
        raise NotImplementedError

    @property
    def is_authz(self) -> bool:
        """True when this is the sessionless-authz form."""
        return self.authz_backend is not None

    def to_userinfo(self) -> dict[str, Any]:
        """Return the navigator-auth userinfo dict the evaluator reads.

        User form: username (username or user_id), user_id, groups, roles,
        programs (lists), superuser (bool). Authz form: exactly
        {'username', 'groups', 'roles'} as the handler builds it.
        tenant_id / channel are never included.
        """
        # FILL IN: two return shapes exactly as documented — bounded by AC-2 and AC-3
        raise NotImplementedError

    def log_fields(self) -> dict[str, Any]:
        """Return {'principal': user_id, 'principal_tenant': tenant_id, 'channel': channel} for logs."""
        return {
            "principal": self.user_id,
            "principal_tenant": self.tenant_id,
            "channel": self.channel,
        }
```
**Why this shape**: The field set, `for_authz` and `to_userinfo` are fixed by spec §3 M1 and §2 item 1. Do not add `org_id`/`client_id`, and do not put `tenant_id` into userinfo. `_AUTHZ_GROUP` exists so the group literal is written once.

### `querysource/exceptions.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'class QueryNotFound(QueryException):' querysource/exceptions.py)
# AFTER — insert below the QueryNotFound class body (anchor `class QueryNotFound(QueryException):`, verified: querysource/exceptions.py:52)


class QueryAccessDenied(QueryException):
    """Principal may not run this query, or the query/tenant is not available to it.

    Message is generic and never names the matched policy; code 404 so HTTP
    layers that surface it keep the same not-found semantics as handlers.
    """

    def __init__(self, message: str = None):
        super().__init__(message or "Query not available.", code=404)
```
**Why**: With a principal, a denial and a missing slug must look identical (resolved at spec time). Code 404 matches the handlers' `web.HTTPNotFound`.

### FILL IN checklist
- [ ] `QSPrincipal.__post_init__`: blank `user_id` → `ValueError`; bounded by spec §2 item 1
- [ ] `QSPrincipal.__post_init__`: authz shape enforcement; bounded by resolved §8 Q2
- [ ] `QSPrincipal.for_authz`: blank backend → `ValueError`; exact handler shape
- [ ] `QSPrincipal.to_userinfo`: two exact shapes; bounded by AC-2 and AC-3

---

## Acceptance Criteria

- [ ] AC-1: `QSPrincipal(user_id="")` and `QSPrincipal(user_id="  ")` raise `ValueError`.
- [ ] AC-2: `QSPrincipal(user_id="35", groups=["a"]).to_userinfo()` has exactly the keys `username, user_id, groups, roles, programs, superuser`. `username` falls back to `user_id`, and there is no `tenant_id` or `channel` key.
- [ ] AC-3: `QSPrincipal.for_authz("authz_useragent").to_userinfo() == {"username": "authz:authz_useragent", "groups": ["authorized", "authz_useragent"], "roles": []}`.
- [ ] AC-4: an `authz_backend` principal with any extra group, role, program or `superuser=True` raises `ValueError`; `for_authz("")` raises `ValueError`.
- [ ] AC-5: `QueryAccessDenied` subclasses `QueryException`, has `code == 404`, and its default message is generic.
- [ ] AC-6: `querysource/auth/principal.py` imports nothing from `navigator_auth`.
- [ ] AC-7: `ruff check querysource/auth/principal.py querysource/exceptions.py tests/auth/test_principal.py` is clean.

---

## Validation Commands

- `pytest tests/auth/test_principal.py -q`

---

## Test Specification

```python
# tests/auth/test_principal.py
import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied, QueryException


class TestQSPrincipal:
    @pytest.mark.parametrize("uid", ["", "   ", None])
    def test_requires_user_id(self, uid): ...

    def test_to_userinfo_shape(self): ...            # AC-2

    def test_normalizes_sequences(self): ...         # lists → tuples of str; a bare str → 1-tuple

    def test_for_authz_shape(self): ...              # AC-3, is_authz True, user form is_authz False

    def test_authz_rejects_extra_claims(self): ...   # AC-4

    def test_log_fields(self): ...


def test_query_access_denied_is_query_exception(): ...   # AC-5
```

---

## Agent Instructions

1. Read the spec (§2 item 1, §3 Module 1).
2. Re-verify the Codebase Contract anchors (`grep -c 'class QueryNotFound(QueryException):' querysource/exceptions.py` → 1).
3. Implement from the blueprint, completing every `# FILL IN:`, without changing a fixed signature or path.
4. Run the Validation Commands and `ruff check`.
5. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
