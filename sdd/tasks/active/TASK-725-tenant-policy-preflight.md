# TASK-725: Preserve policy semantics with isolated tenant decisions

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-724
**Assigned-to**: unassigned

## Context

Implements M4 existing controls of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Implement _enforce_owned_slug with current session extraction, sessionless-authz behavior, resource actions and fail-closed errors. Keep slug rule names unchanged and PBAC optional.
- Use a shallow evaluator copy per tenant check, replacing _cache with {} and _stats with a copied dict. Do not change app evaluator cache/TTL, numeric auth tenants or policy index. Retain coroutine-result handling.
- Extract shared enforcement mechanics rather than copy divergent authentication branches. Add keyword-only internal identity context to policy helpers without breaking existing calls.
- Prepare preflight to check actual saved child slugs, not output aliases, including stored pipeline expansions before execution. Transport task invokes the helper after reference resolution; files/raw actions preserve existing behavior.

**NOT in scope**: New membership policies, upstream navigator-auth changes, or translating schema names to auth org IDs.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/abstract.py` | MODIFY | Reusing auth semantics avoids introducing a second membership layer. |
| `querysource/handlers/multi.py` | MODIFY | Aliases are output labels and cannot stand in for the referenced definition resource. |
| `tests/tenants/test_tenant_policy_preflight.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
import copy
from aiohttp import web
from querysource.tenants import QueryIdentity
from querysource.auth import ResourceType
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite
- `querysource.auth.ResourceType` → `querysource/auth/__init__.py:19`

### Existing Signatures to Use

```text
querysource/handlers/abstract.py:317
async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None:

querysource/auth/pbac.py:28
def setup_pbac(app: web.Application, policy_dir: str='policies', cache_ttl: int=300) -> 'tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]':

querysource/handlers/abstract.py:27
class AbstractHandler(BaseHandler):

querysource/handlers/multi.py:23
class QueryHandler(AbstractHandler):
async def query(self, request: web.Request) -> web.StreamResponse:
```


Installed adapter evidence (read during specification and rechecked for this task):
`navigator_auth/abac/policies/evaluator.py:215` initializes `_cache`/`_stats`;
`:405` defines synchronous `check_access(ctx, resource_type, resource_name, action,
env=None, owner_reports_to=None, org_id=1, client_id=1) -> EvaluationResult`.
These are under `.venv/lib/python3.11/site-packages/`. `handlers/abstract.py:399`
retrieves the app evaluator and `:437` evaluates; preserve its full auth handling.

### Does NOT Exist

- The new tenant registry, repository, model, handler and identity helpers do not
  exist at decomposition time except as dependency blueprints. Do not import them
  until the creating prerequisite is complete.
- No `querysource.remote` implementation exists in this checkout. The versioned
  tenant callable is an external contract, never a public fallback.
- No `tenant` parameter on the current provider checksum or compiled parser
  interface exists; use immutable execution identity and the runtime adapter.

## Implementation Notes

### Pattern to Follow

Use the exact interfaces in spec §3 and the verified existing signatures above.
Modify existing classes in place; a class wrapper in a MODIFY block locates new
methods, not permission to replace the entire class. Preserve unrelated methods,
metaclasses, inheritance, decorators and initialization behavior. CREATE blocks
are whole-file starting points; all bounded FILL IN markers must be completed.

### Key Constraints

- No per-request shared Meta, search_path, global field-policy or pooled-connection
  mutation. No owner fallback. Preserve configured legacy defaults and overrides.
- Tenant is structural definition ownership; SQL may read other permitted schemas.
- Keep strict new type hints, async I/O, existing logging, black formatting and
  existing dependencies. Do not add mandatory PBAC/membership requirements.
- Do not change fixed spec signatures. Refresh anchors after dependencies because
  earlier tasks modify shared files. If a fixed contract cannot work, report the
  concrete mismatch instead of silently changing ownership behavior.

## Implementation Blueprint

### Steps (in order)

1. Implement _enforce_owned_slug with current session extraction, sessionless-authz behavior, resource actions and fail-closed errors. Keep slug rule names unchanged and PBAC optional. **Why:** The user approved existing access controls without new membership checks.
2. Use a shallow evaluator copy per tenant check, replacing _cache with {} and _stats with a copied dict. Do not change app evaluator cache/TTL, numeric auth tenants or policy index. Retain coroutine-result handling. **Why:** The installed evaluator cache has no schema-owner key.
3. Extract shared enforcement mechanics rather than copy divergent authentication branches. Add keyword-only internal identity context to policy helpers without breaking existing calls. **Why:** Authentication branches must not diverge between route families.
4. Prepare preflight to check actual saved child slugs, not output aliases, including stored pipeline expansions before execution. Transport task invokes the helper after reference resolution; files/raw actions preserve existing behavior. **Why:** An alias cannot authorize a differently named saved child.

### `querysource/handlers/abstract.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class AbstractHandler(BaseHandler):` (verified: querysource/handlers/abstract.py:27)
import copy
from aiohttp import web

class AbstractHandler:
    """Existing base handler; preserve public policy names and actions."""

    async def _enforce_owned_slug(self, request: web.Request, identity: QueryIdentity, action: str) -> None:
        """Evaluate existing slug rules with a detached, initially empty decision cache."""
        evaluator = request.app.get("policy_evaluator")
        if request.app.get("security") is None:
            return
        if evaluator is None:
            raise web.HTTPNotFound()
        detached = copy.copy(evaluator)
        detached._cache = {}
        detached._stats = dict(evaluator._stats)
        # FILL IN: reuse existing sessionless-authz/context construction and evaluate
        # on detached; preserve policy resource names, fail-closed and async-result handling.
        raise NotImplementedError
```

**Why:** Reusing auth semantics avoids introducing a second membership layer.

### `querysource/handlers/multi.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryHandler(AbstractHandler):` (verified: querysource/handlers/multi.py:23)
async def _preflight_multiquery(self, request: web.Request, slugs: list, files: list, has_raw_query: bool) -> None:
    """Check real resolved QueryIdentity objects before executing batches; preserve files/raw controls and legacy calls."""
    # FILL IN: Check real resolved QueryIdentity objects before executing batches; preserve files/raw controls and legacy calls.
    raise NotImplementedError
```

**Why:** Aliases are output labels and cannot stand in for the referenced definition resource.

### `tests/tenants/test_tenant_policy_preflight.py` (CREATE)

```python
"""Preserve policy semantics with isolated tenant decisions regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_pbac_disabled_has_no_membership_requirement() -> None:
    """pbac disabled has no membership requirement."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_copy_keeps_app_cache_and_policy_immutable() -> None:
    """copy keeps app cache and policy immutable."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_same_slug_different_owner_no_decision_reuse() -> None:
    """same slug different owner no decision reuse."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_sessionless_authz_async_result_and_denied_child() -> None:
    """sessionless authz async result and denied child."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/handlers/abstract.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/multi.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_policy_preflight.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Implement _enforce_owned_slug with current session extraction, sessionless-authz behavior, resource actions and fail-closed errors. Keep slug rule names unchanged and PBAC optional.
- [ ] AC-2: Use a shallow evaluator copy per tenant check, replacing _cache with {} and _stats with a copied dict. Do not change app evaluator cache/TTL, numeric auth tenants or policy index. Retain coroutine-result handling.
- [ ] AC-3: Extract shared enforcement mechanics rather than copy divergent authentication branches. Add keyword-only internal identity context to policy helpers without breaking existing calls.
- [ ] AC-4: Prepare preflight to check actual saved child slugs, not output aliases, including stored pipeline expansions before execution. Transport task invokes the helper after reference resolution; files/raw actions preserve existing behavior.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_policy_preflight.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_pbac_disabled_has_no_membership_requirement` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_copy_keeps_app_cache_and_policy_immutable` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_same_slug_different_owner_no_decision_reuse` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_sessionless_authz_async_result_and_denied_child` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

## Agent Instructions

1. Read the approved spec and completed dependency packet(s).
2. Refresh source anchors and planned imports after dependencies land; update this
   packet before coding if actual source moved.
3. Set this task in `sdd/tasks/index/per-tenant-queries.json` to `in-progress`.
4. Apply each bounded blueprint, complete every FILL IN branch and test, and
   preserve the five user decisions and fixed spec interfaces.
5. Run focused checks, record results and any unavailable external integration.
6. Move this task file to `sdd/tasks/completed/`, set index status to `done`, and
   fill the completion note. Do not update the historical monolithic index.

## Completion Note

To be completed by the implementing agent: author/date, exact checks and results,
files changed, deployment gates still unverified, and any approved spec deviations.
