---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
---

# Brainstorm: Query Slug `describe` endpoint + relative-date keyword vocabulary

**Date**: 2026-09-15
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

A query slug is a contract: it takes named conditions (placeholders in
`query_raw`, typed by `cond_definition`, defaulted by `conditions`) and
returns a tabular result with a fixed set of columns. Today nothing in
QuerySource exposes that contract as a single, machine-readable JSON
document to a **non-admin caller**:

- `HEAD /api/v2/services/queries/{slug}` returns column **names only**, in
  the `X-Columns` response header (`handlers/service.py:359-439`).
- `PATCH /api/v2/services/queries/{slug}` returns a bare JSON list of column
  names, no types (`handlers/service.py:444-557`), and enforces
  `slug:execute`.
- `cond_definition`, `conditions` (defaults), `fields`/`grouping`/`ordering`,
  `is_cached`/`cache_timeout` are only reachable through the management
  plane (`/api/v1/management/queries/{slug}`, `handlers/manager.py`), which
  also returns `query_raw` and is meant for administrators.
- Column **types** are available for free on the PostgreSQL provider
  (`asyncpg` `stmt.get_attributes()` carries `.type`) but are discarded
  (`providers/pg.py:44-56` keeps only `a.name`).
- The relative-date keyword vocabulary (`TODAY`, `YESTERDAY`, `FDOM`,
  `LDOM`, `CURRENT_YEAR`, `CURRENT_MONTH`, `LAST_YEAR`, plus the ~50
  `cpdef` helpers in `utils/functions.pyx`) is not discoverable: it lives
  in a Rust static (`rust/src/validators.rs:20-35`), a Cython mirror that
  can be overridden by the `UDF_LIST` environment variable
  (`types/validators.pyx:27-33`), and functions without docstrings.

Who is affected:

- **ai-parrot agents.** FEAT-567 (ai-parrot) replaces `QSourceTool` with a
  `QuerySourceToolkit` whose `qs_describe` tool needs exactly this
  document to let an LLM pick a slug, know its parameters and column
  names, and use the date keywords instead of hard-coding dates.
- **A2UI "linked surfaces" (ai-parrot, next feature).** An agent will emit a
  widget/dashboard surface that carries a *data-source descriptor*
  (`slug` + `conditions` + declared params) instead of the data; the
  Navigator Svelte 5 renderer re-fetches the data through
  `/api/v2/services/queries/{slug}` with the viewer's JWT. The renderer
  needs the parameter contract (name, type, default, editable) to render
  a filter bar, and the agent needs it to build the descriptor
  deterministically.
- **`DatasetManager.QuerySlugSource.prefetch_schema()`** (ai-parrot) today
  executes the slug with `querylimit=1` just to learn column names and
  dtypes. A describe endpoint makes that a metadata call.

Why now: the linked-surfaces feature in ai-parrot is blocked on this
contract, and the user decided QuerySource ships it first as a new
minor release.

## Constraints & Requirements

- **Additive only.** No change to the payload or semantics of any existing
  route (`/api/v2/services/queries/{slug}` GET/POST/PATCH/HEAD, v3, v1
  management). Navigator's frontend consumes the bare-list `columns`
  payload and must keep working.
- **Routes (user decision):** `GET /api/v1/queries/{slug}/describe` and a
  sibling vocabulary endpoint under the same namespace,
  `GET /api/v1/queries/vocabulary`. Both are new paths; the `:suffix`
  convention (`slug:csv`, `:meta`) is not reused.
- **Never executes the query.** Column discovery uses a prepared statement
  (`conn.prepare`) where the provider supports it, otherwise the columns
  declared on the slug (`attributes.columns`). No `querylimit=1` sampling
  in v1 (user decision).
- **Never leaks `query_raw`.** The describe payload is for viewers, not
  administrators.
- **PBAC:** enforce `slug:read` on `ResourceType.SLUG` with
  `resource_name=<slug>` (user decision), fail-closed like every other
  handler (`handlers/abstract.py:317+`). `slug:read` is not granted by
  `policies/defaults.yaml` today, so the policy change is part of the
  feature.
- **Viewer credentials.** The endpoint runs under navigator-auth with the
  caller's session/JWT, like the rest of the service plane.
- **Stable, versioned JSON.** The response carries a `version` field;
  parameter types are normalised to a canonical lowercase vocabulary
  (the DB stores hints such as `"STRING"`; the Rust validator is
  case-insensitive, `rust/src/validators.rs:280-305`).
- **Distinguish user parameters from parser-owned placeholders.**
  `{where_cond}`, `{and_cond}`, `{filter}`, `{fields}`, `{group_by}`,
  `{grouping}`, `{order_by}`, `{ordering}`, `{querylimit}`, `{_limit}`,
  `{_offset}`, `{schema}`, `{table}` (`providers/sql.py:48-62`) are
  structural and must never be presented as user inputs.
- **Vocabulary must reflect runtime configuration.** `UDF_LIST`,
  `PG_CONSTANTS` and `PG_UDF` are environment-overridable
  (`types/validators.pyx:27-33`); the endpoint reports what this process
  actually accepts.
- **Out of scope, handled as a separate hotfix first:** the inconsistent
  resolution of date keywords when a condition is typed `date` /
  `datetime` / `timestamp`. `rust/src/validators.rs:296-298` quotes the
  value verbatim (`'FDOM'`) before the UDF check at `:320-328` is ever
  reached, and the Rust generic branch returns the quoted keyword "for the
  Python layer to resolve" while only the Cython path
  (`types/validators.pyx:552-553`) actually calls `to_udf`. The describe
  payload will *advertise* keyword support per parameter, so the hotfix
  is a prerequisite for the release that ships this feature (user
  decision: hotfix on `main`, then this feature).
- Python 3.10–3.13, aiohttp handlers, `pydantic` v2 already used for
  response models in `handlers/_pagination.py`.

---

## Options Explored

### Option A: Dedicated describe handler on the service plane + explicit vocabulary registry

A new `QueryDescribeHandler(AbstractHandler)` (`querysource/handlers/describe.py`)
with two coroutine methods, registered in `services.py` next to the other
service routes:

- `describe` → `GET /api/v1/queries/{slug}/describe`
- `vocabulary` → `GET /api/v1/queries/vocabulary`

`describe` enforces `slug:read`, instantiates `QS(slug=slug, conditions={},
request=request)`, calls `build_provider()` (which resolves the
`QueryModel` and the provider), reads the definition through
`provider.get_definition()` / `provider._get_cond_definition()`, derives the
parameter list (union of `cond_definition` keys, `conditions` default keys
and `{identifier}` placeholders found in `query_raw`, minus the structural
set), asks the provider for typed columns, and serialises a Pydantic
response model. `query_raw` never leaves the handler.

Typed columns come from one **additive** provider hook: a new
`BaseProvider.describe_columns() -> list[dict]` defaulting to
`[{"name": n, "type": None} for n in await self.columns()]`, overridden on
`pgProvider` to keep `attribute.type.name` from the prepared statement.
Existing `columns()` is untouched.

`vocabulary` serves a new explicit registry module
(`querysource/utils/vocabulary.py`): a curated list of keyword entries
(`name`, `category`, `returns`, `description`, `example`, `args`) built on
top of `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF` and a hand-selected subset of
`utils/functions.pyx`. The registry is the single source of truth the LLM
prompt and the frontend filter bar are generated from.

✅ **Pros:**
- Right plane, right permission: viewer-facing, `slug:read`, no SQL leak.
- Zero change to existing payloads; the handler is new code end to end.
- Column types with no query execution on the primary backend (pg).
- One JSON document serves ai-parrot (`qs_describe`), the Svelte renderer
  and `QuerySlugSource.prefetch_schema` alike.
- Vocabulary reflects the running process's `UDF_LIST` overrides.

❌ **Cons:**
- `prepare` needs a syntactically complete statement: placeholders without a
  default cannot be rendered, so those slugs fall back to declared columns
  (`columns_source: "declared"` or `"unavailable"`) with a warning.
- The vocabulary registry is a curated file that must be kept in sync by
  hand when `functions.pyx` gains helpers (no docstrings to introspect).
- Three small touch points (handler, provider hook, policy) instead of one.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | handler + routes | already the server stack |
| `pydantic` v2 | response models (`DescribeResponse`, `ParameterInfo`, `ColumnInfo`, `VocabularyResponse`) | already used in `handlers/_pagination.py:73-200` |
| `asyncpg` | `stmt.get_attributes()` → `attr.type.name` | already the pg driver via asyncdb |
| `navigator-auth` | PBAC `ResourceType.SLUG` + `slug:read` | already wired (`auth/_resource_types.py`) |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/service.py:359-439` — `get_columns`: the exact
  build-provider → `columns()` → fallback-to-`attributes.columns` sequence
  to mirror (and the `SlugNotFound` → 404 handling).
- `querysource/handlers/components.py:41-46` — the `slug:read` enforcement
  call shape and the Google-style docstring convention for new handlers.
- `querysource/providers/pg.py:44-56` — the prepared-statement column
  discovery to extend with types.
- `querysource/providers/abstract.py:128-146` — `get_definition()` and
  `_get_cond_definition()`.
- `querysource/providers/sql.py:37-62` — the `replacement` defaults
  (`firstdate`/`lastdate`/`filterdate` → `current_date`) and the
  `_PARSER_PLACEHOLDERS` structural set.
- `rust/src/safe_dict.rs:116-120` — placeholder key grammar
  (`[A-Za-z0-9_.]`) to reuse for placeholder extraction from `query_raw`.
- `rust/src/validators.rs:20-35`, `querysource/types/validators.pyx:27-33`
  — `UDF_LIST`, `PG_CONSTANTS`, `PG_UDF`.
- `querysource/utils/functions.pyx` — the date helpers the vocabulary
  documents (`today:94`, `fdom:133`, `ldom:183`, `yesterday:217`,
  `previous_month:111`, `last_year:162`, `date_diff:454`, `date_sum:503`,
  `days_ago:264`, `fdow:147`, `ldow:150`, ...).
- `querysource/handlers/_pagination.py:188-200` — `PaginationMeta` /
  `PaginatedResponse` as the Pydantic response-model precedent.
- `policies/defaults.yaml` — `admin_full_access` policy to extend with
  `slug:read`.

---

### Option B: Enrich the existing `columns` (PATCH) response on v2

Change `QueryService.columns` (`handlers/service.py:444-557`) so that it
returns an object (`{"columns": [...], "parameters": [...], ...}`) instead of
a bare list, optionally behind a `?describe=1` flag, and add the vocabulary
block to the same payload.

✅ **Pros:**
- Smallest diff; no new handler or route registration.
- Reuses the provider/columns plumbing already in place.

❌ **Cons:**
- The bare-list payload is a live contract consumed by Navigator; changing
  it (or forking it behind a flag) is exactly the kind of breaking change
  ruled out.
- Stays on the v2 route whose `slug:format` suffix parsing and
  `slug:execute` enforcement do not fit a read-only describe.
- Does not satisfy the user's chosen route (`/api/v1/queries/{slug}/describe`).

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | same handler | no additions |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/service.py:444-557` — the method itself.

---

### Option C: Management-plane `:describe` meta on `QueryManager`

Extend the `{meta}` suffix handling in `handlers/manager.py:81-89` (which
already answers `:meta` with `QueryModel.schema(as_dict=True)`) with a
`:describe` variant on `/api/v1/management/queries/{slug}`, returning the
stored `QueryModel` row projected to the describe shape.

✅ **Pros:**
- The model row is already loaded there; no provider round-trip.
- Consistent with the only existing `:meta` precedent.

❌ **Cons:**
- Wrong plane: management routes are administrative and return `query_raw`;
  the describe consumer is a viewer with a JWT and `slug:read`.
- No provider access means no column types and no columns at all for slugs
  without `attributes.columns`.
- Does not give the vocabulary a home.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `asyncdb` | `QueryModel` access | already used |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/manager.py:81-118` — `:meta` / `insert` dispatch.

---

### Option D (unconventional): Derive columns from `dry_run` + SQL parsing

Run `QS.dry_run()` (`queries/qs.py:529-536`) to obtain the rendered
statement without executing it, then parse the projection list with a SQL
parser (e.g. `sqlglot`) to name and type-guess the columns for every SQL
dialect, not just PostgreSQL.

✅ **Pros:**
- Backend-agnostic column names (BigQuery, SQL Server, MySQL) without a
  live connection.

❌ **Cons:**
- New heavyweight dependency; type inference from SQL text is a guess
  (`SELECT *`, CTEs, functions).
- `dry_run` behaviour differs per provider (`providers/*.py` each implement
  it) and some already hit the datasource.
- Adds a second source of truth for columns next to `prepare`.

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `sqlglot` | parse rendered SQL projections | new dependency, multi-dialect |

🔗 **Existing Code to Reuse:**
- `querysource/queries/qs.py:529-536` — `dry_run`.

---

## Recommendation

**Option A** is recommended because it is the only option that satisfies
all four user decisions at once (new `/api/v1/queries/...` routes, `prepare`
+ declared fallback, `slug:read`, standalone vocabulary endpoint) while
keeping every existing payload byte-identical. Option B trades a smaller
diff for a breaking change on a contract Navigator depends on; Option C
puts a viewer-facing document on the administrative plane and cannot
provide typed columns; Option D buys dialect coverage with a new dependency
and a second, guessed, source of truth. Option A's real cost is the curated
vocabulary registry and the `prepare` limitation on slugs whose placeholders
have no defaults; both are visible in the payload (`columns_source`,
`warnings`) rather than hidden.

---

## Feature Description

### User-Facing Behavior

`GET /api/v1/queries/{slug}/describe` (authenticated, `slug:read`) returns
`200 application/json`:

```json
{
  "version": "1.0",
  "slug": "epson_field_activity",
  "description": "Field activity by store and day",
  "provider": "db",
  "driver": "pg",
  "parser": "SQLParser",
  "program": {"id": 1, "slug": "epson"},
  "parameters": [
    {"name": "firstdate", "type": "date", "raw_type": "date", "default": "current_date",
     "required": false, "source": "cond_definition", "accepts_keywords": true},
    {"name": "lastdate",  "type": "date", "raw_type": "date", "default": "current_date",
     "required": false, "source": "cond_definition", "accepts_keywords": true},
    {"name": "store_id",  "type": "integer", "raw_type": "INTEGER", "default": null,
     "required": true, "source": "placeholder", "accepts_keywords": false}
  ],
  "structural_placeholders": ["and_cond", "fields", "querylimit"],
  "columns": [
    {"name": "visit_date", "type": "date"},
    {"name": "store_id", "type": "integer"},
    {"name": "visits", "type": "integer"}
  ],
  "columns_source": "prepare",
  "capabilities": {
    "fields": true, "filter": true, "group_by": true, "order_by": true,
    "querylimit": true, "pagination": true, "refresh": true,
    "hierarchical_filtering": false
  },
  "declared": {"fields": [], "grouping": [], "ordering": [], "filtering": {}},
  "cache": {"enabled": true, "timeout_seconds": 3600, "refresh_param": "refresh"},
  "vocabulary_url": "/api/v1/queries/vocabulary",
  "warnings": []
}
```

- `parameters[].type` is the canonical lowercase name from the set the
  validator understands (`literal, integer, float, numeric, decimal, epoch,
  boolean, string, field, date, datetime, timestamp, uuid, array, json,
  numrange, int4range, int8range`), or `null` when no hint exists;
  `raw_type` echoes the stored hint verbatim.
- `source` says where the parameter was discovered: `cond_definition`,
  `conditions` (default only), or `placeholder` (found in `query_raw` with
  neither hint nor default).
- `required` is `true` when the parameter has no default and is not
  structural.
- `accepts_keywords` is `true` for `date`/`datetime`/`timestamp` parameters
  (after the prerequisite hotfix lands) and for untyped ones.
- `columns_source` is `prepare` (typed, from the prepared statement),
  `declared` (names from `attributes.columns`, types `null`), or
  `unavailable` (neither), each with an explanatory entry in `warnings`.
- `query_raw`, `dwh_*`, `cache_options`, `created_by`/`updated_by` are never
  included.

`GET /api/v1/queries/vocabulary` (authenticated) returns:

```json
{
  "version": "1.0",
  "case_insensitive": true,
  "keywords": [
    {"name": "TODAY", "category": "date", "returns": "date",
     "description": "Current date in the server timezone (UTC by default).",
     "example": "2026-09-15"},
    {"name": "YESTERDAY", "category": "date", "returns": "date", "...": "..."},
    {"name": "FDOM", "category": "date", "returns": "date",
     "description": "First day of the current month.", "example": "2026-09-01"},
    {"name": "LDOM", "category": "date", "returns": "date", "...": "..."},
    {"name": "CURRENT_YEAR", "category": "date", "returns": "integer", "...": "..."},
    {"name": "CURRENT_MONTH", "category": "date", "returns": "string", "...": "..."},
    {"name": "LAST_YEAR", "category": "date", "returns": "integer", "...": "..."}
  ],
  "constants": ["CURRENT_DATE", "CURRENT_TIMESTAMP"],
  "functions": [
    {"name": "date_diff", "args": [{"name": "value"}, {"name": "diff", "default": 1},
     {"name": "mode", "default": "days"}], "description": "..."}
  ],
  "usage": "Pass a keyword as the condition value, e.g. {\"firstdate\": \"FDOM\", \"lastdate\": \"TODAY\"}."
}
```

`keywords` is exactly the process's effective `UDF_LIST` (environment
override included), each enriched from the curated registry; a keyword
present in `UDF_LIST` but absent from the registry is still listed, with
`description: null`, so the endpoint never hides an accepted value.

### Internal Behavior

1. **Route registration** in `services.py` next to the service routes: two
   `add_get` calls on a `QueryDescribeHandler()` instance.
2. **`describe`**: read `slug` from `match_info`; enforce
   `_enforce_pbac(request, ResourceType.SLUG, slug, "slug:read")`; build
   `QS(slug=slug, conditions={}, request=request)` and `await
   build_provider()`; map `SlugNotFound` → 404, `ParserError` → 406 with the
   handler's standard error envelope.
3. **Parameter analysis** (pure function, unit-testable): from the
   definition take `cond_definition`, `conditions`, `fields`, `grouping`,
   `ordering`, `filtering`, `h_filtering`, `is_cached`, `cache_timeout`,
   `provider`, `parser`, `program_id`, `program_slug`, `description`, and
   the placeholders extracted from `query_raw` with the `[A-Za-z0-9_.]`
   grammar. Structural placeholders are the `_PARSER_PLACEHOLDERS` set;
   the provider `replacement` map supplies implicit defaults
   (`firstdate`/`lastdate`/`filterdate` → `current_date`). Types are
   normalised to the canonical set.
4. **Column discovery**: `await provider.describe_columns()` under a short
   timeout; on `ParserError`/`DriverError`/timeout fall back to
   `definition.attributes.columns`; record `columns_source` and a warning.
5. **Serialisation** through Pydantic models; `web.json_response` with the
   handler's default headers.
6. **`vocabulary`**: enforce authentication only (no slug resource);
   compose the effective `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF` from
   `querysource.types.validators` with the curated registry in
   `querysource/utils/vocabulary.py`; serialise.
7. **Policy**: add `slug:read` to `admin_full_access` in
   `policies/defaults.yaml` and document in `policies/HARDENING.md` that
   describe requires it.
8. **Release**: `CHANGES.rst` entry, version bump to `4.6.0` (new public
   endpoints → minor), then the standard `/release` flow.

### Edge Cases & Error Handling

- Unknown slug → `404` (same envelope as `get_columns`). PBAC deny → `404`
  (fail-closed convention, no existence oracle).
- Provider cannot connect (datasource down) → still `200` with the static
  part of the payload, `columns_source: "unavailable"`, and a warning; the
  contract must stay useful for the agent even when the data plane is not.
- Placeholders without defaults make `prepare` impossible → `declared` or
  `unavailable`, warning `prepare_skipped: unresolved placeholders [...]`.
- Non-SQL providers (REST, Mongo, BigQuery without prepare support) →
  `describe_columns()` default implementation yields untyped names from
  `columns()` when that provider implements it, else the declared fallback.
- `is_raw` slugs and `MultiQuery` (v3) slugs: v1 covers single-provider
  slugs only; a MultiQuery slug returns `400` with a clear message
  (open question).
- Structural placeholder that is *also* listed in `cond_definition`
  (misconfigured slug) → treated as structural, warning emitted.
- `UDF_LIST` overridden by environment → `keywords` reflects the override.
- Very large `attributes.columns` lists are returned as-is (no pagination).

---

## Capabilities

### New Capabilities
- `queryslug-describe`: viewer-facing JSON contract of a query slug
  (parameters, typed columns, capabilities, cache) plus the relative-date
  keyword vocabulary endpoint.

### Modified Capabilities
- `pbac-support`: `slug:read` becomes a granted action in the default
  admin policy and is documented as the describe permission.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/services.py` | modifies | registers `GET /api/v1/queries/{slug}/describe` and `GET /api/v1/queries/vocabulary` |
| `querysource/handlers/describe.py` | new | `QueryDescribeHandler(AbstractHandler)`, Pydantic response models, parameter analysis |
| `querysource/providers/abstract.py` | extends | additive `describe_columns()` default (names only) |
| `querysource/providers/pg.py` | extends | `describe_columns()` keeps `attribute.type.name` from the prepared statement; `columns()` unchanged |
| `querysource/utils/vocabulary.py` | new | curated keyword/function registry over `UDF_LIST` / `PG_CONSTANTS` / `functions.pyx` |
| `policies/defaults.yaml`, `policies/HARDENING.md` | modifies | grant + document `slug:read` |
| `CHANGES.rst`, `querysource/version.py` | modifies | changelog entry, bump to 4.6.0 |
| `tests/` | new | handler tests (mocked provider), parameter-analysis unit tests, pg typed-columns test, vocabulary test with `UDF_LIST` override |
| ai-parrot `parrot_tools` (FEAT-567 `QuerySourceToolkit.qs_describe`) | depends on | downstream consumer, separate repo |
| ai-parrot `parrot/tools/dataset_manager/sources/query_slug.py` (`prefetch_schema`) | depends on (later) | can replace the `querylimit=1` probe with describe |
| ai-parrot A2UI linked surfaces (next feature) | depends on | parameter contract + vocabulary feed the data-source descriptor |
| Rust/Cython UDF typed resolution (separate hotfix) | prerequisite | `rust/src/validators.rs:296-328`, `querysource/types/validators.pyx:552-553` |

No breaking changes. No new runtime dependencies.

---

## Code Context

### User-Provided Code

```text
# Source: user-provided (conversation, 2026-09-15)
# Frontend today calls QuerySource as:
#   POST /api/v2/services/queries/{query_slug}
#   payload: {"lastdate": "2026-08-15", "firstdate": "2026-08-09"}
# Decided routes for this feature:
#   GET /api/v1/queries/{slug}/describe
#   GET /api/v1/queries/vocabulary
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/handlers/abstract.py:27
class AbstractHandler(BaseHandler):
    debug: bool = DEBUG                                   # line 31
    def post_init(self, *args, **kwargs): ...             # line 33 — sets self.logger, self.slug, self._columns
    async def _enforce_pbac(                              # line 317
        self, request: web.Request, resource_type, resource_name: str, action: str
    ) -> None: ...                                        # fail-closed: deny → web.HTTPNotFound

# From querysource/handlers/service.py
class QueryService(AbstractHandler):
    async def query(self, request): ...                   # GET/POST /api/v2/services/queries/{slug}
    async def get_columns(self, request): ...             # line 359 — HEAD, X-Columns header
    async def columns(self, request): ...                 # line 444 — PATCH, bare list JSON

# From querysource/handlers/components.py:20
class ComponentHandler(AbstractHandler):
    async def list_components(self, request: web.Request) -> web.Response:   # line 27
        await self._enforce_pbac(request, resource_type=ResourceType.SLUG,
                                 resource_name="components", action="slug:read")  # lines 41-46

# From querysource/queries/qs.py:43
class QS(...):
    def __init__(self, slug: str = '', conditions: dict = None,
                 request: web.Request = None, loop=None, **kwargs): ...  # line 43
    async def columns(self): ...                          # line 126 — delegates to self._qs.columns()
    async def build_provider(self): ...                   # line 134
    async def dry_run(self): ...                          # line 529 — returns [result, error]
    is_cached: bool                                       # line 63; set from objquery.is_cached at 193
    timeout: property                                     # lines 114-121; set from objquery.cache_timeout at 196

# From querysource/providers/abstract.py
class BaseProvider:
    def get_definition(self) -> Union[QueryModel, dict]: ...   # line 128
    def _get_cond_definition(self) -> dict: ...                # line 133 — normalises None → {}
    async def columns(self): ...                               # line 190 — raises NotImplementedError
    def refresh(self): return self._refresh                    # line 220 — popped from conditions at 78-82
    def checksum(self): return get_hash(self._query)           # line 229

# From querysource/providers/pg.py:44
class pgProvider(...):
    async def columns(self):
        async with await self._connection.connection() as conn:
            stmt, _ = await conn.prepare(self._query)          # line 50
            self._columns = [a.name for a in stmt.get_attributes()]   # line 51 — type discarded

# From querysource/models.py:48
class QueryModel(Model):
    query_slug: str          # primary key, line 49
    description: str         # line 50
    conditions: dict         # jsonb defaults, line 61
    cond_definition: dict    # jsonb type hints, line 62
    fields: List[str]        # line 64
    filtering: dict          # line 65
    ordering: List[str]      # line 66
    grouping: List[str]      # line 67
    h_filtering: bool        # line 69
    query_raw: str           # line 71
    is_raw: bool             # line 72
    is_cached: bool = True   # line 73
    provider: str = 'db'     # line 74
    parser: str = 'SQLParser'   # line 75
    cache_timeout: int = 3600   # line 76
    program_id: int = 1 ; program_slug: str = 'default'   # lines 80-81

# From querysource/handlers/_pagination.py
class PaginationMeta(BaseModel): ...        # line 188 — pydantic v2 response-model precedent
class PaginatedResponse(BaseModel): ...     # line 197
```

#### Verified Imports
```python
from querysource.handlers.abstract import AbstractHandler      # querysource/handlers/components.py:15
from querysource.auth import ResourceType                       # querysource/handlers/components.py:16
from querysource.auth._resource_types import ResourceType       # querysource/auth/_resource_types.py (shim)
from querysource.queries.qs import QS                           # querysource/queries/qs.py:43
from querysource.models import QueryModel                       # querysource/models.py:48
from querysource.types.validators import is_udf                 # querysource/utils/functions.pyx:26
from querysource.utils.functions import fdom, ldom              # querysource/libs/functions/__init__.py:3
```

#### Key Attributes & Constants
- `UDF_LIST` → `["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]` (`rust/src/validators.rs:21-35`; env-overridable mirror `querysource/types/validators.pyx:27-33`)
- `PG_CONSTANTS` → `["CURRENT_DATE", "CURRENT_TIMESTAMP"]`; `PG_UDF` → `["now()"]` (`querysource/types/validators.pyx:31-33`)
- `replacement` → `{"fields": "*", "filterdate": "current_date", "firstdate": "current_date", "lastdate": "current_date", "where_cond": "", "and_cond": "", "filter": ""}` (`querysource/providers/sql.py:37-45`)
- `_PARSER_PLACEHOLDERS` → `where_cond, and_cond, filter, fields, group_by, grouping, order_by, ordering, querylimit, _limit, _offset, schema, table` (`querysource/providers/sql.py:48-62`)
- Placeholder key grammar → non-empty, no `{`, chars in `[A-Za-z0-9_.]` (`rust/src/safe_dict.rs:116-120`)
- `cond_definition` type dispatch (case-insensitive) → `literal | int | integer | float | numeric | decimal | epoch | boolean | string | varchar | field | date | datetime | timestamp | uuid | array | json` (`rust/src/validators.rs:280-305`); `numrange`/`int4range`/`int8range` in `rust/src/pgsql_parser.rs:371-393`
- Date keyword set accepted by `date_diff`/`date_sum` values → `current_date | now | today | yesterday | tomorrow | fdom | ldom | fdcw` (`querysource/utils/functions.pyx:475, 535`)
- Cache key → `sha256(rendered_query)` (`providers/abstract.py:229`, `utils/functions.pyx:39-40`); write `conn.setex(checksum, data, timeout)` (`interfaces/queries.py:296-361`)
- Existing action strings → `slug:execute`, `slug:read`, `slug:list`, `raw_query:execute`, `datasource:use`, `driver:use` (`handlers/service.py:186-192`, `handlers/components.py:41-46`, `policies/defaults.yaml`)
- Ledger → next feature id `FEAT-147`, next task id `TASK-716` (`sdd/tasks/.id_ledger.json`)
- Version → `4.5.16` (`querysource/version.py:9`); release via `.claude/commands/release.md` (bump on `dev` → PR to `main` → GitHub release → `release.yml`)

### Does NOT Exist (Anti-Hallucination)
- ~~`QS.get_definition()`~~ — only `BaseProvider.get_definition()` exists (`providers/abstract.py:128`); reach it through `qs.get_source().get_definition()`.
- ~~`{today}` / `{today-7}` / `{fdom}` placeholder expressions~~ — the Rust substitution only replaces simple identifiers; keywords are resolved as condition **values**, never as placeholder names or arithmetic.
- ~~A `:describe` or `:meta` suffix on `/api/v2/services/queries/{slug}`~~ — the `:` suffix there means output format (`slug:csv`); `:meta` exists only on `/api/v1/management/queries` and returns `QueryModel.schema()`.
- ~~`QueryHandler.columns` returning data~~ — `handlers/multi.py:101-107` always answers 204 "No Columns available".
- ~~Docstrings on `utils/functions.pyx` helpers~~ — `today`, `fdom`, `ldom`, `previous_month`, ... have none; the vocabulary cannot be introspected and must be a curated registry.
- ~~`aiohttp_swagger` / generated OpenAPI~~ — absent; documentation is docstring YAML (`service.py:444-469`) or Google-style (`components.py`).
- ~~`slug:read` granted in `policies/defaults.yaml`~~ — only `slug:execute` and `slug:list` are granted today.
- ~~A `/api/v1/queries/...` namespace~~ — does not exist yet; `/api/v1/management/queries` and `/api/v2/services/queries` are the current ones.
- ~~`previous_year()` returning an int~~ — `functions.pyx:107` returns a `datetime` despite the `cpdef int` signature; exclude from the vocabulary until fixed.

---

## Parallelism Assessment

- **Internal parallelism**: three independent lanes — (1) handler + Pydantic
  models + parameter analysis + route registration, (2) `describe_columns()`
  provider hook (`abstract.py` + `pg.py`) with its asyncpg test, (3)
  vocabulary registry + endpoint + policy/doc updates. Lane 1 depends on
  lane 2 only at integration time (it can stub `describe_columns()`).
- **Cross-feature independence**: no in-flight spec touches
  `handlers/describe.py` or `utils/vocabulary.py`. The prerequisite hotfix
  (UDF typed resolution) touches `rust/src/validators.rs` and
  `types/validators.pyx`, which this feature only *reads*; land the hotfix
  on `main` first and let sync-down bring it to `dev`.
- **Recommended isolation**: mixed.
- **Rationale**: lanes 2 and 3 are small, self-contained files; only lane 1
  needs the shared `services.py` edit, so it should be the last task to
  merge.

---

## Open Questions

- [x] Route shape for describe — *Owner: Jesus Lara*: `GET /api/v1/queries/{slug}/describe`; vocabulary at `GET /api/v1/queries/vocabulary`.
- [x] Column/type discovery strategy — *Owner: Jesus Lara*: prepared statement where supported, fallback to declared `attributes.columns`; never execute.
- [x] Where the date-keyword vocabulary lives — *Owner: Jesus Lara*: its own endpoint, referenced from describe.
- [x] The inconsistent UDF resolution for `date`-typed conditions (Rust quotes before the UDF check; only Cython calls `to_udf`) — *Owner: Jesus Lara*: separate hotfix on `main`, prerequisite for this feature's release.
- [x] PBAC action for describe — *Owner: Jesus Lara*: `slug:read`, added to `policies/defaults.yaml`.
- [ ] Should describe accept query-string conditions (like `get_columns` does) so a caller can help `prepare` render placeholders that have no default, or always run with defaults only? — *Owner: Jesus Lara*
- [ ] Should the vocabulary endpoint require only authentication, or also a PBAC action (e.g. `slug:list`)? — *Owner: Jesus Lara*
- [ ] In-process TTL cache for describe results (keyed by slug + `updated_at`) — needed for v1, or defer until the Svelte renderer shows load? — *Owner: Jesus Lara*
- [ ] MultiQuery (v3) slugs: out of scope for v1 (400 with message) or minimal support returning the declared columns of the final step? — *Owner: Jesus Lara*
- [ ] Which `functions.pyx` helpers enter the curated `functions` block of the vocabulary in v1 (`date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow`/`ldow` are the obvious candidates)? — *Owner: Jesus Lara*
- [ ] Does the hotfix ship as `4.5.17` before `4.6.0`, or is it folded into the `4.6.0` release notes as a prerequisite item? — *Owner: Jesus Lara*
