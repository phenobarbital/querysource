# TASK-902: Semantic governance — PBAC checks, principal, user_attributes, row filters

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-897, TASK-899
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5, §2 S1 (datasource governance). Implements the PBAC side of the feature using
only `querysource.auth.enforcement` primitives: `semantic:execute` on the slug, `semantic:use` per
field, `datasource:use`/`driver:use` on the resolved datasource, the session→`QSPrincipal` mapping,
`user_attributes` and role-keyed `governance.row_filters`. All layers are conjunctive (S8).

---

## Scope

- Implement `principal_from_session`, `user_attributes`, `check`, `enforce_query`, `enforce_datasource`, `governance_filters` exactly as spec §3 M5.
- Tests `tests/semantic/test_governance.py`.

**NOT in scope**: catalog per-field annotation (TASK-906 calls `check()`), filter validation (TASK-900).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/governance.py` | CREATE | governance functions |
| `tests/semantic/test_governance.py` | CREATE | governance tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.enforcement import (AccessDecision, build_eval_context, evaluate,
                                          resolve_evaluator)   # verified: enforcement.py:39,70,103,122
from querysource.auth.principal import QSPrincipal             # verified: principal.py:21
from querysource.auth._resource_types import ResourceType      # verified: _resource_types.py (SEMANTIC added by TASK-899)
from querysource.exceptions import QueryAccessDenied           # verified: exceptions.py:63
from querysource.semantic.exceptions import SemanticFieldDenied      # TASK-897
from querysource.semantic.models import SemanticFilter, SemanticQuery, SemanticResource  # TASK-897
```

### Existing Signatures to Use
```python
# querysource/auth/enforcement.py
class AccessDecision: allowed: bool; pbac_enabled: bool; matched_policy: str | None; reason: str | None  # :39
def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]       # :70
def build_eval_context(*, userinfo: dict, user: Any, session: Any, request: web.Request | None = None) -> Any  # :103
async def evaluate(evaluator, ctx, resource_type, resource_name: str, action: str) -> AccessDecision  # :122 — exceptions/empty name → deny

# querysource/auth/principal.py:21 (frozen dataclass-like)
class QSPrincipal:
    user_id: str; username: str | None; groups: tuple; roles: tuple; programs: tuple
    superuser: bool; tenant_id: str | None; channel: str = "library"; authz_backend: str | None
    def to_userinfo(self) -> dict[str, Any]   # :97 — never includes tenant_id/channel

# querysource/auth/slug_visibility.py:129-133 — session key: navigator_auth.conf.AUTH_SESSION_OBJECT (fallback 'user');
#   userinfo = session.get(AUTH_SESSION_OBJECT, {}) if hasattr(session, 'get') else {}

# querysource/exceptions.py:63
class QueryAccessDenied(QueryException): def __init__(self, message: str = None)   # code 404
```

### Does NOT Exist
- ~~`policy.allowed_metrics` / `policy.row_filters`~~ — one (resource_type, name, action) evaluation at a time.
- ~~`ToolPermissionError`~~ — use `QueryAccessDenied` / `SemanticFieldDenied`.
- ~~Calling `navigator_auth` directly~~ — only through `querysource.auth.enforcement`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/governance.py", "action": "CREATE"},
    {"path": "tests/semantic/test_governance.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/enforcement.py#resolve_evaluator",
    "sym:querysource/auth/enforcement.py#build_eval_context",
    "sym:querysource/auth/enforcement.py#evaluate",
    "sym:querysource/auth/enforcement.py#AccessDecision",
    "sym:querysource/auth/principal.py#QSPrincipal",
    "sym:querysource/exceptions.py#QueryAccessDenied"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- PBAC disabled (`resolve_evaluator` → `(False, _)`) → `AccessDecision(allowed=True, pbac_enabled=False)`.
- No principal and PBAC on → deny (fail-closed).
- Field resource names: `f"{slug}:metric:{name}"` / `f"{slug}:dimension:{name}"`.
- `principal_from_session` sets `channel='http'`; skip (return None) when `user_id` is missing/blank (QSPrincipal raises ValueError on blank).
- `governance_filters`: `default` first, then each role key in `principal.roles` in YAML order; returns `(filters, matched_roles)`.

---

## Implementation Blueprint

### Steps (in order)
1. Implement `principal_from_session` + `user_attributes` — *why*: pure functions used by both paths (AC-11).
2. Implement `check`, then `enforce_query` and `enforce_datasource` on top of it — *why*: one evaluation per (resource, action).
3. Implement `governance_filters`; tests with `fake_evaluator` (conftest, TASK-897).

### `querysource/semantic/governance.py` (CREATE)
```python
"""Semantic governance (FEAT-175 M5): PBAC on slug/fields/datasource, identity, row filters."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..auth._resource_types import ResourceType
from ..auth.enforcement import AccessDecision, build_eval_context, evaluate, resolve_evaluator
from ..auth.principal import QSPrincipal
from ..exceptions import QueryAccessDenied
from .exceptions import SemanticFieldDenied
from .models import SemanticFilter, SemanticQuery, SemanticResource


def principal_from_session(session: Any | None) -> QSPrincipal | None:
    """session['user'] (AUTH_SESSION_OBJECT) → QSPrincipal(channel='http'); None when absent."""
    # FILL IN: same key lookup as slug_visibility.resolve_principal (:129-133); map user_id/username/groups/
    # roles/programs/superuser; normalize missing to () — bounded by AC-11


def user_attributes(principal: QSPrincipal | None) -> dict[str, Any] | None:
    """principal.to_userinfo() plus 'tenant_id'; None without a principal."""
    if principal is None:
        return None
    return {**principal.to_userinfo(), "tenant_id": principal.tenant_id}


async def check(request: web.Request | None, principal: QSPrincipal | None, resource_name: str,
                action: str, *, logger: logging.Logger,
                resource_type: Any = ResourceType.SEMANTIC) -> AccessDecision:
    """One PBAC evaluation; PBAC off → allowed; no principal with PBAC on → denied."""
    enabled, evaluator = resolve_evaluator(request, detached=request is None)
    if not enabled:
        return AccessDecision(allowed=True, pbac_enabled=False)
    # FILL IN: principal None → AccessDecision(allowed=False, pbac_enabled=True, reason="no principal");
    # ctx = build_eval_context(userinfo=principal.to_userinfo(), user=principal.to_userinfo(), session=None,
    # request=request); return await evaluate(evaluator, ctx, resource_type, resource_name, action);
    # logger.debug the decision


async def enforce_query(request: web.Request | None, principal: QSPrincipal | None, slug: str,
                        query: SemanticQuery, *, logger: logging.Logger) -> None:
    """semantic:execute on slug (deny → QueryAccessDenied), then semantic:use per field (→ SemanticFieldDenied)."""
    # FILL IN — bounded by AC-10


async def enforce_datasource(request: web.Request | None, principal: QSPrincipal | None,
                             datasource: str, driver: str, *, logger: logging.Logger) -> None:
    """datasource:use on ResourceType.DATASOURCE and driver:use on ResourceType.DRIVER; deny → QueryAccessDenied."""
    # FILL IN — bounded by AC-21 (same strings as handlers/service.py:358-369)


def governance_filters(resource: SemanticResource, principal: QSPrincipal | None
                       ) -> tuple[list[SemanticFilter | str], list[str]]:
    """'default' + blocks whose key ∈ principal.roles; AND semantics; unknown roles ignored."""
    # FILL IN — bounded by AC-12
```
**Why**: signatures fixed by spec §3 M5; `check()` gains a keyword `resource_type` so
`enforce_datasource` reuses it for DATASOURCE/DRIVER.

### FILL IN checklist
- [ ] `principal_from_session` — AC-11
- [ ] `check` — PBAC on path; FILL IN verify `AccessDecision` constructor field names at enforcement.py:39
- [ ] `enforce_query` / `enforce_datasource` — AC-10 / AC-21
- [ ] `governance_filters` — AC-12
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] PBAC off → every `check()` allowed (AC-10).
- [ ] Denied `epson.sales:metric:margin` → `SemanticFieldDenied` with `.field == 'margin'`, code 403.
- [ ] Denied slug or datasource → `QueryAccessDenied` (AC-10, AC-21).
- [ ] Roles `['analyst']` → default + analyst filters; no principal → default only.
- [ ] `principal_from_session({'user': {...}})` keys match `QSPrincipal.to_userinfo()`.

---

## Validation Commands
- `pytest tests/semantic/test_governance.py -q`

---

## Test Specification

```python
# tests/semantic/test_governance.py
import pytest
from querysource.auth.principal import QSPrincipal
from querysource.semantic import governance
from querysource.semantic.models import SemanticQuery
from querysource.semantic.exceptions import SemanticFieldDenied


async def test_governance_pbac_off_allows_all(monkeypatch):
    monkeypatch.setattr(governance, "resolve_evaluator", lambda request, detached: (False, None))
    assert (await governance.check(None, None, "s", "semantic:execute",
                                   logger=governance.logging.getLogger("t"))).allowed


async def test_governance_field_denied_names_field(fake_evaluator):
    # FILL IN: deny "epson.sales:metric:margin"; enforce_query(...metrics=['margin']) → SemanticFieldDenied('margin')


def test_governance_row_filters_roles_and_default(inline_resource_yaml):
    # FILL IN


def test_principal_from_session_shape():
    p = governance.principal_from_session({"user": {"user_id": "7", "username": "a", "groups": ["g"]}})
    assert set(p.to_userinfo()) >= {"username", "user_id", "groups", "roles"}
```

---

## Agent Instructions

1. Work in the feature worktree; TASK-897, TASK-899 done.
2. Implement; run validation; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-902 semantic-layer verified`.

---

## Completion Note

**Completed by**: sdd-worker (coder seat gpt-5.6-luna, codex, 1 attempt, 44s)
**Date**: 2026-10-15
**Notes**: Merge-tier validation green (24 passed), no fixes. Review recorded as coder-review:74003004ab315f67afb2b9e5. Closed via close_task.sh (finalize_task needs a durable review EvidenceRef no available tool produces).

**Deviations from spec**: none
