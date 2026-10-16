---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [providers, auth, datasources, handlers, querysource]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [semantic-layer, sidemantic, ossie, pbac, metrics, sql-compiler]
---

# Brainstorm: Querysource Semantic Layer (Ossie models compiled by Sidemantic)

**Date**: 2026-10-14
**Author**: Jesus Lara
**Status**: accepted
**Recommended Option**: A

---

## Problem Statement

QuerySource executes stored queries (slugs) whose `query_raw` is SQL or a
provider-specific payload. Business metrics such as "net sales" or "units" are
therefore re-written by hand in every slug, every MultiQS pipeline and every AI
agent that reaches the warehouse. The same KPI drifts between consumers, there
is no single description of what a metric means, and governance (who may see
which metric, which rows) is applied per slug instead of per metric.

We want a **semantic resource** type: a slug whose definition is a semantic
model (Apache Ossie YAML, or Sidemantic native YAML) plus operational
configuration (connection, compiler). Callers never write SQL; they send a
structured request (metrics, dimensions, filters, time grain, limit). QuerySource
compiles it to SQL for the target warehouse dialect, applies governance
(PBAC on the catalog, row-level security at compile time) and executes it
through the existing provider/connection machinery, returning the usual
QuerySource outputs.

Affected users:

- **API consumers and dashboards** get governed metrics through the existing
  `/api/v1/queries/{slug}` route with the usual output formats.
- **AI agents (ai-parrot / MCP tools)** get a deterministic, SQL-free contract
  that cannot leak metrics or rows the principal is not entitled to.
- **Data owners** version metric definitions in the query store or in a
  repo directory and change them through PR / management endpoints.
- **Operators** keep one execution path (asyncdb providers, PBAC, cache).

Why now: PBAC (FEAT-150/151) and the per-tenant query store already give us the
catalog, ownership and principal plumbing; Sidemantic 0.12.0 (2026-09-13)
ships compile-time RLS and a first-class Apache Ossie importer, so the compiler
no longer has to be written in-house.

## Constraints & Requirements

Decisions taken during discovery (Rounds 0–2) are binding for the spec:

- **Flow**: `type: feature`, `base_branch: dev`.
- **Entry point**: a new `BaseProvider` subclass (`semanticProvider`) dispatched
  by the existing slug path (`QS.build_provider` → `Connection.get_provider` →
  `load_provider('semantic')`). The Python `compile()/execute()` API is a thin
  wrapper on top of the same code, not a separate engine.
- **Model store**: the DB record (`QueryModel.query_raw`) holds the QuerySource
  resource YAML (`kind: semantic`, `connection`, `semantic:` block). The
  `semantic.definition` is either an **inline** model document or a **relative
  path** resolved under a configured directory (`QS_SEMANTIC_MODELS_DIR`).
  Inline wins when both are present.
- **Dependency policy**: Sidemantic is an **optional extra**
  (`querysource[semantic]`), used **compile-only** (never DuckDB execution).
  Execution always goes through the datasource's asyncdb connection. A small
  `SemanticCompiler` Protocol keeps Sidemantic objects out of the public
  contract so another compiler can be plugged in later.
- **Governance, both layers**: navigator-auth PBAC gates the resource and the
  per-metric / per-dimension allow-list (new `ResourceType.SEMANTIC`); when PBAC
  is disabled everything is allowed, exactly like `enforce_principal` today.
  Sidemantic `SecurityPolicy` (access gate + `row_filters`) is enforced at
  compile time from `user_attributes` derived from the session / principal, and
  stays deny-by-default for secured models even without PBAC.
- **Filter contract**: structured `{field, op, value}` objects **plus**
  validated predicate strings (parsed with sqlglot, columns checked against the
  model's dimensions, operator allow-list). Nothing reaches the compiler
  unvalidated.
- **Resolved on 2026-10-14 (see Open Questions)**: explicit `semantic` branch
  in `Connection.get_provider()`; `pydantic.BaseModel` for the semantic models
  and `pydantic>=2` declared as a core dependency; AGPL extra approved as an
  unmodified dependency; `user_attributes` always in the
  `QSPrincipal.to_userinfo()` shape; `ResourceType.SEMANTIC` via the shim now
  and upstream later; field permissions named `<slug>:metric:<name>` /
  `<slug>:dimension:<name>` with action `semantic:use`; inline Ossie through a
  temp file and the public adapter API; request-level `time_grain` rewrites
  unsuffixed time dimensions; a catalog endpoint
  `GET /api/v1/semantic/{slug}/catalog` ships in v1; role-based
  `governance.row_filters` live in the resource YAML.
- **Targets**: any dialect Sidemantic supports; dialect is derived from the
  datasource driver (`pg`/`pg_admin`/`postgres` → `postgres`, `bigquery`,
  `mysql`, `mariadb` → `mysql`, `sqlserver` → `tsql`, `clickhouse`, `oracle`,
  `sqlite`, …). Unknown drivers compile with the ANSI generator and a warning.
- **Compile result is an object**, not a string: `sql`, `parameters`,
  `connection`, `dialect`, `model_version`, `warnings`, plus the resolved
  metric/dimension list.
- **Async-first, no event-loop blocking**: compilation is CPU-bound Python
  (sqlglot); it must be short and cached per model revision. Loading a model
  must never open a warehouse connection.
- **No raw SQL from callers**: never concatenate user input into SQL; filters
  are rendered through sqlglot/Sidemantic literal quoting.
- **Licensing**: Sidemantic is AGPL-3.0; QuerySource only imports it as an
  unmodified optional dependency. Legal sign-off recorded as an open question.
- **Code standards**: aiohttp handlers, `navconfig` for settings, `self.logger`,
  Google docstrings, strict type hints, tests in `tests/`.

---

## Options Explored

### Option A: Sidemantic adapter behind a `SemanticCompiler` Protocol (compile-only)

Add a `querysource/semantic/` package with: the public models
(`SemanticQuery`, `SemanticFilter`, `CompiledQuery`, `SemanticModelRef`,
`CompilationTarget`), the `SemanticCompiler` Protocol, a model loader
(inline / directory, Ossie or Sidemantic native YAML, cached per definition
revision), a dialect map from datasource drivers, a filter validator, and a
`SidemanticCompiler` implementation. Add `querysource/providers/semantic.py`
(`semanticProvider`, `__parser__ = None`) that: parses `query_raw` as the
resource YAML, resolves the datasource by name, builds the `SemanticQuery` from
the request conditions, applies PBAC on the catalog, compiles with
`user_attributes`, runs the SQL on the asyncdb connection and returns the
result like `defaultProvider.query`. `dry_run()` returns the compiled SQL.

✅ **Pros:**
- Covers Ossie *and* 14 other formats for free; symmetric aggregates, ratio /
  derived / cumulative metrics, time grains, relationships all come from
  Sidemantic 0.12.0.
- Compile-time RLS (`SecurityPolicy`, deny-by-default, typed literal rendering)
  is exactly the second governance layer we decided on.
- Execution stays on QuerySource's asyncdb providers, PBAC, cache, writers and
  tenant stores; nothing new to operate.
- The Protocol keeps the public contract QuerySource-owned, so the compiler
  can be swapped (Option B) without touching callers.

❌ **Cons:**
- AGPL-3.0 single-author dependency; DuckDB is a hard transitive dependency even
  when never used for execution (`SemanticLayer()` opens an in-memory DuckDB by
  default; we must construct it with a non-executing adapter or accept the
  import cost).
- Inline Ossie documents: `OssieAdapter.parse_document` takes a *path*; inline
  support needs either a temp file or the lower-level
  `parse_ossie_document(bytes, ...)` API (internal, may change).
- "Young project" per its own README; converting formats may hit rough edges.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `sidemantic>=0.12.0,<0.13` | Semantic model + SQL compiler, RLS | AGPL-3.0, Python ≥3.11, released 2026-09-13; hard deps: duckdb≥1.0, sqlglot≥30.1, pydantic≥2, pyyaml≥6, jinja2, antlr4, click, typer. **No** `sidemantic-rs` in `requires_dist`. |
| `sidemantic[ossie]` | Offline JSON-Schema validation of Ossie docs | adds `jsonschema<5,>=4.23`; schemas vendored and checksum-pinned (0.1.1 / 0.2.0.dev0 profiles). |
| `sqlglot>=30.1` | Parse/validate predicate strings, render literals, dialect names | Already installed transitively (30.20.0 in venv) but **not declared** in `pyproject.toml`; declare it in the extra. |
| `pydantic>=2` | `SemanticQuery`, `CompiledQuery`, `SemanticResource` models | Decided: pydantic v2 for the whole `querysource/semantic/` package; 2.12.5 installed, to be declared as a core dependency. |
| `pyyaml>=6` | Parse the resource YAML in `query_raw` | 6.0.3 installed, transitive. |

🔗 **Existing Code to Reuse:**
- `querysource/providers/abstract.py` — `BaseProvider` (`__init__`, `dry_run`, `query`, `close`, `columns`, `describe_columns`).
- `querysource/providers/default.py` — `defaultProvider.query()` pattern (`async with await self._connection.connection() as conn: result, error = await conn.query(sql)`).
- `querysource/queries/qs.py` — `QS.build_provider()` slug path (repository read, `get_provider`, conditions merge).
- `querysource/interfaces/connections.py` — `Connection.get_provider`, `load_provider`, `datasource(name)`, `DATASOURCES`.
- `querysource/datasources/drivers/__init__.py` — `SUPPORTED` driver registry (driver name → dialect map source).
- `querysource/auth/_resource_types.py`, `querysource/auth/enforcement.py` — `ResourceType` shim and `enforce_principal`.
- `querysource/auth/principal.py` — `QSPrincipal.to_userinfo()` as the source of `user_attributes`.
- `querysource/handlers/service.py` — `QueryService.query()` already merges JSON body + query params into `conditions`, so `metrics`/`dimensions`/`filters` arrive at the provider unchanged.
- `querysource/queries/describe.py` — `describe_slug()` to publish the metric/dimension catalog.
- `tests/e2e/conftest.py` — `FakeDefinitionRepository` for provider tests without a database.

---

### Option B: In-house Ossie compiler on sqlglot (no Sidemantic)

Implement the Ossie 0.2.0.dev0 logical model (datasets, fields, relationships,
metrics with multi-dialect expressions) as `datamodel` classes, and write a
planner that builds `SELECT … GROUP BY …` with joins along many-to-one
relationships, using sqlglot for AST construction and transpilation. RLS and
metric permissions implemented entirely with PBAC.

✅ **Pros:**
- No AGPL dependency, no DuckDB in the process, full control of the SQL shape.
- Ossie is the only format we need to parse (Apache-2.0 spec, JSON Schema
  available for validation).

❌ **Cons:**
- Re-implements the hard parts: fan-out-safe aggregation across one-to-many
  joins (symmetric aggregates), ratio/derived/cumulative metrics, time grains
  per dialect, filter pushdown. Months of work and a large test matrix.
- Ossie is incubating (spec 0.2.0.dev0, shape changed between Sept and Oct
  snapshots); we would carry every spec change ourselves.
- No compile-time RLS; row filters would be QuerySource-only.

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `sqlglot>=30.1` | AST building + dialect transpilation | MIT; `sqlglot[rs]` for speed |
| `jsonschema>=4.23` | Validate Ossie docs against `core-spec/ossie-schema.json` | vendor the schema |
| `pyyaml` | Parse documents | transitive today |

🔗 **Existing Code to Reuse:**
- Same provider/connection/PBAC touchpoints as Option A.
- `querysource/parsers/` — `AbstractParser` conventions (Cython) for where a future compiled parser could live; not reused directly.

---

### Option C (unconventional): Sidemantic as a sidecar compile service

Run `sidemantic server api models/ --port 4400` as a separate container next
to QuerySource and call its `POST /compile` endpoint over aiohttp
(`querysource/interfaces/http.py`) with `X-Sidemantic-User` behind
`--trust-user-header`. QuerySource keeps the same `SemanticCompiler` Protocol;
the `HttpSidemanticCompiler` implementation just does an HTTP call and executes
the returned SQL locally.

✅ **Pros:**
- No AGPL code or DuckDB inside the QuerySource process; the sidecar is an
  unmodified upstream image (`sidequery/sidemantic`).
- Model directory can be mounted and hot-reloaded independently of QuerySource
  deploys; the sidecar also exposes MCP for agents.

❌ **Cons:**
- Another service to deploy, secure (bearer token file, header-sanitising
  proxy) and monitor; a network hop on every compile.
- Inline models stored in `query_raw` cannot be served by the sidecar without
  a sync step; the DB-first store decision (Round 1/2) is awkward here.
- Row filters depend on the sidecar trusting our identity header; the
  deny-by-default guarantee lives outside our process.

📊 **Effort:** Medium (code) + ops cost

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `sidequery/sidemantic` (Docker) | Compile service | AGPL applies to the service, not QuerySource |
| `aiohttp` | Client | already in stack |

🔗 **Existing Code to Reuse:**
- `querysource/interfaces/http.py` — HTTP client conventions.
- Everything in Option A except the in-process compiler.

---

## Recommendation

**Option A** is recommended because:

- It delivers the complete feature set (Ossie import, multi-metric types,
  symmetric aggregates, dialect-aware SQL, compile-time RLS) with Medium effort,
  while Option B is High effort for a subset and Option C adds an operational
  dependency that fights the DB-first model store we chose.
- The `SemanticCompiler` Protocol and QuerySource-owned `SemanticQuery` /
  `CompiledQuery` models make the AGPL dependency replaceable: Option B can be
  implemented later as a second compiler without touching providers, handlers or
  callers.
- Compile-only use keeps execution, caching, PBAC and tenant isolation on the
  paths QuerySource already tests; the only new runtime surface is a pure
  function from (model, request, user attributes, dialect) to SQL.
- What we trade off: an AGPL + DuckDB dependency as an **optional extra**
  (import is lazy; the base package stays unaffected), and reliance on a young
  project pinned to `0.12.x`. Both are explicitly accepted in Round 1 and
  recorded in Open Questions for legal/ops confirmation.

---

## Feature Description

### User-Facing Behavior

1. **Registering a semantic resource.** A slug is stored with
   `provider = 'semantic'` and `query_raw` holding the resource YAML:

   ```yaml
   name: epson.sales
   kind: semantic
   connection: epson_warehouse        # datasource name; omitted → default pg
   semantic:
     format: ossie                    # ossie | sidemantic
     compiler: sidemantic             # default
     definition: models/epson_sales.ossie.yaml   # relative to QS_SEMANTIC_MODELS_DIR
     # or an inline document under `definition:` (inline wins when both exist)
     scope: commerce                  # optional Ossie scope when the document has several
   governance:                        # optional, QuerySource-owned (not Ossie syntax)
     row_filters:
       default: []                    # applied to every principal
       analyst:                       # role name as carried by the principal's `roles`
         - {field: region, op: in, value: ["ES", "PT"]}
       country_manager:
         - "region = 'ES'"            # validated string form is accepted too
   ```

   `governance.row_filters` are QuerySource-side row-level filters keyed by
   role; the principal's roles decide which blocks apply, every applicable
   block is AND-ed (most restrictive), and each filter passes the same
   validator as caller-supplied filters. They complement, and never replace,
   the Sidemantic `security:` block inside the model.

   Management endpoints and the tenant repository store it like any other
   slug; the definition revision is the model version.

2. **Querying over HTTP.** `POST /api/v1/queries/epson.sales` (and the tenant
   route) with a JSON body:

   ```json
   {
     "metrics": ["net_sales", "units"],
     "dimensions": ["retailer", "order_month"],
     "filters": [
       {"field": "order_date", "op": ">=", "value": "2026-01-01"},
       "retailer = 'BestBuy'"
     ],
     "time_grain": "month",
     "order_by": ["net_sales desc"],
     "limit": 500
   }
   ```

   Output formats (`:csv`, `queryformat=…`, `_download`) work as for any slug.
   `GET` with the same keys as query parameters is accepted for simple cases.

3. **Inspecting the catalog.** `GET /api/v1/semantic/{slug}/catalog` (and
   the tenant-prefixed form) returns the model's metrics and dimensions with
   descriptions, types, aggregation, time grains and whether the principal may
   use each one; it is the contract AI agents read before building a request.
   `HEAD`/`PATCH` columns and the describe endpoint expose the same catalog for
   existing tooling. `test_slug` / `dry_run` returns the compiled SQL and
   warnings without executing.

4. **Python API.** `QS(slug='epson.sales', conditions={...}, principal=...)`
   works unchanged. A convenience module exposes the two-step form:

   ```python
   compiled = await querysource.semantic.compile(resource="epson.sales", metrics=[...], ...)
   result = await querysource.semantic.execute(compiled)
   ```

   `compiled` is a `CompiledQuery` (sql, parameters, connection, dialect,
   model_version, warnings, metrics, dimensions).

5. **Governance.** A principal without `semantic:execute` on the slug gets the
   same 404-shaped denial as other slugs. A denied metric or dimension returns
   403 naming the field. A model with a `security` block always requires user
   attributes; a request without an identity is denied before any SQL exists.

### Internal Behavior

1. `QS.build_provider()` reads the definition through the repository
   (unchanged). `Connection.get_provider()` gets an explicit
   `provider == 'semantic'` branch, placed next to the `EXTERNAL_PROVIDERS`
   check, that returns `[None, semanticProvider]` without touching the default
   driver. The provider resolves its own connection in `prepare_connection()`
   from the resource YAML's `connection` through `Connection.datasource(name)`,
   or the default PostgreSQL connection when omitted.
2. `semanticProvider.__init__` parses `query_raw` into a `SemanticResource`
   (kind, connection, semantic block, governance block) and builds a
   `SemanticQuery` from `conditions` (`metrics`, `dimensions`, `filters`,
   `time_grain`, `order_by`, `limit`, `offset`); reserved QuerySource keys
   (`refresh`, `_output_options`, …) are stripped first. Unknown keys are
   rejected. All models in `querysource/semantic/` are `pydantic.BaseModel`
   (v2) classes. `time_grain` rewrites every requested time dimension that has
   no explicit suffix to `<dim>__<grain>`; explicit suffixes win.
3. `prepare_connection()`:
   - resolves the datasource and derives the dialect from its driver;
   - loads the model via the `ModelLoader` (inline document or
     `QS_SEMANTIC_MODELS_DIR`-relative path; format `ossie` →
     `OssieAdapter(target_dialect=<dialect>).parse_document(path)`, where an
     inline document is first written to a private temp file so only the
     public adapter API is used; format `sidemantic` → `load_from_file`),
     cached in-process by `(definition revision, dialect)`;
   - **PBAC layer**: resource type `SEMANTIC`, action `semantic:execute` on
     resource name `<slug>`, then one evaluation per requested field on
     `<slug>:metric:<name>` / `<slug>:dimension:<name>` with action
     `semantic:use`; PBAC off → allowed; any deny → `QueryAccessDenied`-style
     error with the field name;
   - builds the principal: the library path already has a `QSPrincipal`; the
     request path builds one from the session so `user_attributes` always have
     the `QSPrincipal.to_userinfo()` shape (`user_id`, `username`, `groups`,
     `roles`, `programs`, `superuser`, `tenant_id`);
   - **governance filters**: selects `governance.row_filters.default` plus the
     blocks whose key matches one of the principal's `roles`, validates them
     like caller filters and appends them to the request (AND semantics);
   - validates filters: structured objects against the dimension list and the
     operator allow-list; strings parsed with sqlglot, every column reference
     must be a model dimension, no subqueries/functions outside the allow-list;
   - calls `SemanticCompiler.compile(model_ref, query, target)` →
     `CompiledQuery`; the Sidemantic implementation calls
     `SemanticLayer.compile(metrics=..., dimensions=..., filters=...,
     order_by=..., limit=..., dialect=..., user_attributes=...)` on a layer
     bound to the loaded graph (no execution adapter), maps `SecurityError` →
     access denied, `ValueError`/unknown field → 400 with the catalog hint.
4. `query()` runs `compiled.sql` on the asyncdb connection exactly as
   `defaultProvider.query` does and returns `[result, error]`; `dry_run()`
   returns `(compiled.sql, None)`; `columns()` returns the selected
   metrics/dimensions; `describe_columns()` returns typed columns.
5. Cache: the existing QS cache keys on slug + conditions, so identical
   semantic requests are served from cache; `model_version` is part of the
   compiled object for traceability and is appended to the cache key so a
   definition change invalidates entries.
6. `querysource.semantic.compile()/execute()` wrap the same provider: compile
   builds the provider up to `prepare_connection()` and returns the
   `CompiledQuery`; execute re-attaches a connection and runs it.
7. The catalog endpoint (`querysource/handlers/semantic.py`,
   `GET /api/v1/semantic/{slug}/catalog`) enforces `semantic:execute`, loads
   the model through the same `ModelLoader`, and returns metrics/dimensions
   from `SemanticLayer.describe_models()` annotated with the principal's
   per-field grants (one `semantic:use` evaluation per field, PBAC off →
   all granted) and the governance row-filter summary for the principal.

### Edge Cases & Error Handling

- **Sidemantic not installed** → provider raises `QueryError(code=501)` with
  the message "install querysource[semantic]"; import is lazy so the base
  package never imports duckdb.
- **Invalid resource YAML / unknown `kind` / unknown `format`** → 400 at
  provider construction (`ParserError` path in `QS.build_provider`).
- **Model file missing, outside `QS_SEMANTIC_MODELS_DIR` (path traversal), or
  failing Ossie validation** → 400 with the Sidemantic diagnostics (codes +
  JSON pointers) in the error payload; path traversal is logged.
- **Both inline and path present** → inline wins, a warning is attached.
- **Unknown metric/dimension** → 400 listing the available names.
- **Denied metric/dimension (PBAC)** → 403 naming the field; denied slug →
  404 (same as `QueryAccessDenied`).
- **Secured model, no user attributes** → Sidemantic `SecurityError` → 403;
  never executed unscoped.
- **Filters**: unsupported operator, column not a dimension, string that fails
  to parse, or more than `QS_SEMANTIC_MAX_FILTERS` → 400.
- **Limit** above `QS_SEMANTIC_MAX_LIMIT` (default 5000) is capped with a
  warning; missing limit uses `QS_SEMANTIC_DEFAULT_LIMIT` (default 500).
- **Dialect not supported by Sidemantic** → compile with the generic generator
  and add a warning; the SQL may still fail at execution and surfaces as the
  normal provider error.
- **Empty result** → `NotFound` ("Empty Result") like other providers.
- **time_grain** on a request with no time dimension → 400; valid grains
  follow Sidemantic (`day|week|month|quarter|year`); an explicit per-dimension
  suffix always wins over the request-level grain.
- **governance.row_filters**: an unknown role key never matches (no filter
  applied, no error); a filter that fails validation is a configuration error
  → 500 logged with the slug and role, never silently skipped; a principal
  without roles gets only `default`.
- **Session without identity on a model that has `security:` or
  `governance.row_filters`** → 403 before compilation.
- **Definition revision changes** → loader cache miss, model recompiled; stale
  cached results are keyed out by `model_version`.
- **Compile latency**: first compile per revision parses YAML + schema; keep
  the loader cache process-wide and bounded (LRU).

---

## Capabilities

### New Capabilities
- `semantic-layer`: semantic resource kind, `querysource/semantic/` package
  (pydantic models, Protocol, loader, filter validation, governance row
  filters, dialect map, Sidemantic compiler), `semanticProvider`, the
  `get_provider` branch, `compile()/execute()` helpers, the catalog endpoint,
  settings, `[semantic]` extra, docs and tests.

### Modified Capabilities
- `pbac-support`: add `ResourceType.SEMANTIC` to the shim and the
  `semantic:execute` / `semantic:use` actions (navigator-auth upstream follow-up
  like TASK-640).
- `describe-queryslug`: describe payload for `provider == 'semantic'` includes
  the metric/dimension catalog with descriptions and grants.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/providers/semantic.py` | new | `semanticProvider(BaseProvider)`, `__parser__ = None`, loaded by `Connection.load_provider('semantic')` |
| `querysource/semantic/` (new package) | new | `models.py`, `protocol.py`, `loader.py`, `filters.py`, `dialects.py`, `compilers/sidemantic.py`, `api.py` (`compile`/`execute`) |
| `querysource/interfaces/connections.py` | extends | explicit `provider == 'semantic'` branch in `get_provider()` returning `[None, semanticProvider]` (decided) |
| `querysource/auth/_resource_types.py` | extends | `SEMANTIC = "semantic"` in the shim; upstream navigator-auth enum + `SEMANTIC_EXECUTE`/`SEMANTIC_USE` as a TASK-640-style follow-up |
| `querysource/handlers/semantic.py` | new | `GET /api/v1/semantic/{slug}/catalog` (+ tenant-prefixed route) with per-field grants |
| `querysource/auth/enforcement.py` | depends on | `enforce_principal` reused per metric/dimension; request path reuses `AbstractHandler._enforce_pbac` |
| `querysource/queries/describe.py` | extends | catalog section for semantic slugs |
| `querysource/conf.py` | extends | `QS_SEMANTIC_MODELS_DIR`, `QS_SEMANTIC_DEFAULT_LIMIT`, `QS_SEMANTIC_MAX_LIMIT`, `QS_SEMANTIC_MAX_FILTERS` |
| `pyproject.toml` | extends | `semantic = ["sidemantic>=0.12.0,<0.13", "sidemantic[ossie]", "sqlglot>=30.1"]`; `pydantic>=2` added to core dependencies (semantic models are pydantic) |
| `querysource/handlers/service.py` | no change expected | JSON body + params already reach the provider as `conditions` |
| `querysource/cache/*` | depends on | cache key must include `model_version` |
| `docs/` | new | `SEMANTIC_LAYER.md` (resource YAML, request contract, governance, dialect table) |
| `tests/semantic/`, `tests/providers/` | new | unit tests with `FakeDefinitionRepository`; opt-in integration test against PostgreSQL |
| Deployment | optional | `uv pip install "querysource[semantic]"` on workers that serve semantic slugs |

No breaking changes: slugs with other providers are untouched; `QueryModel`
gains no columns (`provider='semantic'` + `query_raw` YAML is enough).

---

## Code Context

### User-Provided Code

```python
# Source: user-provided (proposed public API)
compiled = querysource.compile(
    resource="epson.sales",
    metrics=["net_sales", "units"],
    dimensions=["retailer", "order_month"],
    filters=[
        {"field": "order_date", "op": ">=", "value": "2026-01-01"}
    ],
)

result = await querysource.execute(compiled)
```

```yaml
# Source: user-provided (proposed QuerySource resource config, not Ossie syntax)
name: epson.sales
kind: semantic
connection: epson_warehouse

semantic:
  format: ossie
  definition: models/epson_sales.ossie.yaml
  compiler: sidemantic
```

```python
# Source: user-provided (proposed compiler Protocol)
class SemanticCompiler(Protocol):
    def compile(
        self,
        model: SemanticModelRef,
        request: SemanticQuery,
        target: CompilationTarget,
    ) -> CompiledQuery: ...
```

| Campo | Propósito |
|---|---|
| `sql` | Consulta generada |
| `parameters` | Valores enlazados, cuando el adaptador los soporte |
| `connection` / `dialect` | Destino de ejecución |
| `model_version` | Trazabilidad y caché |
| `warnings` | Conversiones parciales o limitaciones |

```python
# Source: user-provided (governance sketch — illustrative, APIs here do NOT exist in QuerySource)
def _apply_policy(self, q: SemanticQuery) -> SemanticQuery:
    policy = self.pbac.evaluate(self.principal, resource="semantic:orders")
    # métricas prohibidas → se rechaza antes de compilar
    denied = set(q.metrics) - policy.allowed_metrics
    if denied:
        raise ToolPermissionError(f"Sin acceso a: {denied}")
    # row-level security → filtro inyectado, no visible al LLM
    if policy.row_filters:
        q.filters.extend(policy.row_filters)   # ["orders.region IN ('ES','PT')"]
    return q
```

```python
# Source: user-provided (structured request sketch)
from pydantic import BaseModel, Field

class SemanticQuery(BaseModel):
    metrics: list[str] = Field(description="Nombres de métricas del catálogo")
    dimensions: list[str] = []
    filters: list[str] = []          # "orders.region = 'ES'"
    time_grain: str | None = None    # day | week | month
    limit: int = Field(default=500, le=5000)
```

```yaml
# Source: user-provided (Sidemantic-native model example)
# .parrot/semantic/sales.yml
models:
  - name: orders
    table: epson.sales            # origen físico
    primary_key: order_id
    dimensions:
      - name: region
        sql: region_code
      - name: order_date
        type: time
        granularity: day
    metrics:
      - name: revenue
        agg: sum
        sql: net_amount
        description: "Ingreso neto sin IVA ni devoluciones"
      - name: avg_ticket
        type: ratio
        numerator: revenue
        denominator: order_count
    relationships:
      - name: stores
        type: many_to_one
        foreign_key: store_id
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/providers/abstract.py:35
class BaseProvider(ABC):
    __parser__ = ...  # class attribute; None disables parser construction (abstract.py:125-137)
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '',
                 connection: Callable = None,
                 definition: Union[QueryModel, dict] = None,
                 conditions: dict = None, request: web.Request = None, **kwargs):  # line 55
        # self._query = definition.query_raw when slug is set (lines 76-80)
        # self._conditions deep-copied, 'refresh' popped (lines 94-113)
        # self._loop from kwargs['loop'] or running loop (lines 114-123)
        # self._driver from kwargs['driver'] (lines 140-146)
    async def prepare_connection(self): ...        # line 259 — calls self._parser.set_options() when parser set
    async def columns(self): ...                   # line 266
    async def describe_columns(self) -> list[dict]: ...  # line 273  [{'name': str, 'type': Optional[str]}]
    async def dry_run(self): return (self._query, None)  # line 298
    @abstractmethod
    async def query(self): ...                     # line 304
    async def close(self): ...                     # line 308

# From querysource/providers/default.py:24
class defaultProvider(BaseProvider):
    __parser__ = SQLParser
    async def query(self):                         # line 117
        async with await self._connection.connection() as conn:
            result, error = await conn.query(self._query)
        # returns [result, error]; raises self.NotFound on empty result (lines 128-136)

# From querysource/queries/qs.py:50
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
                 definition: 'LoadedDefinition | None' = None,
                 principal: 'QSPrincipal | None' = None, ...): ...   # line 56
    async def build_provider(self): ...            # line 186  (slug branch lines 220-311)
    #   repo.get(QueryIdentity(store, slug)) -> loaded_def ; objquery = loaded_def.runtime
    #   self._conn, self._provider = await self.connection.get_provider(objquery, session=..., app=...)
    #   self._qs = self._provider(slug=, query=, qstype='slug', connection=, definition=objquery,
    #                             conditions=, request=, loop=)  ; await self._qs.prepare_connection()
    async def query(self, output_format: str | None = None): ...  # line 457
    async def dry_run(self): ...                   # line 638

# From querysource/interfaces/connections.py:46
class Connection:
    async def get_provider(self, entry: dict, session=None, app=None): ...  # line 262
    #   provider == 'db' -> load_provider('db') + default_connection
    #   provider in EXTERNAL_PROVIDERS ('http','rest') -> [None, provider]
    #   datasource named like provider -> datasource(provider) ; provider = source.driver
    #   else -> load_provider(provider) + default_driver(provider) (conn may be None)
    def load_provider(self, provider: str) -> BaseProvider: ...  # line 374
    #   import_module(f'querysource.providers.{provider}') ; getattr(cls, f'{provider}Provider')
    async def datasource(self, name: str = 'default', session=None, app=None): ...  # line 394
    #   returns (source, AsyncDB(source.driver, dsn=source.dsn, params=...)) ; QueryError 404 if unknown
    async def get_definition_repository(self) -> 'DefinitionRepository': ...  # line 439

# From querysource/models.py:49
class QueryModel(Model):
    query_slug: str          # primary key
    query_raw: str           # line 80 — will hold the semantic resource YAML
    is_raw: bool             # line 81
    provider: str = 'db'     # line 83 — 'semantic' selects the new provider
    parser: str = 'SQLParser'  # line 84
    conditions: Optional[dict]; cond_definition: Optional[dict]; attributes: Optional[dict]
    is_cached: bool = True; cache_timeout: int = 3600; program_slug: str = 'default'

# From querysource/tenants.py:54
class LoadedDefinition:
    identity: QueryIdentity
    runtime: QueryModel
    revision: str            # -> model_version source

# From querysource/repositories/definitions.py:201
async def get(self, identity: QueryIdentity) -> LoadedDefinition  # TenantError(error_code='query_not_found')

# From querysource/auth/enforcement.py:134
async def enforce_principal(principal: QSPrincipal, resource_type: Any, resource_name: str,
                            action: str, *, tenant: str | None = None,
                            logger: logging.Logger | None = None) -> AccessDecision
#   PBAC off -> AccessDecision(allowed=True, pbac_enabled=False); deny -> raises QueryAccessDenied
# From querysource/auth/enforcement.py:22
class AccessDecision: allowed: bool; pbac_enabled: bool; ...
# From querysource/auth/enforcement.py:53
def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]
# From querysource/auth/enforcement.py:105
async def evaluate(evaluator, ctx, resource_type, resource_name: str, action: str) -> AccessDecision

# From querysource/auth/_resource_types.py:17-47
class ResourceType:   # shim; real enum from navigator_auth.abac.policies.resources when it has SLUG/DATASOURCE/DRIVER/RAW_QUERY
    SLUG = "slug"; DATASOURCE = "datasource"; DRIVER = "driver"; RAW_QUERY = "raw_query"

# From querysource/auth/principal.py:21
class QSPrincipal:
    user_id: str; username: str | None; groups: tuple[str, ...]; roles: tuple[str, ...]
    programs: tuple[str, ...]; superuser: bool; tenant_id: str | None; channel: str; authz_backend: str | None
    def to_userinfo(self) -> dict[str, Any]: ...   # line 97 — source for Sidemantic user_attributes

# From querysource/auth/pbac.py:30
class PBACRuntime: guardian: Any; evaluator: Any
def get_pbac_runtime() -> PBACRuntime | None     # line 40

# From querysource/exceptions.py:63
class QueryAccessDenied(QueryException):   # message "Query not available.", code=404

# From querysource/handlers/service.py:134
class QueryService(...):
    async def query(self, request): ...
    #   options = await self.json_data(request) ; params = self.query_parameters(request)
    #   PBAC slug:execute via _enforce_owned_slug / _enforce_pbac (lines 195-215)
    #   conditions = {**options, **params}  (line 288)  -> QS(slug, conditions=...)

# From querysource/queries/describe.py:204
def describe_slug(definition: QueryModel | dict, grants: DescribeGrants, *,
                  columns_link: str, vocabulary_link: str) -> dict

# From querysource/datasources/drivers/pg.py:16
class pgDriver(SQLDriver): driver: str = 'pg'  ...
# From querysource/datasources/drivers/bigquery.py:12
class bigqueryDriver(BaseModel): driver: str = 'bigquery'; driver_type: str = 'asyncdb'
# From querysource/datasources/drivers/__init__.py:29
SUPPORTED = {'pg': {...}, 'redis': {...}, 'oracle': {...}, ...}  # driver name registry

# From tests/e2e/conftest.py:65
class FakeDefinitionRepository:  # .add(**fields) -> QueryModel ; async get(identity) -> LoadedDefinition
```

#### Verified Imports
```python
# These imports have been confirmed to work:
from querysource.providers import BaseProvider                      # querysource/providers/__init__.py:4
from querysource.providers.abstract import BaseProvider             # querysource/providers/abstract.py:35
from querysource.providers.default import defaultProvider           # querysource/providers/default.py:24
from querysource.queries.qs import QS                               # querysource/queries/qs.py:50
from querysource.models import QueryModel                           # querysource/models.py:49
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore  # querysource/tenants.py:35-59
from querysource.repositories import DefinitionRepository           # (verified in TASK-762 contract)
from querysource.auth import ResourceType, QSPrincipal, get_pbac_runtime, setup_pbac  # querysource/auth/__init__.py:17-26
from querysource.auth._resource_types import ResourceType           # querysource/auth/_resource_types.py
from querysource.auth.enforcement import enforce_principal, evaluate, resolve_evaluator, build_eval_context, AccessDecision
from querysource.exceptions import QueryAccessDenied, QueryError, QueryException  # querysource/exceptions.py
from querysource.interfaces.connections import DATASOURCES, PROVIDERS, EXTERNAL_PROVIDERS  # connections.py:40-44
from querysource.datasources.drivers import SUPPORTED, BaseDriver   # drivers/__init__.py
from querysource.conf import ...                                    # navconfig `config.get(...)` pattern, conf.py:1-60
```

```python
# External (sidemantic 0.12.0, verified from upstream source on 2026-10-14):
from sidemantic import SemanticLayer, load_from_directory          # README; loaders.py:170
from sidemantic.loaders import load_from_file                      # loaders.py:693
from sidemantic.adapters.ossie import OssieAdapter, OssieImportError  # adapters/ossie.py:40,64
# SemanticLayer.__init__(connection="duckdb:///:memory:", dialect=None, auto_register=True, ...,
#                        default_limit=None, max_limit=None, enforce_visibility=False, ...)   # core/semantic_layer.py:76
# SemanticLayer.compile(metrics=None, dimensions=None, filters=None, segments=None, order_by=None,
#                       limit=None, offset=None, dialect=None, ungrouped=False, parameters=None, ...,
#                       user_attributes=None, ...) -> str                                      # core/semantic_layer.py:1143
# SemanticLayer.from_catalog(catalog, *, scope_id=None, connection=..., dialect=None, allow_invalid=False, **opts)  # :1982
# SemanticLayer.describe_models(model_names=None) -> dict ; get_model(name) ; list_models()  # :925, :1912, :1934
# OssieAdapter(*, target_dialect="duckdb", source_dialect=None, scope_id=None, import_policy="strict", ...,
#              schema_version="0.2.0.dev0")                                                   # adapters/ossie.py:67
# OssieAdapter.parse_document(path) -> OssieLoweringResult ; parse_catalog(path_or_dir) -> OssieProjectResult
# OssieAdapter.parse(path) -> SemanticGraph  (raises OssieImportError on invalid docs)       # adapters/ossie.py:103-178
# Security: model `security: {access: <jinja bool>, row_filters: [...]}`; compile(..., user_attributes=None)
#   on a secured model raises SecurityError (deny-by-default); values rendered as typed literals  # docs/security.md
```

#### Key Attributes & Constants
- `QueryModel.provider` → `str`, default `'db'` (querysource/models.py:83) — set to `'semantic'`.
- `QueryModel.query_raw` → `str` (querysource/models.py:80) — holds the resource YAML.
- `LoadedDefinition.revision` → `str` (querysource/tenants.py:59) — `model_version` source.
- `EXTERNAL_PROVIDERS = ('http', 'rest')` (querysource/interfaces/connections.py:44).
- `PROVIDERS: dict` provider class cache (connections.py:41); `DATASOURCES: dict` (connections.py:40).
- `QS_PBAC_ENABLED`, `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` read in `querysource/auth/enforcement.py`.
- `ResourceType.SLUG == "slug"`; actions follow `"<resource>:<verb>"` (`slug:execute`, `raw_query:execute`).
- Installed but **undeclared** in `pyproject.toml`: `sqlglot 30.20.0`, `duckdb 1.5.6`, `pydantic 2.12.5`, `pyyaml 6.0.3` (venv, Python 3.11.14).
- `pyproject.toml` extras pattern: `[project.optional-dependencies]` (lines 120-190), e.g. `parquet = ["pyarrow>=25", "fsspec>=2026.7"]`.

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.compile()` / `querysource.execute()`~~ — no top-level functions; to be added under `querysource/semantic/api.py`.
- ~~`querysource/semantic/`~~, ~~`querysource/providers/semantic.py`~~ — do not exist yet.
- ~~`ResourceType.SEMANTIC`~~ — shim has only `SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`; navigator-auth upstream has none for semantics.
- ~~`QueryModel.kind`~~ — there is no `kind` column; the provider is selected by `QueryModel.provider`. `kind: semantic` lives inside the YAML in `query_raw`.
- ~~`self.pbac.evaluate(principal, resource=...)` returning `allowed_metrics` / `row_filters`~~ — navigator-auth evaluation is per (resource_type, resource_name, action) via `evaluate()`/`enforce_principal()`; there is no policy object with metric lists. Field-level permissions must be modelled as one evaluation per field.
- ~~`ToolPermissionError`~~ — not a QuerySource exception; use `QueryAccessDenied` / `QueryError(code=403)`.
- ~~A QuerySource MCP server~~ — MCP tooling lives in ai-parrot (`.agent/workflows/parrot-mcp-server.md`), not in this package.
- ~~`sidemantic-rs` as a PyPI dependency of sidemantic 0.12.0~~ — not in `requires_dist`; the Rust engine is selected via `engine="rust"` only when available. `duckdb>=1.0.0` **is** a hard dependency.
- ~~`SemanticLayer.compile(...)` returning an object~~ — it returns a plain `str`; `CompiledQuery` is built by QuerySource.
- ~~`OssieAdapter.parse(inline_yaml_text)`~~ — path-based only; inline documents need a temp file or the lower-level `parse_ossie_document(bytes, source_identifier, options)` + `lower_ossie_document(...)` functions (internal API).
- ~~`sqlglot` / `pydantic` declared in `pyproject.toml`~~ — installed transitively only.
- ~~`Connection.get_provider` special-casing `'multi'` or any provider other than `'db'`/`'http'`/`'rest'`~~ — generic fallback path.

---

## Parallelism Assessment

- **Internal parallelism**: moderate. The core package (`models`, `protocol`,
  `loader`, `SidemanticCompiler`) and the provider must land in sequence (the
  provider consumes the package). Independent, parallelisable tasks: PBAC
  resource-type shim + per-field enforcement helper; filter validator
  (`filters.py`, sqlglot); driver→dialect map (`dialects.py`); settings +
  `pyproject` extra; describe/catalog payload; docs.
- **Cross-feature independence**: touches `querysource/auth/_resource_types.py`
  and `querysource/interfaces/connections.py`, both shared with PBAC and tenant
  work; no active worktree currently modifies them (verify with
  `/sdd-status` at spec time). `querysource/queries/describe.py` overlaps with
  `describe-queryslug`. The catalog handler is a new file and route
  registration only.
- **Recommended isolation**: `mixed`.
- **Rationale**: the compiler/provider chain is a strict dependency line and
  should stay in one worktree; the auth shim, filter validator, dialect map and
  docs have no shared files with it and can run in individual worktrees.

---

## Open Questions

All questions were resolved with the author on 2026-10-14; the answers are
folded into the sections above and are binding for `/sdd-spec`.

- [x] Connection resolution: add a `provider == 'semantic'` branch in `Connection.get_provider()` (returns `[None, semanticProvider]` and lets the provider call `datasource(name)`), or let the generic fallback stand and have the provider ignore the (possibly `None`) connection it receives? — *Owner: Jesus Lara*: Explicit branch in `get_provider()` returning `[None, semanticProvider]`, mirroring `EXTERNAL_PROVIDERS`; the provider resolves `connection:` from the resource YAML through `Connection.datasource(name)`, or the default PostgreSQL connection when omitted.
- [x] Request model base class: `pydantic.BaseModel` or house `datamodel.BaseModel` for `SemanticQuery`/`CompiledQuery`? — *Owner: Jesus Lara*: `pydantic.BaseModel` (v2) for every model in `querysource/semantic/`; `pydantic>=2` is declared as a core dependency in `pyproject.toml` (it is already installed transitively).
- [x] Legal/ops sign-off on the AGPL-3.0 optional extra (`sidemantic`, unmodified, in-process) and on DuckDB being installed on workers that serve semantic slugs. — *Owner: Jesus Lara*: Approved as an unmodified optional dependency (`querysource[semantic]`); the core package never imports it, no sidemantic source is copied into the repo, and DuckDB is installed only on workers that serve semantic slugs.
- [x] `user_attributes` mapping: which session/principal keys feed Sidemantic row filters, and should the request-path session be normalised through `QSPrincipal.to_userinfo()`? — *Owner: Jesus Lara*: One shape for both paths — the `QSPrincipal.to_userinfo()` dict (`user_id`, `username`, `groups`, `roles`, `programs`, `superuser`, `tenant_id`). The request path builds a `QSPrincipal` from the session before compiling; the library path uses the principal directly.
- [x] navigator-auth upstream: add `ResourceType.SEMANTIC` + actions (TASK-640 style) or keep the string shim? — *Owner: Jesus Lara*: Shim now (`SEMANTIC = "semantic"` in `querysource/auth/_resource_types.py`) so the feature ships independently; a TASK-640-style follow-up adds `SEMANTIC`, `SEMANTIC_EXECUTE` and `SEMANTIC_USE` upstream.
- [x] Per-field PBAC resource naming. — *Owner: Jesus Lara*: Resource type `SEMANTIC`; `semantic:execute` on resource name `<slug>`; field checks on `<slug>:metric:<name>` / `<slug>:dimension:<name>` with action `semantic:use`, so policies can use wildcards such as `epson.sales:metric:*`.
- [x] Inline Ossie documents: lower-level `parse_ossie_document` or temp file + `OssieAdapter.parse_document()`? — *Owner: Jesus Lara*: Temp file + the public `OssieAdapter.parse_document()`; done once per definition revision thanks to the loader cache. No internal Sidemantic APIs.
- [x] `time_grain` semantics. — *Owner: Jesus Lara*: A request-level `time_grain` rewrites every requested time dimension without an explicit suffix to `<dim>__<grain>`; an explicit suffix (`order_date__day`) wins; non-time dimensions are unaffected; a grain on a request with no time dimension is a 400.
- [x] HTTP surface: query routes only, or also a catalog endpoint? — *Owner: Jesus Lara*: Both. Execution on `POST/GET /api/v1/queries/{slug}` (and the tenant route) plus `GET /api/v1/semantic/{slug}/catalog` returning metrics/dimensions with descriptions, types and the principal's grants; describe/HEAD/PATCH columns are updated too.
- [x] PBAC-provided row filters in v1? — *Owner: Jesus Lara*: Yes, declared in the resource YAML: a `governance.row_filters` block keyed by role (plus `default`) in the QuerySource resource document; PBAC/session only supplies the roles. Filters pass the same validator as caller filters, are injected before compile, and are combined with AND (most restrictive) when a principal matches several roles. Sidemantic `security.row_filters` keep working on top.
