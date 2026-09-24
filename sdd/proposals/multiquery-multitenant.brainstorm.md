---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [handlers, multiquery, querysource]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [multi-tenant, multiquery, tenant-routes, slug-dispatch, dry-run, columns]
---

# Brainstorm: Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`

**Date**: 2026-09-24
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

FEAT-147 (`sdd/specs/per-tenant-queries.spec.md`, §2 route table, line 184) promises
that `GET, POST /api/v1/{tenant}/queries/{slug}` is a **"Unified stored single/multi
execution"** route, and that `HEAD, PATCH` on the same path gives **"Column inspection
with existing single/multi response semantics"**. The shipped handler does not honor
that contract.

`TenantQueryHandler.query()` (`querysource/handlers/tenant.py:228-249`) branches on
the *presence* of a slug, not on the *kind* of stored definition:

- slug present → `QueryService.query()` (single-query `QS` path, `handlers/service.py:134`)
- no slug → `QueryHandler.query()` (inline MultiQuery, `handlers/multi.py:196`)

A stored **MultiQuery definition** (a row with `provider='multi'` and a JSON
`query_raw` holding `queries` / `files` / `sources`) addressed by slug under a tenant
is therefore handed to `QS.build_provider()` (`queries/qs.py:167`), which resolves a
provider from `provider='multi'` and treats the JSON payload as a single query. The
request fails, while the exact same definition executes correctly through the
legacy, non-tenant `/api/v3/queries/{slug}` route (`services.py:244-262`), because
`QueryHandler` builds a `MultiQS` whose slug loader understands both shapes
(`queries/multi/__init__.py:216-260`).

The same gap exists on `columns()` (`tenant.py:251-264`) and `test_slug()`
(`tenant.py:266-277`): both delegate unconditionally to `QueryService`.

**Who is affected**: API consumers of tenant-scoped stored pipelines (the only route
that is tenant-aware); operators who cannot expose MultiQuery definitions per tenant
without falling back to the non-isolated v3 route; the FEAT-147 acceptance criterion
U1 ("new tenant handler executes single/multi queries"), which is currently only
half true. The tenant HTTP tests (`tests/tenants/test_tenant_http_routes.py:79`)
exercise the *inline* multi dispatch (no slug) and never a stored multi slug, which
is why the gap was not caught.

## Constraints & Requirements

Decisions taken during discovery (Rounds 0–2) are binding for the spec:

- **Flow**: `type: feature`, `base_branch: dev` (FEAT-147 is unreleased on `dev`).
- **Dispatch rule**: peek at the stored definition and route by kind. A definition is
  **multi iff `provider == 'multi'`** (the same predicate the scheduler uses at
  `querysource/scheduler/scheduler.py:317` and `:415`). `query_raw` sniffing is *not*
  the discriminator (MultiQS may still fall back internally, see edge cases).
- **Exact v2 parity for single slugs**: a single-query slug on the tenant route keeps
  `QueryService` semantics byte-for-byte (headers, Redis cache, 204/404 mapping,
  `_download`/`_filename`, `queryformat`, `X-Slug`), as today.
- **No double read**: the definition loaded by the dispatcher must be **threaded into
  `QS` / `MultiQS`** so `build_provider()` / the MultiQS slug loader do not issue a
  second `SELECT` for the same identity. Identity and revision must still land on
  `_definition_identity` / `_definition_revision` (`interfaces/queries.py:104-107`),
  because result-cache keys and ownership logging depend on them.
- **Scope of routes**: `GET/POST {slug}` execution, `HEAD/PATCH {slug}` columns and
  `GET/POST {slug}/test` dry-run all become kind-aware. `/api/v3/queries` stays
  non-tenant (out of scope).
- **Multi dry-run**: validate without executing — parse the multi JSON, resolve every
  saved child under the tenant (inheritance and explicit `tenant`/`null` overrides as
  MultiQS does at `queries/multi/__init__.py:315-333`), run the ownership preflight,
  and report per-child status. No datasource query runs, no `EXPLAIN`.
- **Multi columns**: add a `columns` array column to the tenant `queries` table (and
  the corresponding model field). `HEAD/PATCH {slug}` on a multi definition returns
  that declared list; when it is empty, answer `204` with `X-Message: No Columns
  available` exactly like v3 (`handlers/multi.py:188`). Describing the *resulting*
  frame is an explicit follow-up, not this feature.
- Tenant selector never leaks into conditions (FEAT-147 AC-4): keep the
  `request['qs_tenant']` convention; never merge tenant into params.
- PBAC: single slugs keep `_enforce_owned_slug` (`handlers/abstract.py:463`); multi
  definitions keep `_preflight_multiquery` (`handlers/multi.py:27`) plus the
  owned-slug preflight on real saved children (`handlers/multi.py:103`), with the
  tenant-isolated evaluator copy.
- Legacy routes (`/api/v2/...`, `/api/v3/...`) and apps without
  `app["qs_tenant_registry"]` must be behaviorally unchanged.
- Conventions: async everywhere, `self.logger`, Google docstrings, strict typing,
  `ruff` gate, tests under `tests/tenants/`.

---

## Options Explored

### Option A: Definition-peek dispatcher in `TenantQueryHandler` + pre-loaded definition threading

The tenant handler resolves the store (`_resolve_or_raise`), loads the definition
**once** through `DefinitionRepository.get(QueryIdentity(store, slug))`
(`repositories/definitions.py:161`), and dispatches on `loaded.runtime.provider`:

- `provider == 'multi'` → `QueryHandler` (MultiQuery path)
- anything else → `QueryService` (single path)

The `LoadedDefinition` is stashed on the request (e.g. `request['qs_definition']`,
alongside the existing `request['qs_tenant']`) and forwarded by both delegates into a
new keyword-only `definition: LoadedDefinition | None = None` argument on `QS` and
`MultiQS`. When present, `QS.build_provider()` skips `repo.get()` and uses
`definition.runtime`, `definition.identity`, `definition.revision`; the MultiQS slug
loader does the same instead of `get_slug()`. Absent → current behavior (legacy
callers, scheduler, Python API untouched).

`columns()` uses the same peek: multi → declared `columns` list or 204; single →
`QueryService.get_columns` / `columns`. `test_slug()` uses the same peek: multi → a new
validate-only dry-run on `QueryHandler`; single → `QueryService.test_slug`.

The `columns` array is added to `TenantQueryDefinition` (`tenant_models.py:24`) **and**
`QueryModel` (`models.py:48`, `Meta.strict = True` at `:105` means the runtime model
rejects unknown keys, so both must declare it), to the documented DDL
(`docs/PER_TENANT_QUERIES.md:40`) and the test DDL fixture
(`tests/tenants/conftest.py:22`).

✅ **Pros:**
- Honors the spec contract literally; single slugs keep exact v2 parity.
- One definition read per request (the peek *is* the read QS/MultiQS would do).
- Discriminator identical to the scheduler's → one definition of "multi" across the
  codebase.
- Legacy routes and Python API are unaffected: the new kwarg defaults to `None`.
- The stashed `LoadedDefinition` gives the describe handler (FEAT-148) a reusable
  hook later.

❌ **Cons:**
- Touches the query core (`queries/qs.py`, `queries/multi/__init__.py`) and the
  models, not only the handler: larger blast radius than a handler-only fix.
- Introduces a schema addition (`columns`) that must be rolled out to every tenant
  store before writes that include it can succeed.
- Two places must agree on the request-key convention (`qs_tenant`, `qs_definition`).

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | handlers, request-scoped storage | already a dependency |
| `asyncdb` (`pg`) | `DefinitionRepository` reads | already a dependency |
| `datamodel` | `TenantQueryDefinition` / `QueryModel` field | Cython validator: no `X \| None` / `list[X]` syntax in `tenant_models.py` (see module docstring) |
| `pytest`, `pytest-asyncio` | unit + opt-in integration tests | existing |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/tenant.py:127` `_resolve_or_raise` — store resolution and 400/404 mapping.
- `querysource/handlers/tenant.py:162` `_repository` — app-published `DefinitionRepository`.
- `querysource/repositories/definitions.py:161` `DefinitionRepository.get` — the single read.
- `querysource/queries/qs.py:167-186` — slug branch of `build_provider()` to short-circuit.
- `querysource/queries/multi/__init__.py:216-260` — MultiQS slug loader to short-circuit.
- `querysource/queries/multi/__init__.py:285-333` — child tenant resolution + repo preflight (basis for the validate-only dry-run).
- `querysource/handlers/multi.py:27` / `:103` — PBAC preflights to reuse in the dry-run.
- `querysource/handlers/multi.py:188` — 204 "No Columns available" response.
- `querysource/scheduler/scheduler.py:317` — canonical `provider == "multi"` predicate.

---

### Option B: Mirror v3 — always delegate slug requests to `QueryHandler`

Replace the slug branch in `TenantQueryHandler.query()` with a call to
`QueryHandler.query()`, exactly as `/api/v3/queries/{slug}` does. `MultiQS` already
loads the slug under the tenant selector and wraps a non-multi definition in its
single-query executor (`queries/multi/__init__.py:252-260`).

✅ **Pros:**
- Smallest diff (a handful of lines in `tenant.py`, one test).
- No schema or query-core changes.

❌ **Cons:**
- Single slugs lose v2 parity: no Redis result cache, different error envelope,
  `X-Slug` handling, `_download`/`_filename`, grouping/filter post-processing
  (`handlers/multi.py:490-560`) are v3 semantics. Rejected in Round 1c.
- `HEAD/PATCH` would collapse to `204` for every definition (`multi.py:188`), a
  regression for single slugs.
- No dry-run at all for the test route (MultiQS has no `dry_run`).

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | handler delegation | existing |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/multi.py:196` `QueryHandler.query`.
- `querysource/services.py:244-262` v3 registration, for parity reference.

---

### Option C: Caller-declared kind (suffix or query parameter)

Keep both delegates but let the caller say which one to use: `GET
/api/v1/{tenant}/queries/{slug}:multi` (the slug/format suffix parser at
`service.py:170` and `multi.py:206` already splits on `:`) or `?kind=multi`.

✅ **Pros:**
- No definition peek, no extra read, no schema change.
- Explicit and cache-friendly at the edge.

❌ **Cons:**
- Pushes a storage detail onto every client; the spec says "unified", not "declared".
- Conflicts with the existing `slug:format` suffix convention (`slug:csv`), so a
  second delimiter or a reserved word is needed.
- A mismatch between the declared kind and the stored row still produces the
  current failure. Rejected in Round 1a.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | match_info / query parsing | existing |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/service.py:168-173` and `handlers/multi.py:204-208` — suffix parsing.

---

### Option D (unconventional): Kind-aware factory in the query layer

Move the decision below the handlers: a classmethod such as
`QS.from_definition(loaded, ...)` returning a `QS` or a `MultiQS`, driven by
`provider`. Handlers, the scheduler (`scheduler/jobs.py:104`) and the Python API would
all obtain the right executor from one place.

✅ **Pros:**
- One definition of "which executor" for HTTP, scheduler and library callers.
- Removes the duplicated `provider == "multi"` checks over time.

❌ **Cons:**
- The HTTP response conventions still live in two handlers, so the factory alone
  does not solve v2 parity; the handler-level dispatch of Option A is still needed.
- Circular-import risk between `queries/qs.py` and `queries/multi/__init__.py`
  (`queries/__init__.py:6-7` imports both).
- Broadens scope into the scheduler and public API for a bug that is handler-local.

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| — | pure refactor | — |

🔗 **Existing Code to Reuse:**
- `querysource/queries/__init__.py:6-7` exports; `querysource/scheduler/jobs.py:104`.

---

## Recommendation

**Option A** is recommended because:

- It is the only option that satisfies both binding decisions at once: **exact v2
  parity for single slugs** (Option B breaks it) and **no caller-declared kind**
  (Option C). Option D solves a different problem and still needs A's dispatch.
- The extra cost is not an extra query: the peek replaces the read `QS`/`MultiQS`
  would perform anyway, provided the `LoadedDefinition` is threaded through. The
  trade-off is a small, additive change in the query core (a keyword-only argument
  defaulting to `None`), which is what Round 2b explicitly chose over a per-request
  memo or a duplicate read.
- Using `provider == 'multi'` as the sole discriminator keeps one definition of
  "multi" with the scheduler. The trade-off (a JSON multi payload saved under
  `provider='db'` executes as single and fails) is accepted and documented; it is a
  data-quality problem the describe/list surfaces can flag later.
- The `columns` column is the price of giving `HEAD/PATCH` something meaningful for
  a pipeline without executing it. It is additive (`SELECT *` on a store without the
  column just yields the default), and the follow-up "describe the resulting frame"
  can populate it.

---

## Feature Description

### User-Facing Behavior

- `GET|POST /api/v1/{tenant}/queries/{slug}` executes the stored definition whatever
  its kind. A single-query definition responds exactly as `/api/v2/services/queries/{slug}`
  does today; a MultiQuery definition responds exactly as `/api/v3/queries/{slug}`
  does today, but resolved under the URL tenant (parent and inheriting children).
  Output suffixes (`slug:csv`, `?queryformat=`) keep working on both kinds.
- `HEAD|PATCH /api/v1/{tenant}/queries/{slug}`: single → existing column inspection;
  multi → `200` with the definition's declared `columns` list, or `204` with
  `X-Message: No Columns available` when the list is empty or absent.
- `GET|POST /api/v1/{tenant}/queries/{slug}/test`: single → existing dry-run with
  `EXPLAIN`; multi → a validate-only report: the parsed pipeline shape (aliases,
  files, sources), one entry per saved child with its resolved owner, existence and
  ownership result, and an overall `works` flag. Nothing is executed.
- Error mapping is unchanged: unknown tenant → 400/404 via `_resolve_or_raise`;
  missing slug → the `query_not_found` TenantError code (404); PBAC deny → 404
  (fail-closed), as today.
- Legacy `/api/v2` and `/api/v3` routes, the scheduler and `QS(...)`/`MultiQS(...)`
  called from Python behave exactly as before.

### Internal Behavior

1. **Resolve and peek** — `TenantQueryHandler` resolves the store, builds a
   `QueryIdentity`, loads the `LoadedDefinition` through the app-published
   `DefinitionRepository`, and stashes both the tenant selector and the loaded
   definition on the request (`qs_tenant`, plus a new request key for the
   definition). Lookup failures map to the existing TenantError envelope before any
   delegate runs.
2. **Classify** — a single helper (handler-side) answers "is multi" from
   `loaded.runtime.provider == 'multi'`. It is the only place that decides.
3. **Delegate** — multi → `QueryHandler.query`; otherwise `QueryService.query`. Both
   delegates read the stashed definition and pass it to `MultiQS(..., definition=...)`
   / `QS(..., definition=...)` next to the existing `tenant=` keyword.
4. **Short-circuit the second read** — `QS.build_provider()` and the MultiQS slug
   loader use the supplied definition's `runtime`, `identity` and `revision` instead
   of calling the repository; `_definition_identity` / `_definition_revision` are set
   exactly as they are today so cache keys and ownership logging are unchanged. When
   no definition is supplied, the current loading code path runs untouched.
5. **Columns** — multi → read `runtime.columns`; empty → the v3 204 response.
6. **Dry-run (multi)** — parse `runtime.query_raw`, normalize `sources` the way
   MultiQS does, resolve each saved child's owner (inherit the URL tenant unless the
   child declares `tenant` or `tenant: null`), check existence via the repository and
   ownership via the tenant-isolated PBAC evaluator, and return the report. Files and
   raw inline children are listed but not checked (existing convention).
7. **Schema/model** — `columns` (array of text, default empty) is declared on
   `TenantQueryDefinition` and `QueryModel`, documented in the provisional DDL and
   added to the test fixture DDL. `_TENANT_COLUMNS` picks it up automatically because
   it is derived from the model.

### Edge Cases & Error Handling

- **`provider='multi'` but `query_raw` is not a multi JSON payload** → still routed to
  `QueryHandler`; MultiQS falls back to single-query mode internally (v3 and scheduler
  behavior, `docs/QSSCHEDULER.md:80-88`). The dry-run reports this as a definition
  warning instead of a child list.
- **Multi JSON saved under `provider='db'`** → treated as single (fails inside QS as
  today). Accepted by decision; document it and surface it in the describe/list
  output later.
- **Slug missing in the tenant store** → 404 with the `query_not_found` code from the
  peek; the delegate is never invoked, so no partial PBAC evaluation happens.
- **Child definitions** may live in a different tenant (`tenant: "other"`) or in the
  legacy store (`tenant: null`); the dry-run resolves each independently and reports
  the resolved owner per child, mirroring execution.
- **Store without the `columns` column** → reads work (row lacks the key → default
  `[]`, HEAD returns 204); a write that includes `columns` fails with the existing
  `tenant_store_unavailable`/driver error mapping (`repositories/definitions.py:_translate_write_error`).
  Deployment order: model first, DDL second.
- **Legacy callers and Python API** never pass `definition=`; their behavior is
  covered by regression tests, and `get_slug()` (`interfaces/connections.py:526`)
  stays as the compatibility loader.
- **Definition mutated between peek and execution** → not a concern: the loaded
  definition is a detached snapshot with a `revision`; execution uses that snapshot,
  the same guarantee `LoadedDefinition` gives today.
- **App without tenant feature** (`qs_tenant_registry` absent) → the tenant routes
  already return 404 "Tenant feature is not configured"; unchanged.

---

## Capabilities

### New Capabilities
- `tenant-kind-aware-slug-dispatch`: `TenantQueryHandler` classifies a stored
  definition by `provider` and delegates to the single or multi execution handler for
  query, columns and test routes.
- `preloaded-definition-execution`: `QS` and `MultiQS` accept an already loaded
  `LoadedDefinition` and skip the repository read while preserving identity/revision.
- `multi-definition-dry-run`: validate-only test route for stored MultiQuery
  definitions (children resolution, existence, ownership; no execution).
- `multi-definition-columns`: `columns` array on tenant definitions, returned by
  HEAD/PATCH for multi definitions (204 when empty).

### Modified Capabilities
- `per-tenant-queries` (FEAT-147): route table rows for `{slug}` execution, columns
  and test become true for stored multi definitions; AC U1 fully satisfied.
- `qsscheduler-multi-support` (FEAT-092): no behavior change, but the `provider ==
  'multi'` predicate is now shared vocabulary; worth cross-referencing in docs.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/handlers/tenant.py` | modifies | peek + classify + dispatch in `query`, `columns`, `test_slug`; stash definition on request |
| `querysource/handlers/service.py` | modifies | `query`, `get_columns`, `columns`, `test_slug` forward the stashed definition to `QS` |
| `querysource/handlers/multi.py` | modifies / extends | `query` forwards definition to `MultiQS`; new multi columns + validate-only dry-run methods |
| `querysource/queries/qs.py` | extends | keyword-only `definition` argument; `build_provider()` short-circuit |
| `querysource/queries/multi/__init__.py` | extends | keyword-only `definition` argument; slug loader short-circuit |
| `querysource/interfaces/queries.py` | extends | store the supplied definition next to `_tenant_selector` (base for both executors) |
| `querysource/tenant_models.py`, `querysource/models.py` | extends | `columns: List[str]` array field on both models (strict runtime model) |
| `querysource/repositories/definitions.py` | depends on | `_TENANT_COLUMNS` derives from the model; no code change expected |
| `docs/PER_TENANT_QUERIES.md`, `tests/tenants/conftest.py` | modifies | DDL gains `columns text[]` |
| `tests/tenants/test_tenant_http_routes.py` | extends | stored-multi dispatch, columns and dry-run cases; regression for single parity |
| `/api/v2`, `/api/v3`, scheduler, Python API | unchanged | must be covered by regression tests (definition kwarg defaults to `None`) |

No new dependencies. No breaking API change. One additive schema change per tenant
store (deploy model before DDL).

---

## Code Context

### User-Provided Code

```python
# Source: user-provided (invocation notes, paraphrased): /api/v1/{tenant}/queries/{slug}
# only dispatches QS queries, not MultiQuery definitions to QueryHandler/MultiQS as the
# spec's route table promises (handlers/tenant.py:240-245); it should be the integrated
# multi-tenant route for both kinds, like /api/v3/queries which is not multi-tenant.
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/handlers/tenant.py:19
def resolve_request_store(request: web.Request, registry: TenantRegistry, payload: Mapping[str, Any] | None = None) -> QueryStore: ...
# From querysource/handlers/tenant.py:127
def _resolve_or_raise(registry: TenantRegistry, tenant: str | None) -> QueryStore: ...
# From querysource/handlers/tenant.py:142
class TenantQueryHandler(AbstractHandler):
    def _registry(self, request: web.Request) -> TenantRegistry: ...          # line 156, app["qs_tenant_registry"]
    def _repository(self, request: web.Request) -> DefinitionRepository: ...  # line 162, app["qs_definition_repository"]
    async def list(self, request: web.Request) -> web.StreamResponse: ...    # line 168
    async def query(self, request: web.Request) -> web.StreamResponse: ...   # line 228; sets request["qs_tenant"]; slug → QueryService (242), else QueryHandler (246)
    async def columns(self, request: web.Request) -> web.StreamResponse: ... # line 251; HEAD → get_columns, else columns (QueryService only)
    async def test_slug(self, request: web.Request) -> web.StreamResponse: ... # line 266; QueryService.test_slug only

# From querysource/handlers/service.py:31
class QueryService(AbstractHandler):
    async def query(self, request): ...        # line 134; reads request.get('qs_tenant') (200, 306); get_source(..., tenant=tenant)
    async def get_columns(self, request): ...  # line 411
    async def columns(self, request): ...      # line 506
    async def test_slug(self, request: web.Request) -> web.StreamResponse: ...  # line 632; dry_run() + EXPLAIN ANALYZE

# From querysource/handlers/multi.py:25
class QueryHandler(AbstractHandler):
    async def _preflight_multiquery(self, request: web.Request, slugs: list, files: list, has_raw_query: bool) -> None: ...        # line 27
    async def _preflight_multiquery_owned(self, request: web.Request, slugs: list, files: list, has_raw_query: bool) -> None: ...  # line 103
    async def columns(self, request: web.Request) -> web.StreamResponse: ...  # line 188; always no_content(204, X-Message: 'No Columns available')
    async def query(self, request: web.Request) -> web.StreamResponse: ...    # line 196; MultiQS(slug=..., ..., tenant=request.get('qs_tenant')) at 335

# From querysource/handlers/abstract.py:270
async def get_source(self, request, slug, conditions, **kwargs) -> QS: ...  # builds QS(slug=..., conditions=..., request=..., lazy=False, **kwargs)
# From querysource/handlers/abstract.py:323
async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None: ...
# From querysource/handlers/abstract.py:463
async def _enforce_owned_slug(self, request: web.Request, identity: QueryIdentity, action: str) -> None: ...

# From querysource/queries/qs.py:35
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None, **kwargs): ...
    # build_provider(): slug branch at line 167 — repo = await self.get_definition_repository();
    # store = repo.registry.resolve(self._tenant_selector); loaded_def = await repo.get(identity);
    # sets self._definition_identity / self._definition_revision (lines 175-180); objquery = loaded_def.runtime
    async def dry_run(self): ...  # line 555

# From querysource/queries/multi/__init__.py:90
class MultiQS(BaseQuery):
    def __init__(self, slug: str = None, queries: list | None = None, files: list | None = None,
                 query: dict | None = None, conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, user_session: object | None = None,
                 *, tenant: str | None = None, **kwargs): ...
    # slug loader in query(): line 216-260 — query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector);
    # multi iff parsed query_raw dict has 'queries' | 'files' | 'sources' (line 235); else single-query wrapper (252-260)
    # child tenant resolution + repo.get preflight: lines 285-333 ("tenant" key: explicit str / explicit None / inherit)

# From querysource/queries/base.py:19
class BaseQuery(AbstractQuery):
    def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None, **kwargs): ...

# From querysource/interfaces/queries.py:41
class AbstractQuery(Connection):
    _tenant_selector: str | None        # line 104
    _definition_identity: Any           # line 106
    _definition_revision: str | None    # line 107

# From querysource/interfaces/connections.py:438
async def get_definition_repository(self) -> "DefinitionRepository": ...
# From querysource/interfaces/connections.py:458
async def get_query_slug(self, slug: str, evt=None, max_retries: int = 3, *, tenant: str | None = None) -> BaseModel: ...  # returns loaded.runtime only
# From querysource/interfaces/connections.py:526
async def get_slug(self, slug: str, program: str = None, evt=None, *, tenant: str | None = None): ...  # raises SlugNotFound

# From querysource/tenants.py:35
@dataclass(frozen=True)
class QueryStore: database_namespace: str; schema: str; table: str; contract: Literal["legacy", "tenant"]; columns: frozenset[str]
# From querysource/tenants.py:46
@dataclass(frozen=True)
class QueryIdentity: store: QueryStore; slug: str
# From querysource/tenants.py:54
@dataclass(frozen=True)
class LoadedDefinition: identity: QueryIdentity; runtime: QueryModel; revision: str
# From querysource/tenants.py:80
class TenantRegistry:
    async def discover(self, conn, allowlist=...) -> ...  # line 158; requires only "query_slug" (146-149)
    def resolve(self, tenant: str | None = None) -> QueryStore: ...  # line 402

# From querysource/repositories/definitions.py:56
class DefinitionRepository:
    def __init__(self, registry: TenantRegistry, connection_factory: Callable[[], Awaitable[AbstractAsyncContextManager[pg]]]) -> None: ...
    def _row_to_persisted(self, row, store) -> tuple[dict, str | None]: ...  # line 88; TenantQueryDefinition(**data) rejects unknown keys
    def _runtime_model(self, persisted, store, legacy_program_slug) -> QueryModel: ...  # line 111; QueryModel(**persisted, program_slug=...)
    async def _fetch_row(self, store: QueryStore, slug: str) -> Mapping[str, Any] | None: ...  # line 121; SELECT * ... WHERE query_slug = $1
    async def get(self, identity: QueryIdentity) -> LoadedDefinition: ...  # line 161; TenantError("query_not_found") when missing
# From querysource/repositories/definitions.py:41
_TENANT_COLUMNS: frozenset = frozenset(TenantQueryDefinition(query_slug="__probe__").columns().keys())

# From querysource/tenant_models.py:24
class TenantQueryDefinition(BaseModel):  # every QueryModel field except program_slug; NO `columns` field today
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)
    provider: str = Field(required=False, default='db')
    query_raw: str = Field(required=False)

# From querysource/models.py:48
class QueryModel(Model):
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)  # line 64
    provider: str = Field(required=False, default='db')                               # line 74
    class Meta: strict = True                                                          # lines 101-105

# From querysource/scheduler/scheduler.py:317 and :415
if provider == "multi": ...            # canonical "stored multi definition" predicate
if row.get("provider") == "multi": return None
```

#### Verified Imports
```python
from querysource.handlers import TenantQueryHandler, QueryService, QueryHandler  # querysource/handlers/__init__.py:10-13
from querysource.handlers.abstract import AbstractHandler                        # querysource/handlers/tenant.py:13
from querysource.repositories import DefinitionRepository                        # querysource/handlers/tenant.py:14
from querysource.tenant_errors import TenantError                                # querysource/handlers/tenant.py:15
from querysource.tenants import QueryStore, TenantRegistry, QueryIdentity, LoadedDefinition  # tenant.py:16; repositories/definitions.py:22-29
from querysource.tenant_models import TenantQueryDefinition                      # querysource/repositories/definitions.py:21
from querysource.queries import QS, MultiQS                                      # querysource/queries/__init__.py:6-7
from querysource.models import QueryModel                                        # querysource/repositories/definitions.py:19
```

#### Key Attributes & Constants
- `request["qs_tenant"]` → `str | None` — set by `TenantQueryHandler` (`tenant.py:238`), read by `QueryService.query` (`service.py:200`) and `QueryHandler` (`multi.py:143, 332`).
- `request.app["qs_tenant_registry"]` → `TenantRegistry`; `request.app["qs_definition_repository"]` → `DefinitionRepository` (`tenant.py:157, 163`).
- `LoadedDefinition.runtime.provider` → `str` (`'db'` default, `'multi'` for pipelines).
- `MultiQS._tenant_selector`, `QS._tenant_selector` → `str | None` (`interfaces/queries.py:104`).
- Route registration: tenant routes `services.py:365-398`; v3 multi routes `services.py:244-262`; v2 single routes `services.py:171-189`.
- Existing test pattern: `tests/tenants/test_tenant_http_routes.py` (MagicMock request with a `storage` dict, `_mock_registry()`, monkeypatched `QueryService`/`QueryHandler`, lines 11-41, 79-124, 162-208).
- DDL fixture: `tests/tenants/conftest.py:22-32` (`_TENANT_QUERIES_DDL`); documented DDL `docs/PER_TENANT_QUERIES.md:40-50`.

### Does NOT Exist (Anti-Hallucination)
- ~~`MultiQS.dry_run()`~~ — only `QS.dry_run()` (`queries/qs.py:555`) and `QueryExecutor.dry_run()` (`queries/executor.py:66`) exist.
- ~~`QueryHandler.test_slug()`~~ / ~~`QueryHandler.get_columns()`~~ — `QueryHandler` has only `columns()` (204) and `query()`.
- ~~`QS(definition=...)`~~ / ~~`MultiQS(definition=...)`~~ — no pre-loaded-definition argument exists today; it is the new capability.
- ~~`request["qs_definition"]`~~ — no such request key today; only `qs_tenant` is set.
- ~~`LoadedDefinition.is_multi`~~ / ~~`QueryModel.is_multi`~~ / ~~`QueryModel.query_type`~~ — the only marker is `provider == 'multi'`.
- ~~`columns` column on `{schema}.queries`~~ / ~~`TenantQueryDefinition.columns`~~ / ~~`QueryModel.columns` field~~ — do not exist (note: `QueryModel.columns()` is the `datamodel` *method* returning the field map, `definitions.py:84`; the new field name must not shadow it — see Open Questions).
- ~~`DefinitionRepository` per-request cache~~ — every `get()` is one `SELECT`.
- ~~`BaseQuery.get_slug`~~ in `queries/base.py` — `get_slug` lives on the `Connection` interface (`interfaces/connections.py:526`).
- ~~`/api/v3/queries` tenant selector~~ — v3 never sets `qs_tenant` (out of scope by decision).
- ~~`TenantQueryHandler.get_columns`~~ — the HEAD branch calls `QueryService.get_columns` inside `columns()`.

---

## Parallelism Assessment

- **Internal parallelism**: Two independent foundations, then dependent handler work.
  (1) `definition=` kwarg on `QS`/`MultiQS`/`AbstractQuery` (queries layer) and
  (2) `columns` field + DDL/docs/fixture (models layer) do not share files. (3) the
  handler dispatch (`tenant.py`, `service.py`, `multi.py` forwarding) depends on (1);
  (4) multi columns + validate-only dry-run (`multi.py`, `tenant.py`) depends on (2)
  and (3) and edits the same handler files as (3).
- **Cross-feature independence**: no active per-spec index touches these files
  (FEAT-101 remote execution is complete). `falsy-refresh.brainstorm.md` (exploration)
  targets `queries/qs.py:385,414` (cache refresh) and `handlers/` parameter coercion;
  a spec from it would overlap on `queries/qs.py` and `handlers/service.py`, so
  sequence the two features rather than run them concurrently.
- **Recommended isolation**: `per-spec`.
- **Rationale**: four or five small tasks with heavy overlap on `handlers/tenant.py`
  and `handlers/multi.py`; the two independent foundations are each well under an
  hour of work, so separate worktrees would cost more merge effort than they save.

---

## Open Questions

- [x] Feature or hotfix, and base branch? — *Owner: Jesus Lara*: feature on `dev`.
- [x] How does the tenant route decide single vs multi? — *Owner: Jesus Lara*: peek at the stored definition; multi iff `provider == 'multi'` (no `query_raw` sniffing).
- [x] Which tenant routes are in scope? — *Owner: Jesus Lara*: `GET/POST {slug}`, `HEAD/PATCH {slug}`, `GET/POST {slug}/test`; `/api/v3/queries` stays non-tenant.
- [x] Response contract for single slugs on the tenant route? — *Owner: Jesus Lara*: exact v2 (`QueryService`) parity.
- [x] Is a second definition read acceptable? — *Owner: Jesus Lara*: no; thread the `LoadedDefinition` into `QS`/`MultiQS`.
- [x] Dry-run semantics for a multi definition? — *Owner: Jesus Lara*: validate without executing (children resolution, existence, ownership, per-child report).
- [x] Columns semantics for a multi definition? — *Owner: Jesus Lara*: add a `columns` list to `{tenant}.queries`, return it on HEAD/PATCH; 204 like v3 when empty; describing the resulting frame is a follow-up.
- [ ] Field name: `columns` collides with the `datamodel` `Model.columns()` method used at `repositories/definitions.py:84` and `:42` (`_TENANT_COLUMNS`). Keep `columns` at the DDL level and name the model field differently (e.g. `output_columns` mapped to column `columns`), or pick another DDL name? — *Owner: Jesus Lara*
- [ ] Should the legacy `public.queries` table also gain the `columns` column, or stay read-only-compatible (reads default to `[]`, writes must not include it)? — *Owner: Jesus Lara*
- [ ] Dry-run report envelope for multi: reuse `QueryService.test_slug`'s keys (`works`, `generated_at`, ...) with an added `children` list, or a distinct multi-specific shape? — *Owner: Jesus Lara*
- [ ] `provider='multi'` with non-multi `query_raw`: keep the v3/scheduler fallback to single execution, or reject with 422 on the tenant route only? — *Owner: Jesus Lara*
- [ ] Should the stashed `LoadedDefinition` request key be shared with the FEAT-148 describe handler (`handlers/describe.py:302-339`) so it also stops re-loading the definition? — *Owner: Jesus Lara*
- [ ] Test strategy: unit tests with the existing MagicMock/monkeypatch pattern only, or also an opt-in integration test (`QS_TEST_POSTGRES_DSN`, `tests/tenants/test_integration.py`) with a real stored multi definition? — *Owner: Jesus Lara*
