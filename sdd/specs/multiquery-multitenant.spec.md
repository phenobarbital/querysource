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
tags: [multi-tenant, multiquery, tenant-routes, slug-dispatch, dry-run, columns-definition]
---

# Feature Specification: Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`

**Feature ID**: FEAT-151
**Date**: 2026-09-24
**Author**: Jesus Lara
**Status**: approved
**Target version**: 5.1.0
**Brainstorm**: `sdd/proposals/multiquery-multitenant.brainstorm.md` (accepted, Option A)

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

FEAT-147 (`sdd/specs/per-tenant-queries.spec.md` §2 route table, line 184) promises
that `GET, POST /api/v1/{tenant}/queries/{slug}` is a **"Unified stored single/multi
execution"** route and that `HEAD, PATCH` on the same path gives **"Column inspection
with existing single/multi response semantics"**. The shipped handler does not honor
that contract.

`TenantQueryHandler.query()` (`querysource/handlers/tenant.py:228-249`) branches on
the *presence* of a slug, not on the *kind* of stored definition: slug present →
`QueryService.query()` (single-query `QS` path); no slug → `QueryHandler.query()`
(inline MultiQuery). A stored **MultiQuery definition** (row with `provider='multi'`
and a JSON `query_raw` holding `queries` / `files` / `sources`) addressed by slug
under a tenant is therefore handed to `QS.build_provider()` (`queries/qs.py:167`),
which treats the JSON payload as a single query and fails. The exact same definition
executes correctly through the legacy, non-tenant `/api/v3/queries/{slug}` route
(`services.py:244-262`), because `QueryHandler` builds a `MultiQS` whose slug loader
understands both shapes (`queries/multi/__init__.py:216-260`).

The same gap exists on `columns()` (`tenant.py:251-264`) and `test_slug()`
(`tenant.py:266-277`): both delegate unconditionally to `QueryService`.

Affected: API consumers of tenant-scoped stored pipelines; operators who cannot
expose MultiQuery definitions per tenant without falling back to the non-isolated v3
route; FEAT-147 acceptance criterion U1 ("new tenant handler executes single/multi
queries"), which is only half true. The tenant HTTP tests
(`tests/tenants/test_tenant_http_routes.py:79`) exercise the *inline* multi dispatch
(no slug) and never a stored multi slug, which is why the gap was not caught.

### Goals
- `GET|POST /api/v1/{tenant}/queries/{slug}` executes a stored definition whatever
  its kind, resolved under the URL tenant.
- A stored definition is classified as **multi iff `provider == 'multi'`** — the same
  predicate the scheduler uses (`scheduler/scheduler.py:317`, `:415`). `query_raw`
  is never sniffed by the dispatcher.
- Single-query slugs keep **exact v2 parity** (`QueryService` semantics: headers,
  Redis cache, 204/404 mapping, `_download`/`_filename`, `queryformat`, `X-Slug`).
- The definition is loaded **once** per request: the dispatcher's `LoadedDefinition`
  is threaded into `QS` / `MultiQS`, which skip their own repository read while still
  populating `_definition_identity` / `_definition_revision`.
- `HEAD|PATCH {slug}` and `GET|POST {slug}/test` become kind-aware: multi columns
  come from a new `columns_definition` array on the definition (204 when empty);
  multi dry-run validates without executing.
- Legacy `/api/v2`, `/api/v3`, the scheduler and the Python API (`QS(...)`,
  `MultiQS(...)`) are behaviorally unchanged.

### Non-Goals (explicitly out of scope)
- Making `/api/v3/queries` tenant-aware (decided in brainstorm Round 1b).
- Describing the *resulting* frame of a pipeline for HEAD/PATCH — an explicit
  follow-up; this feature returns only the declared `columns_definition`.
- Executing children during the multi dry-run (`EXPLAIN` per child was rejected in
  brainstorm Round 2c).
- Always delegating slug requests to `QueryHandler` like v3 (brainstorm Option B,
  rejected: breaks v2 parity) and caller-declared kind suffixes (Option C, rejected).
- Changing the v3 route's own PBAC behavior for stored multi parents (see §7 gotcha).
- Rewriting the scheduler or the Python API around a kind-aware factory (brainstorm
  Option D, rejected as out of scope).

---

## 2. Architectural Design

### Overview

**Definition-peek dispatcher + pre-loaded definition threading** (brainstorm Option A).

`TenantQueryHandler` resolves the store (`_resolve_or_raise`), builds a
`QueryIdentity(store, slug)`, loads the `LoadedDefinition` **once** through the
app-published `DefinitionRepository` (`repositories/definitions.py:161`), and
classifies it with a single helper: `definition.runtime.provider == 'multi'`. It
stashes the tenant selector and the loaded definition on the request
(`request['qs_tenant']`, new `request['qs_definition']`) and delegates:

- multi → `QueryHandler` (`handlers/multi.py`): `query()`, the kind-aware
  `columns()`, and a new validate-only `test_slug()`.
- otherwise → `QueryService` (`handlers/service.py`): `query()`, `get_columns()`,
  `columns()`, `test_slug()`, exactly as today.

Both delegates forward the stashed definition into a new keyword-only
`definition: LoadedDefinition | None = None` argument on `QS` and `MultiQS`
(declared once on `AbstractQuery`, `interfaces/queries.py:48`). When present,
`QS.build_provider()` skips `repo.get()` and uses `definition.runtime`,
`definition.identity`, `definition.revision`; the MultiQS slug loader does the same
instead of `get_slug()`, and additionally records identity/revision (today the
compatibility `get_slug()` discards them). Absent → current behavior, untouched.

Decisions carried from the brainstorm and resolved here:
- Discriminator is `provider == 'multi'` only. A `provider='multi'` row whose
  `query_raw` is not multi JSON still goes to `QueryHandler`; `MultiQS` falls back to
  single-query mode (v3 and scheduler parity, `docs/QSSCHEDULER.md:80-88`). The
  dry-run reports this as a `warnings` entry.
- `columns_definition` (array of text, default `[]`) is added to
  `TenantQueryDefinition` **and** `QueryModel` (`Meta.strict = True`,
  `models.py:105`, rejects unknown keys on the runtime model), to the tenant DDL and
  to the legacy `public.queries` DDL (`ALTER TABLE ... ADD COLUMN`). Deployment
  order: model first, DDL second (reads on a store without the column default to
  `[]`).
- Multi HEAD → `204` with `X-Columns` / `X-Slug` headers (the same shape
  `QueryService.get_columns` uses, `service.py:474-484`), `X-Message: No Columns
  found` when empty. Multi PATCH → `200` JSON list when non-empty, `204` with
  `X-Message: No Columns available` (v3 shape, `multi.py:188`) when empty.
- Multi dry-run extends the single envelope (`service.py:723-733`): same top-level
  keys plus `kind: "multi"`, `children`, `files`, `sources`, `warnings`.
- **Authorize before loading** (design research S1): the tenant handler enforces
  `_enforce_owned_slug(identity, action="slug:execute")` on the parent identity
  **before** `DefinitionRepository.get()`, for all three routes and both kinds, so
  an unauthorized caller never triggers a definition read and cannot distinguish an
  existing slug from a missing one (both → 404). This mirrors `QueryService`, which
  authorizes at `service.py:200-215` before `get_source` (`:307`). For single slugs
  `QueryService` evaluates again (allow → allow, fresh per-request cache); the
  duplicate evaluation is accepted. For multi definitions this is the *only*
  parent check, because `QueryHandler.query()` preflights inline children only
  (`multi.py:293-323`) and a stored parent has `_queries == {}` there.
- One `_prepare()` helper on the tenant handler does resolve → authorize → load →
  classify → stash, so `query`, `columns` and `test_slug` cannot drift (S2).
- Writes never break on a store that lacks the new column (S7): the repository omits
  `columns_definition` from `create()`/`upsert()` when it is empty, so only an
  explicit non-empty declaration requires the migrated column.

### Component Diagram
```
GET|POST|HEAD|PATCH /api/v1/{tenant}/queries/{slug}[/test]
        │
        ▼
TenantQueryHandler (handlers/tenant.py)  — _prepare(request):
   1. _resolve_or_raise ──► TenantRegistry.resolve ──► QueryStore ; slug = match_info slug minus ':format'
   2. _enforce_owned_slug(QueryIdentity(store, slug), 'slug:execute')      (AUTHORIZE FIRST)
   3. _load_definition  ──► DefinitionRepository.get(QueryIdentity) ──► LoadedDefinition   (ONE read)
   4. _is_multi(definition)  == (runtime.provider == 'multi')
   5. request['qs_tenant'], request['qs_definition']
        │
        ├── single ──► QueryService.query | get_columns | columns | test_slug
        │                  └─► get_source(..., tenant=, definition=) ──► QS(definition=)
        │                          └─► build_provider(): skip repo.get(), use definition.*
        │
        └── multi ───► QueryHandler.query | columns | test_slug
                           ├─► MultiQS(slug=, tenant=, definition=)
                           │        └─► slug loader: skip get_slug(), use definition.runtime
                           ├─► columns(): definition.runtime.columns_definition → 204/200
                           └─► test_slug(): parse query_raw, resolve children, repo.get + PBAC, report
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `TenantQueryHandler` (`handlers/tenant.py:142`) | modifies | `query`, `columns`, `test_slug` share one `_prepare` (resolve → authorize → load → classify → stash); new `_load_definition`, `_is_multi` helpers; new request key |
| `BaseQuery` (`queries/base.py:19`) | modifies | forwards `definition=` explicitly to `AbstractQuery` (S3: `**kwargs` would otherwise swallow it) |
| `DefinitionRepository.create/upsert` (`repositories/definitions.py:292, 328`) | modifies | omit empty `columns_definition` from INSERT column lists (S7) |
| `QueryService` (`handlers/service.py:31`) | modifies | four `get_source(...)` call sites forward `definition=request.get('qs_definition')` |
| `QueryHandler` (`handlers/multi.py:25`) | modifies / extends | `query()` forwards definition to `MultiQS`; `columns()` becomes definition-aware; new `test_slug()` |
| `AbstractQuery` (`interfaces/queries.py:41`) | extends | keyword-only `definition` argument stored as `_preloaded_definition` |
| `QS` (`queries/qs.py:35`) | extends | passes `definition` through; `build_provider()` short-circuit at `qs.py:172-186` |
| `MultiQS` (`queries/multi/__init__.py:90`) | extends | passes `definition` through; slug loader short-circuit at `multi/__init__.py:225`; sets identity/revision |
| `TenantQueryDefinition` (`tenant_models.py:24`), `QueryModel` (`models.py:48`) | extends | `columns_definition: List[str]` array field |
| `DefinitionRepository._TENANT_COLUMNS` (`repositories/definitions.py:41`) | depends on | derives from the model; picks the field up automatically |
| `docs/PER_TENANT_QUERIES.md:40`, `tests/tenants/conftest.py:22` | modifies | tenant DDL gains `columns_definition text[]`; legacy `ALTER TABLE public.queries` documented |
| `/api/v2`, `/api/v3`, `scheduler/jobs.py`, Python API | unchanged | `definition` defaults to `None`; covered by regression tests |

### Data Models
```python
# querysource/tenant_models.py (modifies :50) and querysource/models.py (modifies :67)
# NOTE: module tenant_models.py must not use `X | None` / `list[X]` syntax (Cython datamodel validator).
columns_definition: List[str] = Field(
    required=False,
    db_type='array',
    default_factory=list,
    comment='Declared output columns of a multi-query definition (HEAD/PATCH inspection).',
)
```

```sql
-- docs/PER_TENANT_QUERIES.md provisional DDL (tenant stores)
columns_definition TEXT[] DEFAULT '{}'::text[],
-- legacy store
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition TEXT[] DEFAULT '{}'::text[];
```

Write policy (S7): `DefinitionRepository.create()` and `upsert()` drop
`columns_definition` from the INSERT column list when its value is empty (`[]`),
exactly as they already drop `None` values (`definitions.py:303, 347`), so a
not-yet-migrated store keeps accepting writes; only an explicit non-empty
declaration requires the column and otherwise fails through
`_translate_write_error`.

Request-scoped keys (aiohttp `request[...]`):

| Key | Type | Set by | Read by |
|---|---|---|---|
| `qs_tenant` | `str \| None` | `TenantQueryHandler` (existing, `tenant.py:238`) | `QueryService`, `QueryHandler` (existing) |
| `qs_definition` | `LoadedDefinition` | `TenantQueryHandler` (new) | `AbstractHandler.get_source` callers in `QueryService`; `QueryHandler.query/columns/test_slug` |

Multi dry-run envelope (JSON, `queryformat == 'json'`):
```json
{
  "slug": "<slug>", "kind": "multi", "works": true, "error": null,
  "generated": 0.012, "execution": null,
  "tenant": "<url tenant or null>", "store": "<schema>.<table>",
  "children": [
    {"alias": "a", "slug": "child_a", "kind": "slug", "tenant": "<resolved>", "store": "<schema>.<table>",
     "exists": true, "allowed": true, "error": null},
    {"alias": "r", "slug": null, "kind": "raw", "tenant": null, "store": null,
     "exists": null, "allowed": null, "error": null}
  ],
  "files": ["f1"], "sources": ["sharepoint"], "warnings": [],
  "conditions": {}, "query": {"queries": {"...": "..."}}
}
```
`conditions` / `query` are omitted when `ignore_query` is set, mirroring the single
envelope. The single envelope is **not** changed (no `kind` key) to preserve v2 parity.

### New Public Interfaces
```python
# querysource/queries/qs.py / querysource/queries/multi/__init__.py — new keyword-only argument
QS(slug=..., conditions=..., request=..., loop=..., *, tenant=None, definition=None, **kwargs)
MultiQS(slug=..., queries=..., files=..., query=..., conditions=..., request=..., loop=...,
        user_session=..., *, tenant=None, definition=None, **kwargs)

# querysource/handlers/multi.py — new handler method (routed only via TenantQueryHandler)
QueryHandler.test_slug(request) -> web.StreamResponse
```

---

## 3. Module Breakdown

> Define the discrete modules that will be implemented.
> These directly map to Task Artifacts in Phase 2.

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Pre-loaded definition threading | yes | kwarg name `definition`, attribute `_preloaded_definition`, short-circuit sites `qs.py:172-186` and `multi/__init__.py:225`; identity/revision assignment copied from `qs.py:179-180` | — |
| M2: `columns_definition` field + DDL | yes | field spec in §2 Data Models; DDL text fixed; files listed in §6 Edit Sites | — |
| M3: Kind-aware tenant dispatch | yes | request key `qs_definition`; helper names `_load_definition`, `_is_multi`; error mapping in §7; parent `_enforce_owned_slug` for multi only | — |
| M4: Multi columns + validate-only dry-run | yes | response shapes fixed in §2 (headers, 204/200, envelope); child resolution rules copied from `multi/__init__.py:315-333` | — |
| M5: Tests + docs | yes | test names and fixtures in §4 | — |

### Module 1: Pre-loaded definition threading (queries layer)
- **Path**: `querysource/interfaces/queries.py`, `querysource/queries/qs.py`, `querysource/queries/multi/__init__.py`
- **Responsibility**: let a caller hand an already loaded `LoadedDefinition` to `QS` /
  `MultiQS` so the slug lookup is skipped, while identity and revision are recorded
  exactly as the repository path records them.
- **Depends on**: existing `LoadedDefinition` (`tenants.py:54`), `QS.build_provider`
  (`qs.py:167`), MultiQS slug loader (`multi/__init__.py:216`).
- **Interface Skeleton** *(signatures + docstrings only — bodies belong to task blueprints, FEAT-545)*:
  ```python
  # modifies querysource/interfaces/queries.py:48  (AbstractQuery.__init__)
  class AbstractQuery(Connection):  # verified: querysource/interfaces/queries.py:41
      def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
                   loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None,
                   definition: "LoadedDefinition | None" = None, **kwargs):
          """Accept an optional pre-loaded definition; stored as ``self._preloaded_definition``
          next to ``self._tenant_selector`` (verified: interfaces/queries.py:104). Never validated
          here; consumers check ``definition.identity.slug == self.slug`` before trusting it."""

  # modifies querysource/queries/base.py:39  (BaseQuery.__init__ — forward `definition=definition` next to `tenant=tenant`;
  #   S3: without the explicit forward the keyword is swallowed by **kwargs down to Connection.__init__)

  # modifies querysource/queries/qs.py:49  (QS.__init__ — pass `definition=` to super().__init__)
  # modifies querysource/queries/qs.py:172-186  (build_provider slug branch)
  #   if self._preloaded_definition is not None and self._preloaded_definition.identity.slug == self._query:
  #       loaded_def = self._preloaded_definition   (no repo.get(); identity/revision assignment unchanged)

  # modifies querysource/queries/multi/__init__.py:107  (MultiQS.__init__ — pass `definition=`)
  # modifies querysource/queries/multi/__init__.py:225  (slug loader)
  #   use self._preloaded_definition.runtime instead of await self.get_slug(...) when present and slug matches;
  #   in BOTH branches set self._definition_identity / self._definition_revision (new for MultiQS).

  # new in querysource/queries/multi/__init__.py (S5 — pure helper shared by execution preflight and dry-run)
  @staticmethod
  def resolve_child_owner(query_cfg: dict, parent_tenant: str | None, registry: "TenantRegistry") -> tuple[str | None, "QueryStore"]:
      """Apply the child tenant rule — explicit str, explicit None (legacy store), or inherit
      ``parent_tenant`` when the key is absent — and resolve the store via ``registry.resolve``
      (rule verified: queries/multi/__init__.py:315-324). Pure: no I/O, no request access.
      The execution preflight loop (multi/__init__.py:315-324) is rewritten to call it."""
  ```

### Module 2: `columns_definition` field + DDL
- **Path**: `querysource/tenant_models.py`, `querysource/models.py`, `querysource/repositories/definitions.py`, `docs/PER_TENANT_QUERIES.md`, `tests/tenants/conftest.py`
- **Responsibility**: declare the array field on both persistence models (strict runtime
  model requires both), make writes tolerant of un-migrated stores (S7), document the
  tenant DDL column and the legacy `ALTER TABLE`, extend the test DDL fixture.
- **Depends on**: nothing in this spec (runs concurrently with M1).
- **Interface Skeleton**:
  ```python
  # modifies querysource/tenant_models.py:50  (insert after `grouping`)
  columns_definition: List[str] = Field(required=False, db_type='array', default_factory=list,
                                        comment='Declared output columns of a multi-query definition (HEAD/PATCH inspection).')
  # modifies querysource/models.py:67  (insert after `grouping`, identical declaration)
  # modifies querysource/repositories/definitions.py:303 and :347 (create/upsert) — after the `is not None` filter:
  #   if not persisted.get('columns_definition'): persisted.pop('columns_definition', None)
  # modifies docs/PER_TENANT_QUERIES.md:47 (DDL block) + new "Legacy store migration" note with the ALTER TABLE
  # modifies tests/tenants/conftest.py:29 (_TENANT_QUERIES_DDL gains "columns_definition text[] DEFAULT '{}'::text[], ")
  ```

### Module 3: Kind-aware tenant dispatch (handlers)
- **Path**: `querysource/handlers/tenant.py`, `querysource/handlers/service.py`, `querysource/handlers/abstract.py` (no change expected — `get_source` already forwards `**kwargs`, `abstract.py:270-285`)
- **Responsibility**: one read, one classification, one dispatch for `query`, `columns`,
  `test_slug`; forward the definition from `QueryService` into `QS`.
- **Depends on**: M1 (`definition=` kwarg), M4 (`QueryHandler.test_slug`, kind-aware `columns`).
- **Interface Skeleton**:
  ```python
  # modifies querysource/handlers/tenant.py:142  (TenantQueryHandler)
  class TenantQueryHandler(AbstractHandler):  # verified: querysource/handlers/tenant.py:142
      async def _prepare(self, request: web.Request) -> tuple[QueryStore, str, LoadedDefinition, bool]:
          """Shared front half of every slug route (S2): resolve the URL tenant
          (_resolve_or_raise, verified: tenant.py:127); strip a ':format' suffix from
          match_info['slug']; AUTHORIZE the parent identity with
          _enforce_owned_slug(request, identity=QueryIdentity(store, slug), action='slug:execute')
          (verified: abstract.py:463) BEFORE any repository read (S1); then _load_definition;
          then stash request['qs_tenant'] and request['qs_definition'].
          Returns (store, slug, definition, is_multi)."""

      async def _load_definition(self, request: web.Request, store: QueryStore, slug: str) -> LoadedDefinition:
          """Load the stored definition once via request.app['qs_definition_repository']
          (verified: tenant.py:162). TenantError code 404 (query_not_found) → web.HTTPNotFound
          (indistinguishable from the PBAC deny of S1); 400 → web.HTTPBadRequest; 503
          (tenant_store_unavailable) → self.error(response={'message': ...}, status=err.code)."""

      @staticmethod
      def _is_multi(definition: LoadedDefinition) -> bool:
          """True iff ``definition.runtime.provider == 'multi'`` (scheduler predicate,
          verified: querysource/scheduler/scheduler.py:317). No query_raw sniffing."""

      async def query(self, request: web.Request) -> web.StreamResponse:      # modifies tenant.py:228
          """Slug present: _prepare(); multi → QueryHandler.query; else QueryService.query
          (which re-evaluates PBAC itself, accepted). No slug: unchanged inline QueryHandler.query."""
      async def columns(self, request: web.Request) -> web.StreamResponse:    # modifies tenant.py:251
          """_prepare(); multi → QueryHandler.columns (definition-aware); single → HEAD get_columns / PATCH columns."""
      async def test_slug(self, request: web.Request) -> web.StreamResponse:  # modifies tenant.py:266
          """_prepare(); multi → QueryHandler.test_slug (validate-only); single → QueryService.test_slug."""

  # modifies querysource/handlers/service.py:307, :438, :580, :705 — each existing call becomes
  #   self.get_source(request, slug, conditions, driver=args, tenant=tenant, definition=request.get('qs_definition'))
  ```

### Module 4: Multi columns + validate-only dry-run (`QueryHandler`)
- **Path**: `querysource/handlers/multi.py`
- **Responsibility**: forward the pre-loaded definition into `MultiQS`; answer HEAD/PATCH
  from `columns_definition`; validate a stored multi definition without executing it.
- **Depends on**: M1 (`MultiQS(definition=)`), M2 (`runtime.columns_definition`).
- **Interface Skeleton**:
  ```python
  # modifies querysource/handlers/multi.py:335  (qs = MultiQS(... tenant=_tenant, definition=request.get('qs_definition')))

  # modifies querysource/handlers/multi.py:188
  async def columns(self, request: web.Request) -> web.StreamResponse:
      """HEAD: 204 with X-Columns=repr(list), X-Slug, and X-Message='No Columns found' when empty
      (shape verified: handlers/service.py:474-484). PATCH: 200 json list when non-empty, else the
      existing 204 X-Message='No Columns available'. Source: request.get('qs_definition').runtime
      .columns_definition; no definition on the request → existing 204 (v3 callers unchanged)."""

  # new in querysource/handlers/multi.py
  async def test_slug(self, request: web.Request) -> web.StreamResponse:
      """Validate-only dry-run of a stored multi definition (request['qs_definition'] required;
      missing → 400). Parses runtime.query_raw as JSON (DefaultEncoder semantics). A non-multi
      payload is NOT an error (decision: keep the MultiQS fallback): works=True, children=[],
      warnings=['query_raw is not a multi-query payload; MultiQS will fall back to single-query
      mode']. Normalizes `sources` via MultiQS._normalize_sources (verified:
      queries/multi/__init__.py:174). For each `queries` entry: alias, slug or raw kind;
      (tenant, store) via MultiQS.resolve_child_owner(query_cfg, request['qs_tenant'], registry)
      (M1, S5); exists via DefinitionRepository.get (TenantError → exists=False, error=<code>);
      allowed via _enforce_owned_slug(identity, action='slug:execute') when
      request.app.get('security') is not None (web.HTTPNotFound → allowed=False), else
      allowed=None. works = every saved child exists and none is denied. Never constructs
      ThreadQuery/executors, opens no datasource connection, runs no EXPLAIN (S5). Returns the
      §2 envelope with status 200 (json) or text/plain of the parsed definition for
      txt/plain/raw formats."""
  ```

### Module 5: Tests and documentation
- **Path**: `tests/tenants/test_tenant_http_routes.py`, new `tests/tenants/test_tenant_multi_dispatch.py`, new `tests/queries/test_preloaded_definition.py`, `tests/tenants/test_integration.py`, `docs/PER_TENANT_QUERIES.md`
- **Responsibility**: unit coverage for M1–M4 with the existing MagicMock/monkeypatch
  pattern; one opt-in integration test with a real stored multi definition; docs.
- **Depends on**: M1–M4.
- **Interface Skeleton**: test names in §4.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_qs_uses_preloaded_definition_and_skips_repo` | M1 | `QS(slug, definition=loaded)`: `get_definition_repository` is never awaited; `_definition_identity`/`_revision` equal the supplied ones |
| `test_qs_ignores_mismatched_preloaded_definition` | M1 | slug differs from `definition.identity.slug` → repository path runs |
| `test_multiqs_uses_preloaded_definition_and_records_identity` | M1 | `MultiQS(slug, definition=loaded)`: `get_slug` not called; multi JSON parsed; identity/revision set |
| `test_multiqs_without_definition_unchanged` | M1 | regression: `get_slug` path still used |
| `test_models_declare_columns_definition` | M2 | both models expose the field with `[]` default; `_TENANT_COLUMNS` contains it; `QueryModel(**persisted)` accepts it |
| `test_runtime_model_without_column_defaults_empty` | M2 | a persisted row lacking the key yields `columns_definition == []` |
| `test_multiqs_resolve_child_owner_rules` | M1 | inherit / explicit tenant / explicit `null` → expected `(tenant, store)`; execution preflight uses the helper |
| `test_repository_write_omits_empty_columns_definition` | M2 | `create`/`upsert` INSERT column list excludes `columns_definition` when `[]`, includes it when non-empty |
| `test_stored_multi_slug_dispatches_to_query_handler` | M3 | `provider='multi'` → `QueryHandler.query` called, `request['qs_definition']` set |
| `test_stored_single_slug_dispatches_to_query_service` | M3 | `provider='db'` (and JSON `query_raw` under `provider='db'`) → `QueryService.query` |
| `test_authorization_precedes_definition_load` | M3 | S1: `_enforce_owned_slug` awaited before `repo.get`; on deny, `repo.get` is never called and an existing slug and a missing slug produce identical 404 responses |
| `test_one_repository_read_per_request` | M3 | S2/S8: `_FakeRepo.get` called exactly once for query, columns and test routes; the definition object captured in `QS`/`MultiQS` is the same instance |
| `test_single_slug_response_parity` | M3 | S8: headers (`X-Slug`, `Content-Type`), status mapping, `_download`/`_filename`, `queryformat` for a single slug via the tenant route equal `QueryService.query` called directly with the same request |
| `test_query_service_forwards_definition_to_qs` | M3 | `get_source` receives `definition=` from the request key on all four sites |
| `test_missing_slug_returns_404_before_dispatch` | M3 | `TenantError(query_not_found)` → 404; neither delegate instantiated |
| `test_slug_format_suffix_stripped_before_peek` | M3 | `slug:csv` → identity slug is `slug`; delegate still receives the suffix |
| `test_columns_head_patch_kind_aware` | M3/M4 | HEAD multi → 204 + `X-Columns`; PATCH multi non-empty → 200 list; empty → 204 `X-Message`; single unchanged (`test_columns_test_and_output_suffixes` still passes) |
| `test_multi_dry_run_reports_children` | M4 | children with inherit / explicit / null tenant resolve to the right store; exists/allowed computed; `works` semantics |
| `test_multi_dry_run_non_multi_payload_warns` | M4 | `provider='multi'` + SQL `query_raw` → `works=True`, one warning, empty children |
| `test_multi_dry_run_requires_definition` | M4 | no `qs_definition` → 400 |
| `test_inline_multi_dispatch_unchanged` | M3 | existing `test_single_multi_inline_dispatch` remains green |
| `test_v3_routes_do_not_set_definition` | M3 | `QueryHandler.query` via v3 passes `definition=None` |

### Integration Tests
| Test | Description |
|---|---|
| `test_tenant_stored_multi_definition_executes` (opt-in, `tenant_services` fixture, `QS_TEST_POSTGRES_DSN` + `QS_TEST_REDIS_URL`) | Provision a tenant schema with the new DDL, insert an inheriting child, an explicit-`null` (legacy) child and a `provider='multi'` parent with `columns_definition`; run `GET /api/v1/{tenant}/queries/{parent}` (200 frame, `_definition_revision` equals the repository revision), `HEAD` (204 + `X-Columns`), `GET .../test` (envelope with both children resolved to their stores), and the single child (v2 headers) (S9) |
| `tests/tenants/test_integration.py` existing suite | Unchanged; the DDL fixture change must not break it |

### Test Data / Fixtures
```python
# tests/tenants/test_tenant_multi_dispatch.py — reuse _mock_registry / _mock_request from
# tests/tenants/test_tenant_http_routes.py (verified :11-41); add:
def _loaded(slug: str, provider: str, query_raw: str = "", columns_definition: list | None = None) -> LoadedDefinition:
    """Build a LoadedDefinition with a QueryModel runtime for the mock store (no DB)."""

class _FakeRepo:
    async def get(self, identity): ...   # returns the LoadedDefinition or raises TenantError("query_not_found")

MULTI_RAW = '{"queries": {"a": {"slug": "child_a"}, "b": {"slug": "child_b", "tenant": null}, "r": {"query": "SELECT 1"}}, "files": {}}'
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] AC-1: `GET|POST /api/v1/{tenant}/queries/{slug}` for a `provider='multi'` definition is served by `QueryHandler`/`MultiQS` under the URL tenant (parent and inheriting children resolve to the tenant store).
- [ ] AC-2: A non-multi definition on the same route is served by `QueryService` with byte-identical response conventions to `/api/v2/services/queries/{slug}` (existing tenant route tests unchanged and green).
- [ ] AC-3: The dispatcher classifies by `runtime.provider == 'multi'` only; a JSON multi payload saved under `provider='db'` is treated as single (documented), and `provider='multi'` with plain SQL still executes through `MultiQS` fallback.
- [ ] AC-4: Exactly one `DefinitionRepository.get` per tenant slug request: `QS`/`MultiQS` given `definition=` never call the repository or `get_slug` for the top-level slug, and `_definition_identity`/`_definition_revision` are populated on both executors.
- [ ] AC-5: `QS(...)`/`MultiQS(...)` without `definition=`, `/api/v2`, `/api/v3` and scheduler jobs behave exactly as before (regression tests green; `definition` defaults to `None`).
- [ ] AC-6: Multi HEAD → 204 with `X-Columns`/`X-Slug` (+ `X-Message: No Columns found` when empty); multi PATCH → 200 JSON list or 204 `X-Message: No Columns available` when empty; single HEAD/PATCH unchanged.
- [ ] AC-7: `columns_definition` exists on `TenantQueryDefinition` and `QueryModel` (array, default `[]`), is a member of `_TENANT_COLUMNS`, is documented in the tenant DDL and as an `ALTER TABLE` for `public.queries`, and is present in the test DDL fixture; a row without the column reads as `[]`.
- [ ] AC-8: Multi `GET|POST {slug}/test` executes no datasource query and no `EXPLAIN`; returns the §2 envelope with `kind: "multi"`, one `children` entry per `queries` item with resolved tenant/store, `exists`, `allowed`; `works` is true iff every saved child exists and none is denied.
- [ ] AC-9: The tenant handler enforces parent ownership (`_enforce_owned_slug`, `slug:execute`, tenant-isolated evaluator) **before** the definition read on every slug route and for both kinds; on deny the repository is never called and an existing slug is indistinguishable from a missing one (404).
- [ ] AC-14: `DefinitionRepository.create`/`upsert` omit `columns_definition` from the INSERT when it is empty; a non-empty value on an un-migrated store fails through `_translate_write_error`.
- [ ] AC-15: `MultiQS.resolve_child_owner` is the single implementation of the child tenant rule, used by both the execution preflight and the multi dry-run.
- [ ] AC-16: `columns_definition` is writable through the existing repository API (`create`, `upsert`, `patch`) and therefore through the management CRUD; no dedicated endpoint (§8 Q4).
- [ ] AC-10: Tenant selector never enters conditions/params (FEAT-147 AC-4 preserved); only `request['qs_tenant']` and `request['qs_definition']` carry routing context.
- [ ] AC-11: Unknown tenant → 400/404 via `_resolve_or_raise`; missing slug → 404 `query_not_found`; store unavailable → 503 with the TenantError code; all raised before any delegate runs.
- [ ] AC-12: `pytest tests/tenants tests/queries -q` and `ruff check` on changed paths pass; the opt-in integration test passes when `QS_TEST_POSTGRES_DSN`/`QS_TEST_REDIS_URL` are set.
- [ ] AC-13: `docs/PER_TENANT_QUERIES.md` documents kind-aware dispatch, the `columns_definition` column, the legacy migration and the multi dry-run envelope.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

Verified against commit `869c443` (dev, 2026-09-24).

### Verified Imports
```python
from querysource.handlers import TenantQueryHandler, QueryService, QueryHandler  # verified: querysource/handlers/__init__.py:10-13
from querysource.handlers.abstract import AbstractHandler                        # verified: querysource/handlers/tenant.py:13
from querysource.repositories import DefinitionRepository                        # verified: querysource/handlers/tenant.py:14
from querysource.tenant_errors import TenantError                                # verified: querysource/handlers/tenant.py:15
from querysource.tenants import QueryStore, TenantRegistry                       # verified: querysource/handlers/tenant.py:16
from querysource.tenants import QueryIdentity, LoadedDefinition, DefinitionPage, quote_identifier  # verified: querysource/repositories/definitions.py:22-29
from querysource.tenant_models import TenantQueryDefinition                      # verified: querysource/repositories/definitions.py:21
from querysource.models import QueryModel                                        # verified: querysource/repositories/definitions.py:19
from querysource.queries import QS, MultiQS                                      # verified: querysource/queries/__init__.py:6-7
from querysource.exceptions import DataNotFound, EmptySentence, QueryError, QueryException  # verified: querysource/queries/qs.py:23-28
```

### Existing Class Signatures
```python
# querysource/handlers/tenant.py
def resolve_request_store(request: web.Request, registry: TenantRegistry, payload: Mapping[str, Any] | None = None) -> QueryStore:  # line 19
def _resolve_or_raise(registry: TenantRegistry, tenant: str | None) -> QueryStore:  # line 127; TenantError 404 → HTTPNotFound, else HTTPBadRequest
class TenantQueryHandler(AbstractHandler):  # line 142
    def _registry(self, request: web.Request) -> TenantRegistry:            # line 156; request.app["qs_tenant_registry"] or 404
    def _repository(self, request: web.Request) -> DefinitionRepository:    # line 162; request.app["qs_definition_repository"] or 404
    async def list(self, request: web.Request) -> web.StreamResponse:       # line 168
    async def query(self, request: web.Request) -> web.StreamResponse:      # line 228; request["qs_tenant"] = tenant at 238; slug branch 240-244; QueryHandler 246-249
    async def columns(self, request: web.Request) -> web.StreamResponse:    # line 251; HEAD → get_columns, else columns
    async def test_slug(self, request: web.Request) -> web.StreamResponse:  # line 266

# querysource/handlers/service.py
class QueryService(AbstractHandler):  # line 31
    async def query(self, request): ...        # line 134; PBAC 200-215 (_enforce_owned_slug when registry present); get_source at 307
    async def get_columns(self, request): ...  # line 411; get_source at 438; 204 + X-Columns/X-Slug/X-Message at 474-484
    async def columns(self, request): ...      # line 506; get_source at 580; json_response(columns, 200) at 611
    async def test_slug(self, request: web.Request) -> web.StreamResponse: ...  # line 632; get_source at 705; envelope 723-733 (slug, works, error, generated, execution[, conditions, query])

# querysource/handlers/multi.py
class QueryHandler(AbstractHandler):  # line 25
    async def _preflight_multiquery(self, request: web.Request, slugs: list, files: list, has_raw_query: bool) -> None: ...        # line 27
    async def _preflight_multiquery_owned(self, request: web.Request, slugs: list, files: list, has_raw_query: bool) -> None: ...  # line 103; skips when request.app.get("security") is None (129) or no registry (152)
    async def columns(self, request: web.Request) -> web.StreamResponse: ...  # line 188; no_content(204, X-Message='No Columns available')
    async def query(self, request: web.Request) -> web.StreamResponse: ...    # line 196; slug/format split 202-208; _tenant = request.get('qs_tenant') at 332; MultiQS(...) at 335-343

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):  # line 33
    async def get_source(self, request, slug, conditions, **kwargs) -> QS: ...  # line 270; QS(slug=, conditions=, loop=, request=, lazy=False, **kwargs)
    async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None: ...  # line 323
    async def _enforce_owned_slug(self, request: web.Request, identity: QueryIdentity, action: str) -> None: ...     # line 463
    # inherited from navigator BaseView: no_content(headers: dict = None, content_type='application/json') -> Response;
    # json_response(response: dict = None, reason=None, headers=None, status=200, ...); error(response: dict = None, exception=None, status=400, ...)

# querysource/interfaces/queries.py
class AbstractQuery(Connection):  # line 41
    def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None, **kwargs): ...  # line 48-56
    _tenant_selector: str | None        # line 104
    _definition_identity: Any           # line 106
    _definition_revision: str | None    # line 107
    _encoder: DefaultEncoder            # line ~99 (self._encoder = DefaultEncoder())

# querysource/queries/base.py
class BaseQuery(AbstractQuery):  # line 19; __init__ passes tenant=... to AbstractQuery (line 34-41)

# querysource/queries/qs.py
class QS(BaseQuery):  # line 35
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None, **kwargs): ...  # line 41-59
    # build_provider slug branch: line 167; repo = await self.get_definition_repository() (174);
    # store = repo.registry.resolve(self._tenant_selector) (175); identity = QueryIdentity(store=store, slug=self._query) (176);
    # loaded_def = await repo.get(identity) (177); self._definition_identity/_revision = loaded_def.identity/revision (179-180);
    # objquery = loaded_def.runtime (186)
    async def dry_run(self): ...  # line 555

# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):  # line 90
    def __init__(self, slug: str = None, queries: list | None = None, files: list | None = None, query: dict | None = None,
                 conditions: dict = None, request: web.Request = None, loop: asyncio.AbstractEventLoop = None,
                 user_session: object | None = None, *, tenant: str | None = None, **kwargs): ...  # line 96-116
    @staticmethod
    def _normalize_sources(raw) -> list: ...  # line 174
    # slug loader: query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector) (225);
    # multi iff parsed query_raw dict has 'queries'|'files'|'sources' (235-237); single wrapper 252-260
    # child preflight loop 285-333: child_slug = query_cfg.get("slug") (293); raw child has "query" key (295);
    # tenant rule 315-322 ("tenant" in query_cfg → explicit value incl. None, else inherit self._tenant_selector);
    # child_store = repo.registry.resolve(child_tenant) (324); await repo.get(QueryIdentity(store=child_store, slug=child_slug)) (329-330)

# querysource/interfaces/connections.py
async def get_definition_repository(self) -> "DefinitionRepository": ...  # line 438
async def get_query_slug(self, slug: str, evt=None, max_retries: int = 3, *, tenant: str | None = None) -> BaseModel: ...  # line 458; returns loaded.runtime
async def get_slug(self, slug: str, program: str = None, evt=None, *, tenant: str | None = None): ...  # line 526; raises SlugNotFound

# querysource/tenants.py
@dataclass(frozen=True) class QueryStore: database_namespace: str; schema: str; table: str; contract: Literal["legacy","tenant"]; columns: frozenset[str]  # line 35
@dataclass(frozen=True) class QueryIdentity: store: QueryStore; slug: str        # line 46
@dataclass(frozen=True) class LoadedDefinition: identity: QueryIdentity; runtime: QueryModel; revision: str  # line 54
class TenantRegistry:  # line 80
    async def discover(self, conn, allowlist=...): ...  # line 158; only "query_slug" required (146-149)
    def resolve(self, tenant: str | None = None) -> QueryStore: ...  # line 402

# querysource/repositories/definitions.py
_TENANT_COLUMNS: frozenset = frozenset(TenantQueryDefinition(query_slug="__probe__").columns().keys())  # line 41-43
class DefinitionRepository:  # line 56
    def _allowed_columns(self, store) -> frozenset: ...                     # line 75; legacy: QueryModel(...).columns().keys() at 84
    def _row_to_persisted(self, row, store) -> tuple[dict, str | None]: ... # line 88; TenantQueryDefinition(**data) rejects unknown keys
    def _runtime_model(self, persisted, store, legacy_program_slug) -> QueryModel: ...  # line 111; QueryModel(**persisted, program_slug=...)
    async def _fetch_row(self, store: QueryStore, slug: str) -> Mapping[str, Any] | None: ...  # line 121; SELECT * ... WHERE query_slug = $1 LIMIT 1
    async def get(self, identity: QueryIdentity) -> LoadedDefinition: ...  # line 161; TenantError(error_code="query_not_found") when missing

# querysource/tenant_models.py
class TenantQueryDefinition(BaseModel):  # line 24; module forbids `X | None` and `list[X]` syntax (docstring)
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)  # line 50
    provider: str = Field(required=False, default='db')                                # line 60
    query_raw: str = Field(required=False)                                             # line 57

# querysource/models.py
class QueryModel(Model):  # line 48
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)    # line 64
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)  # line 67
    provider: str = Field(required=False, default='db')                                 # line 74
    class Meta: strict = True                                                            # lines 101-105

# querysource/scheduler/scheduler.py
if provider == "multi": ...                    # line 317 — canonical stored-multi predicate
if row.get("provider") == "multi": return None # line 415
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `TenantQueryHandler._load_definition` | `DefinitionRepository.get(QueryIdentity)` | await | `repositories/definitions.py:161` |
| `TenantQueryHandler._is_multi` | `LoadedDefinition.runtime.provider` | attribute | `tenants.py:54`, `models.py:74` |
| `TenantQueryHandler.query` (multi) | `AbstractHandler._enforce_owned_slug(request, identity=, action="slug:execute")` | await | `handlers/abstract.py:463` |
| `TenantQueryHandler.query` (multi) | `QueryHandler.query(request)` | delegate | `handlers/multi.py:196` |
| `TenantQueryHandler.query` (single) | `QueryService.query(request)` | delegate | `handlers/service.py:134` |
| `QueryService.*` | `AbstractHandler.get_source(..., definition=request.get('qs_definition'))` | kwargs → `QS(**kwargs)` | `handlers/abstract.py:278-285` |
| `QueryHandler.query` | `MultiQS(..., tenant=_tenant, definition=request.get('qs_definition'))` | constructor | `handlers/multi.py:335-343` |
| `QS.build_provider` | `self._preloaded_definition` instead of `repo.get(identity)` | attribute | `queries/qs.py:172-186` |
| `MultiQS.query` slug loader | `self._preloaded_definition.runtime` instead of `get_slug()` | attribute | `queries/multi/__init__.py:225` |
| `QueryHandler.test_slug` | `MultiQS._normalize_sources`, `TenantRegistry.resolve`, `DefinitionRepository.get`, `_enforce_owned_slug` | calls | `multi/__init__.py:174`, `tenants.py:402`, `definitions.py:161`, `abstract.py:463` |
| `QueryHandler.columns` | `request.get('qs_definition').runtime.columns_definition` | attribute (new field) | M2 |
| Existing tests | `_mock_registry`, `_mock_request` | fixtures | `tests/tenants/test_tenant_http_routes.py:11-41` |

### Does NOT Exist (Anti-Hallucination)
- ~~`MultiQS.dry_run()`~~ — only `QS.dry_run()` (`queries/qs.py:555`) and `QueryExecutor.dry_run()` (`queries/executor.py:66`).
- ~~`QueryHandler.test_slug()`~~ / ~~`QueryHandler.get_columns()`~~ — do not exist yet; `test_slug` is created by M4, `get_columns` is never added (HEAD and PATCH both go through `QueryHandler.columns`).
- ~~`QS(definition=...)`~~ / ~~`MultiQS(definition=...)`~~ / ~~`AbstractQuery._preloaded_definition`~~ — created by M1.
- ~~`request["qs_definition"]`~~ — created by M3; only `qs_tenant` exists today.
- ~~`LoadedDefinition.is_multi`~~ / ~~`QueryModel.is_multi`~~ / ~~`QueryModel.query_type`~~ — the only marker is `provider == 'multi'`.
- ~~`columns_definition` column / `TenantQueryDefinition.columns_definition` / `QueryModel.columns_definition`~~ — created by M2. **Never name the field `columns`**: `Model.columns()` is the `datamodel` method returning the field map (`repositories/definitions.py:42, 84`).
- ~~`QueryStore.tenant`~~ — `QueryStore` has no `tenant` attribute (`tenants.py:35-42`); the selector is the `schema` name resolved by `TenantRegistry.resolve`.
- ~~`DefinitionRepository` per-request cache~~ — every `get()` is one `SELECT`.
- ~~`BaseQuery.get_slug`~~ in `queries/base.py` — `get_slug` lives on the `Connection` interface (`interfaces/connections.py:526`).
- ~~`TenantQueryHandler.get_columns`~~ — the HEAD branch calls `QueryService.get_columns` inside `columns()`.
- ~~`/api/v3/queries` tenant selector~~ — v3 never sets `qs_tenant` (out of scope).
- ~~`attributes.columns` as the multi columns source~~ — `QueryService.get_columns` falls back to `definition['attributes']['columns']` (`service.py:470-471`) for *single* queries only; multi uses `columns_definition` by decision.

### Edit Sites (Blueprint Anchors)

Verified against: `869c443`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/interfaces/queries.py` | MODIFY | `            tenant: str \| None = None,` (inside `AbstractQuery.__init__`, followed by `            **kwargs`) | `interfaces/queries.py:55` | 1 |
| `querysource/interfaces/queries.py` | MODIFY | `        self._tenant_selector = tenant` | `interfaces/queries.py:104` | 1 |
| `querysource/queries/base.py` | MODIFY | `            tenant=tenant,` (inside `BaseQuery.__init__` super call) | `queries/base.py:39` | 1 |
| `querysource/queries/qs.py` | MODIFY | `            tenant: str \| None = None,` (inside `QS.__init__`) | `queries/qs.py:49` | 1 |
| `querysource/queries/qs.py` | MODIFY | `            loaded_def = await repo.get(identity)` | `queries/qs.py:177` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `            tenant: str \| None = None,` (inside `MultiQS.__init__`) | `queries/multi/__init__.py:107` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `            query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)` | `queries/multi/__init__.py:225` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                child_store = repo.registry.resolve(child_tenant)` (preflight loop → `resolve_child_owner`) | `queries/multi/__init__.py:324` | 1 |
| `querysource/repositories/definitions.py` | MODIFY | `        persisted = {k: v for k, v in persisted.items() if v is not None}` — **2 occurrences**: in `create()` (:303, preceded by `persisted = validated.to_dict()` at :300) and `upsert()` (:347); both get the same following line | `repositories/definitions.py:303, 347` | 2 |
| `querysource/tenant_models.py` | MODIFY | `    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)` | `tenant_models.py:50` | 1 |
| `querysource/models.py` | MODIFY | `    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)` | `models.py:67` | 1 |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | `    description VARCHAR,` | `docs/PER_TENANT_QUERIES.md:47` | 1 |
| `tests/tenants/conftest.py` | MODIFY | `    "description varchar, "` | `tests/tenants/conftest.py:29` | 1 |
| `querysource/handlers/tenant.py` | MODIFY | `        slug = request.match_info.get("slug")` | `handlers/tenant.py:240` | 1 |
| `querysource/handlers/tenant.py` | MODIFY | `    async def columns(self, request: web.Request) -> web.StreamResponse:` | `handlers/tenant.py:251` | 1 |
| `querysource/handlers/tenant.py` | MODIFY | `    async def test_slug(self, request: web.Request) -> web.StreamResponse:` | `handlers/tenant.py:266` | 1 |
| `querysource/handlers/service.py` | MODIFY | `            if query := await self.get_source(request, slug, conditions, driver=args, tenant=tenant):` — **3 occurrences**: in `query()` (preceded by `tenant = request.get('qs_tenant')` at 306), in `get_columns()` (437), in `columns()` (579); all three change identically | `handlers/service.py:307, 438, 580` | 3 |
| `querysource/handlers/service.py` | MODIFY | `            query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant)` | `handlers/service.py:705` | 1 |
| `querysource/handlers/multi.py` | MODIFY | `    async def columns(self, request: web.Request) -> web.StreamResponse:` | `handlers/multi.py:188` | 1 |
| `querysource/handlers/multi.py` | MODIFY | `        qs = MultiQS(` | `handlers/multi.py:335` | 1 |
| `tests/tenants/test_tenant_multi_dispatch.py` | CREATE | — | — | — |
| `tests/queries/test_preloaded_definition.py` | CREATE | — | — | — |
| `tests/tenants/test_integration.py` | MODIFY | append one opt-in test; anchor = end of file | — | — |
| `tests/tenants/test_tenant_http_routes.py` | MODIFY | `async def test_single_multi_inline_dispatch(monkeypatch) -> None:` (extend with `_FakeRepo` app key) | `tests/tenants/test_tenant_http_routes.py:79` | 1 |

---

## 7. Implementation Notes & Constraints

> Architecture decisions stay with the thinking model. A delegated
> implementation may only express a decision already recorded here and in
> the TASK's implementation blocks — it must never invent an API, choose a
> file, or resolve an open design question.

### Patterns to Follow
- Request-scoped routing context lives on the aiohttp request (`request['qs_tenant']`
  convention, `tenant.py:238`); never merge tenant or definition into conditions/params.
- Lazy imports of `QueryService`/`QueryHandler` inside the tenant handler methods
  (`tenant.py:242, 246`) — keep them lazy (import cycle avoidance) and keep them
  monkeypatchable at `querysource.handlers.service.QueryService` /
  `querysource.handlers.multi.QueryHandler` (the existing tests patch those paths).
- PBAC: tenant-isolated evaluator via `_enforce_owned_slug` (`abstract.py:463`);
  fail-closed (`web.HTTPNotFound`) on unexpected resolution errors, mirroring
  `_preflight_multiquery_owned` (`multi.py:157-165`).
- Error envelope: preserve TenantError machine codes (`err.code`) as in
  `service.py:315-324`; never collapse to 500.
- `tenant_models.py` must keep `Optional[...]` / `List[...]` typing (Cython
  `datamodel` validator; module docstring) — copy the `grouping` declaration.
- MultiQS-style JSON parsing of `query_raw`: use the query object's `_encoder`
  (`DefaultEncoder`) semantics; a parse failure is not an error on the tenant route
  (fallback decision), it is a warning.
- Google docstrings, strict typing, `self.logger`, async only; `ruff check` before commit.

### Known Risks / Gotchas
- **Strict runtime model**: `QueryModel.Meta.strict = True` — adding the field to
  `TenantQueryDefinition` without `QueryModel` makes `_runtime_model` raise on every
  tenant row once the DDL is deployed. M2 must land both models in one task.
- **Deployment order**: model before DDL. A tenant store *with* the column and a
  model *without* it breaks reads (`TenantQueryDefinition(**data)` rejects unknown
  keys); a model with the field and a store without the column is safe for reads.
  Writes including `columns_definition` on a store lacking it surface through
  `_translate_write_error` (`definitions.py:129`).
- **v3 parent PBAC gap**: `QueryHandler.query()` does not evaluate `slug:execute` on a
  stored multi parent (only inline children, `multi.py:293-323`). This spec closes it
  on the tenant route only (AC-9); v3 is untouched (non-goal).
- **`provider='multi'` + non-multi `query_raw`** → `MultiQS` single-query fallback
  (v3/scheduler parity); dry-run emits a warning. **Multi JSON under `provider='db'`**
  → single path, fails inside QS as today; document in `docs/PER_TENANT_QUERIES.md`.
- **Slug/format suffix**: the tenant handler must strip `:format` from
  `match_info['slug']` before building the `QueryIdentity` (both delegates split on
  `:` at `service.py:170` and `multi.py:206`); otherwise the peek looks up `slug:csv`.
- **Definition mismatch guard**: `QS`/`MultiQS` use the pre-loaded definition only when
  `definition.identity.slug == slug`; otherwise they load normally. Prevents a stale
  request key from being reused by a nested/child query object.
- **Children of a stored multi** are still loaded by `MultiQS` through the repository
  (`multi/__init__.py:329`); AC-4 covers the top-level slug only. The same holds for
  the `provider='multi'` + non-multi `query_raw` fallback: the synthetic single child
  (`multi/__init__.py:252-260`) is its own `QS` and performs its own load (design
  research S10 caveat, accepted).
- **Authorize before load** (S1): never call `repo.get()` before `_enforce_owned_slug`
  in the tenant handler; a deny and a missing slug must both be a plain 404.
- **Inline multi child PBAC** (S4, escalated): `_preflight_multiquery_owned` resolves
  every inline child against `request['qs_tenant']` (`multi.py:171-175`) even when the
  child declares another `tenant`; pre-existing FEAT-147 behavior, see §8 Q5.
- **Existing `attributes.columns` fallback** in `QueryService.get_columns`
  (`service.py:470-471`) is a single-query convention; do not extend it to multi.
- `describe.py:302-315` builds its own `QS` and passes `tenant=store.tenant`, an
  attribute `QueryStore` does not have — pre-existing, out of scope (see §8 Q3).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `aiohttp` | existing | handlers, request-scoped storage |
| `asyncdb` (`pg`) | existing | `DefinitionRepository` reads |
| `datamodel` | existing | `TenantQueryDefinition` / `QueryModel` field |
| `pytest`, `pytest-asyncio` | existing | unit + opt-in integration tests |

No new dependencies.

---

## Worktree Strategy

- **Isolation**: one feature worktree for the spec
  (`.claude/worktrees/feat-FEAT-151-multiquery-multitenant`, branched from
  `origin/dev`); the `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph** (evidence in parentheses):
  - M1 (queries layer: `definition=` kwarg, `resolve_child_owner`) — no in-spec dependency.
  - M2 (`columns_definition` field, DDL, repository write policy) — no in-spec dependency.
    **M1 ∥ M2**: disjoint files (`interfaces/queries.py`, `queries/base.py`, `queries/qs.py`,
    `queries/multi/__init__.py` vs `tenant_models.py`, `models.py`,
    `repositories/definitions.py`, `docs/`, `tests/tenants/conftest.py`).
  - M4 → M1 (`MultiQS(definition=)` at `multi.py:335`; `resolve_child_owner` in
    `test_slug`), M4 → M2 (`runtime.columns_definition` in `columns()`).
  - M3 → M1 (`QS(definition=)` via `get_source` kwargs), M3 → M4
    (`QueryHandler.test_slug` and the definition-aware `columns()` are dispatch targets).
  - M5 → M1–M4.
- **Shared files**: `querysource/handlers/multi.py` is modified only by M4;
  `querysource/handlers/tenant.py` and `handlers/service.py` only by M3;
  `queries/multi/__init__.py` only by M1; `tests/tenants/test_tenant_http_routes.py` only
  by M5. No file is modified by two modules, so no task serialization beyond the graph.
- **Exclusive resources**: none (no Cython/Rust rebuild, no lockfile, no DB migration
  executed by the code — the DDL is documentation plus the test fixture).
- **Cross-feature dependencies**: none to merge first. `falsy-refresh` (FEAT-149, tasks
  active) targets `queries/qs.py:385,414` and handler parameter coercion; its diff and
  M1's `qs.py:49,177` edits are in different functions, so the features may proceed in
  parallel with an ordinary merge.

---

## 8. Open Questions

> Questions that must be resolved before or during implementation.

- [x] Feature or hotfix, and base branch? — *Resolved in brainstorm*: feature on `dev`.
- [x] How does the tenant route decide single vs multi? — *Resolved in brainstorm*: peek at the stored definition; multi iff `provider == 'multi'` (no `query_raw` sniffing).
- [x] Which tenant routes are in scope? — *Resolved in brainstorm*: `GET/POST {slug}`, `HEAD/PATCH {slug}`, `GET/POST {slug}/test`; `/api/v3/queries` stays non-tenant.
- [x] Response contract for single slugs on the tenant route? — *Resolved in brainstorm*: exact v2 (`QueryService`) parity.
- [x] Is a second definition read acceptable? — *Resolved in brainstorm*: no; thread the `LoadedDefinition` into `QS`/`MultiQS`.
- [x] Dry-run semantics for a multi definition? — *Resolved in brainstorm*: validate without executing (children resolution, existence, ownership, per-child report).
- [x] Columns semantics for a multi definition? — *Resolved in brainstorm*: add a `columns_definition` list to `{tenant}.queries`, return it on HEAD/PATCH; 204 like v3 when empty; describing the resulting frame is a follow-up.
- [x] Field name collision with `Model.columns()`? — *Resolved in brainstorm*: name both the DDL column and the model field `columns_definition`; never `columns`.
- [x] Should the legacy `public.queries` table also gain the column? — *Resolved at spec time (user)*: yes, document `ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition TEXT[]` alongside the tenant DDL; both models declare the field.
- [x] Dry-run report envelope for multi? — *Resolved at spec time (user)*: extend the single envelope (`slug, works, error, generated, execution[, conditions, query]`) with `kind: "multi"`, `children`, `files`, `sources`, `warnings`; the single envelope is unchanged.
- [x] `provider='multi'` with non-multi `query_raw`? — *Resolved at spec time (user)*: keep the fallback (route to `QueryHandler`, `MultiQS` single-query mode, dry-run warning).
- [x] Test strategy? — *Resolved at spec time (author default, not re-asked)*: unit tests with the existing MagicMock/monkeypatch pattern plus one opt-in integration test gated by `tenant_services`.
- [ ] Q3: Should the FEAT-148 describe handler (`handlers/describe.py:302-315`) reuse `request['qs_definition']` and stop re-loading the definition (and fix its `tenant=store.tenant` call)? Suggested: separate follow-up, not in this feature. — *Owner: Jesus Lara*
- [x] Q4: Should `columns_definition` become writable through the management CRUD (`handlers/manager.py`) in this feature, or only via SQL until the "describe the resulting frame" follow-up populates it? Default: readable everywhere, writable through existing generic CRUD because `_TENANT_COLUMNS` is model-derived; no dedicated endpoint. — *Owner: Jesus Lara*: Yes, become writable.
- [ ] Q5 (design research S4, escalated): `QueryHandler._preflight_multiquery_owned` authorizes every *inline* child against the parent's store (`multi.py:171-175`) while `MultiQS` executes a child with an explicit `tenant` from that child's store (`multi/__init__.py:315-324`). Fix it in this feature by making the preflight consume `(slug, store)` pairs from `MultiQS.resolve_child_owner`, or open a separate FEAT-147 follow-up? — *Owner: Jesus Lara*

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: `gpt-5.6-luna` (codex-cli 0.156.1, reasoning
> effort high, 3 min 45 s) · Status: completed
> · Transcript: `sdd/state/FEAT-151/design_research/`

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Preserve authorization-before-definition lookup (risk) | CONFIRM | Verified: `QueryService` authorizes (`service.py:200-215`) before `get_source` (`:307`); peeking first would leak slug existence. Tenant handler now authorizes the parent identity before `repo.get()` on all routes and kinds; single path re-evaluates inside `QueryService` (accepted). | §2 Overview, §3 M3 `_prepare`, §5 AC-9, §4 `test_authorization_precedes_definition_load` |
| S2 | Centralize tenant load, error mapping, dispatch (architecture) | CONFIRM | One `_prepare()` helper shared by `query`/`columns`/`test_slug`; one `DefinitionRepository.get` per request asserted by test. | §3 M3, §4 `test_one_repository_read_per_request` |
| S3 | Thread the definition through the shared query base explicitly (api) | CONFIRM | Verified: `BaseQuery.__init__` forwards `tenant=` explicitly (`base.py:39`) and the rest via `**kwargs`; an unforwarded `definition` would be swallowed silently. Explicit forward added. | §3 M1, §6 Edit Sites `base.py:39` |
| S4 | Resolve child PBAC against each child owner (risk) | ESCALATE | Verified mismatch (`multi.py:171-175` vs `multi/__init__.py:315-324`) but it lives in the inline multi path (FEAT-147), not in stored-slug dispatch (`_queries == {}` there). Human decides scope. | §8 Q5, §7 gotcha |
| S5 | Pure child-resolution/preflight helper shared by execution and dry-run (architecture) | CONFIRM | `MultiQS.resolve_child_owner()` extracted from the preflight loop and reused by `QueryHandler.test_slug`; dry-run builds no executors, opens no connection, runs no EXPLAIN. | §3 M1/M4, §5 AC-15 |
| S6 | Handle multi columns directly, ownership before revealing declaration (api) | CONFIRM | Already designed (definition-aware `QueryHandler.columns`, exact empty-204 headers); S1 ordering guarantees ownership precedes disclosure; separate HEAD/PATCH tests listed. | §2, §3 M4, §4 |
| S7 | Migration / old-store write policy for `columns_definition` (risk) | CONFIRM | Verified: `create()`/`upsert()` INSERT every non-None field (`definitions.py:303, 347`), so a defaulted `[]` would fail on an un-migrated store. Repository omits the field when empty; `public.queries` gets the documented ALTER TABLE. | §2 Data Models, §3 M2, §5 AC-14, §6 Edit Sites |
| S8 | Exact single-parity and no-double-read contract tests (testing) | CONFIRM | Added parity, read-count and identity/revision propagation tests. | §4 |
| S9 | Opt-in integration suite for schema + stored-multi coverage (testing) | CONFIRM | Integration test extended with inheriting and explicit-`null` children and a revision assertion. | §4 Integration Tests |
| S10 | Reject malformed `provider='multi'` payloads with 422 on tenant routes (alternative) | REJECT | User decision (brainstorm Round 2, spec clarification): keep the `MultiQS` fallback for v3/scheduler parity. Its read-count observation is valid and recorded as an accepted exception to AC-4. | — (caveat noted in §7) |

Summary: **8** confirmed · **1** rejected · **1** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-24 | Jesus Lara | Initial draft from accepted brainstorm (Option A) |
