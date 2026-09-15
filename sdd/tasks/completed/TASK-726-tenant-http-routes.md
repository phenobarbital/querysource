# TASK-726: Expose unified tenant execution and inspection routes

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-725
**Assigned-to**: unassigned

## Context

Implements M4 tenant handler/routes of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Add TenantQueryHandler list/query/columns/test_slug to the selector module. Reuse request-independent execution helpers from service/multi handlers; do not instantiate QueryManager just to call get.
- Register GET/POST collection, GET/POST stored slug, HEAD/PATCH columns and GET/POST slug/test exactly as spec. Register collection slash aliases directly without POST redirects; legacy routes take precedence.
- Preserve legacy v2/v3 definition storage, output suffixes and inspection semantics. Existing legacy tenant conditions must not redirect storage.
- Reserve management tenant routing and test literal queries/test/qs names against raw routes. Ensure request is propagated for existing credential/policy behavior and tenant selector never reaches query conditions.

**NOT in scope**: A second tenant CRUD API and external-worker server implementation.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/tenant.py` | MODIFY | Append the handler at module scope after the complete selector function; this is a dependency-created module. |
| `querysource/handlers/__init__.py` | MODIFY | Export the new handler without changing existing imports. |
| `querysource/services.py` | MODIFY | One routing owner selects the store before shared execution helpers run. |
| `querysource/handlers/service.py` | MODIFY | Tenant single execution reuses provider/output behavior instead of forking it. |
| `querysource/handlers/multi.py` | MODIFY | Saved and inline pipelines need the same request credential propagation. |
| `tests/tenants/test_tenant_http_routes.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.handlers.abstract import AbstractHandler
from .tenant import TenantQueryHandler
import pytest
from querysource.handlers.tenant import TenantQueryHandler
from querysource.queries import QS, MultiQS
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.handlers.abstract.AbstractHandler` → `querysource/handlers/abstract.py:27`
- `querysource.handlers.tenant` → planned `querysource/handlers/tenant.py` created by TASK-723; verify after prerequisite
- `querysource.queries.QS` → `querysource/queries/__init__.py:6`
- `querysource.queries.MultiQS` → `querysource/queries/__init__.py:7`

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/services.py:98
def setup(self, app: web.Application) -> web.Application:

querysource/handlers/__init__.py:6
from .service import QueryService

querysource/services.py:50
class QuerySource(metaclass=Singleton):
def __init__(self, **kwargs):

querysource/handlers/service.py:32
class QueryService(AbstractHandler):
async def query(self, request):

querysource/handlers/multi.py:23
class QueryHandler(AbstractHandler):
async def query(self, request: web.Request) -> web.StreamResponse:
```

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

1. Add TenantQueryHandler list/query/columns/test_slug to the selector module. Reuse request-independent execution helpers from service/multi handlers; do not instantiate QueryManager just to call get. **Why:** A request-bound management view is not a reusable listing service.
2. Register GET/POST collection, GET/POST stored slug, HEAD/PATCH columns and GET/POST slug/test exactly as spec. Register collection slash aliases directly without POST redirects; legacy routes take precedence. **Why:** POST redirects and generic tenant routes can break existing routing.
3. Preserve legacy v2/v3 definition storage, output suffixes and inspection semantics. Existing legacy tenant conditions must not redirect storage. **Why:** New routing must preserve old API semantics.
4. Reserve management tenant routing and test literal queries/test/qs names against raw routes. Ensure request is propagated for existing credential/policy behavior and tenant selector never reaches query conditions. **Why:** Credentials and policy need the real request and resolved owner.

### `querysource/handlers/tenant.py` (MODIFY)

```python
# Planned dependency anchor: `def resolve_request_store(request: web.Request, registry: TenantRegistry, payload: Mapping[str, Any] | None=None) -> QueryStore:` in TASK-723 CREATE blueprint.
# occurrences: 1 in dependency blueprint; live source is not yet present.
# FILL IN: verify live anchor/count after dependency lands before editing.
from querysource.handlers.abstract import AbstractHandler

class TenantQueryHandler(AbstractHandler):
    """One tenant handler selects existing single/multi execution behavior."""

    async def list(self, request: web.Request) -> web.StreamResponse:
        """List selected tenant definitions with management pagination conventions."""
        # FILL IN: Resolve URL owner; reuse extracted execution/response helpers; exact route/status/suffix contract from spec §2.
        raise NotImplementedError

    async def query(self, request: web.Request) -> web.StreamResponse:
        """Execute stored single/multi or inline multi under URL owner."""
        # FILL IN: Resolve URL owner; reuse extracted execution/response helpers; exact route/status/suffix contract from spec §2.
        raise NotImplementedError

    async def columns(self, request: web.Request) -> web.StreamResponse:
        """Inspect selected definition using existing single/multi semantics."""
        # FILL IN: Resolve URL owner; reuse extracted execution/response helpers; exact route/status/suffix contract from spec §2.
        raise NotImplementedError

    async def test_slug(self, request: web.Request) -> web.StreamResponse:
        """Dry-run selected saved definition without executing its data query."""
        # FILL IN: Resolve URL owner; reuse extracted execution/response helpers; exact route/status/suffix contract from spec §2.
        raise NotImplementedError
```

**Why:** Append the handler at module scope after the complete selector function; this is a dependency-created module.

### `querysource/handlers/__init__.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `from .service import QueryService` (verified: querysource/handlers/__init__.py:6)
from .tenant import TenantQueryHandler
# FILL IN: add TenantQueryHandler to existing __all__; preserve all current exports.
```

**Why:** Export the new handler without changing existing imports.

### `querysource/services.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QuerySource(metaclass=Singleton):` (verified: querysource/services.py:50)
# Add inside setup after existing explicit route registration, before readiness.
handler = TenantQueryHandler()
self.app.router.add_get("/api/v1/{tenant}/queries/", handler.list)
self.app.router.add_post("/api/v1/{tenant}/queries/", handler.query)
self.app.router.add_get("/api/v1/{tenant}/queries", handler.list)
self.app.router.add_post("/api/v1/{tenant}/queries", handler.query)
self.app.router.add_get("/api/v1/{tenant}/queries/{slug}", handler.query, allow_head=False)
self.app.router.add_post("/api/v1/{tenant}/queries/{slug}", handler.query)
self.app.router.add_head("/api/v1/{tenant}/queries/{slug}", handler.columns)
self.app.router.add_patch("/api/v1/{tenant}/queries/{slug}", handler.columns)
self.app.router.add_get("/api/v1/{tenant}/queries/{slug}/test", handler.test_slug)
self.app.router.add_post("/api/v1/{tenant}/queries/{slug}/test", handler.test_slug)
# FILL IN: retain exact suffix parsing and legacy route precedence; assert collision tests.
```

**Why:** One routing owner selects the store before shared execution helpers run.

### `querysource/handlers/service.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryService(AbstractHandler):` (verified: querysource/handlers/service.py:32)
async def query(self, request):
    """Extract owner-aware shared execution helper; keep legacy route fixed to configured default and response semantics."""
    # FILL IN: Extract owner-aware shared execution helper; keep legacy route fixed to configured default and response semantics.
    raise NotImplementedError
```

**Why:** Tenant single execution reuses provider/output behavior instead of forking it.

### `querysource/handlers/multi.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryHandler(AbstractHandler):` (verified: querysource/handlers/multi.py:23)
async def query(self, request: web.Request) -> web.StreamResponse:
    """Extract owner-aware multi execution helper; preserve legacy default and pass real request and owner context."""
    # FILL IN: Extract owner-aware multi execution helper; preserve legacy default and pass real request and owner context.
    raise NotImplementedError
```

**Why:** Saved and inline pipelines need the same request credential propagation.

### `tests/tenants/test_tenant_http_routes.py` (CREATE)

```python
"""Expose unified tenant execution and inspection routes regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_route_method_matrix_and_slash_aliases() -> None:
    """route method matrix and slash aliases."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_single_multi_inline_dispatch() -> None:
    """single multi inline dispatch."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_and_management_precedence() -> None:
    """legacy and management precedence."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_columns_test_and_output_suffixes() -> None:
    """columns test and output suffixes."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/handlers/tenant.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/__init__.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/services.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/service.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/handlers/multi.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_http_routes.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Add TenantQueryHandler list/query/columns/test_slug to the selector module. Reuse request-independent execution helpers from service/multi handlers; do not instantiate QueryManager just to call get.
- [ ] AC-2: Register GET/POST collection, GET/POST stored slug, HEAD/PATCH columns and GET/POST slug/test exactly as spec. Register collection slash aliases directly without POST redirects; legacy routes take precedence.
- [ ] AC-3: Preserve legacy v2/v3 definition storage, output suffixes and inspection semantics. Existing legacy tenant conditions must not redirect storage.
- [ ] AC-4: Reserve management tenant routing and test literal queries/test/qs names against raw routes. Ensure request is propagated for existing credential/policy behavior and tenant selector never reaches query conditions.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_http_routes.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_route_method_matrix_and_slash_aliases` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_single_multi_inline_dispatch` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_and_management_precedence` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_columns_test_and_output_suffixes` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

The dispatched seat `minimax` (backend nova, model minimax.minimax-m2.5)
came back `fidelity_violation`: its commit included changes to
`querysource/handlers/abstract.py`, which is not in this task's declared
file list. The underlying idea — reading a tenant context off the
request and threading it into `get_source()`'s `QS` construction — was
architecturally sound, so it was reused, but repositioned entirely into
this task's own in-scope files rather than the shared, out-of-scope
`abstract.py`: every call site of `self.get_source(request, slug,
conditions, driver=args)` in `service.py` already forwards `**kwargs`
straight through to `QS(**kwargs)`, and `QS` already accepts a
keyword-only `tenant=` parameter (TASK-721) — so a one-line
`tenant = request.get('qs_tenant')` plus `tenant=tenant` at each
existing call site achieves the identical propagation without touching
`abstract.py` at all. Implemented fresh by the orchestrator (attempt 3).

- `querysource/handlers/tenant.py`: `TenantQueryHandler` with
  `list`/`query`/`columns`/`test_slug`. `list()` goes directly through
  `DefinitionRepository.list()` — AC-1's explicit "do not instantiate
  QueryManager just to call get" is satisfied literally: `QueryManager`
  is never imported or touched by this handler at all — and matches
  `QueryManager._paginate_list`'s `data`/`meta`/`X-Total-*` response
  envelope (TASK-723) for shape consistency between the management and
  tenant listing endpoints. `query()`/`columns()`/`test_slug()` resolve
  and validate the URL tenant selector via a shared `_resolve_or_raise`
  helper, then delegate to `QueryService`/`QueryHandler` — `query()`
  dispatches to single-query execution (`QueryService`) when a `slug`
  path segment is present, or inline/multi execution (`QueryHandler`)
  when it is not; `columns()` dispatches on `request.method`
  (`HEAD` → `get_columns`, else `columns`). The tenant selector is
  threaded only through `request['qs_tenant']`, never merged into query
  conditions or the request body (AC-4).
- `querysource/handlers/service.py`/`querysource/handlers/multi.py`: the
  minimal tenant-threading described above, applied identically at
  every existing `get_source()`/`MultiQS()` construction call site
  (4 in `service.py`: `query`/`get_columns`/`columns`/`test_slug`; 1 in
  `multi.py`: `query`). Every pre-existing (non-tenant) caller never
  sets `request['qs_tenant']`, so `request.get('qs_tenant')` is always
  `None` for them — `QS`/`MultiQS`'s own existing default — so legacy
  v2/v3 behavior is provably unchanged (AC-3), not merely assumed.
- `querysource/services.py`: registered `GET`/`POST` collection (with
  and without a trailing slash — AC-2 "register collection slash
  aliases directly without POST redirects"), `GET`/`POST` stored slug,
  `HEAD`/`PATCH` columns, `GET`/`POST` `.../test`, all under
  `/api/v1/{tenant}/queries...`. Registered **last** in `setup()` — after
  every fixed-literal route (management, datasources, `qs`/variables,
  v2/v3 multi-query, component docs, Airtable OAuth). aiohttp's
  `UrlDispatcher` resolves routes in registration order, so this
  ordering is precisely what makes a literal first path segment like
  `"management"`/`"qs"`/`"datasources"` resolve against the existing,
  more specific route rather than being captured as `{tenant}` (AC-4) —
  verified with a route-registration-index test asserting the exact
  ordering, not assumed from reading the code.
- `querysource/handlers/__init__.py`: exported `TenantQueryHandler`.
- `tests/tenants/test_tenant_http_routes.py`: 6 tests — the 4 from the
  blueprint plus 2 added (unknown-tenant selector rejection,
  tenant-feature-not-configured → 404 rather than a `KeyError`, the same
  class of "app hasn't wired up the tenant feature" case TASK-723/724/725
  each needed to handle). `test_route_method_matrix_and_slash_aliases`
  and `test_legacy_and_management_precedence` build a real
  `QuerySource(lazy=True).setup(app)` and introspect the actual
  registered routes/order; `test_single_multi_inline_dispatch` and
  `test_columns_test_and_output_suffixes` monkeypatch `QueryService`/
  `QueryHandler` at their own module attributes (picked up correctly by
  `tenant.py`'s lazy, call-time imports) to verify real
  `TenantQueryHandler` dispatch without needing a live database.

Checks run (this worktree, `.venv` from the primary checkout):

- `pytest tests/tenants/test_tenant_http_routes.py -q` → 6 passed.
- `pytest tests/tenants tests/handlers tests/test_abstract_multi.py
  --continue-on-collection-errors -q` → 149 passed, 1 pre-existing
  collection error (`tests/handlers/test_airtable_oauth.py`, missing
  `aioresponses` dependency, unrelated and present before this branch).
- `ruff check` on all six changed/created files — fixed an import-order/
  `__all__`-sort issue in `querysource/handlers/__init__.py` (both
  introduced by this task's own edit); every other finding
  (`BLE001`/`DTZ005`/`SIM102`/`TRY401`/`RUF015`/pre-existing unused
  `sys`/`subprocess` imports in `services.py`) was checked line-by-line
  against exactly where this task's edits landed and confirmed
  pre-existing, unrelated code — left as-is, matching the convention on
  every prior task in this feature. `querysource/handlers/tenant.py`
  itself has zero findings.

Files changed: `querysource/handlers/tenant.py` (new class),
`querysource/handlers/__init__.py`, `querysource/services.py`,
`querysource/handlers/service.py`, `querysource/handlers/multi.py`,
`tests/tenants/test_tenant_http_routes.py`.

Deployment gates still unverified: `black --check` could not run in this
environment (same gap noted on every prior task). No live PostgreSQL/
Redis was used; single/multi dispatch is verified against monkeypatched
`QueryService`/`QueryHandler`, not a real query execution — that
integration gate remains open per the spec's own broader "yes after DDL
fixture review" note.

No spec deviations: a second tenant CRUD API and an external-worker
server implementation are explicitly out of scope for this task and were
not created.
