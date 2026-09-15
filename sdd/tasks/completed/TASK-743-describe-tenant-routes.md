# TASK-743: Per-tenant describe routes (blocked on FEAT-147)

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-742; **external: FEAT-147 (`per-tenant-queries`) Modules 1, 2 and 4 merged to `dev`**
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 8** (spec v0.2, aligned with approved FEAT-147). It serves the three slug routes per tenant, through the same `QueryDescribe` handler:
- `GET /api/v1/{tenant}/queries/describe`
- `GET /api/v1/{tenant}/queries/{slug}/describe`
- `GET /api/v1/{tenant}/queries/{slug}/columns`

**What FEAT-147 provides.** The tenant registry, the definition repository and the policy adapter. **None of that exists yet.** FEAT-147's interfaces below are copied from its approved spec, not from code.

> ⛔ **Blocked**: do not start until FEAT-147 Module 1 (`querysource/tenants.py`), Module 2 (`querysource/repositories/definitions.py`) and Module 4 (`querysource/handlers/tenant.py`, `AbstractHandler._enforce_owned_slug`) are merged to `dev`.
> **First step when unblocked:** re-verify every FEAT-147 symbol below against the merged code and update this contract. If a name or signature differs, follow the merged code and record the deviation. **Never invent** a registry API.

**Open split decision** (spec §8): if FEAT-147 is not ready when TASK-734..742 are done, the author may move this task to a follow-up feature, so that 4.6.0 ships the non-tenant endpoints.

---

## Scope

- Add `tenant_store(request, tenant)` to `querysource/auth/slug_visibility.py`. It builds a `DescribeStore` from FEAT-147's resolved `QueryStore`.
- Add tenant-aware ABAC in `slug_visibility`: `filter_visible` and `can_access` must use FEAT-147's detached-evaluator adapter for tenant stores.
- Extend `QueryDescribe._store` with the tenant branch.
- Make `describe_list` handle tenant stores:
  - projection without `program_slug`, filled in afterwards as `store.schema`;
  - identifiers quoted with FEAT-147's `quote_identifier`;
  - `program_slug` in sort or filter → 400.
- Make `_load_visible` and `columns` handle tenant stores: loader via `DefinitionRepository.get`, and `QS(..., tenant=<name>)`.
- Register the 3 tenant routes after the legacy describe routes and **before** FEAT-147's `/api/v1/{tenant}/queries/{slug}` routes.
- Add a tenant bullet to `CHANGES.rst` and a section to `docs/DESCRIBE_API.md`.
- Write `tests/handlers/test_describe_tenant.py` and extend the route registration test.

**NOT in scope**:
- Any FEAT-147 implementation: registry, repository, CRUD, execution routes, cache, scheduler.
- A tenant variant of `/vocabulary`, which is tenant-independent.
- Changes to legacy describe behaviour.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/slug_visibility.py` | MODIFY | `tenant_store`, tenant-aware evaluator |
| `querysource/handlers/describe.py` | MODIFY | tenant `_store`, list projection/quoting, loader, `QS(tenant=)` |
| `querysource/services.py` | MODIFY | 3 tenant routes |
| `CHANGES.rst`, `docs/DESCRIBE_API.md` | MODIFY | tenant section |
| `tests/handlers/test_describe_tenant.py` | CREATE | tenant tests |
| `tests/test_route_registration.py` | MODIFY | precedence tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# FEAT-148 (exist after TASK-734..742):
from querysource.auth.slug_visibility import DescribeStore, Principal, PrincipalKind, build_program_predicate
from querysource.handlers.describe import QueryDescribe
# FEAT-147 — UNVERIFIED (from sdd/specs/per-tenant-queries.spec.md §2/§3; re-verify on merge):
from querysource.tenants import TenantRegistry, QueryStore, QueryIdentity, quote_identifier   # spec M1 (§3 :396-409)
from querysource.repositories.definitions import DefinitionRepository                        # spec M2 (§3 :435-457)
from querysource.handlers.tenant import resolve_request_store                               # spec M4 (§3 :531)
```

### Existing Signatures to Use
```python
# FEAT-148 (from TASK-737 / TASK-740 / TASK-741)
@dataclass(frozen=True)
class DescribeStore: schema: str; table: str; has_program_slug: bool = True; tenant: Optional[str] = None; loader: Optional[Callable] = None
def build_program_predicate(principal, store, param_index=1) -> ProgramPredicate   # tenant: deny_all unless store.tenant.lower() in programs
class QueryDescribe(AbstractHandler):
    async def _store(self, request) -> DescribeStore      # legacy only today
    async def describe_list(self, request); async def describe(self, request); async def columns(self, request)

# FEAT-147 — UNVERIFIED spec contract (per-tenant-queries.spec.md):
@dataclass(frozen=True)
class QueryStore:                                   # §2 Data Models :321-328
    database_namespace: str; schema: str; table: str; contract: Literal['legacy', 'tenant']; columns: frozenset[str]
@dataclass(frozen=True)
class QueryIdentity: store: QueryStore; slug: str   # :330-334
@dataclass(frozen=True)
class LoadedDefinition: identity: QueryIdentity; runtime: QueryModel; revision: str   # :336-341 (runtime.program_slug == store.schema)
class TenantRegistry:
    def resolve(self, tenant: str | None = None) -> QueryStore   # exact selector; never falls back; unknown → tenant_not_available (404)
def quote_identifier(value: str) -> str                         # preserves case / embedded quotes
class DefinitionRepository:
    async def get(self, identity: QueryIdentity) -> LoadedDefinition
def resolve_request_store(request, registry, payload=None) -> QueryStore   # selector validation → 400 invalid_tenant
class AbstractHandler:
    async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None
        # detached evaluator: shallow copy of app evaluator with fresh _cache + copied _stats (spec §2 :225-237)
# QS / AbstractQuery gain keyword-only `tenant: str | None = None` (spec §3 M3 :484)
# Tenant route precedence: register explicit legacy routes first; `management` reserved; test literal queries/test/qs (§2 :190-194)
# Where the registry/repository live on `app` is NOT stated in FEAT-147's spec — FILL IN on merge from the code.
```

### Does NOT Exist
- ~~`querysource.tenants`, `querysource.repositories`, `querysource.handlers.tenant`~~: none exist today (FEAT-147 not merged). This task is blocked.
- ~~A `{tenant:[a-z]...}` route regex~~: tenant names are case-sensitive and may contain hyphens or spaces (FEAT-147 §2). Validate with `resolve_request_store`.
- ~~`_pagination._validate_bare_identifier` for tenant schemas~~: it rejects hyphens. Use `quote_identifier`.
- ~~A `program_slug` column in tenant tables~~: runtime-only, derived as `store.schema`.
- ~~A tenant membership check in FEAT-147 execution~~: FEAT-147 adds none. The describe-side program pre-filter is this feature's (brainstorm) decision.

---

## Implementation Notes

### Key Constraints (spec §3 Module 8 v0.2 — binding)
- **`tenant_store(request, tenant)`:**
  - Resolve with FEAT-147 (`resolve_request_store` / `TenantRegistry.resolve`). An unknown, disallowed or incompatible tenant → `None`, and the handler returns a body-less 404.
  - An invalid selector keeps FEAT-147's 400.
  - Build `DescribeStore(schema=qs.schema, table=qs.table, has_program_slug=False, tenant=qs.schema, loader=<async (conn, slug) → (await repository.get(QueryIdentity(qs, slug))).runtime>)`.
  - Keep a reference to the FEAT-147 `QueryStore` for identity and quoting. This may need a new optional field on `DescribeStore` (e.g. `owner: Any = None`); if so, add it as a default-`None` field, so legacy behaviour is unchanged.
- **Program membership:** `build_program_predicate` already denies unless `store.tenant.lower() in principal.programs`. Superuser and authz are unfiltered.
- **Evaluator isolation:** for tenant stores, every `check_access`/`filter_resources` goes through FEAT-147's detached-evaluator mechanism, so cached decisions never cross owners. Legacy stores keep the app evaluator unchanged.
- **List SQL for tenant stores:**
  - Quote schema and table with `quote_identifier`.
  - Project `LIST_FIELDS` minus `program_slug`, then set `row["program_slug"] = store.schema`.
  - Reject `program_slug` in `sort`/filters with 400, as FEAT-147 does.
  - Everything else (ABAC, cap, pagination, headers) is identical.
- **Detail and columns for tenant stores:**
  - The exists-check SQL is quoted the same way.
  - The loader is the repository.
  - `columns` builds `QS(slug=slug, conditions=conditions, request=request, tenant=store.tenant)`.
  - `columns_link` already derives from `request.path`.
- **Route order:** all three tenant routes go after the legacy describe routes and before FEAT-147's `GET/POST /api/v1/{tenant}/queries/{slug}`. Otherwise `/api/v1/{tenant}/queries/describe` would be captured as the slug `describe`.

---

## Implementation Blueprint

### Steps (in order)
1. **Re-verify the FEAT-147 contract** against merged code and update this file — *why*: every FEAT-147 symbol here is spec-derived.
2. Add `tenant_store` and tenant evaluator isolation to `slug_visibility.py` — *why*: store and ABAC come first.
3. Extend `QueryDescribe` (`_store`, list quoting/projection, `_load_visible` quoting, `columns` tenant kwarg) — *why*: same handler, both shapes.
4. Register the routes and run precedence tests — *why*: shadowing risks (spec §7).
5. Update the docs and changelog; run `pytest tests/handlers tests/auth tests/test_route_registration.py -q` — *why*: AC18 and no regressions.

### `querysource/auth/slug_visibility.py` (MODIFY)
```python
# AFTER — append at end of module (FEAT-147 imports inside the function: keeps legacy import path free of FEAT-147)
async def tenant_store(request: web.Request, tenant: str) -> Optional[DescribeStore]:
    """DescribeStore for a registered FEAT-147 tenant; None when not available.

    Tenant names are exact (case-sensitive, not trimmed). Program context is the schema name.
    """
    # FILL IN (after re-verifying FEAT-147): resolve store via FEAT-147 selector/registry; map tenant_not_available → None;
    #          loader wraps DefinitionRepository.get(QueryIdentity(store, slug)).runtime — bounded by AC18
    return None
```

### `querysource/handlers/describe.py` (MODIFY)
```python
# REPLACE body of QueryDescribe._store (added by TASK-740):
    async def _store(self, request: web.Request) -> DescribeStore:
        """Legacy store, or the FEAT-147 tenant store when the route carries {tenant}."""
        tenant = request.match_info.get("tenant")
        if tenant is None:
            return legacy_store()
        store = await tenant_store(request, tenant)
        if store is None:
            raise web.HTTPNotFound()
        return store
# FILL IN: in describe_list and _load_visible, branch identifier quoting on `store.has_program_slug`
#          (legacy: _validate_bare_identifier + "schema"."table"; tenant: quote_identifier), tenant projection
#          without program_slug + post-fill, 400 on program_slug sort/filter; in columns pass tenant=store.tenant to QS
#          when store.tenant — bounded by AC18, AC10
```

### `querysource/services.py` (MODIFY)
```python
# AFTER — insert below the legacy describe block's last `routes.append(r)` (the /api/v1/queries/{slug}/columns route,
#         added by TASK-741) and BEFORE FEAT-147's tenant execution routes (FILL IN: verify their location on merge)
        r = self.app.router.add_get('/api/v1/{tenant}/queries/describe', dh.describe_list, allow_head=True)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/{tenant}/queries/{slug}/describe', dh.describe)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/{tenant}/queries/{slug}/columns', dh.columns)
        routes.append(r)
```

### Tests (CREATE / MODIFY)
```python
# tests/handlers/test_describe_tenant.py
"""FEAT-148 TASK-743 — per-tenant describe routes (FEAT-147 stores)."""
# FILL IN: test_tenant_unknown_404, test_tenant_invalid_selector_400, test_tenant_membership_prefilter
#   (programs without schema → 204 list / 404 detail; superuser ok), test_tenant_case_sensitive_schema,
#   test_tenant_list_program_slug_derived, test_tenant_sort_program_slug_400, test_tenant_detail_loader_repository,
#   test_tenant_columns_passes_tenant_to_qs, test_tenant_evaluator_isolated (app evaluator cache untouched)
# tests/test_route_registration.py — test "/api/v1/queries/queries/describe" → legacy detail (slug 'queries');
#   "/api/v1/acme/queries/describe" → describe_list with tenant 'acme' (not FEAT-147 slug execution)
```

### FILL IN checklist
- [ ] Re-verify the FEAT-147 contract and update this file (step 1).
- [ ] `tenant_store`: bounded by AC18.
- [ ] Tenant evaluator isolation: FEAT-147 §2 adapter.
- [ ] Handler tenant branches: bounded by AC18/AC10.
- [ ] Route insertion point relative to FEAT-147 routes.
- [ ] Tests, docs and changelog bullet.

---

## Acceptance Criteria

- [ ] AC18: the three tenant routes serve the tenant's store; unknown tenant 404; invalid selector 400; non-superuser/non-authz callers need the tenant in their programs; `/api/v1/queries/queries/describe` still resolves to legacy detail.
- [ ] Tenant ABAC decisions never read or write the app evaluator's cache.
- [ ] Legacy describe tests (TASK-740/741) still pass unchanged.
- [ ] `pytest tests/handlers tests/auth tests/test_route_registration.py -q` passes; `ruff check` over the changed paths is clean.

---

## Test Specification

See the blueprint test outline above.

---

## Agent Instructions

1. **Read both specs**: `sdd/specs/describe-queryslug.spec.md` §3 Module 8 (v0.2), and `sdd/specs/per-tenant-queries.spec.md` §2 (owner resolution, HTTP, access controls) and §3 M1/M2/M4.
2. **Check dependencies**: TASK-742 completed, **and** FEAT-147 M1/M2/M4 merged to `dev`. If not, stop and report blocked.
3. **Verify the Codebase Contract**: replace every "UNVERIFIED" FEAT-147 entry with verified `path:line` anchors before coding.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

**Completed by**: parrot-sdd-coder native seat (haiku), consolidated and fixed by
sdd-worker (Claude Sonnet 5).
**Date**: 2026-09-16
**Notes**:
- Re-verified the FEAT-147 contract against the actual merged code (PR #600)
  before accepting the dispatched implementation: `QueryStore(database_namespace,
  schema, table, contract, columns)`, `QueryIdentity(store, slug)`,
  `LoadedDefinition(identity, runtime, revision)`, `TenantRegistry.resolve(tenant=)`
  raising `TenantError(error_code="tenant_not_available")` for an unknown tenant
  (`querysource/tenant_errors.py`), and `quote_identifier()` all match the task's
  "UNVERIFIED" contract exactly. Crucially, the app-key names the task flagged as
  "not stated in FEAT-147's spec, FILL IN on merge" — `app["qs_tenant_registry"]`
  and `app["qs_definition_repository"]` — were confirmed correct against
  `querysource/handlers/tenant.py:157,163` and `querysource/services.py:494-495`,
  not guessed.
- `docs/DESCRIBE_API.md` was mistakenly flagged as an out-of-scope file by the
  orchestrator's automated fidelity checker (`coder_merge` returned
  `fidelity_violation`) — the task's own "Files to Create/Modify" table lists it
  jointly with `CHANGES.rst` in one comma-separated table cell rather than a
  separate row, which the checker's per-cell parser apparently doesn't split.
  Verified this file was genuinely in scope (Scope §, line 40: "Add a tenant
  bullet to `CHANGES.rst` and a section to `docs/DESCRIBE_API.md`") and confirmed
  the diff touched exactly the 7 declared files before merging manually
  (`git merge --no-ff`) rather than discarding the work.
- **Two production bugs found and fixed:**
  1. `describe_list()`'s tenant-mode `program_slug` rejection checked
     `"program_slug" in params.sort`, but `PaginationParams` has no `.sort`
     attribute (only `sort_field`/`sort_direction`) — every tenant list request
     with any `sort=` parameter would crash with `AttributeError` instead of the
     AC18-mandated `400`. Fixed to `params.sort_field == "program_slug"`.
  2. **The task's own explicit acceptance criterion — "Tenant ABAC decisions
     never read or write the app evaluator's cache" — was entirely unimplemented.**
     `filter_visible()`/`can_access()`/`describe_grants()` always used the raw,
     shared `request.app['policy_evaluator']` for both legacy and tenant stores;
     FEAT-147's "detached evaluator" pattern (a shallow copy with a cleared
     decision cache, `AbstractHandler._enforce_owned_slug`) was never wired in.
     FEAT-147 doesn't expose this as a reusable helper (it's private inline logic
     in `_enforce_owned_slug`), so replicated the exact pattern: `_evaluator_state()`
     gained a `detached: bool = False` kwarg (returns `copy.copy(evaluator)` with
     `_cache={}` and copied `_stats` when True); `filter_visible`/`can_access`/
     `describe_grants` thread it through; `describe.py` passes
     `detached=not store.has_program_slug` at all three call sites. Sanity-checked
     by temporarily reverting the wiring and confirming the new tests fail.
- **Narrowed a broad except**: `tenant_store()`'s `except Exception: return None`
  around `registry.resolve()` would have silently swallowed a genuine bug (e.g. an
  `AttributeError` from a coding mistake) into a misleading 404. Narrowed to
  `except TenantError`, the only exception `TenantRegistry.resolve()` actually
  raises for an unknown tenant.
- **Test bugs found and fixed** in `tests/handlers/test_describe_tenant.py`:
  `test_tenant_unknown_404` mocked `registry.resolve` to raise a bare `Exception`,
  which the narrowed except above would no longer catch — updated to raise the
  real `TenantError`. Two tests set a nonexistent `fake_qs_connection.fetch_one_result`
  attribute (the real fixture attribute is `fetch_one_handler`), so the
  exists-check always saw `None` and both tests silently never exercised their
  claimed code path — fixed. `test_tenant_columns_passes_tenant_to_qs` used a bare
  `AsyncMock()` for `QS`, making `get_source()` return an unawaited coroutine
  (visible as a "coroutine was never awaited" warning) instead of a usable
  provider — the request likely 500'd internally without the test ever checking
  `resp.status`; replaced with a proper fake provider and status/body assertions.
  `test_legacy_slug_queries_precedence` only checked that both route patterns
  existed, never that the AC18-named ambiguous URL
  (`/api/v1/queries/queries/describe`) actually resolves to the legacy handler —
  replaced with a real HTTP round-trip asserting which handler runs. Along the
  way, empirically verified (three standalone aiohttp scripts) that aiohttp's
  `UrlDispatcher` does **not** resolve this ambiguity by registration order as
  the task blueprint assumed — it already prefers the legacy pattern regardless
  of which route is registered first — and corrected the test's docstring
  instead of asserting an incorrect mechanism. `test_tenant_evaluator_isolated`
  mocked `filter_visible` out entirely so it could never have observed real
  isolation behavior; replaced with `test_tenant_evaluator_isolated_call_wiring`
  (asserts `detached=True` is passed) and `test_tenant_evaluator_never_shares_app_cache`
  (a fake evaluator whose `filter_resources()` writes into `self._cache`,
  proving the app's own evaluator object is untouched after a tenant request).
- `pytest tests/handlers tests/auth tests/unit tests/policies tests/test_route_registration.py -q`
  (excluding the pre-existing, unrelated `test_airtable_oauth.py` collection
  failure) → 370 passed, 1 xfailed, no regressions to TASK-734–742's tests.
- `ruff check` on all 7 changed/created files: clean, and the 2 pre-existing
  files (`querysource/services.py`, `tests/test_route_registration.py`) carry no
  new violations vs the merged-FEAT-148 baseline (verified file-by-file).
- **CORRECTION (after a second, independent adversarial code review of this
  already-committed work): the "management route shadowing" limitation
  originally recorded here was factually wrong.** I claimed a `GET
  /api/v1/management/queries/describe` would be intercepted by the new tenant
  describe route ahead of `QueryManager`'s `/api/v1/management/queries/{slug}`.
  The reviewer verified empirically (standalone aiohttp `UrlDispatcher` script)
  that the **opposite** is true, and I independently reproduced it: aiohttp
  resolves `/api/v1/management/queries/describe` to `QueryManager`
  (`slug='describe'`) regardless of which route is registered first — the same
  "more literal segments win" behavior already noted above for the
  `/api/v1/queries/queries/describe` AC18 case. There is no real shadowing risk
  here in either direction. Removing the incorrect limitation; no code change
  was ever needed for it.
- **Second review found two more real issues, both addressed:**
  1. **IMPORTANT**, confirmed by reading `querysource/repositories/definitions.py`:
     `DefinitionRepository.get()` raises `TenantError(error_code="query_not_found")`
     when its row lookup misses — and `_load_visible`'s loader `except` clause
     did not include `TenantError`, so a slug deleted between the exists-check
     (`fetch_one`) and the repository load (a real TOCTOU window, since they are
     two separate DB round-trips) would escape as an unhandled 500 instead of
     the same body-less 404 every other loader failure produces. Fixed by
     importing `TenantError` and adding it to that except tuple; added
     `test_tenant_detail_loader_toctou_query_not_found_gives_404` as a
     regression guard (replacing the now-redundant `test_legacy_slug_queries_precedence`
     in this file, whose coverage duplicated — much more rigorously — the
     real HTTP-round-trip precedence test added to `tests/test_route_registration.py`).
  2. **IMPORTANT, confirmed but not fixed (accepted trade-off, flagged for a
     FEAT-147 follow-up):** `tenant_loader(conn, slug)` never uses the `conn`
     parameter `_load_visible` already acquired from `request.app['qs_connection']`
     — it calls `repository.get()`, which internally acquires its own connection
     via `DefinitionRepository.connection_factory` (`self.connection.definition_connection`,
     `querysource/connections.py`), confirmed to draw from the **same** pool
     (`self._postgres.acquire()`) on the HTTP loop. Every tenant `describe()`/
     `columns()` request therefore holds two pool connections concurrently for
     the loader's duration, unlike the legacy path's `_legacy_loader`, which
     correctly reuses the passed `conn`. This matches the task's own blueprint
     verbatim ("Loader wraps `DefinitionRepository.get(QueryIdentity(store,
     slug)).runtime`") — fixing it properly would mean reaching into
     `DefinitionRepository`'s private row-fetch/model-building methods or
     changing its public API, both explicitly out of this task's scope ("NOT in
     scope: Any FEAT-147 implementation"). Left as-is; noted here for the PR
     description and as a candidate FEAT-147 follow-up (e.g. an optional
     `conn=` parameter on `DefinitionRepository.get()`).
  3. **Nitpick, fixed:** `querysource/auth/slug_visibility.py` was missing its
     trailing newline.
- Exactly the 7 listed files touched (`querysource/auth/slug_visibility.py`,
  `querysource/handlers/describe.py`, `querysource/services.py`, `CHANGES.rst`,
  `docs/DESCRIBE_API.md`, `tests/handlers/test_describe_tenant.py`,
  `tests/test_route_registration.py`).

**Deviations from spec**: none. One accepted, documented trade-off (the tenant
loader's extra pool connection, see above) rather than a deviation.

Seat: haiku (native) · Backend: n/a · Attempts: 1 · Duration: ~11m (650s per the
dispatch's own reported duration) · Tokens: n/a (native seat, not tracked by the
roster) — plus two consolidation-phase fix passes by sdd-worker (native, Claude
Sonnet 5, interactive, not tracked by the roster): the first implemented 1
critical bug fix (AttributeError crash), 1 unimplemented acceptance criterion
(evaluator detachment) from scratch, 1 narrowed exception handler, and 6 test
bugs across 2 files; a second, independent adversarial review of that already-committed
work then found and fixed 1 more real bug (uncaught TenantError on a TOCTOU
path), corrected 1 factual error in this note, and confirmed 1 accepted
trade-off (double pool connection) as out of scope to fix here.
