# TASK-737: Slug visibility service (principal, program pre-filter, ABAC)

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-735, TASK-736
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 3**: the fail-closed authorisation core of the describe API. It answers, for one request:
- who the caller is (a `Principal`);
- which program pre-filter applies (an SQL fragment plus bound args, or deny-all);
- which slugs ABAC allows (`slug:list OR slug:execute` for lists, `slug:describe OR slug:execute` for single slugs);
- what the caller may additionally see (`DescribeGrants`).

**Constraints:**
- It must mirror `AbstractHandler._enforce_pbac`, which it does **not** modify.
- It cannot use `Guardian.filter_resources`, because that requires an authenticated session and so breaks sessionless authz.
- **Dependencies:** it needs TASK-735's config keys and TASK-736's `DescribeGrants` model.
- **Naming:** the store wrapper is `DescribeStore` (spec v0.2), **not** `QueryStore`, which FEAT-147 owns.

---

## Scope

- Create `querysource/auth/slug_visibility.py` with:
  - `PrincipalKind`, `Principal`, `DescribeStore`, `ProgramPredicate`;
  - `normalize_programs`, `resolve_principal`, `legacy_store`, `build_program_predicate`, `is_admin`;
  - `filter_visible`, `can_access`, `describe_grants`;
  - private helpers `_evaluator_state` and `_eval_context`.
- Write `tests/auth/test_slug_visibility.py`.

**NOT in scope**:
- `tenant_store` and tenant evaluator detachment (TASK-743).
- Handler code (TASK-740).
- Modifying `querysource/auth/__init__.py` or `handlers/abstract.py`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/slug_visibility.py` | CREATE | visibility service |
| `tests/auth/test_slug_visibility.py` | CREATE | unit tests (mocked evaluator/session) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from aiohttp import web
from navigator_session import SessionData                             # verified: querysource/handlers/abstract.py:8
from querysource.auth._resource_types import ResourceType             # verified: querysource/auth/_resource_types.py (ResourceType.SLUG, shim fallback)
from querysource.conf import (
    QS_PBAC_ALLOW_SESSIONLESS_AUTHZ,                                   # verified: querysource/conf.py:441
    QS_DESCRIBE_ADMIN_GROUPS,                                          # added by TASK-735
)
from querysource.models import QueryModel                              # verified: querysource/models.py:48
from querysource.queries.describe import DescribeGrants               # added by TASK-736
# Lazy (inside functions only — mirrors handlers/abstract.py:370,410-412):
from navigator_auth.conf import AUTHZ_BACKEND_KEY, AUTH_SESSION_OBJECT   # fallback 'authz_backend' on ImportError (abstract.py:369-372)
from navigator_auth.abac.context import EvalContext                      # verified: handlers/abstract.py:410
from navigator_auth.abac.policies.environment import Environment         # verified: handlers/abstract.py:411
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py:317-449 — the behaviour to mirror (do NOT modify):
guardian = request.app.get('security')          # None → PBAC disabled (allow)                :339-341
# sessionless: if QS_PBAC_ALLOW_SESSIONLESS_AUTHZ and request.get(AUTHZ_BACKEND_KEY):
#   userinfo = {'username': f'authz:{backend}', 'groups': ['authorized', backend], 'roles': []}  :357-380
evaluator = request.app.get('policy_evaluator') # None while guardian set → deny + logger.error  :392-398
userinfo = session.get(AUTH_SESSION_OBJECT, {}) if hasattr(session, 'get') else {}          :413-419
ctx = EvalContext(request=request, user=user, userinfo=userinfo, session=session)            :420-425
result = evaluator.check_access(ctx=ctx, resource_type=..., resource_name=..., action=..., env=Environment())  :431-437
if inspect.iscoroutine(result): result = await result                                        :438-439
result.allowed                                                                               :440

# navigator_auth/abac/policies/evaluator.py
def check_access(self, ctx, resource_type, resource_name: str, action: str, env=None, ...) -> EvaluationResult  # :405
def filter_resources(self, ctx, resource_type, resource_names: List[str], action: str, env=None, org_id=1, client_id=1) -> FilteredResources  # :516
class FilteredResources: allowed: List[str]; denied: List[str]; policies_applied: List[str]  # :74

# navigator_auth/decorators.py:462-492 — userinfo["programs"] membership semantics (reference)
# navigator_auth/conf.py:271 — userinfo key "superuser" (DEFAULT_MAPPING "superuser": "is_superuser")

# querysource/models.py:101-107 — QueryModel.Meta.schema / .name (read only; NEVER assign)
QueryModel.get(query_slug=slug, _connection=conn)   # loader pattern, verified interfaces/connections.py:463

# tests: tests/handlers/test_abstract_pbac_helpers.py — MagicMock request/app, AsyncMock, patch('...QS_PBAC_ALLOW_SESSIONLESS_AUTHZ', True)
```

### Does NOT Exist
- ~~`Guardian.filter_resources` usable for sessionless authz~~: it calls `is_authenticated` (`guardian.py:209-268`). Use `app['policy_evaluator']` directly.
- ~~`querysource.auth.slug_visibility`, `DescribeStore`, `Principal`~~: created by this task.
- ~~`querysource.tenants.QueryStore`~~: FEAT-147, not merged. Do not import it here.
- ~~A program pre-filter anywhere in the codebase~~: none exists.
- ~~`slug:describe_internal` or any admin PBAC action~~: admin is superuser OR `QS_DESCRIBE_ADMIN_GROUPS` only.

---

## Implementation Notes

### Key Constraints (spec §2 principal table — binding)
- **`resolve_principal(request, session)` never raises. Classification order:**
  1. If `session is None`: when `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` is on and `request.get(AUTHZ_BACKEND_KEY)` is set → `AUTHZ` with the synthetic `userinfo`, and `groups=('authorized', backend)`. Otherwise → `NONE`.
  2. Take `userinfo = session.get(AUTH_SESSION_OBJECT, {})`; a non-dict becomes `{}`.
  3. `userinfo.get('superuser') is True` → `SUPERUSER`.
  4. Otherwise, `programs = normalize_programs(userinfo.get('programs'))`. Non-empty → `PROGRAMS`, else → `NO_PROGRAMS`.
  - `groups` are always normalized to lowercase strings from `userinfo.get('groups')`, which may be a list of strings or of dicts with `group_name`/`name`.
- **`normalize_programs`:** accepts `None`, `str`, a list or a tuple. Each item maps to `getattr(item,'slug',None) or getattr(item,'name',None) or (item.get('slug') or item.get('name') if dict) or str(item)`, then lowercase and strip. Drop empties, deduplicate, return a **sorted** tuple.
- **`build_program_predicate(principal, store, param_index=1)`:**
  - `NONE` / `NO_PROGRAMS` → `ProgramPredicate(deny_all=True)`.
  - `SUPERUSER` / `AUTHZ` → `ProgramPredicate()`.
  - `PROGRAMS` and `store.has_program_slug` → `sql='lower("program_slug") = ANY($<param_index>::text[])'`, `args=(sorted(set(programs) | {"default"}),)`.
  - `PROGRAMS` and not `has_program_slug` (tenant) → `deny_all = (store.tenant or "").lower() not in programs`. Used by TASK-743.
- **`_evaluator_state(request)`** returns `(pbac_enabled: bool, evaluator | None)`. `security` absent → `(False, None)`; `security` present but no evaluator → `(True, None)`, plus `logger.error`.
- **`filter_visible`:**
  - PBAC disabled → `list(slugs)`.
  - Enabled with no evaluator → `[]`.
  - Otherwise, one `evaluator.filter_resources(ctx, ResourceType.SLUG, slugs, primary)`, awaited if it is a coroutine. Then, if `fallback_action` is set and some names were denied, a **second** call over the denied remainder only.
  - `allowed = set(first.allowed) | set(second.allowed)`; return `[s for s in slugs if s in allowed]`, which preserves order.
  - Any exception → `logger.error` and `[]` (fail closed).
  - Empty `slugs` → `[]` with no evaluator call.
- **`can_access`:** same semantics via `check_access`, falling back to `fallback_action` only when primary is denied. An exception → `False`.
- **`_eval_context(request, principal)`:**
  - For `AUTHZ`: `EvalContext(request=request, user=None, userinfo=principal.userinfo, session=None)`.
  - Otherwise: `user = userinfo or None`, with `session=principal.session`.
- **`describe_grants`:** `raw = await can_access(request, principal, slug, "slug:describe_raw")` (no fallback); `admin = is_admin(principal)`.
- **`is_admin`:** `principal.kind is PrincipalKind.SUPERUSER`, or `set(principal.groups) & set(QS_DESCRIBE_ADMIN_GROUPS)`.
- **`legacy_store()`:** `DescribeStore(schema=QueryModel.Meta.schema, table=QueryModel.Meta.name, has_program_slug=True, tenant=None, loader=_legacy_loader)`, where `async def _legacy_loader(conn, slug): return await QueryModel.get(query_slug=slug, _connection=conn)`.
- **Import binding.** Import `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` and `QS_DESCRIBE_ADMIN_GROUPS` into the module namespace, so tests patch `querysource.auth.slug_visibility.<NAME>`.

---

## Implementation Blueprint

### Steps (in order)
1. Create the models and `normalize_programs`/`resolve_principal` — *why*: every other function takes a `Principal`.
2. Add `legacy_store`, `build_program_predicate` and `is_admin` — *why*: these are pure and easy to unit test.
3. Add `_evaluator_state`, `_eval_context`, `filter_visible`, `can_access` and `describe_grants` — *why*: the ABAC layer, fail closed.
4. Write the tests and run `pytest tests/auth/test_slug_visibility.py -q` — *why*: AC4–AC7.

### `querysource/auth/slug_visibility.py` (CREATE — part 1)
```python
"""Slug visibility for the describe API (FEAT-148).

Principal resolution, program pre-filter predicates and fail-closed ABAC checks.
Mirrors ``AbstractHandler._enforce_pbac`` (handlers/abstract.py:317) without modifying it.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from aiohttp import web

from ..conf import QS_DESCRIBE_ADMIN_GROUPS, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
from ..models import QueryModel
from ..queries.describe import DescribeGrants
from ._resource_types import ResourceType

logger = logging.getLogger(__name__)


class PrincipalKind(str, Enum):
    """Caller classification (spec §2 principal table)."""

    SUPERUSER = "superuser"
    PROGRAMS = "programs"
    AUTHZ = "authz"
    NO_PROGRAMS = "no_programs"
    NONE = "none"


@dataclass(frozen=True)
class Principal:
    """Resolved caller identity for describe checks."""

    kind: PrincipalKind
    userinfo: dict = field(default_factory=dict)
    groups: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    session: Any = None


@dataclass(frozen=True)
class DescribeStore:
    """Definitions table addressed by the describe API (NOT FEAT-147's QueryStore)."""

    schema: str
    table: str
    has_program_slug: bool = True
    tenant: Optional[str] = None
    loader: Optional[Callable[[Any, str], Awaitable[Any]]] = None


@dataclass(frozen=True)
class ProgramPredicate:
    """SQL pre-filter fragment + bound args, or deny-all."""

    deny_all: bool = False
    sql: str = ""
    args: tuple = ()


def normalize_programs(raw: Any) -> tuple[str, ...]:
    """Program objects/dicts/strings → sorted, lowercase, de-duplicated slugs."""
    # FILL IN: per Implementation Notes
    raise NotImplementedError


async def resolve_principal(request: web.Request, session: Optional[Any]) -> Principal:
    """Classify the caller; never raises (see spec §2 principal table)."""
    # FILL IN: sessionless authz branch (lazy AUTHZ_BACKEND_KEY import w/ 'authz_backend' fallback),
    #          userinfo extraction (lazy AUTH_SESSION_OBJECT), superuser / programs / no_programs — bounded by AC4/AC5
    raise NotImplementedError
```
**Why this shape**: the names and fields are fixed by the spec §2 Data Models, as amended in v0.2. Lazy navigator-auth imports keep this module importable when PBAC is disabled, as in `abstract.py`.

### `querysource/auth/slug_visibility.py` (CREATE — part 2, append)
```python
async def _legacy_loader(conn: Any, slug: str) -> QueryModel:
    """Load a legacy definition on an already-acquired connection (never mutates Meta)."""
    return await QueryModel.get(query_slug=slug, _connection=conn)


def legacy_store() -> DescribeStore:
    """Store over ``QueryModel.Meta.schema``/``.name`` (models.py:101-107)."""
    return DescribeStore(
        schema=QueryModel.Meta.schema, table=QueryModel.Meta.name,
        has_program_slug=True, tenant=None, loader=_legacy_loader,
    )


def build_program_predicate(
    principal: Principal, store: DescribeStore, param_index: int = 1
) -> ProgramPredicate:
    """Program pre-filter predicate (spec §2); bound args only, never interpolated values."""
    # FILL IN: per Implementation Notes — bounded by AC5/AC10
    raise NotImplementedError


def is_admin(principal: Principal) -> bool:
    """Superuser OR a session group in QS_DESCRIBE_ADMIN_GROUPS."""
    # FILL IN
    raise NotImplementedError


def _evaluator_state(request: web.Request) -> tuple[bool, Any]:
    """Return (pbac_enabled, evaluator); logs an error when guardian is set without evaluator."""
    # FILL IN
    raise NotImplementedError


def _eval_context(request: web.Request, principal: Principal) -> Any:
    """EvalContext exactly as _enforce_pbac builds it (abstract.py:405-425)."""
    from navigator_auth.abac.context import EvalContext
    # FILL IN: AUTHZ → user=None, userinfo=synthetic, session=None; else user=userinfo or None
    raise NotImplementedError


async def filter_visible(
    request: web.Request, principal: Principal, slugs: Iterable[str],
    primary_action: str, fallback_action: Optional[str] = None,
) -> list[str]:
    """Order-preserving subset allowed by primary OR fallback; fail-closed; allow-all when PBAC disabled."""
    # FILL IN: bounded by AC6 (fallback only over the denied remainder; exceptions → [])
    raise NotImplementedError


async def can_access(
    request: web.Request, principal: Principal, slug: str,
    primary_action: str, fallback_action: Optional[str] = None,
) -> bool:
    """Non-raising single-slug check; same semantics as filter_visible."""
    # FILL IN: check_access with Environment(); iscoroutine guard; exceptions → False — bounded by AC6
    raise NotImplementedError


async def describe_grants(request: web.Request, principal: Principal, slug: str) -> DescribeGrants:
    """raw = slug:describe_raw (no fallback, raw_query:execute never implies it); admin = is_admin."""
    return DescribeGrants(
        raw=await can_access(request, principal, slug, "slug:describe_raw"),
        admin=is_admin(principal),
    )
```
**Why**: fail closed everywhere (spec §2 ABAC rules). The fallback runs only on denied names, which halves evaluator cost in the common case.

### `tests/auth/test_slug_visibility.py` (CREATE)
```python
"""FEAT-148 TASK-737 — slug visibility service."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.auth import slug_visibility as sv


def _request(app: dict | None = None, extra: dict | None = None):
    req = MagicMock()
    req.app = app or {}
    data = dict(extra or {})
    req.get.side_effect = data.get
    return req


def _session(userinfo: dict):
    s = MagicMock()
    s.get.side_effect = lambda k, d=None: userinfo if k == "session" else d
    return s


def test_normalize_programs(): ...                          # FILL IN: objects .slug/.name, dicts, mixed case, dupes, empties
async def test_resolve_principal_kinds(): ...               # FILL IN: superuser/programs/no_programs/authz(flag+stamp)/none
def test_program_predicate_legacy_and_tenant(): ...         # FILL IN: sql fragment, args include 'default', param_index=2, deny_all
def test_is_admin_groups_config(): ...                      # FILL IN: patch sv.QS_DESCRIBE_ADMIN_GROUPS
async def test_filter_visible_fallback_only_on_remainder(): ...  # FILL IN: evaluator.filter_resources call args; order kept
async def test_filter_visible_fail_closed_on_error(): ...   # FILL IN
async def test_filter_visible_pbac_disabled_allows_all(): ...
async def test_guardian_without_evaluator_denies(): ...
async def test_can_access_fallback_and_coroutine_result(): ...
async def test_describe_grants_raw_not_implied_by_raw_query_execute(): ...
def test_legacy_store_never_mutates_meta(): ...             # FILL IN: Meta.schema/name unchanged, has_program_slug True
```

### FILL IN checklist
- [ ] `normalize_programs`, `resolve_principal`: bounded by AC4/AC5.
- [ ] `build_program_predicate`: bounded by AC5/AC10.
- [ ] `is_admin`, `_evaluator_state`, `_eval_context`.
- [ ] `filter_visible`, `can_access`: bounded by AC6.
- [ ] All test bodies.

---

## Acceptance Criteria

- [ ] `pytest tests/auth/test_slug_visibility.py -q` passes.
- [ ] The principal table (spec §2) is implemented exactly, including sessionless authz only when the flag is on **and** the request is stamped.
- [ ] Program predicates use bound args only and include `default`; `NO_PROGRAMS` is deny-all (AC5).
- [ ] ABAC fallback runs only over the denied remainder; evaluator errors deny; PBAC disabled allows (AC6).
- [ ] `raw_query:execute` never grants `raw`.
- [ ] `QueryModel.Meta` is never assigned.
- [ ] `ruff check querysource/auth/slug_visibility.py tests/auth/test_slug_visibility.py` is clean.

---

## Test Specification

See the blueprint test file above. `tests/auth/` already exists (`tests/auth/test_pbac_bootstrap.py`).

---

## Agent Instructions

1. **Read the spec** (§2 principal & ABAC rules, §3 Module 3, §5 AC4–AC7).
2. **Check dependencies**: TASK-735 (config keys) and TASK-736 (`DescribeGrants`) must be in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract**: re-read `handlers/abstract.py:317-449`.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
