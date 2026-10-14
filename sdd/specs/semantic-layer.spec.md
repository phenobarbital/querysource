---
type: feature
base_branch: dev
projects: [providers, auth, datasources, handlers, querysource]
tags: [semantic-layer, sidemantic, ossie, pbac, metrics, sql-compiler]
---

# Feature Specification: Querysource Semantic Layer (Ossie models compiled by Sidemantic)

**Feature ID**: FEAT-175
**Date**: 2026-10-14
**Author**: Jesus Lara / Claude
**Status**: approved
**Target version**: 5.3.0
**Exploration**: `sdd/proposals/semantic-layer.brainstorm.md` (Option A, status accepted)

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource executes stored queries (slugs) whose `query_raw` is SQL or a
provider-specific payload. Business metrics such as "net sales" or "units" are
therefore re-written by hand in every slug, every MultiQS pipeline and every AI
agent that reaches the warehouse. The same KPI drifts between consumers, there
is no single description of what a metric means, and governance (who may see
which metric, which rows) is applied per slug instead of per metric.

We want a **semantic resource** type: a slug whose definition is a semantic
model (Apache Ossie YAML, or Sidemantic native YAML) plus operational
configuration (connection, compiler, governance). Callers never write SQL; they
send a structured request (metrics, dimensions, filters, time grain, limit).
QuerySource compiles it to SQL for the target warehouse dialect, applies
governance (PBAC on the catalog, row-level security at compile time) and
executes it through the existing provider/connection machinery, returning the
usual QuerySource outputs.

Affected users: API consumers and dashboards (governed metrics through the
existing query routes), AI agents in ai-parrot (a deterministic, SQL-free
contract that cannot leak metrics or rows), data owners (definitions versioned
in the query store or a repo directory) and operators (one execution path:
asyncdb providers, PBAC, cache).

### Goals
- A slug with `provider = 'semantic'` and a resource YAML in `query_raw` is
  executed by a new `semanticProvider` through the unchanged
  `QS.build_provider()` slug path; HTTP (`/api/v1/queries/{slug}` and the
  tenant route) and the Python `QS(...)` API work without changes.
- Callers send a structured `SemanticQuery` (metrics, dimensions, filters,
  `time_grain`, `order_by`, `limit`, `offset`); QuerySource compiles it to SQL
  for the dialect derived from the datasource driver and runs it on that
  datasource's asyncdb connection. No raw SQL from callers, ever.
- Compilation is delegated to Sidemantic 0.12.x behind a QuerySource-owned
  `SemanticCompiler` Protocol, **compile-only**: the base package never imports
  Sidemantic or DuckDB; the `querysource[semantic]` extra is optional.
- Two governance layers: navigator-auth PBAC (new `ResourceType.SEMANTIC`,
  `semantic:execute` on the slug, `semantic:use` per metric/dimension; allow-all
  when PBAC is off) and compile-time row-level security (Sidemantic
  `security:` block fed by `user_attributes`, deny-by-default, plus role-keyed
  `governance.row_filters` from the resource YAML).
- A compile result is an object (`CompiledQuery`: `sql`, `parameters`,
  `connection`, `dialect`, `model_version`, `warnings`, `metrics`,
  `dimensions`), returned by `dry_run()` and by
  `querysource.semantic.compile()`.
- A catalog endpoint (`GET /api/v1/semantic/{slug}/catalog`) lists metrics and
  dimensions with descriptions, types and the principal's grants, so agents can
  build a request without guessing.
- Model loading never opens a warehouse connection; compiled layers are cached
  per definition revision and dialect.

### Non-Goals (explicitly out of scope)
- Executing through Sidemantic or DuckDB (compile-only by decision).
- An in-house Ossie compiler (brainstorm Option B, rejected: High effort for a
  subset) or a Sidemantic sidecar service (Option C, rejected: fights the
  DB-first model store).
- An MCP server inside QuerySource; MCP tooling lives in ai-parrot and will
  consume the catalog endpoint and `compile()/execute()`.
- Upstream changes to navigator-auth (the `SEMANTIC` enum value and actions
  ship through the string shim now; the upstream PR is a TASK-640-style
  follow-up).
- Management endpoints for authoring semantic models; the existing slug
  management (`QueryManager`) stores the resource YAML like any `query_raw`.
- PBAC-policy-supplied row filters (policy attributes); v1 row filters come
  from the model's `security:` block and the resource's `governance` block.

---

## 2. Architectural Design

### Overview

A **semantic slug** is an ordinary `QueryModel` row with `provider='semantic'`
whose `query_raw` holds a small QuerySource resource document (not Ossie
syntax):

```yaml
name: epson.sales
kind: semantic
connection: epson_warehouse        # datasource name; omitted → default PostgreSQL
semantic:
  format: ossie                    # ossie | sidemantic
  compiler: sidemantic             # only value in v1
  definition: models/epson_sales.ossie.yaml   # path under QS_SEMANTIC_MODELS_DIR …
  # … or an inline mapping (the Ossie / Sidemantic document itself); inline wins
  scope: commerce                  # optional Ossie scope when a document has several
governance:                        # optional, QuerySource-owned
  row_filters:
    default: []
    analyst:
      - {field: region, op: in, value: ["ES", "PT"]}
    country_manager:
      - "region = 'ES'"
```

Resolution follows the existing slug path. `QS.build_provider()` loads the
definition through the repository as today. `Connection.get_provider()` gets an
explicit `provider == 'semantic'` branch, next to the `EXTERNAL_PROVIDERS`
branch, that returns `[None, semanticProvider]` without touching the default
driver. `semanticProvider` (`__parser__ = None`) then:

1. parses `query_raw` into a `SemanticResource` (pydantic v2) and the request
   `conditions` into a `SemanticQuery`, stripping reserved QuerySource keys and
   rejecting unknown ones;
2. in `prepare_connection()` resolves the datasource (`Connection.datasource`)
   or the default PostgreSQL connection, derives the SQL dialect from the
   driver name, loads the model through the `ModelLoader` (inline document or
   `QS_SEMANTIC_MODELS_DIR`-relative path, cached by revision + dialect),
   builds the principal (library path: `QSPrincipal`; request path: a
   `QSPrincipal` built from the session so `user_attributes` always have the
   `QSPrincipal.to_userinfo()` shape), enforces PBAC (`semantic:execute` on the
   slug, `semantic:use` per field), appends the role-matched
   `governance.row_filters`, validates every filter, and calls
   `SemanticCompiler.compile()` to get a `CompiledQuery`;
3. `query()` runs `compiled.sql` on the asyncdb connection exactly like
   `defaultProvider.query()`; `dry_run()` returns `(compiled.sql, None)`;
   `columns()` / `describe_columns()` return the selected fields.

The `SidemanticCompiler` binds a `SemanticLayer` to a **compile-only adapter**
(a `BaseDatabaseAdapter` subclass whose execution methods raise), so no DuckDB
connection is ever opened; the adapter's `dialect` is the target dialect, which
also satisfies `from_catalog`'s target/runtime dialect guard. Models are loaded
with Sidemantic's public `load_from_file()` (inline documents are first written
to a private temp file with the format's suffix); `SemanticLayer.compile(...,
dialect=..., user_attributes=...)` returns the SQL string that QuerySource wraps
in `CompiledQuery`. Field names in a request may be unqualified
(`net_sales`): the compiler resolves them against the catalog, qualifies them
(`orders.net_sales`), and rejects ambiguous names.

Filters are accepted as structured objects (`{field, op, value}`, operator
allow-list) **and** as predicate strings, which are parsed with sqlglot; every
column reference must be a model dimension and only an allow-list of scalar
functions is permitted. Both forms are rendered to Sidemantic filter strings
with sqlglot literal quoting. `time_grain` rewrites every requested time
dimension without an explicit suffix to `<dim>__<grain>`; explicit suffixes win.

Four contracts were tightened after the independent design review (§9):

- **Datasource governance (S1).** `QueryService.query()` enforces
  `datasource:use` on `definition.provider` (`service.py:346-365`), which for a
  semantic slug is the literal `'semantic'`. The provider therefore enforces
  `datasource:use` on the *resolved* datasource name and `driver:use` on its
  driver itself, in `prepare_connection()`, for both the request and the
  principal paths, before loading the model.
- **No bound parameters (S4).** asyncdb execution is `conn.query(sql)` with no
  parameter binding (`default.py:123`). `CompiledQuery.sql` is therefore
  complete, literal SQL (Sidemantic and sqlglot render typed, quoted literals);
  `CompiledQuery.parameters` carries only the canonical request
  (`{"request": SemanticQuery.model_dump(), "roles": [...]}`) for traceability
  and `execute()`, never values to bind.
- **Identity and principal propagation (S2).** `QS.build_provider()` does not
  hand `self._principal` to providers; the slug branch's provider arguments gain
  `"principal": self._principal` so the library path reaches the provider.
  Request path: the provider builds the principal from `request['user_session']`.
- **4xx taxonomy (S8).** `QS.build_provider()` wraps any provider exception
  into `QueryError(500)` unless it is a `ParserError` (`qs.py:318-325`). All
  semantic errors derive from `SemanticError(QueryException)` and the slug
  branch re-raises `SemanticError` unchanged, exactly as it already does for
  `RawQueryPlaceholderError` (`qs.py:315`). Governance layers are
  conjunctive: resource PBAC, per-field PBAC, Sidemantic `SecurityPolicy` and
  `governance.row_filters` must all pass; none can relax another.
- **Legacy dry-run contract (S7).** `dry_run()` keeps returning the
  `(sql, None)` tuple that `QS.dry_run()` and `QueryService.test_slug()`
  unpack; the object result is exposed as `semanticProvider.compiled` and
  through `querysource.semantic.compile()`.
- **Cache identity (S3).** `BaseProvider.checksum()` hashes only `_query`
  (`abstract.py:330`); the provider overrides it to hash
  `sql + model_version + dialect + connection`, and `model_version` is a digest
  of the resolved document bytes, so a path-backed model change invalidates
  results even when the DB revision is unchanged.
- **Transport conditions (S5).** `QueryService` merges `{**json_body,
  **query_params}` (`service.py:295`), so query parameters win over the body.
  `SemanticQuery.from_conditions()` strips the documented transport keys and
  rejects any other unknown key.

The Python two-step API (`querysource.semantic.compile()` / `execute()`) wraps
the same provider: `compile()` builds the provider up to `prepare_connection()`
and returns the `CompiledQuery`; `execute()` re-attaches the connection and runs
it. The catalog endpoint (`querysource/handlers/semantic.py`) reuses the
`ModelLoader`, enforces `semantic:execute`, and annotates every field with the
principal's `semantic:use` decision.

Decisions carried verbatim from the brainstorm (all ten open questions were
resolved there on 2026-10-14): explicit `get_provider` branch; pydantic v2
models with `pydantic>=2` promoted to a core dependency; AGPL Sidemantic
approved as an unmodified optional extra; one `user_attributes` shape for both
paths; `ResourceType.SEMANTIC` via the shim now; field resources
`<slug>:metric:<name>` / `<slug>:dimension:<name>` with action
`semantic:use`; inline Ossie through a temp file and the public adapter API;
request-level `time_grain`; a catalog endpoint in v1; role-keyed
`governance.row_filters` in the resource YAML, AND-ed across matching roles.

### Component Diagram
```
HTTP  POST /api/v1/queries/{slug}            GET /api/v1/semantic/{slug}/catalog
      QueryService.query ──┐                  SemanticCatalog.catalog ──┐
                           │                                            │
Python  QS(slug, conditions, principal)   querysource.semantic.compile()/execute()
                           │                                            │
                           ▼                                            │
                 QS.build_provider ── DefinitionRepository.get ─────────┤ (LoadedDefinition)
                           │                                            │
                 Connection.get_provider ──'semantic'──▶ [None, semanticProvider]
                           │                                            │
                           ▼                                            ▼
                 semanticProvider ───────────────────────────▶ ModelLoader (cache by revision+dialect)
                   │  SemanticResource / SemanticQuery (models.py)       │
                   │  governance.py ── PBAC (enforcement.py) ── user_attributes
                   │  filters.py (sqlglot) ── dialects.py (driver → dialect)
                   ▼                                                     ▼
                 SemanticCompiler (protocol.py) ◀── SidemanticCompiler ── SemanticLayer + CompileOnlyAdapter
                   │  CompiledQuery(sql, dialect, connection, model_version, warnings)
                   ▼
                 Connection.datasource(name) ── AsyncDB conn.query(sql) ── QS output / cache
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QS.build_provider()` (`querysource/queries/qs.py:186`) | uses, unchanged | slug path reads the definition, calls `get_provider`, instantiates the provider with `definition=objquery`, `conditions`, `request`, `loop` |
| `Connection.get_provider()` (`querysource/interfaces/connections.py:264`) | modifies | new `elif provider == 'semantic'` branch before `EXTERNAL_PROVIDERS` returning `[None, self.load_provider('semantic')]` |
| `Connection.load_provider()` (`connections.py:376`) | uses | imports `querysource.providers.semantic.semanticProvider` by convention |
| `Connection.datasource()` (`connections.py:396`) | uses | resolves `SemanticResource.connection`; returns `(source, AsyncDB)`; `source.driver` feeds the dialect map |
| `BaseProvider` (`querysource/providers/abstract.py:35`) | extends | `semanticProvider.__parser__ = None`; overrides `prepare_connection`, `dry_run`, `query`, `columns`, `describe_columns`, `close` |
| `defaultProvider.query()` (`providers/default.py:117`) | pattern | `async with await self._connection.connection() as conn: result, error = await conn.query(sql)` |
| `QueryModel` (`querysource/models.py:49`) | uses, no new columns | `provider='semantic'`, `query_raw` = resource YAML |
| `LoadedDefinition.revision` (`querysource/tenants.py:59`) | uses | `model_version` source; `QS` stores it as `_definition_revision` |
| `ResourceType` shim (`querysource/auth/_resource_types.py`) | modifies | adds `SEMANTIC = _StringResourceType("semantic")` to the fallback class and the `_check` tuple stays unchanged (upstream enum may not have it yet) |
| `enforcement.resolve_evaluator / build_eval_context / evaluate` (`querysource/auth/enforcement.py:70,103,122`) | uses | one evaluation per (resource, action); PBAC off → allowed |
| `QSPrincipal` (`querysource/auth/principal.py:21`) | uses | `to_userinfo()` is the `user_attributes` dict; request path builds one from the session |
| `QueryService.query()` (`querysource/handlers/service.py:134`) | unchanged | JSON body + query params reach the provider as `conditions` (`service.py:295`); `slug:execute` already enforced there |
| `QueryDescribe` (`querysource/handlers/describe.py:70`) | pattern | `_principal`, `_store`, `_load_visible` reused by the catalog handler |
| `describe_slug()` (`querysource/queries/describe.py:204`) | modifies | adds `derived.semantic` (catalog summary) when `provider == 'semantic'` |
| `QuerySource.setup()` (`querysource/services.py:132`) | modifies | registers the catalog routes after the tenant describe routes (`services.py:225`) |
| `querysource/handlers/__init__.py` | modifies | exports `SemanticCatalog` |
| `querysource/conf.py` | modifies | `QS_SEMANTIC_MODELS_DIR`, `QS_SEMANTIC_DEFAULT_LIMIT`, `QS_SEMANTIC_MAX_LIMIT`, `QS_SEMANTIC_MAX_FILTERS`, `QS_SEMANTIC_CACHE_SIZE` after the PBAC block (`conf.py:475-477`) |
| `pyproject.toml` | modifies | `pydantic>=2` in core deps; new `semantic` extra |
| QS cache (`QS.query`, `is_cached`) | depends on | cache key gains `model_version` (set by the provider on `self._definition`-derived checksum) |

### Data Models
```python
# querysource/semantic/models.py — pydantic v2 (decided in brainstorm Q2)
from pydantic import BaseModel, Field

FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "not in", "between", "like", "ilike",
              "is null", "is not null")
TIME_GRAINS = ("day", "week", "month", "quarter", "year")

class SemanticFilter(BaseModel):
    field: str                       # dimension name, qualified or not
    op: str                          # one of FILTER_OPS (validated)
    value: Any | None = None         # scalar, list (in / between) or None (is null)

class SemanticQuery(BaseModel):
    metrics: list[str] = Field(default_factory=list, description="Catalog metric names")
    dimensions: list[str] = Field(default_factory=list)
    filters: list[SemanticFilter | str] = Field(default_factory=list)
    time_grain: str | None = None    # one of TIME_GRAINS
    order_by: list[str] = Field(default_factory=list)   # "<field> [asc|desc]"
    limit: int | None = None         # defaulted / capped by settings at compile time
    offset: int = 0
    # model_config = ConfigDict(extra="forbid")  → unknown keys are a 400

class SemanticDefinition(BaseModel):
    format: Literal["ossie", "sidemantic"] = "ossie"
    compiler: Literal["sidemantic"] = "sidemantic"
    definition: str | dict[str, Any]     # relative path (str) or inline document (dict)
    scope: str | None = None

class GovernanceBlock(BaseModel):
    row_filters: dict[str, list[SemanticFilter | str]] = Field(default_factory=dict)  # role → filters; "default" applies to all

class SemanticResource(BaseModel):
    name: str
    kind: Literal["semantic"]
    connection: str | None = None
    semantic: SemanticDefinition
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)

class SemanticModelRef(BaseModel):   # what the loader hands to the compiler
    slug: str
    revision: str
    format: str
    scope: str | None
    source_path: Path                # resolved file (temp file for inline)
    model_version: str               # "<revision>:<sha256(document)[:12]>"

class CompilationTarget(BaseModel):
    connection: str | None           # datasource name or None (default pg)
    driver: str                      # datasource driver name ('pg', 'bigquery', …)
    dialect: str                     # sqlglot/sidemantic dialect
    user_attributes: dict[str, Any] | None
    default_limit: int
    max_limit: int

class CompiledQuery(BaseModel):
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    connection: str | None
    dialect: str
    model_version: str
    metrics: list[str]               # qualified names actually compiled
    dimensions: list[str]
    warnings: list[str] = Field(default_factory=list)

class CatalogField(BaseModel):
    name: str                        # qualified "model.field"
    kind: Literal["metric", "dimension"]
    type: str | None                 # dimension type / metric agg
    description: str | None
    granularities: list[str] = Field(default_factory=list)   # time dimensions
    granted: bool                    # principal's semantic:use decision

class CatalogPayload(BaseModel):
    slug: str
    model_version: str
    dialect: str
    models: list[str]
    metrics: list[CatalogField]
    dimensions: list[CatalogField]
    row_filters_applied: list[str]   # governance roles that matched the principal
```

### New Public Interfaces
```python
# querysource/semantic/protocol.py
class SemanticCompiler(Protocol):
    def compile(self, model: SemanticModelRef, request: SemanticQuery,
                target: CompilationTarget) -> CompiledQuery: ...
    def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]: ...

# querysource/semantic/api.py (re-exported from querysource/semantic/__init__.py)
async def compile(resource: str, *, metrics: list[str], dimensions: list[str] | None = None,
                  filters: list[dict | str] | None = None, time_grain: str | None = None,
                  order_by: list[str] | None = None, limit: int | None = None, offset: int = 0,
                  tenant: str | None = None, principal: QSPrincipal | None = None) -> CompiledQuery: ...
async def execute(compiled: CompiledQuery, *, tenant: str | None = None,
                  principal: QSPrincipal | None = None, output_format: str | None = None): ...

# querysource/providers/semantic.py
class semanticProvider(BaseProvider): ...        # loaded by Connection.load_provider('semantic')

# querysource/handlers/semantic.py
class SemanticCatalog(AbstractHandler):
    async def catalog(self, request: web.Request) -> web.Response: ...   # GET .../semantic/{slug}/catalog
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Semantic models & protocol | yes | pydantic v2, `extra="forbid"` on `SemanticQuery`, field/op/grain constants fixed above; `SemanticCompiler` Protocol with two methods | — |
| M2: Dialects & filter validation | yes | `DRIVER_DIALECTS` table fixed in §7; sqlglot parse with column/function allow-lists; errors are `SemanticRequestError(code=400)` | — |
| M3: Model loader | yes | inline → temp file with format suffix; `load_from_file`; LRU keyed `(revision, dialect, format, scope)`; path-traversal guard | — |
| M4: Sidemantic compiler | no | compile-only adapter and name qualification are decided, but the mapping of Sidemantic exceptions and `describe_models()` output shape must be verified against the installed package during implementation | shape of `describe_models()` and `SecurityError` module path unverified |
| M5: Governance | yes | resource naming, action strings, AND semantics and `principal_from_session()` mapping fixed below | — |
| M6: Provider | yes | subclass of `BaseProvider`, `__parser__ = None`; `query()` mirrors `defaultProvider.query()` | — |
| M7: Wiring (connections branch, shim, settings, extra) | yes | anchors in §6 Edit Sites | — |
| M8: Python API | yes | thin wrappers over `QS` | — |
| M9: Catalog endpoint & describe | yes | route paths and `QueryDescribe` helpers fixed | — |
| M10: Docs & integration test | yes | `docs/SEMANTIC_LAYER.md`; opt-in Postgres test | — |

### Module 1: Semantic models & compiler Protocol
- **Path**: `querysource/semantic/__init__.py`, `querysource/semantic/models.py`, `querysource/semantic/protocol.py`, `querysource/semantic/exceptions.py`
- **Responsibility**: QuerySource-owned public contract (request, resource, compile result, catalog) and the compiler Protocol; no Sidemantic import.
- **Depends on**: `pydantic>=2` (core dependency after M7), `querysource/exceptions.py`
- **Interface Skeleton**:
  ```python
  # querysource/semantic/models.py  (new) — see §2 Data Models for every class; constraints:
  class SemanticQuery(BaseModel):
      """Structured request; unknown keys rejected (extra='forbid'); `time_grain` ∈ TIME_GRAINS."""
      @classmethod
      def from_conditions(cls, conditions: dict[str, Any]) -> "SemanticQuery":
          """Build from QS `conditions`, dropping reserved QS keys (refresh, _output_options, _csv_options,
          _graph_options, _report_options, queryformat, _download, _filename) first. Raises SemanticRequestError (400)."""

  class SemanticResource(BaseModel):
      """Parsed `query_raw` of a semantic slug (kind == 'semantic')."""
      @classmethod
      def from_yaml(cls, text: str) -> "SemanticResource":
          """yaml.safe_load + validate; raises SemanticResourceError (400) on malformed documents."""

  # querysource/semantic/protocol.py  (new)
  class SemanticCompiler(Protocol):
      """Adapter contract; implementations never leak their internal objects through CompiledQuery."""
      def compile(self, model: SemanticModelRef, request: SemanticQuery, target: CompilationTarget) -> CompiledQuery: ...
      def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]: ...

  # querysource/semantic/exceptions.py  (new)
  class SemanticError(QueryException):         # QueryException verified: querysource/exceptions.py:6 — base; re-raised as-is by QS.build_provider (M7)
      """Base of every semantic-layer error; carries `code` like QueryException."""
  class SemanticRequestError(SemanticError):   # code=400 — unknown field, bad operator, unparsable filter, bad grain, limit abuse
  class SemanticResourceError(SemanticError):  # code=400 — malformed resource YAML / model document / non-SQL datasource
  class SemanticGovernanceError(SemanticError):  # code=500 — invalid governance.row_filters (fail-closed, logged with slug+role)
  class SemanticCompilerUnavailable(SemanticError):  # code=501 — sidemantic not installed
  class SemanticFieldDenied(SemanticError):    # code=403 — names the denied metric/dimension (or 'security' for SecurityError)
  ```

### Module 2: Dialect map & filter validation
- **Path**: `querysource/semantic/dialects.py`, `querysource/semantic/filters.py`
- **Responsibility**: driver → sqlglot dialect; validation and rendering of structured and string filters to Sidemantic filter strings.
- **Depends on**: Module 1; `sqlglot>=30.1` (in the `semantic` extra; imported lazily inside `filters.py`)
- **Interface Skeleton**:
  ```python
  # querysource/semantic/dialects.py  (new)
  DRIVER_DIALECTS: dict[str, str]   # see §7 table; keys are querysource driver names (SUPPORTED, drivers/__init__.py:30)
  def dialect_for_driver(driver: str) -> tuple[str, str | None]:
      """Return (dialect, warning). Unknown driver → ('ansi', 'dialect not mapped for driver X; ANSI SQL generated')."""

  # querysource/semantic/filters.py  (new)
  class FilterValidator:
      """Validates caller and governance filters against the model catalog."""
      def __init__(self, dimensions: dict[str, CatalogField], *, dialect: str, max_filters: int) -> None: ...
      def render(self, filters: list[SemanticFilter | str]) -> list[str]:
          """Return Sidemantic filter strings. Structured: op ∈ FILTER_OPS, field ∈ dimensions, value typed by the
          dimension. String: sqlglot.parse_one(dialect) → every Column must be a dimension, no subqueries, functions
          only from ALLOWED_FUNCTIONS (lower, upper, date_trunc, coalesce, abs, round, length). Raises SemanticRequestError."""
  ```

### Module 3: Model loader
- **Path**: `querysource/semantic/loader.py`
- **Responsibility**: resolve the definition (inline or path), guard the path, hash the document, write inline text to a private temp file, and hand a `SemanticModelRef` to the compiler; cache loaded layers.
- **Depends on**: Module 1; settings from Module 7 (`QS_SEMANTIC_MODELS_DIR`, `QS_SEMANTIC_CACHE_SIZE`)
- **Interface Skeleton**:
  ```python
  # querysource/semantic/loader.py  (new)
  class ModelLoader:
      """Resolves semantic documents; one process-wide instance (functools.lru_cache-style bounded dict)."""
      def __init__(self, models_dir: Path, *, cache_size: int) -> None: ...
      def resolve(self, slug: str, revision: str, resource: SemanticResource) -> SemanticModelRef:
          """Inline definition wins over path (warning recorded). Path must resolve under models_dir
          (realpath; otherwise SemanticResourceError 400, logged). model_version = f"{revision}:{sha256[:12]}".
          Inline → NamedTemporaryFile(delete=False, suffix='.ossie.yaml' | '.yml') under a private dir."""
      def cached(self, key: tuple[str, str, str, str | None]) -> Any | None: ...
      def store(self, key: tuple[str, str, str, str | None], layer: Any) -> None: ...
  ```

### Module 4: Sidemantic compiler
- **Path**: `querysource/semantic/compilers/__init__.py`, `querysource/semantic/compilers/sidemantic.py`
- **Responsibility**: the only module importing `sidemantic`; builds a compile-only `SemanticLayer`, loads the document, qualifies names, applies `time_grain`, compiles with `user_attributes`, maps errors, produces the catalog.
- **Depends on**: Modules 1–3; `sidemantic>=0.12.0,<0.13` (lazy import; `SemanticCompilerUnavailable` when missing)
- **Interface Skeleton**:
  ```python
  # querysource/semantic/compilers/sidemantic.py  (new)
  class CompileOnlyAdapter(BaseDatabaseAdapter):   # verified upstream: sidemantic/db/base.py:176 (abstract methods execute, executemany, fetchone, fetch_record_batch, get_tables, get_columns, close, dialect, raw_connection)
      """Never executes; `dialect` returns the target dialect so SemanticLayer(connection=adapter) opens nothing."""
      def __init__(self, dialect: str) -> None: ...

  class SidemanticCompiler:
      """SemanticCompiler implementation (Protocol from querysource/semantic/protocol.py)."""
      def __init__(self, loader: ModelLoader) -> None: ...
      def _layer(self, model: SemanticModelRef, target: CompilationTarget) -> "SemanticLayer":
          """Cache hit or: SemanticLayer(connection=CompileOnlyAdapter(target.dialect), dialect=target.dialect,
          auto_register=False, default_limit=target.default_limit, max_limit=target.max_limit);
          load_from_file(layer, model.source_path, ossie_scope_id=model.scope,
                         ossie_adapter_options={'target_dialect': target.dialect})   # verified upstream: sidemantic/loaders.py:693,231"""
      def qualify(self, layer, names: list[str], *, kind: str) -> list[str]:
          """'net_sales' → 'orders.net_sales' when unique in the catalog; ambiguous/unknown → SemanticRequestError (400) listing candidates."""
      def compile(self, model, request, target) -> CompiledQuery:
          """layer.compile(metrics=, dimensions=, filters=, order_by=, limit=, offset=, dialect=target.dialect,
          user_attributes=target.user_attributes)  # verified upstream: sidemantic/core/semantic_layer.py:1143
          SecurityError → SemanticFieldDenied(403); ValueError/KeyError on unknown field → SemanticRequestError(400)."""
      def catalog(self, model, target) -> list[CatalogField]:
          """From layer.describe_models() (verified upstream: semantic_layer.py:925) + layer.get_model(name) dimensions/metrics."""
  ```

### Module 5: Governance
- **Path**: `querysource/semantic/governance.py`
- **Responsibility**: PBAC evaluations (slug + per field), `user_attributes`, principal from session, governance row filters.
- **Depends on**: Module 1; `querysource/auth/enforcement.py`, `querysource/auth/principal.py`, `querysource/auth/_resource_types.py` (after Module 7 adds `SEMANTIC`)
- **Interface Skeleton**:
  ```python
  # querysource/semantic/governance.py  (new)
  def principal_from_session(session: Any | None) -> QSPrincipal | None:   # QSPrincipal verified: querysource/auth/principal.py:21
      """session['user'] (navigator_auth AUTH_SESSION_OBJECT, as slug_visibility.resolve_principal reads it,
      verified: querysource/auth/slug_visibility.py:132-138) → QSPrincipal(user_id, username, groups, roles,
      programs, superuser, channel='http'). None when no session/userinfo."""
  def user_attributes(principal: QSPrincipal | None) -> dict[str, Any] | None:
      """principal.to_userinfo() (verified: principal.py:97) plus 'tenant_id'; None when no principal."""
  async def check(request: web.Request | None, principal: QSPrincipal | None,
                  resource_name: str, action: str, *, logger: logging.Logger) -> AccessDecision:
      """resolve_evaluator(request, detached=request is None) (verified: enforcement.py:70); PBAC off → allowed;
      build_eval_context(userinfo=principal.to_userinfo(), user=..., session=None, request=request) (verified: :103);
      evaluate(evaluator, ctx, ResourceType.SEMANTIC, resource_name, action) (verified: :122)."""
  async def enforce_query(request, principal, slug: str, query: SemanticQuery, *, logger) -> None:
      """check(slug, 'semantic:execute') → deny raises QueryAccessDenied (verified: exceptions.py:63);
      then for each metric m: check(f'{slug}:metric:{m}', 'semantic:use'), each dimension d:
      check(f'{slug}:dimension:{d}', 'semantic:use') → deny raises SemanticFieldDenied(field)."""
  async def enforce_datasource(request, principal, datasource: str, driver: str, *, logger) -> None:
      """ResourceType.DATASOURCE/<datasource> 'datasource:use' and ResourceType.DRIVER/<driver> 'driver:use'
      (same resource types/actions QueryService uses, verified: handlers/service.py:346-365); deny → QueryAccessDenied.
      Required because the handler's own check sees only the literal provider name 'semantic' (S1)."""
  def governance_filters(resource: SemanticResource, principal: QSPrincipal | None) -> tuple[list[SemanticFilter | str], list[str]]:
      """row_filters['default'] + blocks whose key ∈ principal.roles; returns (filters, matched_roles). AND semantics
      (every filter appended). No principal → only 'default'."""
  ```

### Module 6: Semantic provider
- **Path**: `querysource/providers/semantic.py`
- **Responsibility**: the `BaseProvider` subclass that ties Modules 1–5 to the slug execution path.
- **Depends on**: Modules 1–5, 7; `BaseProvider` (`querysource/providers/abstract.py:35`), `Connection.datasource` (`connections.py:396`)
- **Interface Skeleton**:
  ```python
  # querysource/providers/semantic.py  (new)
  class semanticProvider(BaseProvider):   # BaseProvider verified: querysource/providers/abstract.py:35
      """Compile a SemanticQuery against the slug's semantic model and run the SQL on its datasource."""
      __parser__ = None                    # abstract.py:126 — no parser is built
      def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                   definition: Union[QueryModel, dict] = None, conditions: dict = None,
                   request: web.Request = None, **kwargs):   # signature verified: abstract.py:55-64
          """Parses definition.query_raw → SemanticResource; conditions → SemanticQuery; raises
          SemanticResourceError / SemanticRequestError (both 400 via QueryError in QS.build_provider, qs.py:299-310)."""
      async def prepare_connection(self) -> None:
          """Order: principal (kwargs['principal'] or principal_from_session(request['user_session'])) → datasource
          (QueryConnection().datasource(name) or default pg connection) → enforce_datasource (datasource:use, driver:use)
          → dialect → model (ModelLoader) → enforce_query (semantic:execute, semantic:use per field) → governance
          filters → filter validation → compile → self.compiled: CompiledQuery; self._query = compiled.sql.
          Every failure is a SemanticError subclass (4xx/501) or QueryAccessDenied; never a bare Exception."""
      async def dry_run(self) -> tuple[str, None]: ...           # (self.compiled.sql, None) — legacy tuple kept (S7)
      async def query(self) -> list:                             # mirrors defaultProvider.query, default.py:117-148
          """[result, error]; executes compiled.sql verbatim (literal SQL, no parameter binding — S4); NotFound on empty."""
      def checksum(self) -> str:                                 # overrides abstract.py:330
          """get_hash(f'{compiled.sql}|{compiled.model_version}|{compiled.dialect}|{compiled.connection}') (S3)."""
      async def columns(self) -> list[str]: ...                  # compiled.metrics + compiled.dimensions (output aliases)
      async def describe_columns(self) -> list[dict]: ...        # [{'name', 'type'}] from the catalog
      async def close(self) -> None: ...
      compiled: CompiledQuery                                    # set by prepare_connection; read by api.compile()
  ```

### Module 7: Wiring — provider branch, QS pass-through, resource type, settings, dependencies
- **Path**: `querysource/interfaces/connections.py`, `querysource/queries/qs.py`, `querysource/auth/_resource_types.py`, `querysource/conf.py`, `pyproject.toml`
- **Responsibility**: the small edits that make the provider reachable, pass the principal through, surface semantic 4xx errors, and make the feature configurable.
- **Depends on**: Module 1 (`SemanticError` import in `qs.py`); otherwise none (can land first)
- **Interface Skeleton**:
  ```python
  # querysource/interfaces/connections.py  (modifies connections.py:285)
      elif provider == 'semantic':      # inserted BEFORE `elif provider in EXTERNAL_PROVIDERS:`
          _provider = self.load_provider('semantic')
          return [None, _provider]      # the provider resolves its own datasource

  # querysource/queries/qs.py  (modifies the slug branch only)
                  "loop": self._loop,
                  "principal": self._principal,     # added to the slug-branch args dict (qs.py:303-311) — S2
              }
              self._qs = self._provider(**args)
              await self._qs.prepare_connection()
              return self
          except (RawQueryPlaceholderError, SemanticError):   # qs.py:315 — SemanticError surfaces with its own code (S8)
              raise
  # `from ..semantic.exceptions import SemanticError` — core import, no sidemantic dependency

  # querysource/auth/_resource_types.py  (modifies :45)
      SEMANTIC = _StringResourceType("semantic")   # fallback shim; the upstream `_check` tuple (:22-27) is NOT extended

  # querysource/conf.py  (modifies after :477)
  QS_SEMANTIC_MODELS_DIR = config.get('QS_SEMANTIC_MODELS_DIR', fallback=str(BASE_DIR / 'semantic'))
  QS_SEMANTIC_DEFAULT_LIMIT = config.getint('QS_SEMANTIC_DEFAULT_LIMIT', fallback=500)
  QS_SEMANTIC_MAX_LIMIT = config.getint('QS_SEMANTIC_MAX_LIMIT', fallback=5000)
  QS_SEMANTIC_MAX_FILTERS = config.getint('QS_SEMANTIC_MAX_FILTERS', fallback=32)
  QS_SEMANTIC_CACHE_SIZE = config.getint('QS_SEMANTIC_CACHE_SIZE', fallback=64)
  QS_SEMANTIC_MAX_MODEL_BYTES = config.getint('QS_SEMANTIC_MAX_MODEL_BYTES', fallback=2 * 1024 * 1024)

  # pyproject.toml  (modifies :117 and :179)
      "pydantic>=2,<3",                      # core dependency (brainstorm Q2)
  semantic = ["sidemantic[ossie]>=0.12.0,<0.13", "sqlglot>=30.1"]
  ```

### Module 8: Python API
- **Path**: `querysource/semantic/api.py` (+ re-export in `querysource/semantic/__init__.py`)
- **Responsibility**: `compile()` / `execute()` two-step helpers over `QS`.
- **Depends on**: Modules 1, 6; `QS` (`querysource/queries/qs.py:50`)
- **Interface Skeleton**:
  ```python
  # querysource/semantic/api.py  (new)
  async def compile(resource: str, *, metrics: list[str], dimensions=None, filters=None, time_grain=None,
                    order_by=None, limit=None, offset=0, tenant=None, principal=None) -> CompiledQuery:
      """QS(slug=resource, conditions=SemanticQuery(...).model_dump(), tenant=tenant, principal=principal)
      (QS.__init__ verified: qs.py:56-64); await qs.build_provider() (qs.py:186); return qs._qs.compiled; await qs.close()."""
  async def execute(compiled: CompiledQuery, *, tenant=None, principal=None, output_format=None):
      """Re-run through QS with the same conditions (conditions are embedded in CompiledQuery.parameters['request'])
      and return qs.query(output_format) (verified: qs.py:457). Never executes compiled.sql directly from the caller."""
  ```

### Module 9: Catalog endpoint & describe integration
- **Path**: `querysource/handlers/semantic.py`, `querysource/handlers/__init__.py`, `querysource/services.py`, `querysource/queries/describe.py`
- **Responsibility**: `GET /api/v1/semantic/{slug}/catalog` and `GET /api/v1/{tenant}/semantic/{slug}/catalog`; `derived.semantic` in the describe payload.
- **Depends on**: Modules 1, 3–5, 7; `QueryDescribe` helpers (`handlers/describe.py:75-143`), `AbstractHandler._enforce_pbac` (`handlers/abstract.py:335`)
- **Interface Skeleton**:
  ```python
  # querysource/handlers/semantic.py  (new)
  class SemanticCatalog(QueryDescribe):   # QueryDescribe verified: querysource/handlers/describe.py:70 — reuses _principal/_store/_load_visible
      """Read-only catalog for semantic slugs."""
      async def catalog(self, request: web.Request) -> web.Response:
          """200 CatalogPayload | 401 (no principal) | 404 (missing / not visible / provider != 'semantic' / PBAC deny)
          | 501 (compiler unavailable). Tenant-aware through self._store() (legacy or {tenant} store) and
          self._load_visible() (slug visibility + can_access); then ResourceType.SEMANTIC '<slug>' 'semantic:execute'
          via self._enforce_pbac (verified: handlers/abstract.py:335); then governance.check() per field.
          Redaction (S6): a denied field is serialized with name, kind and granted=false ONLY — description, type,
          granularities and any expression are omitted; model names of fully denied models are still listed."""

  # querysource/handlers/__init__.py  (modifies :6 and :20)   from .semantic import SemanticCatalog ; 'SemanticCatalog',
  # querysource/services.py  (modifies after :225)
          sc = SemanticCatalog()
          r = self.app.router.add_get('/api/v1/semantic/{slug}/catalog', sc.catalog); routes.append(r)
          r = self.app.router.add_get('/api/v1/{tenant}/semantic/{slug}/catalog', sc.catalog); routes.append(r)
  # querysource/queries/describe.py  (modifies :260)
  #   when base.get('provider') == 'semantic': derived['semantic'] = {'format', 'connection', 'definition_kind': 'inline'|'path',
  #   'governance_roles': [...]} parsed with SemanticResource.from_yaml (no compiler import; catalog link to the endpoint)
  ```

### Module 10: Documentation & integration test
- **Path**: `docs/SEMANTIC_LAYER.md`, `tests/integration/test_semantic_postgres.py`
- **Responsibility**: operator/consumer documentation and an opt-in end-to-end test against PostgreSQL with `querysource[semantic]` installed.
- **Depends on**: Modules 1–9
- **Interface Skeleton**: documentation only (resource YAML reference, request contract, governance, dialect table, settings, error table); the integration test is skipped unless `QS_SEMANTIC_IT=1` and `sidemantic` imports.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_semantic_query_from_conditions_strips_reserved_keys` | M1 | `refresh`, `_output_options`, `queryformat` removed; unknown key → `SemanticRequestError` |
| `test_semantic_query_rejects_bad_grain_and_limit` | M1 | `time_grain='hour'` → 400; `limit=-1` → 400 |
| `test_semantic_resource_from_yaml_inline_and_path` | M1 | inline dict and path string both validate; `kind != 'semantic'` → `SemanticResourceError` |
| `test_dialect_for_driver_table` | M2 | every key of `SUPPORTED` maps or yields the ANSI warning |
| `test_filter_structured_ops_and_unknown_dimension` | M2 | each `FILTER_OPS` renders; unknown field → 400; `op='drop'` → 400 |
| `test_filter_string_rejects_subquery_and_function` | M2 | `"region = (SELECT 1)"`, `"pg_sleep(1) = 1"` → 400; `"lower(region) = 'es'"` accepted |
| `test_filter_string_literal_quoting` | M2 | `"1 OR 1=1"` value is rendered as a quoted literal |
| `test_loader_inline_wins_and_warns` | M3 | inline + path → inline used, warning recorded |
| `test_loader_rejects_path_traversal` | M3 | `../../etc/passwd` → `SemanticResourceError`, logged |
| `test_loader_model_version_and_cache_key` | M3 | same revision+dialect → cache hit; changed document → new `model_version` |
| `test_compiler_unavailable_501` | M4 | `sidemantic` import patched to fail → `SemanticCompilerUnavailable` |
| `test_compiler_qualifies_and_rejects_ambiguous` | M4 (skip without sidemantic) | `revenue` → `orders.revenue`; duplicate names → 400 with candidates |
| `test_compiler_time_grain_rewrite` | M4 (skip without sidemantic) | `order_date` + `time_grain=month` → `orders.order_date__month`; explicit `__day` wins |
| `test_compiler_security_error_maps_to_403` | M4 (skip without sidemantic) | secured model, `user_attributes=None` → `SemanticFieldDenied` |
| `test_compile_only_adapter_never_executes` | M4 (skip without sidemantic) | `CompileOnlyAdapter.execute` raises; `SemanticLayer(connection=adapter)` opens no DuckDB |
| `test_governance_pbac_off_allows_all` | M5 | no runtime → all `check()` allowed |
| `test_governance_field_denied_names_field` | M5 | fake evaluator denies `epson.sales:metric:margin` → `SemanticFieldDenied('margin')` |
| `test_governance_row_filters_roles_and_default` | M5 | roles `['analyst']` → default + analyst filters; no principal → default only; unknown role ignored |
| `test_principal_from_session_shape` | M5 | session `{'user': {...}}` → `QSPrincipal.to_userinfo()` keys |
| `test_get_provider_semantic_branch` | M7 | `Connection.get_provider(AttrDict(provider='semantic'))` → `[None, semanticProvider]` and no `default_driver` call |
| `test_resource_type_semantic_shim` | M7 | `ResourceType.SEMANTIC == 'semantic'` on the fallback class |
| `test_conf_semantic_defaults` | M7 | settings default values |
| `test_provider_dry_run_returns_compiled_sql` | M6 (e2e, skip without sidemantic) | `FakeDefinitionRepository` slug with inline Sidemantic model; `QS.dry_run()` returns SQL containing `SUM(` |
| `test_provider_rejects_unknown_metric_400` | M6 | error lists available metrics |
| `test_provider_limit_capped_with_warning` | M6 | `limit=99999` → `QS_SEMANTIC_MAX_LIMIT`, warning present |
| `test_api_compile_returns_compiled_query` | M8 | `compile()` returns `CompiledQuery` with `model_version` and no execution |
| `test_catalog_route_registered` | M9 | both catalog routes present in `QuerySource.setup()` (pattern: `tests/test_route_registration.py`) |
| `test_catalog_401_404_200` | M9 | no session → 401; non-semantic slug → 404; semantic slug → 200 with `granted` per field |
| `test_catalog_redacts_denied_fields` | M9 | denied metric appears with `name`, `kind`, `granted=false` only (no description/type) |
| `test_catalog_tenant_route_uses_tenant_store` | M9 | `/api/v1/{tenant}/semantic/{slug}/catalog` resolves the tenant store (pattern: `tests/handlers/test_describe_tenant.py`) |
| `test_describe_semantic_derived` | M9 | `describe_slug()` adds `derived.semantic` for `provider='semantic'` |
| `test_provider_enforces_datasource_and_driver_use` | M6 | fake evaluator denying `datasource:use` on `epson_warehouse` → `QueryAccessDenied` before the model is loaded |
| `test_provider_semantic_error_surfaces_with_code` | M7 | `SemanticRequestError` raised in `prepare_connection` reaches `QS.build_provider()` callers with code 400, not 500 |
| `test_provider_receives_principal` | M7 | `QS(slug, principal=p)` → provider `kwargs['principal'] is p` |
| `test_provider_checksum_includes_model_version` | M6 | same SQL, different `model_version` → different `checksum()` |
| `test_dry_run_tuple_contract` | M6 | `QS.dry_run()` on a semantic slug returns `(sql, None)`; `qs._qs.compiled` is a `CompiledQuery` |
| `test_multiqs_semantic_child_without_principal_is_denied_on_secured_model` | M6 | a MultiQS child semantic slug whose model has `security:` and no principal → 403 (fail-closed; see §8 Q13) |

### Integration Tests
| Test | Description |
|---|---|
| `test_semantic_slug_end_to_end_postgres` | Opt-in (`QS_SEMANTIC_IT=1`): create a table, a semantic slug with an inline Sidemantic model and `governance.row_filters`, run `POST /api/v1/queries/{slug}` with metrics/dimensions/filters as a role-bearing user, assert rows and that denied rows are absent; `dry_run` SQL is Postgres dialect |
| `test_semantic_catalog_end_to_end` | Same fixture: `GET /api/v1/semantic/{slug}/catalog` lists metrics with descriptions and `granted` flags |

### Test Data / Fixtures
```python
# tests/semantic/conftest.py
@pytest.fixture
def sidemantic_available():
    pytest.importorskip("sidemantic")

@pytest.fixture
def inline_resource_yaml() -> str:
    """Resource YAML with an inline Sidemantic-native model (orders: revenue, order_count, region, order_date)."""

@pytest.fixture
def semantic_definitions(definitions):            # reuses tests/e2e/conftest.py::definitions (FakeDefinitionRepository)
    definitions.add(query_slug="epson.sales", provider="semantic", query_raw=inline_resource_yaml)
    return definitions

@pytest.fixture
def fake_evaluator(monkeypatch):
    """Patch querysource.auth.enforcement.resolve_evaluator to return (True, evaluator) whose check_access denies a chosen resource."""
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] AC-1 A `QueryModel` row with `provider='semantic'` and a valid resource YAML in `query_raw` executes through `QS(slug=...)` and `POST /api/v1/queries/{slug}` (and the tenant route) with no change to `QueryService` or `QS.build_provider`.
- [ ] AC-2 `Connection.get_provider()` returns `[None, semanticProvider]` for `provider == 'semantic'` without calling `default_driver()`; the provider resolves `connection:` through `Connection.datasource(name)` or the default PostgreSQL connection when omitted.
- [ ] AC-3 The base package imports neither `sidemantic` nor `duckdb`; without the extra, a semantic slug fails with HTTP 501 and the message names `querysource[semantic]`.
- [ ] AC-4 `SemanticLayer` is always constructed with the `CompileOnlyAdapter`; no DuckDB connection is opened in any code path (asserted by a test that makes `duckdb.connect` raise).
- [ ] AC-5 The SQL dialect is derived from the datasource driver per the §7 table; unmapped drivers compile with ANSI and a warning in `CompiledQuery.warnings`.
- [ ] AC-6 `querysource.semantic.compile()` and `semanticProvider.compiled` expose a `CompiledQuery` with `sql`, `parameters`, `connection`, `dialect`, `model_version`, `metrics`, `dimensions`, `warnings`; `dry_run()` keeps the legacy `(sql, None)` tuple; `model_version` is a digest of the resolved document bytes plus the definition revision and changes when either changes.
- [ ] AC-21 The provider enforces `datasource:use` on the resolved datasource name and `driver:use` on its driver (resource types `DATASOURCE` / `DRIVER`) for both the request and the principal paths, before the model is loaded; a deny raises `QueryAccessDenied`.
- [ ] AC-22 Every semantic failure is a `SemanticError` subclass or `QueryAccessDenied`; `QS.build_provider()` re-raises `SemanticError` unchanged so HTTP callers get 400/403/501, never a wrapped 500.
- [ ] AC-23 `semanticProvider.checksum()` covers `sql`, `model_version`, `dialect` and `connection`; a path-backed model edit with an unchanged DB revision produces a new cache key.
- [ ] AC-24 `CompiledQuery.sql` is complete literal SQL executed verbatim with `conn.query(sql)`; `CompiledQuery.parameters` never carries values that would need binding.
- [ ] AC-25 The slug-branch provider arguments in `QS.build_provider()` include `principal`; no other provider breaks (they accept `**kwargs`).
- [ ] AC-26 The catalog endpoint redacts denied fields to `name`, `kind`, `granted=false`; the tenant-prefixed route resolves the tenant store.
- [ ] AC-7 `query_raw` accepts the definition inline or as a path relative to `QS_SEMANTIC_MODELS_DIR`; inline wins with a warning when both are present; a path resolving outside the directory is rejected (400) and logged.
- [ ] AC-8 Inline Ossie documents are loaded only through Sidemantic's public `load_from_file()` on a private temp file; no `sidemantic.interchange` internals are imported.
- [ ] AC-9 All models in `querysource/semantic/` are `pydantic.BaseModel` (v2); `pydantic>=2,<3` is a core dependency in `pyproject.toml`.
- [ ] AC-10 With PBAC enabled, `semantic:execute` is evaluated on `ResourceType.SEMANTIC` / `<slug>` and `semantic:use` on `<slug>:metric:<name>` / `<slug>:dimension:<name>` for every requested field; a denied slug raises `QueryAccessDenied` (404) and a denied field returns 403 naming the field. With PBAC disabled every check is allowed.
- [ ] AC-11 `user_attributes` passed to the compiler always equal `QSPrincipal.to_userinfo()` (+ `tenant_id`) for both the request path (principal built from the session) and the library path; a model with a `security:` block and no principal is denied (403) before any SQL exists.
- [ ] AC-12 `governance.row_filters` blocks for `default` and every role in `principal.roles` are validated like caller filters and AND-ed into the request; an unknown role is ignored; an invalid governance filter is a 500 that logs slug and role.
- [ ] AC-13 Structured filters accept exactly `FILTER_OPS`; string filters are parsed with sqlglot and rejected (400) on unknown columns, subqueries or non-allow-listed functions; literal values are quoted so `"1 OR 1=1"` cannot escape the predicate.
- [ ] AC-14 `time_grain` rewrites unsuffixed time dimensions to `<dim>__<grain>`, explicit suffixes win, and a grain with no time dimension is a 400; unknown metrics/dimensions return 400 listing the available names; ambiguous unqualified names return 400 with candidates.
- [ ] AC-15 `limit` defaults to `QS_SEMANTIC_DEFAULT_LIMIT` and is capped at `QS_SEMANTIC_MAX_LIMIT` with a warning; more than `QS_SEMANTIC_MAX_FILTERS` filters is a 400.
- [ ] AC-16 `GET /api/v1/semantic/{slug}/catalog` and the tenant-prefixed route return `CatalogPayload` (200), 401 without a principal, 404 for missing/non-semantic/denied slugs; `describe_slug()` adds `derived.semantic` for semantic slugs.
- [ ] AC-17 Identical requests are served from the QS cache and the cache key includes `model_version`.
- [ ] AC-18 `ResourceType.SEMANTIC == "semantic"` is available from the shim; a follow-up task for the navigator-auth upstream enum is filed (not required for completion).
- [ ] AC-19 All unit tests pass (`pytest tests/semantic tests/providers/test_semantic_provider.py tests/handlers/test_semantic_catalog.py -q`); Sidemantic-dependent tests skip cleanly when the extra is absent; `ruff check` is clean on every touched file.
- [ ] AC-20 `docs/SEMANTIC_LAYER.md` documents the resource YAML, request contract, governance layers, dialect table, settings and error codes; no existing public API changes.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Verified against commit `6e6bd94` (dev, 2026-10-14) after rebasing the brainstorm onto `origin/dev`.

### Verified Imports
```python
from querysource.providers import BaseProvider                      # verified: querysource/providers/__init__.py:4
from querysource.providers.abstract import BaseProvider             # verified: querysource/providers/abstract.py:35
from querysource.providers.default import defaultProvider           # verified: querysource/providers/default.py:24
from querysource.queries.qs import QS                               # verified: querysource/queries/qs.py:50
from querysource.queries.base import BaseQuery                      # verified: querysource/queries/base.py:24
from querysource.connections import QueryConnection                 # verified: tests/e2e/conftest.py:21 (import in use)
from querysource.models import QueryModel                           # verified: querysource/models.py:49
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore  # verified: querysource/tenants.py:46,54 (+ QueryStore :35)
from querysource.repositories import DefinitionRepository           # verified: tests/e2e/conftest.py docstring + handlers/tenant.py (TASK-762)
from querysource.auth import ResourceType, QSPrincipal, get_pbac_runtime, setup_pbac  # verified: querysource/auth/__init__.py:17-26
from querysource.auth._resource_types import ResourceType           # verified: querysource/auth/_resource_types.py:17-47
from querysource.auth.enforcement import (AccessDecision, resolve_evaluator, build_eval_context, evaluate, enforce_principal)  # verified: enforcement.py:39,70,103,122,151
from querysource.auth.principal import QSPrincipal                  # verified: querysource/auth/principal.py:21
from querysource.auth.request_gate import enforce_request_pbac, get_request_session  # verified: querysource/auth/request_gate.py:27,65
from querysource.auth.slug_visibility import resolve_principal, PrincipalKind, legacy_store, tenant_store, can_access  # verified: slug_visibility.py:26,100,176,385,329
from querysource.exceptions import QueryException, QueryError, QueryAccessDenied, ParserError  # verified: querysource/exceptions.py:6,49,63,86
from querysource.interfaces.connections import DATASOURCES, PROVIDERS, EXTERNAL_PROVIDERS  # verified: connections.py:40,41,44
from querysource.datasources.drivers import SUPPORTED, BaseDriver   # verified: querysource/datasources/drivers/__init__.py:30 (+ :5)
from querysource.handlers import QueryDescribe, QueryService, TenantQueryHandler  # verified: querysource/handlers/__init__.py:6-15
from querysource.handlers.abstract import AbstractHandler           # verified: querysource/handlers/abstract.py:33
from querysource.handlers.describe import QueryDescribe             # verified: querysource/handlers/describe.py:70
from querysource.queries.describe import describe_slug              # verified: querysource/queries/describe.py:204
from querysource.conf import QS_PBAC_ENABLED, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ  # verified: querysource/conf.py:455,475
```
```python
# External — sidemantic 0.12.0 (verified from upstream source on 2026-10-14; re-check against the installed wheel)
from sidemantic import SemanticLayer                                 # sidemantic/core/semantic_layer.py:70
from sidemantic.loaders import load_from_file, load_from_directory   # sidemantic/loaders.py:693,170
from sidemantic.db.base import BaseDatabaseAdapter                   # sidemantic/db/base.py:176
from sidemantic.adapters.ossie import OssieAdapter, OssieImportError # sidemantic/adapters/ossie.py:64,40
# SemanticLayer.__init__(connection: str | BaseDatabaseAdapter = "duckdb:///:memory:", dialect=None, auto_register=True, ...,
#                        default_limit=None, max_limit=None, enforce_visibility=False, ...)      # semantic_layer.py:76-93
#   an adapter instance is used as-is: self.adapter = connection; self.dialect = dialect or connection.dialect   # :230-233
# SemanticLayer.compile(metrics=None, dimensions=None, filters=None, segments=None, order_by=None, limit=None, offset=None,
#                       dialect=None, ungrouped=False, parameters=None, ..., user_attributes=None, ...) -> str  # :1143-1163
# SemanticLayer.describe_models(model_names=None) -> dict ; get_model(name) -> Model ; list_models() -> list[str]  # :925,:1912,:1934
# SemanticLayer.from_catalog(catalog, *, scope_id=None, connection=..., dialect=None, allow_invalid=False)   # :1982
# load_from_file(layer, file, *, strict=True, ossie_scope_id=None, ossie_adapter_options=None) -> None  # loaders.py:693
#   ossie_adapter_options keys: scope_id, target_dialect, source_dialect, consumer_profile, import_policy, preserve_source, schema_revision  # loaders.py:216-224
#   target_dialect defaults to layer.dialect                                                              # loaders.py:231
#   *.ossie.yaml|.ossie.yml|.ossie.json are detected by name; other YAML by shape (version/name/datasets)  # loaders.py:439,478
# BaseDatabaseAdapter abstract: execute, executemany, fetchone, fetch_record_batch, get_tables, get_columns, close, dialect, raw_connection  # db/base.py:208-296
# Security: compile(user_attributes=None) on a model with `security:` raises SecurityError (deny-by-default)  # docs/security.md
```

### Existing Class Signatures
```python
# querysource/providers/abstract.py
class BaseProvider(SourceHooksMixin, ABC):                                    # line 35
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                 definition: Union[QueryModel, dict] = None, conditions: dict = None,
                 request: web.Request = None, **kwargs):                       # line 55
        # self._query = definition.query_raw when slug is set (:76-80); self._conditions deep-copied, 'refresh' popped (:94-113)
        # self._loop from kwargs['loop'] (:114-123); parser built only `if self.__parser__:` (:125-137); self._driver (:140-146)
    async def prepare_connection(self): ...                                   # line 259 — awaits parser.set_options() when parser set
    async def columns(self): ...                                              # line 266
    async def describe_columns(self) -> list[dict]: ...                       # line 273
    async def dry_run(self): return (self._query, None)                       # line 298
    @abstractmethod
    async def query(self): ...                                                # line 304
    async def close(self): ...                                                # line 308

# querysource/providers/default.py
class defaultProvider(BaseProvider):                                          # line 24
    async def query(self):                                                    # line 117
        async with await self._connection.connection() as conn:              # line 122
            result, error = await conn.query(self._query)                     # returns [result, error]; NotFound on empty

# querysource/queries/qs.py
class QS(BaseQuery):                                                          # line 50
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop=None, *, tenant: str | None = None, definition: 'LoadedDefinition | None' = None,
                 principal: 'QSPrincipal | None' = None, ...)                 # line 56
    async def build_provider(self): ...                                       # line 186; slug branch :223-325
    #   loaded_def = await repo.get(QueryIdentity(store, slug)); objquery = loaded_def.runtime     (:260)
    #   self._conn, self._provider = await self.connection.get_provider(objquery, session=..., app=...)  (:263)
    #   args = {slug, query, qstype, connection, definition=objquery, conditions, request, "loop": self._loop}  (:303-311)
    #   self._qs = self._provider(**args); await self._qs.prepare_connection()                         (:312-313)
    #   except RawQueryPlaceholderError: raise                                                        (:315-317)
    #   except Exception as err: raise QueryError(..., code=400 if isinstance(err, ParserError) else 500)  (:318-325)
    async def query(self, output_format: str | None = None): ...              # line 457
    #   if self.is_cached: checksum = self._qs.checksum(); cache_key = self.result_cache_key(checksum)   (:476-483)
    async def dry_run(self): ...                                              # line 638

# querysource/providers/abstract.py
    def checksum(self): return get_hash(self._query)                          # line 330

# querysource/cache_identity.py
def result_cache_key(identity: QueryIdentity, revision: str, provider_checksum: str) -> str   # line 65 — qs:r2:<sha256(store, slug, revision, checksum)>
# querysource/interfaces/queries.py
    def result_cache_key(self, provider_checksum: str) -> str                 # line 244

# querysource/handlers/service.py — datasource/driver PBAC after provider resolution
    _ds_name = getattr(getattr(getattr(query, '_qs', None), '_definition', None), 'provider', 'db') or 'db'   # line 347-351
    await self._enforce_pbac(request, resource_type=ResourceType.DATASOURCE, resource_name=_ds_name, action="datasource:use")  # :358-363
    await self._enforce_pbac(request, resource_type=ResourceType.DRIVER, resource_name=_drv_name, action="driver:use")        # :364-369

# querysource/interfaces/connections.py
class Connection:                                                             # line 46
    async def get_provider(self, entry: dict, session=None, app=None): ...   # line 264
    #   'db' branch; `elif provider in EXTERNAL_PROVIDERS:` (:285) → [None, provider]; datasource branch; generic fallback
    def load_provider(self, provider: str) -> BaseProvider: ...               # line 376 — querysource.providers.<p>.<p>Provider
    async def datasource(self, name: str = 'default', session=None, app=None): ...  # line 396 — (source, AsyncDB) ; QueryError 404

# querysource/models.py
class QueryModel(Model):                                                      # line 49
    query_raw: str = Field(required=False)                                    # line 78
    is_raw: bool = Field(required=False, default=False)                       # line 79
    provider: str = Field(required=False, default='db')                       # line 81

# querysource/tenants.py
class QueryIdentity: store: QueryStore; slug: str                             # line 46
class LoadedDefinition: identity: QueryIdentity; runtime: QueryModel; revision: str   # line 54-59

# querysource/repositories/definitions.py
async def get(self, identity: QueryIdentity) -> LoadedDefinition             # line 201

# querysource/auth/enforcement.py
class AccessDecision: allowed: bool; pbac_enabled: bool; ...                  # line 39
def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]   # line 70
def build_eval_context(*, userinfo: dict, user: Any, session: Any, request: web.Request | None = None) -> Any  # line 103
async def evaluate(evaluator, ctx, resource_type, resource_name: str, action: str) -> AccessDecision  # line 122
async def enforce_principal(principal: QSPrincipal, resource_type, resource_name: str, action: str, *,
                            tenant: str | None = None, logger=None) -> AccessDecision   # line 151 — deny → QueryAccessDenied

# querysource/auth/principal.py
class QSPrincipal:                                                            # line 21
    user_id: str; username: str | None; groups: tuple[str, ...]; roles: tuple[str, ...]; programs: tuple[str, ...]
    superuser: bool; tenant_id: str | None; channel: str = "library"; authz_backend: str | None
    def to_userinfo(self) -> dict[str, Any]: ...                              # line 97 — username, user_id, groups, roles, programs, superuser

# querysource/auth/slug_visibility.py
async def resolve_principal(request, session) -> Principal                   # line 100 — userinfo = session.get(AUTH_SESSION_OBJECT='user', {}) (:132-138)

# querysource/auth/request_gate.py
async def enforce_request_pbac(request, resource_type, resource_name: str, action: str, *,
                               logger, session_resolver=None, allow_sessionless=None) -> None   # line 65 — deny → web.HTTPNotFound

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):                                           # line 33
    async def _get_user_session(self, request) -> Optional[SessionData]: ... # line 313
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None  # line 335

# querysource/handlers/describe.py
class QueryDescribe(AbstractHandler):                                         # line 70
    async def _principal(self, request) -> Principal: ...                    # line 75 — HTTPUnauthorized when none
    async def _store(self, request) -> DescribeStore: ...                    # line 83
    async def _load_visible(self, request, principal, store, slug): ...      # line 93 — 404 when hidden/missing; returns the model

# querysource/queries/describe.py
def describe_slug(definition: QueryModel | dict, grants: DescribeGrants, *, columns_link: str, vocabulary_link: str) -> dict  # line 204; returns {**payload, "derived": derived, "redacted": redacted} (:260)

# querysource/exceptions.py
class QueryException(Exception): __init__(self, message, code=..., ...)      # line 6
class QueryError(QueryException): "500 unless a 4xx/502 is passed"            # line 49
class QueryAccessDenied(QueryException): message "Query not available.", code=404   # line 63
class ParserError(QueryException)                                             # line 86

# tests/e2e/conftest.py
class FakeDefinitionRepository: add(**fields) -> QueryModel; async get(identity) -> LoadedDefinition   # line 65
@pytest.fixture definitions(monkeypatch) -> FakeDefinitionRepository          # line 125 (patches BaseQuery.get_definition_repository)
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `semanticProvider` | `Connection.load_provider('semantic')` | module/class naming convention `querysource.providers.semantic.semanticProvider` | `connections.py:376-392` |
| `Connection.get_provider` branch | `EXTERNAL_PROVIDERS` branch | new `elif provider == 'semantic'` placed before it | `connections.py:285` |
| `semanticProvider.prepare_connection` | `QueryConnection().datasource(name)` | datasource lookup; `source.driver` → dialect | `connections.py:396-437` |
| `semanticProvider.__init__` | `BaseProvider.__init__` | `super().__init__(...)`; `definition.query_raw` → resource YAML | `abstract.py:55-80` |
| `semanticProvider.query` | asyncdb connection | `async with await self._connection.connection() as conn: await conn.query(sql)` | `default.py:122-123` |
| `governance.check` | `resolve_evaluator` / `build_eval_context` / `evaluate` | one evaluation per (resource, action) | `enforcement.py:70,103,122` |
| `governance.principal_from_session` | session `['user']` dict | same key `resolve_principal` reads | `slug_visibility.py:132-138` |
| `SemanticCatalog.catalog` | `QueryDescribe._principal/_store/_load_visible`, `AbstractHandler._enforce_pbac` | inheritance / method calls | `describe.py:75-143`, `abstract.py:335` |
| catalog routes | `QuerySource.setup()` | `self.app.router.add_get(...)` after tenant describe routes | `services.py:207-226` |
| `describe_slug` extension | `derived` dict | new `derived['semantic']` key before the return | `describe.py:260` |
| `SidemanticCompiler._layer` | `SemanticLayer(connection=CompileOnlyAdapter)` + `load_from_file` | upstream public API | `semantic_layer.py:76,230`; `loaders.py:693` |
| `api.compile` | `QS.build_provider()` then `qs._qs.compiled` | provider attribute | `qs.py:186,312` |
| `QS.build_provider` slug branch | `semanticProvider(**args)` | `args["principal"] = self._principal` (new key) | `qs.py:303-311` |
| `QS.build_provider` slug branch | `SemanticError` | `except (RawQueryPlaceholderError, SemanticError): raise` | `qs.py:315` |
| `governance.enforce_datasource` | `ResourceType.DATASOURCE` / `ResourceType.DRIVER` | same resource/action strings as `QueryService` | `service.py:358-369` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.compile()` / `querysource.execute()`~~ — top-level functions do not exist; the API lives in `querysource/semantic/api.py`.
- ~~`querysource/semantic/`~~, ~~`querysource/providers/semantic.py`~~, ~~`querysource/handlers/semantic.py`~~ — created by this feature.
- ~~`ResourceType.SEMANTIC`~~ — not in the shim (`SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY` only) nor upstream; added to the shim by M7.
- ~~`QueryModel.kind`~~ — no such column; `kind: semantic` lives inside the YAML in `query_raw`; the provider is selected by `QueryModel.provider`.
- ~~`policy.allowed_metrics` / `policy.row_filters`~~ — navigator-auth evaluates one (resource_type, resource_name, action) at a time; there is no policy object with field lists.
- ~~`ToolPermissionError`~~ — not a QuerySource exception; use `QueryAccessDenied` / `SemanticFieldDenied`.
- ~~`Connection.get_provider` special-casing `'multi'`~~ — only `'db'`, `EXTERNAL_PROVIDERS`, datasource name and the generic fallback exist.
- ~~`QS_SEMANTIC_*` settings~~ — none exist in `conf.py`; added by M7.
- ~~`sqlglot` / `pydantic` in `pyproject.toml`~~ — installed transitively only (sqlglot 30.20.0, pydantic 2.12.5 in the venv); declared by M7.
- ~~`sidemantic-rs` as a dependency of sidemantic 0.12.0~~ — not in `requires_dist`; `duckdb>=1.0.0` **is** a hard dependency (never used by this feature).
- ~~`SemanticLayer.compile()` returning an object~~ — it returns `str`; `CompiledQuery` is built by QuerySource.
- ~~`OssieAdapter.parse(text)`~~ — path-based only (`parse_document(path)`, `parse_catalog(path_or_dir)`); inline documents go through a temp file + `load_from_file`.
- ~~`sidemantic.db.unavailable.UnavailableDatabaseAdapter` as our adapter~~ — exists upstream but raises `ModuleNotFoundError` on execute; this feature ships its own `CompileOnlyAdapter` instead of depending on that internal class.
- ~~A QuerySource MCP server~~ — MCP tooling lives in ai-parrot.
- ~~`tests/semantic/`~~, ~~`tests/providers/test_semantic_provider.py`~~, ~~`tests/handlers/test_semantic_catalog.py`~~ — created by this feature.

### Edit Sites (Blueprint Anchors)

Verified against: `6e6bd94`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/semantic/__init__.py` | CREATE | — | — | — |
| `querysource/semantic/models.py` | CREATE | — | — | — |
| `querysource/semantic/protocol.py` | CREATE | — | — | — |
| `querysource/semantic/exceptions.py` | CREATE | — | — | — |
| `querysource/semantic/dialects.py` | CREATE | — | — | — |
| `querysource/semantic/filters.py` | CREATE | — | — | — |
| `querysource/semantic/loader.py` | CREATE | — | — | — |
| `querysource/semantic/governance.py` | CREATE | — | — | — |
| `querysource/semantic/api.py` | CREATE | — | — | — |
| `querysource/semantic/compilers/__init__.py` | CREATE | — | — | — |
| `querysource/semantic/compilers/sidemantic.py` | CREATE | — | — | — |
| `querysource/providers/semantic.py` | CREATE | — | — | — |
| `querysource/handlers/semantic.py` | CREATE | — | — | — |
| `querysource/interfaces/connections.py` | MODIFY | `        elif provider in EXTERNAL_PROVIDERS:` | `connections.py:285` | 1 |
| `querysource/queries/qs.py` | MODIFY | `                    "loop": self._loop` **without** trailing comma, followed by `                }` and `                self._qs = self._provider(**args)` (slug branch; the `query`/`raw` branch at :358 has `"loop": self._loop,` with a comma and `**self.kwargs`) | `qs.py:310-312` | 2 (substring) / 1 (exact line) |
| `querysource/queries/qs.py` | MODIFY | `            except RawQueryPlaceholderError:` preceded by `                await self._qs.prepare_connection()` / `                return self` inside `if self._type == 'slug':` (first occurrence; the second at :364 is the `query`/`raw` branch) | `qs.py:315` | 2 |
| `querysource/queries/qs.py` | MODIFY | `    RawQueryPlaceholderError,` followed by `)` (end of the `from ..exceptions import (` block that starts at :24; add `from ..semantic.exceptions import SemanticError` after the closing `)`) | `qs.py:31-32` | 1 |
| `querysource/auth/_resource_types.py` | MODIFY | `        RAW_QUERY = _StringResourceType("raw_query")` | `_resource_types.py:45` | 1 |
| `querysource/conf.py` | MODIFY | `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ = config.getboolean(` followed by `    'QS_PBAC_ALLOW_SESSIONLESS_AUTHZ', fallback=False` and `)` | `conf.py:475-477` | 1 |
| `pyproject.toml` | MODIFY | `    "lark>=1.3.1",` (last core dependency) | `pyproject.toml:117` | 1 |
| `pyproject.toml` | MODIFY | `parquet-all = [` (insert the `semantic` extra before it) | `pyproject.toml:179` | 1 |
| `querysource/handlers/__init__.py` | MODIFY | `from .describe import QueryDescribe` | `handlers/__init__.py:6` | 1 |
| `querysource/handlers/__init__.py` | MODIFY | `    'QueryDescribe',` | `handlers/__init__.py:20` | 1 |
| `querysource/services.py` | MODIFY | `        r = self.app.router.add_get('/api/v1/{tenant}/queries/{slug}/columns', dh.columns)` followed by `        routes.append(r)` (the tenant one, not the legacy `'/api/v1/queries/{slug}/columns'` at :216) | `services.py:225-226` | 1 (full line; `dh.columns)` appears 2×) |
| `querysource/services.py` | MODIFY | `from .handlers import (` | `services.py:22` | 1 |
| `querysource/queries/describe.py` | MODIFY | `    return {**payload, "derived": derived, "redacted": redacted}` | `describe.py:260` | 1 |
| `docs/SEMANTIC_LAYER.md` | CREATE | — | — | — |
| `tests/semantic/__init__.py`, `tests/semantic/conftest.py`, `tests/semantic/test_models.py`, `tests/semantic/test_filters.py`, `tests/semantic/test_dialects.py`, `tests/semantic/test_loader.py`, `tests/semantic/test_governance.py`, `tests/semantic/test_sidemantic_compiler.py`, `tests/semantic/test_api.py` | CREATE | — | — | — |
| `tests/providers/test_semantic_provider.py` | CREATE | — | — | — |
| `tests/handlers/test_semantic_catalog.py` | CREATE | — | — | — |
| `tests/auth/test_resource_types_semantic.py` | CREATE | — | — | — |
| `tests/integration/test_semantic_postgres.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Provider shape from `defaultProvider` (`querysource/providers/default.py`): `super().__init__(...)`, work in `prepare_connection()`, `[result, error]` from `query()`, `self.NotFound(...)` on empty results, `self._logger` for logging.
- Lazy imports for optional dependencies, exactly like `querysource/auth/_resource_types.py` and the `[geo]`/`[parquet]` extras: `sidemantic` and `sqlglot` are imported inside functions of `compilers/sidemantic.py` and `filters.py`; an `ImportError` becomes `SemanticCompilerUnavailable` (501) with the message `install querysource[semantic]`.
- PBAC evaluation through `querysource.auth.enforcement` primitives (never `navigator_auth` directly); deny on the slug is `QueryAccessDenied` (404-shaped, generic message); deny on a field is 403 naming the field.
- Settings through `navconfig` `config.get/getint` in `querysource/conf.py`, next to the PBAC block.
- Pydantic v2 models (`model_config = ConfigDict(extra="forbid")` on the request model); `model_dump()` when handing data to `QS`.
- Tests: Sidemantic-dependent tests use `pytest.importorskip("sidemantic")`; provider tests reuse `tests/e2e/conftest.py::definitions`; route tests follow `tests/test_route_registration.py`.
- Name qualification: a request name without a dot is resolved against all models in the layer; exactly one match → qualified; zero → 400 with available names; many → 400 with candidates.

**Driver → dialect table (M2, `DRIVER_DIALECTS`)**

| querysource driver (`SUPPORTED` key) | dialect | note |
|---|---|---|
| `pg`, `pg_admin` | `postgres` | default when `connection:` is omitted |
| `bigquery` | `bigquery` | |
| `mysql`, `mariadb` | `mysql` | |
| `sqlserver` | `tsql` | |
| `oracle` | `oracle` | |
| `sqlite` | `sqlite` | |
| `jdbc` | `ansi` + warning | JDBC does not reveal the backend |
| `arangodb`, `cassandra`, `delta`, `documentdb`, `elastic`, `hazel`, `iceberg`, `influx`, `mongo`, `redis`, `rethink`, `salesforce`, `scylladb` | **rejected** (400 `SemanticResourceError`: non-SQL datasource) | compile output could never run there |

### Known Risks / Gotchas
- **DuckDB import**: `import sidemantic` pulls `duckdb` at import time (hard dependency). Only `compilers/sidemantic.py` imports it, lazily; the `CompileOnlyAdapter` guarantees no connection is opened (AC-4).
- **Sidemantic is young** (its README says so) and pinned to `0.12.x`; `describe_models()` output shape and the `SecurityError` import path must be confirmed against the installed wheel during M4 (hence M4 is not delegation-eligible).
- **Inline documents and temp files**: temp files live in a private per-process directory (`tempfile.mkdtemp(prefix="qs-semantic-")`), are reused through the loader cache and removed on `close()`/process exit; never under `QS_SEMANTIC_MODELS_DIR`.
- **Path traversal**: `(models_dir / definition).resolve()` must start with `models_dir.resolve()`; symlinks escaping the directory are rejected and logged with the slug.
- **Governance filter failures are configuration errors**: an invalid filter in `governance.row_filters` must surface as 500 (logged with slug and role), never silently dropped (fail-closed).
- **No identity on a secured model**: Sidemantic denies by default; QuerySource maps `SecurityError` to 403 before executing anything. The request path must therefore always try to build a principal from the session even when PBAC is off.
- **Cache key**: QS caches by slug + conditions; the provider must fold `model_version` into the checksum so a definition change never serves stale rows (AC-17).
- **`get_provider` ordering**: the new branch must sit before `elif provider in EXTERNAL_PROVIDERS:` and after the `'db'` branch; the generic fallback would otherwise call `default_driver('semantic')` and log an exception.
- **ANSI fallback** SQL may fail at execution on exotic dialects; the warning is attached to `CompiledQuery` and the execution error surfaces as the normal provider error.
- **Large catalogs**: the catalog endpoint lists every field; keep it O(fields) with one PBAC evaluation per field and rely on navigator-auth's decision cache.
- **AGPL-3.0**: Sidemantic is consumed unmodified as an optional extra; no source is copied into the repository (brainstorm Q3).
- **MultiQS children (S2, escalated)**: MultiQS builds child `QueryObject`s without a principal; a semantic child whose model has a `security:` block is therefore denied (fail-closed) until principal propagation is designed (§8 Q13). Unsecured semantic children compile normally.
- **Model file size**: path-backed documents are capped at `QS_SEMANTIC_MAX_MODEL_BYTES` (default 2 MiB) and parsed with `yaml.safe_load` only (S9).
- **Query-parameter precedence**: a `metrics` query parameter overrides a `metrics` body key because `QueryService` merges `{**body, **params}`; documented in `docs/SEMANTIC_LAYER.md` (S5).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pydantic` | `>=2,<3` (core) | request/resource/compiled models (brainstorm Q2) |
| `sidemantic[ossie]` | `>=0.12.0,<0.13` (extra `semantic`) | semantic compiler, Ossie importer, offline schema validation (`jsonschema`); AGPL-3.0, Python ≥3.11 |
| `sqlglot` | `>=30.1` (extra `semantic`) | filter string parsing/validation, literal rendering (already a sidemantic dependency) |
| `pyyaml` | `>=6` (already transitive) | resource YAML parsing (`yaml.safe_load`) |

---

## Worktree Strategy

- **Isolation**: one feature worktree per spec
  (`.claude/worktrees/feat-FEAT-175-semantic-layer`); the `sdd-coder` engine
  gives each task its own sub-worktree inside it.
- **Module dependency graph** (evidence in parentheses; modules without an
  edge are expected to run concurrently):
  - M2 → M1 (`filters.py` imports `SemanticFilter`, `CatalogField`, `SemanticRequestError`)
  - M3 → M1, M7 (`loader.py` imports `SemanticResource`, `SemanticModelRef`; reads `QS_SEMANTIC_MODELS_DIR`, `QS_SEMANTIC_CACHE_SIZE`, `QS_SEMANTIC_MAX_MODEL_BYTES`)
  - M4 → M1, M3 (`compilers/sidemantic.py` imports `ModelLoader`, `CompiledQuery`, `CatalogField`)
  - M5 → M1, M7 (`governance.py` imports `ResourceType.SEMANTIC` added by M7 and the semantic exceptions)
  - M6 → M1, M2, M3, M4, M5, M7 (`providers/semantic.py` composes all of them; reachable only through the M7 `get_provider` branch)
  - M7 → M1 (`qs.py` imports `SemanticError`); otherwise independent
  - M8 → M6 (`api.py` reads `semanticProvider.compiled`)
  - M9 → M1, M3, M4, M5, M7 (`handlers/semantic.py` uses the loader, the compiler's `catalog()`, governance checks and the shim)
  - M10 → M1–M9 (documents and exercises the whole feature)
- **Shared files** (their tasks get serialized): `querysource/semantic/__init__.py`
  (M1 creates it, M8 adds the `compile`/`execute` re-exports);
  `querysource/conf.py` and `pyproject.toml` (M7 only, one task);
  `querysource/queries/qs.py` (M7 only).
- **Exclusive resources**: `pyproject.toml` + `uv.lock` (adding `pydantic` to
  core and the `semantic` extra) — that task is `parallel: false`. No Cython or
  Rust rebuild is needed; no migration.
- **Cross-feature dependencies**: none in flight on the touched files at spec
  time (`FEAT-174 dt-timezone-transforms` merged into `dev` on 2026-10-14 and
  does not touch them). The navigator-auth upstream enum (AC-18) is a
  follow-up, not a prerequisite.

---

## 8. Open Questions

> All brainstorm questions were resolved on 2026-10-14; they are carried here for the audit trail.

- [x] Connection resolution for the semantic provider — *Resolved in brainstorm*: Explicit branch in `get_provider()` returning `[None, semanticProvider]`, mirroring `EXTERNAL_PROVIDERS`; the provider resolves `connection:` from the resource YAML through `Connection.datasource(name)`, or the default PostgreSQL connection when omitted.
- [x] Request model base class — *Resolved in brainstorm*: `pydantic.BaseModel` (v2) for every model in `querysource/semantic/`; `pydantic>=2` is declared as a core dependency in `pyproject.toml`.
- [x] AGPL-3.0 optional extra and DuckDB on workers — *Resolved in brainstorm*: Approved as an unmodified optional dependency (`querysource[semantic]`); the core package never imports it, no sidemantic source is copied into the repo, DuckDB installed only on workers that serve semantic slugs.
- [x] `user_attributes` mapping — *Resolved in brainstorm*: One shape for both paths, the `QSPrincipal.to_userinfo()` dict; the request path builds a `QSPrincipal` from the session before compiling.
- [x] navigator-auth upstream enum — *Resolved in brainstorm*: Shim now (`SEMANTIC = "semantic"`), TASK-640-style upstream follow-up adds `SEMANTIC`, `SEMANTIC_EXECUTE`, `SEMANTIC_USE`.
- [x] Per-field PBAC naming — *Resolved in brainstorm*: Resource type `SEMANTIC`; `semantic:execute` on `<slug>`; field checks on `<slug>:metric:<name>` / `<slug>:dimension:<name>` with action `semantic:use`.
- [x] Inline Ossie documents — *Resolved in brainstorm*: Temp file + the public adapter API (`load_from_file`), once per definition revision via the loader cache; no internal Sidemantic APIs.
- [x] `time_grain` semantics — *Resolved in brainstorm*: request-level grain rewrites unsuffixed time dimensions to `<dim>__<grain>`; explicit suffix wins; grain without a time dimension is a 400.
- [x] HTTP surface — *Resolved in brainstorm*: execution on the existing query routes plus `GET /api/v1/semantic/{slug}/catalog` (and tenant-prefixed) with per-field grants; describe/columns updated.
- [x] PBAC-provided row filters — *Resolved in brainstorm*: Yes, declared in the resource YAML as role-keyed `governance.row_filters` (+ `default`), validated like caller filters, AND-ed across matching roles, on top of Sidemantic `security.row_filters`.
- [x] Target version — *Resolved at spec time*: 5.3.0.
- [ ] Q12 Should `execute(compiled)` in the Python API accept a `CompiledQuery` produced by another process (serialized), or only one produced in-process? v1 embeds the original request in `CompiledQuery.parameters['request']` and re-runs through `QS`, so both work; confirm this is acceptable for ai-parrot's cache. — *Owner: Jesus Lara* (decidable during implementation; does not block tasks)
- [ ] Q13 (escalated from §9 S2) MultiQS child semantic slugs: `MultiQS` builds child `QueryObject`s without a principal (`querysource/queries/obj.py`, `querysource/queries/multi/sources/executors.py`), so secured semantic children are denied fail-closed in v1. Should FEAT-175 propagate the parent principal to children (touching MultiQS), or is that a follow-up feature? — *Owner: Jesus Lara* (v1 ships fail-closed; does not block tasks), yes, propagate principal to children

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: `gpt-5.6-luna` (codex-cli 0.160.0, reasoning high, 98 s) · Status: completed
> · Transcript: `sdd/state/FEAT-175/design_research/`

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Resolve semantic type and datasource before provider construction (architecture) | CONFIRM (partially) | Dispatch already keys on the `QueryModel.provider` column, not on `kind` in `query_raw`, so a semantic document cannot reach the DB provider; but the reviewer is right that `QueryService` enforces `datasource:use` on the literal `'semantic'` (`service.py:347-363`). The provider now enforces `datasource:use`/`driver:use` on the resolved datasource itself. | §2 Overview, M5 `enforce_datasource`, AC-21 |
| S2 | Propagate principal identity into semantic compilation and MultiQS children (risk) | CONFIRM + ESCALATE | Verified: `QS.build_provider()` never passes `self._principal` to providers (`qs.py:303-311`). The slug-branch args gain `principal`. MultiQS child propagation touches `obj.py`/`executors.py` and is escalated; v1 is fail-closed for secured children. | M7, AC-25, §8 Q13 |
| S3 | Key caches by compiled parameters, identity and external model content (risk) | CONFIRM | Verified: `BaseProvider.checksum()` hashes only `_query` (`abstract.py:330`) and `result_cache_key` adds only store/slug/revision (`cache_identity.py:65`). `semanticProvider.checksum()` folds `model_version` (digest of resolved bytes), dialect and connection. User identity is already in the SQL (RLS literals). | M6 `checksum`, AC-23 |
| S4 | Define how CompiledQuery parameters reach asyncdb (api) | CONFIRM | Verified: execution is `conn.query(self._query)` with no binding (`default.py:123`). `sql` is complete literal SQL; `parameters` carries the canonical request only. | §2 Overview, M6 `query`, AC-24 |
| S5 | Normalize and reject transport conditions before semantic validation (api) | CONFIRM | Already in M1 `from_conditions`; added the body-vs-query precedence (`{**options, **params}`, `service.py:295`) and documented it. | M1, §7 Known Risks |
| S6 | Make the catalog endpoint tenant-aware and field-redacting (risk) | CONFIRM | Tenant awareness comes from `QueryDescribe._store/_load_visible`; added explicit redaction of denied fields to `name/kind/granted=false`. | M9, AC-26 |
| S7 | Preserve the legacy dry_run tuple while exposing the object result separately (api) | CONFIRM | Verified `QS.dry_run()`/`QueryService.test_slug()` unpack `(result, error)`; the spec keeps the tuple and exposes `semanticProvider.compiled` + `api.compile()`. | M6 `dry_run`, AC-6 |
| S8 | Make policy layers conjunctive and preserve fail-closed error semantics (risk) | CONFIRM | Verified `QS.build_provider()` wraps provider errors into `QueryError(500)` unless `ParserError` (`qs.py:318-325`). Added `SemanticError` base and the re-raise in the slug branch; stated that the four governance layers are conjunctive. | M1 exceptions, M7, AC-22 |
| S9 | Constrain directory-backed model paths and hash resolved bytes (risk) | CONFIRM | Already in M3 (realpath containment, digest); added a size cap `QS_SEMANTIC_MAX_MODEL_BYTES` and `yaml.safe_load` only. | M3, M7 settings, §7 |
| S10 | Keep the optional compiler dependency behind the lazy provider boundary (architecture) | CONFIRM | Already the design (`load_provider` convention, lazy import in `compilers/sidemantic.py`); tests with the fake repository added (`test_compiler_unavailable_501`, `test_compile_only_adapter_never_executes`). | M4, M6, AC-3/AC-4 |
| S11 | Use an explicit dialect registry rather than inferring from class names (architecture) | CONFIRM | Already M2 `DRIVER_DIALECTS` with explicit non-SQL rejection and ANSI warning; `test_dialect_for_driver_table` covers every `SUPPORTED` key. | M2, §7 table, AC-5 |
| S12 | Test semantic behavior through every existing execution surface (testing) | CONFIRM | Added tests for the tenant catalog route, legacy dry-run tuple, principal pass-through, checksum, datasource PBAC and the MultiQS child fail-closed case. | §4 Unit/Integration tests |

Summary: **12** confirmed (S1 partially, S2 also escalated) · **0** rejected · **1** escalated (S2 → §8 Q13).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-14 | Jesus Lara / Claude | Initial draft from the accepted brainstorm (FEAT-175) |
