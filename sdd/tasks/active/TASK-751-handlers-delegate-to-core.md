# TASK-751: Handlers delegate PBAC evaluation to the shared core

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-750
**Assigned-to**: unassigned

---

## Context

Implements the handler half of spec §3 **Module 4** (resolved: "extract the core;
handlers and slug_visibility delegate"). `_enforce_pbac` and `_enforce_owned_slug`
each carry their own evaluator lookup, detached copy, EvalContext construction and
`check_access` call. This task replaces those blocks with calls to
`querysource.auth.enforcement`. **HTTP behaviour must not change at all**: 404 on
deny, fail-closed without a session or evaluator, the sessionless-authz synthetic
identity, and the missing-name short-circuit. The existing suites are the behaviour pin.

---

## Scope

- In `AbstractHandler._enforce_pbac` (`handlers/abstract.py:323`):
  - Replace the evaluator lookup (`:400-406`) with `resolve_evaluator(request, detached=False)`.
  - Replace the `EvalContext(...)` construction (`:431-436`) with `build_eval_context(..., request=request)`.
  - Replace `evaluator.check_access` plus the `iscoroutine` guard (`:443-451`) with `await evaluate(...)`.
  - Map `decision.allowed is False` to the existing info log and `raise web.HTTPNotFound()`.
- Make the same three replacements in `_enforce_owned_slug` (`:463`), using `resolve_evaluator(request, detached=True)` (replacing the lookup + copy at `:484-493`), with `ctx` at `:543` and `check_access` at `:551`.
- Keep in place, unchanged: `_get_user_session`, the sessionless-authz branches, the missing-name short-circuit, `userinfo`/`user` selection and all log messages.

**NOT in scope**: `slug_visibility.py` (TASK-752); `handlers/multi.py` `guardian.filter_resources` calls; any new behaviour.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/abstract.py` | MODIFY | `_enforce_pbac` / `_enforce_owned_slug` delegate to core |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.enforcement import build_eval_context, evaluate, resolve_evaluator   # created by TASK-750
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py
async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None  # :323
    # :346-348  guardian = request.app.get('security'); if guardian is None: return
    # :359-365  if not resource_name: log + raise web.HTTPNotFound()
    # :367-403  session / sessionless authz → authz_userinfo, else raise web.HTTPNotFound()
    # :405-411  evaluator = request.app.get('policy_evaluator'); None → logger.error + raise web.HTTPNotFound()
    # :418-429  userinfo / user selection
    # :431-436  ctx = EvalContext(request=request, user=user, userinfo=userinfo, session=session)
    # :443-451  result = evaluator.check_access(...); iscoroutine guard
    # :452-461  if not result.allowed: logger.info("PBAC denied: ...", ..., matched_policy, reason); raise web.HTTPNotFound()
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None   # :463
    # :484-488  evaluator lookup / security check (order: evaluator get, security None → return, evaluator None → 404)
    # :491-493  detached = copy.copy(evaluator); detached._cache = {}; detached._stats = dict(evaluator._stats)
    # :543      ctx = EvalContext(...)
    # :551      result = detached.check_access(...)

# querysource/auth/enforcement.py (TASK-750)
def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]
def build_eval_context(*, userinfo: dict, user: Any, session: Any, request: web.Request | None = None) -> Any
async def evaluate(evaluator, ctx, resource_type, resource_name: str, action: str) -> AccessDecision
    # AccessDecision(allowed, pbac_enabled, matched_policy, reason)
```

Behaviour pins (must pass **unmodified**): `tests/handlers/test_abstract_pbac_helpers.py`,
`tests/handlers/test_queryservice_pbac_smoke.py`, `tests/handlers/test_queryexecutor_pbac_smoke.py`,
`tests/handlers/test_multiquery_pbac_smoke.py`, `tests/tenants/test_tenant_policy_preflight.py`,
`tests/integration/test_pbac_enforcement.py`.

### Does NOT Exist
- ~~A new exception type on the handler path~~: handlers keep raising `web.HTTPNotFound()`.
- Do not route handlers through ~~`enforce_principal`~~. That is the principal path, which always detaches and raises `QueryAccessDenied`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/abstract.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_owned_slug"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Outcome equivalence, not code equivalence.** Each input (PBAC off, missing name, no session, authz on/off, missing evaluator, allow, deny, evaluator exception) must produce the same outcome as before.
- **One intentional semantic tightening is allowed:** today an exception raised by `check_access` propagates out of the handler as a 500. After delegation, `evaluate` turns it into a deny, so the handler returns 404. This matches the spec's fail-closed rule. Record it in the Completion Note.
- The "PBAC misconfigured" error log stays: `resolve_evaluator` logs it. Keep the handler's `raise web.HTTPNotFound()` when the evaluator is `None`.
- `copy` and `inspect` imports may become unused after the change. Remove them only if `ruff` reports them unused.

---

## Implementation Blueprint

### Steps (in order)
1. Import the three core functions near the other `querysource` imports — *why*: this is the only new dependency.
2. In `_enforce_pbac`, swap the evaluator lookup, ctx build and check for the core calls, keeping every log line and `raise` — *why*: the HTTP contract is pinned by existing tests.
3. Do the same in `_enforce_owned_slug` with `detached=True` — *why*: tenant-owned checks must never touch the app cache (FEAT-147 L227-234).
4. Run all the behaviour-pin suites unmodified.

### `querysource/handlers/abstract.py` (MODIFY) — `_enforce_pbac` evaluation tail
```python
# occurrences: 1 (verified: grep -c "        result = evaluator.check_access(" querysource/handlers/abstract.py)
# REPLACE the evaluator lookup at :405-411 with:
        pbac_enabled, evaluator = resolve_evaluator(request, detached=False)
        if evaluator is None:
            raise web.HTTPNotFound()
# REPLACE `ctx = EvalContext(...)` at :431-436 with:
        ctx = build_eval_context(
            userinfo=userinfo, user=user, session=session, request=request,
        )
# REPLACE `result = evaluator.check_access(...)` + iscoroutine guard at :443-451, and the
#   `if not result.allowed:` block at :452-461, with:
        decision = await evaluate(evaluator, ctx, resource_type, resource_name, action)
        if not decision.allowed:
            self.logger.info(
                "PBAC denied: %s/%s action=%s policy=%s reason=%s",
                resource_type,
                resource_name,
                action,
                decision.matched_policy,
                decision.reason,
            )
            raise web.HTTPNotFound()
# FILL IN: keep the lazy `from navigator_auth.conf import AUTH_SESSION_OBJECT` import (still used by
#          userinfo selection); drop the EvalContext/Environment lazy imports only if now unused
```
**Why**: `pbac_enabled` is already known to be true at this point (the guardian check at :346 returned early otherwise), so only the `None` evaluator matters here.

### `querysource/handlers/abstract.py` (MODIFY) — `_enforce_owned_slug`
```python
# occurrences: 1 (verified: grep -c "        result = detached.check_access(" querysource/handlers/abstract.py)
# REPLACE the lookup + detached copy (:484-493) with:
        pbac_enabled, detached = resolve_evaluator(request, detached=True)
        if not pbac_enabled:
            return  # PBAC disabled — fast-path no-op
        if detached is None:
            raise web.HTTPNotFound()
# REPLACE `ctx = EvalContext(...)` (:543) with build_eval_context(..., request=request)
# REPLACE `result = detached.check_access(...)` + guard + deny block (:551-567) with
#   `decision = await evaluate(detached, ctx, "slug", identity.slug, action)` and the same
#   info log ("PBAC denied: slug=%s action=%s policy=%s reason=%s") + raise web.HTTPNotFound()
# FILL IN: preserve the exact original ordering of the security/evaluator checks — bounded by
#          tests/tenants/test_tenant_policy_preflight.py passing unmodified
```
**Why**: `resource_type="slug"` is the literal the original call used (`abstract.py:551-556`). Keep it, because the preflight tests assert on it.

### FILL IN checklist
- [ ] Lazy import cleanup in `_enforce_pbac`, only what `ruff` flags as unused
- [ ] `_enforce_owned_slug`: check ordering preserved; the preflight tests are the bound
- [ ] Completion Note records the evaluator-exception → 404 tightening

---

## Acceptance Criteria

- [ ] AC-1: `grep -c "check_access(" querysource/handlers/abstract.py` → `0`.
- [ ] AC-2: All behaviour-pin suites listed in the Codebase Contract pass **without modification**.
- [ ] AC-3: `_enforce_owned_slug` never mutates the app evaluator's `_cache` or `_stats` (asserted by `test_copy_keeps_app_cache_and_policy_immutable`).
- [ ] AC-4: `ruff check querysource/handlers/abstract.py` is clean.

---

## Validation Commands

- `pytest tests/handlers/test_abstract_pbac_helpers.py -q`
- `pytest tests/handlers/test_queryservice_pbac_smoke.py -q`
- `pytest tests/handlers/test_queryexecutor_pbac_smoke.py -q`
- `pytest tests/handlers/test_multiquery_pbac_smoke.py -q`
- `pytest tests/tenants/test_tenant_policy_preflight.py -q`
- `pytest tests/integration/test_pbac_enforcement.py -q`

---

## Test Specification

No new test file. This refactor is pinned by the existing suites above. If a pin fails, fix the
refactor, **never the test**.

---

## Agent Instructions

1. Confirm TASK-750 is completed and `querysource.auth.enforcement` imports.
2. Run the Validation Commands **before** editing to record a green baseline. Report any pre-existing failure instead of fixing it.
3. Refactor, re-run the Validation Commands and `ruff check`.
4. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
