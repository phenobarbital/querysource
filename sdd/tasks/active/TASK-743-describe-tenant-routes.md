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

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
