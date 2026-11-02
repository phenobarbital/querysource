---
type: feature
base_branch: dev
projects: [querysource, auth, handlers]
tags: [pbac, authorization, management-api, hardening]
---

# Feature Specification: PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback

**Feature ID**: FEAT-182
**Date**: 2026-10-29
**Author**: Juan Ruffato / Claude
**Status**: approved
**Target version**: 5.3.0
**Consumer**: FieldSync brainstorm `querysource-pbac-policies` (fieldsync repo,
branch `sdd/querysource-pbac-policies`). FieldSync will switch
`QS_PBAC_ENABLED=true` (staging first) and needs these gaps closed **inside
QuerySource**, not with a host middleware.

---

## 1. Motivation & Business Requirements

### Problem Statement

With `QS_PBAC_ENABLED=true`, QuerySource enforces PBAC on execution
(`slug:execute` → `datasource:use` → `driver:use`), raw SQL
(`raw_query:execute`), datasource/driver listing and describe. Three surfaces
are still not governable by policy:

1. **Management API has no PBAC.** `QueryManager` (`/api/v1/management/queries`
   and `/api/v1/management/queries/{slug}`: GET/POST/PUT/PATCH/DELETE) is full
   slug CRUD, and GET returns the definition **including `query_raw`, the SQL
   text**. It is gated only by `_enforce_scheduler_grant`
   (`handlers/manager.py:153`), which fires only for scheduled pg_admin
   pipelines. `QueryMoveView` (`/api/v1/management/queries/{slug}/move`) and
   `QueryStoreCatalog.list` (`/api/v1/management/query-stores`) have no check
   either. Any authenticated user can read, create, edit, move or delete any slug.
2. **The direct driver route is unusable under PBAC.**
   `/api/v2/queries/{driver}/{source}/{attribute}[/{var}]`
   (`services.py:335-342`) is served by `QueryService.query`. That handler has
   no `slug` path parameter, so `slug` is `None` (`handlers/service.py:180`), and
   the `slug:execute` check runs with an empty name. `enforce_request_pbac`
   (and `evaluate`) treat a missing `resource_name` as a deny, so the route
   returns **404 for everyone, admins included**, the moment PBAC is on. Hosts
   use it for legitimate non-SQL drivers (FieldSync's AgGrid widget calls
   `/api/v2/queries/http/rssapp/get_bundle`).
3. **Describe cannot be restricted while execution is open.** Listing and
   detail accept the hard-coded fallback `slug:execute`
   (`handlers/describe.py:138,236`). A host that must keep `slug:execute` open
   to every user (every dashboard runs slugs) therefore cannot restrict the
   slug catalogue to a group.

4. **Free SQL on the test endpoint has no PBAC** (found at task time, rev 1.1).
   `POST /api/v2/test/queries` (`services.py:181` → `QueryService.run_query`,
   `handlers/service.py:52`) takes an arbitrary `query` and `driver` from the
   body and runs them through `get_source(slug=None, query=…)` with **no
   `_enforce_pbac` call at all**. `QueryExecutor` (`/api/v1/queries/run`)
   already gates the same capability with `raw_query:execute`. This endpoint
   bypasses it.

A fifth, smaller issue: when `QS_PBAC_ENABLED=True` but `setup_pbac()` fails
(for example, bad YAML), QS logs a warning and **runs with enforcement off**
(`services.py:159-163`). That is a silent fail-open.

### Goals
- Every management endpoint is PBAC-checked with dedicated actions, so hosts
  can restrict slug CRUD and SQL visibility by policy.
- The direct driver route is evaluated against the **driver**, with its own
  action, so hosts can allow a harmless driver (`http`) and keep SQL drivers
  restricted without touching slug execution.
- Hosts can turn off the `slug:execute` fallback on describe/list.
- Hosts can make a failed PBAC bootstrap fatal.
- The shipped `policies/defaults.yaml` admin policy covers the new actions.

### Non-Goals (explicitly out of scope)
- Per-programme / per-tenant policy conditions (navigator-auth ignores
  `conditions.context`). That is a separate feature.
- Changing the execution chain (`slug:execute` → `datasource:use` → `driver:use`).
- Any FieldSync-side policy files. Those live in the fieldsync spec.
- The ABAC `uri:*` middleware.

---

## 2. Architectural Design

### Overview

All changes reuse the existing request gate (`auth/request_gate.py::enforce_request_pbac`,
FEAT-160): fail-closed, 404 on deny, no-op when PBAC is off. Back-compat rule:
**when PBAC is disabled, behaviour is byte-identical to today.** The new
flags default to today's behaviour.

New action vocabulary (strings; navigator-auth accepts free-form actions in YAML):

| Action | Resource | Checked by |
|---|---|---|
| `slug:view` | `slug:<slug>` (single) / `slug:*` for the list, `:meta` and the store catalogue | `QueryManager.get`, `QueryStoreCatalog.list` |
| `slug:manage` | `slug:<slug>` | `QueryManager.post/put/patch/delete`, `QueryMoveView.post` |
| `driver:query` | `driver:<driver>` | `QueryService.query` when the request carries no slug (direct driver route) |
| `raw_query:execute` (existing) | `raw_query:raw_query` | `QueryService.run_query` (`POST /api/v2/test/queries`), same resource name as `QueryExecutor` |

The management **list** is all-or-nothing (`slug:view` on `*`), not filtered
per item. `_paginate_list` paginates and counts in SQL
(`manager.py:452-503`), so a post-filter would corrupt `total` and pages.
Per-slug catalogue visibility is what the describe API (`filter_visible`) is
for.

`slug:read` is **not** reused: it already names the component catalogue
(`handlers/components.py:45,89`), and overloading it would let a component-doc
grant expose definitions.

### Component Diagram
```
management routes ──→ QueryManager / QueryMoveView / QueryStoreCatalog
                              │
                              └──→ enforce_request_pbac(slug, slug:view|slug:manage)
                                   filter_visible(slug:view)        (list)
/api/v2/queries/{driver}/… ──→ QueryService.query
                              ├─ slug present → slug:execute (unchanged)
                              └─ no slug      → driver:query on {driver}
describe list/detail ──→ filter_visible / can_access(primary, fallback?)
                              └─ fallback = "slug:execute" only if QS_DESCRIBE_EXECUTE_FALLBACK
QuerySource.setup ──→ setup_pbac → None? → raise if QS_PBAC_STRICT_BOOT
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `auth/request_gate.enforce_request_pbac` | uses | single-resource gate, 404 on deny |
| `auth/slug_visibility.filter_visible` | uses | list filtering; primary action only |
| `handlers/manager.QueryManager` | modifies | add checks to get/post/put/patch/delete |
| `handlers/query_moves.QueryMoveView` | modifies | `slug:manage` on the moved slug |
| `handlers/query_stores.QueryStoreCatalog` | modifies | `slug:view` on `*` |
| `handlers/service.QueryService` | modifies | no-slug branch → `driver:query` |
| `handlers/describe` | modifies | fallback conditional on flag |
| `services.QuerySource.setup` | modifies | strict boot flag |
| `conf.py` | extends | two new flags |
| `policies/defaults.yaml` | modifies | admin policy gets the new actions |

### Data Models
No new models. Two config flags:

```python
# querysource/conf.py
QS_DESCRIBE_EXECUTE_FALLBACK: bool  # default True (today's behaviour)
QS_PBAC_STRICT_BOOT: bool           # default False (today's behaviour)
```

### New Public Interfaces
None beyond the action names and flags above (documented in `docs/`).

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Management API gate | yes | actions/resources fixed in §2; reuse `enforce_request_pbac` + `filter_visible`; 404 on deny | — |
| M2: Driver-route gate | yes | `driver:query` on `match_info['driver']` when slug is None | — |
| M3: Describe fallback flag | yes | flag name/default fixed | — |
| M4: Strict boot | yes | flag name/default fixed; raise `RuntimeError` | — |
| M5: Policies + docs | yes | add actions to `admin_full_access` | — |

### Module 1: Management API gate
- **Paths**: `querysource/handlers/manager.py`, `querysource/handlers/query_moves.py`, `querysource/handlers/query_stores.py`
- **Responsibility**:
  - `GET …/queries/{slug}`, and `?slug=` / `slug:meta` on the collection: `slug:view` on that slug, then 404 on deny.
  - `GET …/queries` (list/paginate) and `GET …/queries:meta` (schema, no slug): `slug:view` on `*` (all-or-nothing; see §2).
  - The gate runs **before** the method's `try:` block, because `get()`'s `except Exception` (`manager.py:361`) would otherwise swallow the `HTTPNotFound` into a 500.
  - `POST`/`PUT`/`PATCH`/`DELETE`: `slug:manage` on the target slug, evaluated **before** any repository read or write and before `_enforce_scheduler_grant` (which stays as an additional check).
  - `QueryMoveView.post`: `slug:manage` on the slug before the move transaction.
  - `QueryStoreCatalog.list`: `slug:view` on `*`.
- **Depends on**: existing `enforce_request_pbac`, `filter_visible`.
- **Interface Skeleton**:
  ```python
  # querysource/handlers/manager.py (modifies)
  class QueryManager(QueryView):  # verified: manager.py:47
      async def _enforce_definition_access(self, slug: str | None, action: str) -> None:
          """slug:view / slug:manage gate; slug None → '*'. No-op when PBAC is off; 404 on deny."""
  ```

### Module 2: QueryService gates (driver route + test endpoint)
- **Path**: `querysource/handlers/service.py`
- **Responsibility**:
  - In `query()`, when `slug is None` (only the direct driver route reaches `query()` without a slug), evaluate `driver:query` with `ResourceType.DRIVER` on `args['driver']` instead of `slug:execute`. A missing driver name means 404 (the existing fail-closed rule). The later `datasource:use` / `driver:use` checks after provider resolution stay as they are.
  - `get_columns`, `columns` and `test_slug` are **not** changed: their routes always carry `{slug}`, so their `slug = None` branches are unreachable from HTTP.
  - In `run_query()`, gate `raw_query:execute` on `ResourceType.RAW_QUERY` / `"raw_query"` (same name as `QueryExecutor._enforce_payload`, `handlers/executor.py`) **before** `get_source`. Also gate `driver:use` on the body's `driver` (default `'db'`), mirroring the executor's driver check.
- **Depends on**: none.

### Module 3: Describe fallback flag
- **Paths**: `querysource/conf.py`, `querysource/handlers/describe.py`
- **Responsibility**: `QS_DESCRIBE_EXECUTE_FALLBACK` (bool, default `True`). When `False`, the describe detail/columns (`describe.py:138`) and list (`describe.py:236`) pass `fallback_action=None`. `slug:describe_raw` is unchanged (it already has no fallback).

### Module 4: Strict boot
- **Paths**: `querysource/conf.py`, `querysource/services.py`
- **Responsibility**: `QS_PBAC_STRICT_BOOT` (bool, default `False`). When `QS_PBAC_ENABLED` and the flag are both true and `setup_pbac()` returns no guardian, raise `RuntimeError` from `setup()` instead of the warning at `services.py:159-163`.

### Module 5: Policies and docs
- **Paths**: `policies/defaults.yaml`, `policies/HARDENING.md`, `docs/DESCRIBE_API.md`, `docs/PBAC_PROGRAMMATIC.md` (or the PBAC doc that lists actions), `CHANGES.rst`
- **Responsibility**:
  - Add `slug:view`, `slug:manage` and `driver:query` to `admin_full_access` in `policies/defaults.yaml`.
  - Document the new actions, both flags, and a hardening example: "catalogue and management for group X; execution open". The example must show the direct-driver allow (`driver:query` on `driver:http`).
  - Add a CHANGES entry that flags the behaviour change for hosts running PBAC: management and driver-route calls now need explicit grants, so `baseline.yaml`-style `*:*` floors keep working, while narrower setups must add the new actions.

---

## 4. Test Specification

Tests build requests with `aiohttp.test_utils.make_mocked_request`, with the
session installed the way navigator-session installs it. They evaluate against a **real
`PolicyEvaluator` loaded from YAML fixtures** (pattern:
`tests/policies/test_default_policies_load.py`), not a mocked `_enforce_pbac`.
Each new denial test is mutation-checked: revert the gate and the test goes red.

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_manager_get_single_denied_404` | M1 | user without `slug:view` → 404, no repo read |
| `test_manager_list_requires_view_star` | M1 | list/`:meta` → 404 without `slug:view` on `*`; with it, unchanged pagination |
| `test_manager_gate_not_swallowed` | M1 | denial is a 404, never the 500 from `get()`'s catch-all |
| `test_run_query_requires_raw_query` | M2 | `POST /api/v2/test/queries` → 404 without `raw_query:execute`; `get_source` never called |
| `test_manager_write_methods_require_manage` | M1 | POST/PUT/PATCH/DELETE → 404 without `slug:manage`; repo untouched |
| `test_manager_admin_full_access` | M1/M5 | shipped `defaults.yaml` admin passes all methods |
| `test_move_requires_manage` | M1 | move denied without `slug:manage` |
| `test_store_catalog_requires_view` | M1 | catalogue denied without `slug:view` |
| `test_management_pbac_off_unchanged` | M1 | no `app['security']` → identical responses to today |
| `test_driver_route_evaluates_driver_query` | M2 | `/api/v2/queries/http/...` allowed with `driver:query` on `driver:http`; `pg` denied |
| `test_driver_route_admin_not_blocked` | M2 | regression: admin no longer gets 404 for an empty slug name |
| `test_slug_route_unchanged` | M2 | slug present → still `slug:execute` |
| `test_describe_fallback_disabled` | M3 | flag False: user with only `slug:execute` sees no list/detail |
| `test_describe_fallback_default` | M3 | flag default: today's behaviour |
| `test_strict_boot_raises` | M4 | bad policy dir + strict → `RuntimeError`; non-strict → warning (today) |

### Integration Tests
| Test | Description |
|---|---|
| `test_management_routes_policy_matrix` | real app (`QuerySource.setup`) with PBAC on: matrix of {admin, group with view+manage, plain user} × {list, get, post, delete, move, catalogue, driver route http/pg, describe} |

---

## 5. Acceptance Criteria

- [ ] With PBAC on, a user lacking `slug:view` cannot read any definition or `query_raw` through `/api/v1/management/queries*` (404 / filtered list).
- [ ] With PBAC on, a user lacking `slug:manage` cannot create, update, patch, delete or move a slug, and the repository is untouched.
- [ ] `POST /api/v2/test/queries` requires `raw_query:execute` (and `driver:use` on the requested driver), like `/api/v1/queries/run`.
- [ ] `/api/v2/queries/{driver}/…` is decided by `driver:query` on the driver. An admin is allowed. `http` can be granted without granting `pg`.
- [ ] `QS_DESCRIBE_EXECUTE_FALLBACK=false` removes the `slug:execute` fallback from describe list/detail/columns.
- [ ] `QS_PBAC_STRICT_BOOT=true` makes a failed PBAC bootstrap abort startup.
- [ ] With PBAC off, every touched endpoint behaves exactly as before (tests prove it).
- [ ] `policies/defaults.yaml` admin policy includes the three new actions, and the shipped-policy load test passes.
- [ ] Docs and `CHANGES.rst` updated; version bumped to 5.3.0.
- [ ] `pytest tests/handlers tests/auth tests/policies` green.

---

## 6. Codebase Contract

Verified against QuerySource `dev` @ `b3bca15` (2026-10-29).

### Verified Imports
```python
from querysource.auth import ResourceType                       # manager.py:22
from querysource.auth.request_gate import enforce_request_pbac  # manager.py:24
from querysource.auth.slug_visibility import filter_visible     # slug_visibility.py:259
from querysource.auth.enforcement import resolve_evaluator, evaluate, build_eval_context
```

### Existing Class Signatures
```python
# querysource/auth/request_gate.py
async def enforce_request_pbac(request, resource_type, resource_name: str, action: str, *,
                               logger, session_resolver=None, allow_sessionless=None) -> None
#   no app['security'] → return; empty resource_name → HTTPNotFound; deny → HTTPNotFound

# querysource/auth/slug_visibility.py
async def filter_visible(... , primary_action: str, fallback_action: str | None = None)  # :259
async def can_access(... , primary_action: str, fallback_action: str | None = None)      # :329

# querysource/handlers/manager.py
class QueryManager(QueryView):                                   # :47
    async def _enforce_scheduler_grant(self, repo, identity, data) -> None  # :153
    async def get(self)      # :247
    async def patch(self)    # :513
    async def delete(self)   # :638
    async def put(self)      # :775
    async def post(self)     # :890

# querysource/handlers/query_moves.py
class QueryMoveView(QueryManager):                               # :32
    async def post(self) -> web.Response                         # :72

# querysource/handlers/query_stores.py
class QueryStoreCatalog:                                         # :67
    async def list(self, request: web.Request) -> web.Response   # :70

# querysource/handlers/service.py
#   query(): slug from args['slug'] (:173) or None (:180); slug:execute at :202-225
#   same None-slug pattern in get_columns (:431), columns (:559), test_slug (:699)
#   driver popped from options (:80-82)

# querysource/handlers/describe.py
#   detail fallback "slug:describe", "slug:execute" (:138); list "slug:list", "slug:execute" (:236)

# querysource/conf.py
QS_PBAC_ENABLED (:463) · QS_POLICY_PATH (:464) · QS_PBAC_CACHE_TTL (:465) · QS_PBAC_ALLOW_SESSIONLESS_AUTHZ (:475)

# querysource/services.py
#   management routes (:256-263); driver routes (:335-342); PBAC bootstrap warning (:159-163)
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `_enforce_definition_access` | `enforce_request_pbac` | call | `request_gate.py` |
| list filtering | `filter_visible` | call | `slug_visibility.py:259` |
| driver gate | `ResourceType.DRIVER` | enum/shim | `auth/_resource_types.py` |

### Does NOT Exist (Anti-Hallucination)
- ~~Any PBAC check in `QueryManager.get/post/put/patch/delete`~~ — only `_enforce_scheduler_grant`.
- ~~`slug:view`, `slug:manage`, `driver:query` actions~~ — new in this feature (navigator-auth's `ActionType` enum does not list them; YAML actions are free strings).
- ~~`QS_DESCRIBE_EXECUTE_FALLBACK`, `QS_PBAC_STRICT_BOOT`~~ — new.
- ~~A `slug` path parameter on `/api/v2/queries/{driver}/{source}/{attribute}`~~ — none.

### Edit Sites (Blueprint Anchors)
Verified against: `b3bca15`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | `class QueryManager(QueryView):` | `:47` | 1 |
| `querysource/handlers/query_moves.py` | MODIFY | `class QueryMoveView(QueryManager):` | `:32` | 1 |
| `querysource/handlers/query_stores.py` | MODIFY | `    async def list(self, request: web.Request) -> web.Response:` | `:70` | 1 |
| `querysource/handlers/service.py` | MODIFY | `                resource_name=slug,` (inside `query()`'s `else:` PBAC branch) | `:223` | 1 |
| `querysource/handlers/service.py` | MODIFY | `            identity = QueryIdentity(store=store, slug=slug)` | `:213` | 1 |
| `querysource/handlers/service.py` | MODIFY | `        conditions = {**options, **params}` + next line `self.logger.debug(f'Test Query: …')` (in `run_query`, just before `get_source`) | `:90-92` | substring of the `get_source` anchor matches 4× (`:93,307,443,588`); anchor on the `Test Query` debug line instead |
| `querysource/handlers/describe.py` | MODIFY | `"slug:list", "slug:execute",` | `:236` | 1 |
| `querysource/handlers/describe.py` | MODIFY | `"slug:describe", "slug:execute",` | `:138` | 1 |
| `querysource/conf.py` | MODIFY | `QS_PBAC_ENABLED = config.getboolean('QS_PBAC_ENABLED', fallback=False)` | `:463` | 1 |
| `querysource/services.py` | MODIFY | `"QS_PBAC_ENABLED=True but PBAC bootstrap returned None. "` | `:161` | 1 |
| `policies/defaults.yaml` | MODIFY | `      - "raw_query:execute"` | `:26` | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Reuse `enforce_request_pbac` / `filter_visible`; never re-implement evaluation.
- Deny = 404 (no enumeration), as in every existing QS gate.
- The PBAC-off fast path must stay a no-op with no extra DB reads.

### Known Risks / Gotchas
- **Behaviour change for PBAC-on hosts** (navigator-api runs PBAC always on). Management and direct-driver calls now need grants. navigator-api's `baseline.yaml` (`*:*` allow floor) keeps them working; document it in CHANGES.
- List filtering on large stores: `filter_visible` already handles batches (`QS_DESCRIBE_MAX_SCAN`). Reuse it; do not loop single checks.
- `driver:use` stays on the slug path. Do not restrict `driver:use` to protect the direct route; that is what `driver:query` is for.

### External Dependencies
None new (navigator-auth as already pinned).

---

## 8. Open Questions

- [x] Should `QS_DESCRIBE_EXECUTE_FALLBACK` eventually default to `False` (next major)? — *Owner: Juan (decided with our recommendation; Jesús validates at the end)*: not in this feature. The default stays `True` (today's behaviour) in 5.3.0. Flipping it is a breaking change for a later major, and Jesús may raise it at review.
- [x] Do navigator-api callers use the management API or the direct driver route with non-admin users? — *Owner: Juan (decided with our recommendation; Jesús/Javier validate at the end)*: non-blocking. navigator-api ships `baseline.yaml` (`*:*` allow for every authenticated user, priority 1), so the new `slug:view` / `slug:manage` / `driver:query` checks are allowed there exactly as today. The `CHANGES.rst` entry (M5) tells PBAC-on hosts without such a floor to grant the new actions before upgrading.

---

## 9. Design Research Cross-Check

Skipped: spec written directly from the FieldSync brainstorm's verified findings; no codex pass yet.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-29 | Juan Ruffato / Claude | Initial draft |
| 1.0 | 2026-10-29 | Juan Ruffato / Claude | Open questions resolved (non-blocking); approved |
| 1.1 | 2026-10-29 | Juan Ruffato / Claude | Task-time findings: `run_query` free-SQL gap added to M2; management list is all-or-nothing (SQL pagination); `get_columns`/`columns`/`test_slug` out of M2 (their routes always carry `{slug}`); gate must sit before `get()`'s catch-all |
