# TASK-750: Request-optional PBAC evaluation core

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-748, TASK-749
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 3**, the security-critical heart of FEAT-150. It is
the single implementation of evaluator lookup, detaching, EvalContext
construction, evaluation and fail-closed handling. Handlers (TASK-751),
describe visibility (TASK-752), QS (TASK-753) and MultiQS (TASK-754) all
delegate to it. It also exports the new public names from `querysource.auth`.

---

## Scope

- Create `querysource/auth/enforcement.py` with `AccessDecision`, `_ServiceRequest`, `resolve_evaluator`, `build_eval_context`, `evaluate` and `enforce_principal`.
- Export `QSPrincipal` and `get_pbac_runtime` from `querysource/auth/__init__.py`.
- Write `tests/auth/test_enforcement.py`, including the real-evaluator contract test, which is skipped when the Rust engine is not installed.

**NOT in scope**: changing handlers, `slug_visibility`, QS or MultiQS (TASK-751 to TASK-754).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/enforcement.py` | CREATE | evaluation core |
| `querysource/auth/__init__.py` | MODIFY | export `QSPrincipal`, `get_pbac_runtime` |
| `tests/auth/test_enforcement.py` | CREATE | unit + contract tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.principal import QSPrincipal                       # created by TASK-748
from querysource.auth.pbac import get_pbac_runtime                       # created by TASK-749
from querysource.exceptions import QueryAccessDenied                     # created by TASK-748
from querysource.conf import QS_PBAC_ENABLED, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ   # verified: querysource/conf.py:441,453
from querysource.auth._resource_types import ResourceType                # verified: querysource/auth/_resource_types.py (SLUG, RAW_QUERY)
# lazy only, inside functions (navigator-auth 0.26.0):
from navigator_auth.abac.context import EvalContext
from navigator_auth.abac.policies.environment import Environment
from multidict import CIMultiDict, CIMultiDictProxy                      # transitive via aiohttp
from yarl import URL                                                     # transitive via aiohttp
```

### Existing Signatures to Use
```python
# querysource/auth/__init__.py:17-21
from querysource.auth.credentials import CredentialResolver, ResolvedCredentials  # noqa: E402
from querysource.auth.pbac import setup_pbac  # noqa: E402
from querysource.auth._resource_types import ResourceType  # noqa: E402
__all__ = ("CredentialResolver", "ResolvedCredentials", "setup_pbac", "ResourceType", "logger")   # :21

# querysource/auth/slug_visibility.py:227-257 — _evaluator_state: the logic to generalize
#   guardian = request.app.get('security'); None → (False, None)
#   evaluator = request.app.get('policy_evaluator'); None → logger.error(...) and (True, None)
#   detached → copy.copy(evaluator); ._cache = {}; ._stats = dict(getattr(evaluator, "_stats", {}))

# querysource/handlers/abstract.py:443-451 — evaluation call shape
result = evaluator.check_access(ctx=ctx, resource_type=resource_type, resource_name=resource_name,
                                action=action, env=Environment())
if inspect.iscoroutine(result):
    result = await result
# result.allowed, getattr(result, 'matched_policy', None), getattr(result, 'reason', None)

# navigator-auth 0.26.0
EvalContext.__init__(self, request, user, userinfo, session, *args, org_id=None, client_id=None, **kwargs)
  # reads request.remote, .method, .headers.get('referer'), .path_qs, .path, .headers, .rel_url;
  # request.is_authenticated inside try/except AttributeError; _resolve_tenant(request, userinfo, ...) guards request.headers
PolicyEvaluator.check_access(ctx, resource_type, resource_name, action, env=None, owner_reports_to=None,
                             org_id=1, client_id=1)
  # reads only ctx.userinfo / ctx.user
```

### Does NOT Exist
- ~~`EvalContext.from_userinfo`~~ in navigator-auth 0.26.0. It is planned upstream (spec §2 contract), so **feature-detect it with `hasattr(EvalContext, "from_userinfo")`** and never call it unconditionally.
- ~~`check_access(..., bypass_cache=...)`~~: detaching is the only isolation mechanism.
- ~~`querysource.auth.enforcement`~~: created here.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/enforcement.py", "action": "CREATE"},
    {"path": "querysource/auth/__init__.py", "action": "MODIFY"},
    {"path": "tests/auth/test_enforcement.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/slug_visibility.py#_evaluator_state",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **No top-level navigator-auth import** in `enforcement.py`. Import inside `build_eval_context`/`evaluate` (spec AC: no new module-level navigator-auth import; `_resource_types.py`'s existing one is pre-existing).
- **Fail-closed everywhere PBAC is on**: missing evaluator, evaluator exception or empty `resource_name` means deny.
- **"PBAC off" means no runtime or no guardian.** The principal path allows with a debug log, and warns **once per process** when `QS_PBAC_ENABLED` is true (resolved at spec time).
- **Authz principal** (`principal.is_authz`): when PBAC is on and `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` is false, raise `QueryAccessDenied` without evaluating. When the flag is on, evaluate with `user=None` and `userinfo=principal.to_userinfo()`, exactly as `handlers/abstract.py:418-421` does.
- A user principal evaluates with `user=userinfo`, `userinfo=userinfo`, `session=None` (handler parity, `abstract.py:423-429`).
- `enforce_principal` **always** uses `detached=True`.
- Logging goes through `logging.getLogger(__name__)`. A deny logs at info with `principal.log_fields()`, `tenant`, resource, action, matched policy and reason. The `QueryAccessDenied` message stays generic.

---

## Implementation Blueprint

### Steps (in order)
1. Create `querysource/auth/enforcement.py` from the two blocks below — *why*: names and signatures are fixed by spec §3 M3, and TASK-751 to TASK-754 import them verbatim.
2. Complete the `FILL IN`s following the Key Constraints — *why*: they are the fail-closed/no-op matrix of spec §2's decision table.
3. Add the two exports to `querysource/auth/__init__.py` — *why*: `from querysource.auth import QSPrincipal, get_pbac_runtime` is the public surface (spec §2 New Public Interfaces).
4. Write the tests, including `test_real_evaluator_contract` gated like `tests/policies/test_authorized_policy.py:17-26`.

### `querysource/auth/enforcement.py` (CREATE) — part 1
```python
"""Request-optional PBAC evaluation core shared by handlers, describe and QS (FEAT-150)."""
from __future__ import annotations

import copy
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from querysource.auth.pbac import get_pbac_runtime
from querysource.auth.principal import QSPrincipal
from querysource.conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ, QS_PBAC_ENABLED
from querysource.exceptions import QueryAccessDenied

_log = logging.getLogger(__name__)
_WARNED_ABSENT_RUNTIME = False


@dataclass(frozen=True)
class AccessDecision:
    """Outcome of one evaluation. pbac_enabled=False means 'no PBAC configured' (allowed=True)."""

    allowed: bool
    pbac_enabled: bool
    matched_policy: str | None = None
    reason: str | None = None


class _ServiceRequest:
    """Neutral stand-in satisfying EvalContext.__init__ for request-less evaluation.

    Carries no caller-controlled data; navigator-auth 0.26.0 check_access never reads it.
    """

    def __init__(self) -> None:
        from multidict import CIMultiDict, CIMultiDictProxy
        from yarl import URL

        self.remote = None
        self.method = "INTERNAL"
        self.headers = CIMultiDictProxy(CIMultiDict())
        self.path = ""
        self.path_qs = ""
        self.rel_url = URL("")

    def get(self, key: str, default: Any = None) -> Any:
        """Mapping-style access used by request.get(...) callers; always the default."""
        return default


def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]:
    """Return (pbac_enabled, evaluator) from request.app or the process runtime.

    Guardian without evaluator → (True, None) plus an error log. detached=True
    returns a shallow copy with a fresh _cache and copied _stats; the original
    evaluator is never mutated.
    """
    # FILL IN: request → request.app.get('security'/'policy_evaluator'); None → get_pbac_runtime()
    #          (None runtime or None guardian → (False, None)) — mirror slug_visibility.py:239-257 exactly
    # FILL IN: detached copy exactly as slug_visibility.py:250-255
    raise NotImplementedError


def build_eval_context(*, userinfo: dict, user: Any, session: Any,
                       request: web.Request | None = None) -> Any:
    """Build a navigator-auth EvalContext.

    request given → EvalContext(request=request, user=, userinfo=, session=) (unchanged handler path).
    request None → EvalContext.from_userinfo(userinfo, user=, session=) when navigator-auth provides
    it, else EvalContext(request=_ServiceRequest(), ...).
    """
    from navigator_auth.abac.context import EvalContext

    # FILL IN: three branches as documented; feature-detect with hasattr(EvalContext, "from_userinfo")
    raise NotImplementedError
```

### `querysource/auth/enforcement.py` (CREATE) — part 2 (append)
```python
async def evaluate(evaluator: Any, ctx: Any, resource_type: Any, resource_name: str,
                   action: str) -> AccessDecision:
    """Run check_access with the iscoroutine guard; any exception or empty name → deny."""
    from navigator_auth.abac.policies.environment import Environment

    if not resource_name:
        return AccessDecision(allowed=False, pbac_enabled=True, reason="missing resource_name")
    try:
        result = evaluator.check_access(
            ctx=ctx,
            resource_type=resource_type,
            resource_name=resource_name,
            action=action,
            env=Environment(),
        )
        if inspect.iscoroutine(result):
            result = await result
    except Exception:  # pylint: disable=W0703
        _log.exception("PBAC evaluator error (fail-closed): %s/%s action=%s",
                         resource_type, resource_name, action)
        return AccessDecision(allowed=False, pbac_enabled=True, reason="evaluator error")
    return AccessDecision(
        allowed=bool(result.allowed),
        pbac_enabled=True,
        matched_policy=getattr(result, "matched_policy", None),
        reason=getattr(result, "reason", None),
    )


async def enforce_principal(principal: QSPrincipal, resource_type: Any, resource_name: str,
                            action: str, *, tenant: str | None = None,
                            logger: logging.Logger | None = None) -> AccessDecision:
    """Principal-path gate (always detached).

    PBAC off → allowed (debug log; one warning per process when QS_PBAC_ENABLED).
    Evaluator missing → QueryAccessDenied. Authz-form principal with PBAC active and
    QS_PBAC_ALLOW_SESSIONLESS_AUTHZ false → QueryAccessDenied; flag on → evaluated with
    user=None. Deny → QueryAccessDenied. Logs principal.log_fields(), tenant selector,
    resource, action, decision, matched policy and reason.
    """
    log = logger or _log
    pbac_enabled, evaluator = resolve_evaluator(None, detached=True)
    # FILL IN: PBAC-off branch incl. the once-per-process warning via _WARNED_ABSENT_RUNTIME — bounded by AC-3
    # FILL IN: evaluator None → log + raise QueryAccessDenied() — bounded by AC-4
    # FILL IN: authz flag gate, then userinfo/user selection per Key Constraints — bounded by AC-6
    # FILL IN: ctx = build_eval_context(..., request=None); decision = await evaluate(...);
    #          log outcome with **principal.log_fields(); deny → raise QueryAccessDenied() (generic message)
    raise NotImplementedError
```
**Why this shape**: `evaluate` is complete because its contract (the `iscoroutine` guard, fail-closed, result attributes) is copied from `abstract.py:443-461` and must not drift. The branching in `enforce_principal` is left as `FILL IN` because it encodes the spec's decision matrix, and the tests pin it.

### `querysource/auth/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from querysource.auth._resource_types import ResourceType  # noqa: E402' querysource/auth/__init__.py)
# AFTER — insert below that line (verified: querysource/auth/__init__.py:19)
from querysource.auth.principal import QSPrincipal  # noqa: E402
from querysource.auth.pbac import get_pbac_runtime  # noqa: E402

# occurrences: 1 (verified: grep -c '__all__ = (' querysource/auth/__init__.py)
# REPLACE the __all__ line (verified: querysource/auth/__init__.py:21) with:
__all__ = (
    "CredentialResolver", "ResolvedCredentials", "setup_pbac", "ResourceType",
    "QSPrincipal", "get_pbac_runtime", "logger",
)
```
**Why**: Only these two names are public (spec §2). `enforcement` stays an internal module and is imported by path, so importing `querysource.auth` never pulls navigator-auth.

### FILL IN checklist
- [ ] `resolve_evaluator`: request vs runtime lookup and detached copy; mirror `slug_visibility.py:239-257`
- [ ] `build_eval_context`: three branches; `hasattr` feature detection
- [ ] `enforce_principal`: PBAC-off/once-warning, missing evaluator, authz flag, evaluate + log + raise; AC-3 to AC-7
- [ ] Tests incl. `test_real_evaluator_contract` (skip unless `_RS_PEP_AVAILABLE`)

---

## Acceptance Criteria

- [ ] AC-1: `resolve_evaluator(request, detached=False)` returns the app evaluator. `resolve_evaluator(None, ...)` reads `get_pbac_runtime()`. A guardian without an evaluator returns `(True, None)`.
- [ ] AC-2: with `detached=True`, the returned evaluator is a different object with an empty `_cache` and a copied `_stats`, and the original's `_cache` and `_stats` are unchanged.
- [ ] AC-3: with no runtime, `enforce_principal` returns `AccessDecision(allowed=True, pbac_enabled=False)`. With `QS_PBAC_ENABLED` patched true, exactly **one** warning is logged across two calls.
- [ ] AC-4: a runtime whose evaluator is `None` (guardian set) → `QueryAccessDenied`.
- [ ] AC-5: an evaluator raising, or returning an unresolved coroutine that resolves to deny, → deny/`QueryAccessDenied`. An allow result → a returned decision with `allowed=True`.
- [ ] AC-6: an authz principal with the flag off → `QueryAccessDenied` and `check_access` **not called**. With the flag on → `check_access` is called with `ctx.user is None` and `ctx.userinfo == principal.to_userinfo()`.
- [ ] AC-7: `build_eval_context(request=None, ...)` builds a real `EvalContext` via `_ServiceRequest` on 0.26.0. When `EvalContext.from_userinfo` is monkeypatched in, it is called instead and `_ServiceRequest` is not instantiated.
- [ ] AC-8: `test_real_evaluator_contract` (skipped without the Rust engine): with a policy allowing group `sales` on `slug:report_a`, a request-less context with groups `["sales"]` is allowed on `report_a` and denied on `report_b`.
- [ ] AC-9: no new module-level navigator-auth import: `grep -nE '^(from|import) navigator_auth' querysource/auth/enforcement.py querysource/auth/principal.py` prints nothing. (`querysource/auth/_resource_types.py` already imports `navigator_auth.abac.policies.resources` at module load. That is pre-existing and out of scope.)
- [ ] AC-10: `ruff check querysource/auth/enforcement.py querysource/auth/__init__.py tests/auth/test_enforcement.py` is clean.

---

## Validation Commands

- `pytest tests/auth/test_enforcement.py -q`
- `pytest tests/auth/test_slug_visibility.py -q`

---

## Test Specification

```python
# tests/auth/test_enforcement.py
import pytest
from unittest.mock import MagicMock

from querysource.auth import enforcement
from querysource.auth.enforcement import (
    AccessDecision, build_eval_context, enforce_principal, evaluate, resolve_evaluator,
)
from querysource.auth.pbac import _set_pbac_runtime, clear_pbac_runtime
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    clear_pbac_runtime()
    monkeypatch.setattr(enforcement, "_WARNED_ABSENT_RUNTIME", False)
    yield
    clear_pbac_runtime()


def test_resolve_evaluator_request_and_runtime(): ...          # AC-1
def test_resolve_evaluator_detached_copy(): ...                 # AC-2
async def test_enforce_principal_pbac_off_and_warn_once(monkeypatch, caplog): ...   # AC-3
async def test_enforce_principal_missing_evaluator(): ...       # AC-4
async def test_evaluate_exception_is_deny(): ...                # AC-5
async def test_evaluate_awaits_coroutine(): ...                 # AC-5
async def test_enforce_principal_authz_flag(monkeypatch): ...   # AC-6
def test_build_eval_context_without_request(): ...              # AC-7
def test_build_eval_context_prefers_from_userinfo(monkeypatch): ...  # AC-7
def test_real_evaluator_contract(tmp_path): ...                 # AC-8 (skipif no _RS_PEP_AVAILABLE)
```

---

## Agent Instructions

1. Confirm TASK-748 and TASK-749 are in `sdd/tasks/completed/` and that `QSPrincipal`, `QueryAccessDenied` and `get_pbac_runtime` import.
2. Re-verify the anchors (`grep -c` in the blueprint).
3. Implement, test and lint. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

Created `querysource/auth/enforcement.py` with `AccessDecision`,
`_ServiceRequest`, `resolve_evaluator`, `build_eval_context`, `evaluate` and
`enforce_principal`, filling in every `FILL IN` per the decision matrix:
`resolve_evaluator` mirrors `slug_visibility._evaluator_state` for both the
request and request-less (runtime) paths, including the detached-copy
semantics; `build_eval_context` feature-detects `EvalContext.from_userinfo`
(absent on the installed navigator-auth 0.26.0) and falls back to
`_ServiceRequest`; `enforce_principal` implements PBAC-off (allow +
once-per-process warning when `QS_PBAC_ENABLED`), missing-evaluator deny,
authz-flag gating (`QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`), and deny→
`QueryAccessDenied` with full decision logging via `principal.log_fields()`.
Exported `QSPrincipal`/`get_pbac_runtime` from `querysource/auth/__init__.py`.

Wrote `tests/auth/test_enforcement.py` (11 tests incl.
`test_real_evaluator_contract`, gated on `_RS_PEP_AVAILABLE` like
`tests/policies/test_authorized_policy.py:17` — the Rust engine IS
installed here, so it ran for real, not skipped). Had to build the Cython
extensions in this worktree (`make build-inplace`; artifacts are gitignored,
not committed) because `querysource/models.py` (imported by
`slug_visibility.py`) requires the compiled `utils/functions` extension,
which a fresh worktree checkout does not carry. All 55 `tests/auth/` tests
pass (including TASK-748/749's and the pre-existing
`test_pbac_bootstrap.py`/`test_slug_visibility.py` suites, unmodified).
`ruff check` clean on all three files. Verified AC-9: no new module-level
`navigator_auth` import in `enforcement.py`/`principal.py`.

**Completed by**: sdd-worker (Sonnet)
**Date**: 2026-09-24
