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

Author/date: sdd-worker (orchestrated via parrot-sdd-coder), 2026-09-15.

Attempt 1 (seat `mistral`, backend nova, model mistral.devstral-2-123b)
left a dirty sub-worktree (`dirty_task_worktree`, uncommitted changes to
`abstract.py`/`multi.py` + the untracked test file — a real ~19-minute
attempt, not a stub). Attempt 2 (seat `minimax`, backend nova, model
minimax.minimax-m2.5) completed and merged.
`AbstractHandler._enforce_owned_slug` (`querysource/handlers/abstract.py`)
was verified correct against every AC on review: shallow evaluator copy
(`copy.copy`), `_cache` replaced with `{}`, `_stats` copied (not shared),
session extraction via the real `_get_user_session`, sessionless-authz
gated by `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`, real
`navigator_auth.abac.context.EvalContext`/`...policies.environment.Environment`
construction (both verified to genuinely exist and import cleanly, not
assumed), and `inspect.iscoroutine(result)`-gated awaiting for
`check_access`'s coroutine-or-sync return. Two classes of problem found
and fixed on review:

- `querysource/handlers/multi.py`'s new `_preflight_multiquery_owned`
  read the registry from `app["tenant_registry"]` — the same wrong key
  TASK-723 found and fixed elsewhere (TASK-720 publishes
  `app["qs_tenant_registry"]`, verified again against
  `querysource/services.py`). Its fallback called `QuerySource().registry`
  — an attribute that does not exist on `QuerySource` at all (TASK-720
  exposes the registry only via the async `initialize_tenants()`, never a
  plain `.registry` property); this fallback could only ever raise
  `AttributeError`. Both the broken fallback and a second
  `except Exception: return` around `registry.resolve()` silently
  **skipped** the ownership check on any error — fail-**open**, directly
  contradicting AC-1's "fail-closed errors" and the established
  convention in the very same file: the pre-existing
  `_preflight_multiquery` (called immediately before this new method)
  already does `except Exception as exc: ... raise
  web.HTTPNotFound() from exc`. Fixed the key; dropped the broken
  fallback entirely (an absent `qs_tenant_registry` app key now means
  "tenant feature not wired up for this app" — a legitimate no-op,
  matching TASK-723/724's established fallback convention — not an error
  to swallow); made a genuine `registry.resolve()` failure raise
  `HTTPNotFound` instead of silently letting the batch through
  unverified.
- `tests/tenants/test_tenant_policy_preflight.py`'s four original tests
  never touched the real `querysource.handlers.abstract.AbstractHandler`
  class at all: a `DummyHandler` class hand-duplicated a full second copy
  of `_enforce_owned_slug`'s logic, and every test called *that* copy.
  A bug in the real implementation — or any future change to it — would
  never be caught by this suite; the tests would keep "passing" against
  a frozen, disconnected clone. This is a more insidious version of
  TASK-724's `assert True` placeholders: it looks like real coverage but
  provides none. Rewrote to bind the real
  `AbstractHandler._enforce_owned_slug`/`_get_user_session` **function
  objects** (not reimplementations) to a minimal harness instance, using
  a small dict-backed `_FakeRequest` (avoiding the `MagicMock`
  `__getitem__`/`__setitem__` round-trip gotcha already documented from
  TASK-723) extended with exactly the attributes the real
  `navigator_auth.abac.context.EvalContext.__init__` reads
  (`remote`/`method`/`headers`/`path_qs`/`path`/`rel_url`) — discovered
  by running the rewritten tests against the real code and reading the
  resulting `AttributeError`s one at a time, not guessed upfront. Also
  added three new tests for `_preflight_multiquery_owned` (AC-4), which
  had zero coverage in the original test file despite being defined in
  one of only two files this task was scoped to modify.

Checks run (this worktree, `.venv` from the primary checkout):

- `pytest tests/tenants/test_tenant_policy_preflight.py -q` → 7 passed
  (4 original scenarios rewritten to exercise real code + 3 new
  `_preflight_multiquery_owned` tests).
- `pytest tests/tenants tests/handlers tests/test_abstract_multi.py
  --continue-on-collection-errors -q` → 143 passed, 1 pre-existing
  collection error (`tests/handlers/test_airtable_oauth.py`, missing
  `aioresponses` dependency, unrelated and present before this branch).
  `tests/handlers/test_multiquery_pbac_smoke.py` (31 tests, the existing
  PBAC/multiquery regression suite) passed unchanged.
- `ruff check querysource/handlers/multi.py
  tests/tenants/test_tenant_policy_preflight.py` → fixed an import-order
  shuffle and an unused local variable within lines this task
  added/modified; `querysource/handlers/abstract.py`'s pre-existing
  `RUF013`/`TRY401`/etc. findings are all on lines well outside
  `_enforce_owned_slug` and were left as-is, matching the convention on
  every prior task in this feature.

Files changed (beyond the original merge): `querysource/handlers/multi.py`
(`_preflight_multiquery_owned`'s app key and error-handling only —
`_enforce_owned_slug` in `abstract.py` needed no changes),
`tests/tenants/test_tenant_policy_preflight.py` (complete rewrite from a
duplicated-fake-based suite to one exercising the real production code).

Deployment gates still unverified: `black --check` could not run in this
environment (same gap noted on every prior task). No live PBAC/navigator-auth
deployment or real navigator_session backend was used — every test
constructs a real `EvalContext`/`Environment` but mocks `check_access`
itself and the session/evaluator objects around it.

No spec deviations: new membership policies, upstream navigator-auth
changes, and translating schema names to auth org IDs are explicitly out
of scope for this task and were not touched.
