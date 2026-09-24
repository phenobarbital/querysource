# TASK-752: slug_visibility delegates evaluation to the shared core

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-750
**Assigned-to**: unassigned

---

## Context

Implements the describe half of spec §3 **Module 4**. `querysource/auth/slug_visibility.py`
(FEAT-148) carries the third copy of the evaluator-lookup, detach and EvalContext logic
(`_evaluator_state`, `_eval_context`), plus its own `check_access` calls inside
`can_access`. This task makes those delegate to `querysource.auth.enforcement`. **Every
public and private name and signature stays**, and the describe behaviour is unchanged.

---

## Scope

- `_evaluator_state(request, *, detached=False)` becomes `return resolve_evaluator(request, detached=detached)`.
- `_eval_context(request, principal)` delegates to `build_eval_context(...)`, keeping the AUTHZ/regular split:
  - AUTHZ: `user=None`, `session=None`.
  - Regular: `user=userinfo or None`, `session=principal.session`.
- In `can_access`, route the primary and fallback `evaluator.check_access(...)` calls (`:380`, `:398`) through `await evaluate(...)`. Keep the "allowed on primary, else fallback" logic.
- `filter_visible` keeps `evaluator.filter_resources(...)` unchanged. It is out of scope, because the core has no batch primitive.

**NOT in scope**: `handlers/abstract.py` (TASK-751), `filter_visible`'s `filter_resources`, and the `Principal` / `resolve_principal` types.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/slug_visibility.py` | MODIFY | helpers + `can_access` delegate to core |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.enforcement import build_eval_context, evaluate, resolve_evaluator   # created by TASK-750
```

### Existing Signatures to Use
```python
# querysource/auth/slug_visibility.py
class PrincipalKind(str, Enum): SUPERUSER, PROGRAMS, AUTHZ, NO_PROGRAMS, NONE     # :25
@dataclass(frozen=True)
class Principal: kind; userinfo: dict; groups: tuple; programs: tuple; session: Any   # :36
def _evaluator_state(request: web.Request, *, detached: bool = False) -> tuple[bool, Any]:   # :227
def _eval_context(request: web.Request, principal: Principal) -> Any:                     # :260
    # AUTHZ → EvalContext(request=request, user=None, userinfo=principal.userinfo, session=None)
    # else  → EvalContext(request=request, user=principal.userinfo or None, userinfo=principal.userinfo, session=principal.session)
async def can_access(request, principal, slug, primary_action, fallback_action=None, *, detached=False) -> bool:  # :353
    # :363 pbac_enabled, evaluator = _evaluator_state(request, detached=detached)
    # :366-371 disabled → True; evaluator None → False
    # :380 primary_result = evaluator.check_access(ctx=, resource_type=ResourceType.SLUG, resource_name=slug, action=primary_action, env=Environment())
    # :398 fallback_result = evaluator.check_access(... action=fallback_action ...)
    # :414-416 except Exception: logger.exception("Error in can_access"); return False

# querysource/auth/enforcement.py (TASK-750)
def resolve_evaluator(request, *, detached: bool) -> tuple[bool, Any]
def build_eval_context(*, userinfo: dict, user, session, request=None) -> Any
async def evaluate(evaluator, ctx, resource_type, resource_name: str, action: str) -> AccessDecision
```

Behaviour pins (must pass unmodified): `tests/auth/test_slug_visibility.py`, `tests/handlers/test_describe_tenant.py`.

### Does NOT Exist
- ~~A batch `evaluate_many` in the core~~: keep `filter_resources` as is.
- Do not replace `Principal` with ~~`QSPrincipal`~~. They are different types (describe vs library caller).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/slug_visibility.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/slug_visibility.py#_evaluator_state",
    "sym:querysource/auth/slug_visibility.py#_eval_context",
    "sym:querysource/auth/slug_visibility.py#can_access"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `evaluate` already turns exceptions into a deny, so `can_access`'s outer `try/except` stays as a safety net (it returns `False`, which is the same outcome).
- The fallback check runs only when the primary decision is not allowed, exactly as today.
- Keep `_evaluator_state` and `_eval_context` as module-level names. Tests and `filter_visible` call them.

---

## Implementation Blueprint

### Steps (in order)
1. Import the three core functions — *why*: this is the only new dependency.
2. Replace the bodies of `_evaluator_state` and `_eval_context` — *why*: removes the third duplicated copy (spec §2 item 4).
3. Route the two `can_access` checks through `evaluate` — *why*: spec AC requires `grep -c "check_access(" querysource/auth/slug_visibility.py` → 0.
4. Run the pin suites.

### `querysource/auth/slug_visibility.py` (MODIFY) — helpers
```python
# occurrences: 1 (verified: grep -c 'def _evaluator_state(request: web.Request, \*, detached: bool = False) -> tuple\[bool, Any\]:' querysource/auth/slug_visibility.py)
# REPLACE the body of _evaluator_state (:227-257), keeping its docstring:
    return resolve_evaluator(request, detached=detached)

# occurrences: 1 (verified: grep -c 'def _eval_context(request: web.Request, principal: Principal) -> Any:' querysource/auth/slug_visibility.py)
# REPLACE the body of _eval_context (:260-281):
    """EvalContext exactly as _enforce_pbac builds it, via the shared core."""
    if principal.kind == PrincipalKind.AUTHZ:
        return build_eval_context(
            userinfo=principal.userinfo, user=None, session=None, request=request,
        )
    return build_eval_context(
        userinfo=principal.userinfo,
        user=principal.userinfo if principal.userinfo else None,
        session=principal.session,
        request=request,
    )
```

### `querysource/auth/slug_visibility.py` (MODIFY) — `can_access`
```python
# occurrences: 1 (verified: grep -c '        primary_result = evaluator.check_access(' querysource/auth/slug_visibility.py)
# REPLACE the primary check_access call + its iscoroutine guard (:380-390) and the
#   fallback call + guard (:398-408) with:
        primary = await evaluate(evaluator, ctx, ResourceType.SLUG, slug, primary_action)
        if primary.allowed:
            return True
        if fallback_action:
            fallback = await evaluate(evaluator, ctx, ResourceType.SLUG, slug, fallback_action)
            return fallback.allowed
        return False
# FILL IN: drop the now-unused `Environment` lazy import inside can_access only if ruff flags it
#          (filter_visible still uses Environment in its own scope)
```
**Why**: `ResourceType.SLUG` and the action strings are unchanged, so policy matching is identical.

### FILL IN checklist
- [ ] Unused-import cleanup in `can_access` only

---

## Acceptance Criteria

- [ ] AC-1: `grep -c "check_access(" querysource/auth/slug_visibility.py` → `0`.
- [ ] AC-2: `tests/auth/test_slug_visibility.py` and `tests/handlers/test_describe_tenant.py` pass unmodified.
- [ ] AC-3: `_evaluator_state(request, detached=True)` still returns a copy whose `_cache` is empty while the app evaluator is untouched.
- [ ] AC-4: `ruff check querysource/auth/slug_visibility.py` is clean.

---

## Validation Commands

- `pytest tests/auth/test_slug_visibility.py -q`
- `pytest tests/handlers/test_describe_tenant.py -q`

---

## Test Specification

No new test file. The existing describe suites are the behaviour pin.

---

## Agent Instructions

1. Confirm TASK-750 is completed.
2. Run the Validation Commands before editing to record a green baseline.
3. Refactor, re-run the Validation Commands and `ruff check`.
4. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
