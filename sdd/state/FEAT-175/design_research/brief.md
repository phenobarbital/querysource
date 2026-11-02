<!--
  sdd/templates/design_research.prompt.md — neutral design-research brief (FEAT-545).
  Rendered by /sdd-spec section 3b and piped to `codex exec ... --output-schema
  design_research.schema.json`.
  FORBIDDEN INPUTS: never paste the spec draft, the spec author's reasoning, a preferred
  conclusion, or any text written by the model that will author the spec. The brief carries
  ONLY the accepted exploration document (brainstorm/proposal) and verified code anchors.
  Placeholders (double-curly-brace tokens, deliberately NOT written with literal braces in
  this comment — a renderer that does a naive whole-document string replace must not also
  rewrite this sentence): problem_statement, constraints_and_goals,
  recommended_option_or_scope, code_context_paths, open_questions, question.
-->
# Independent design review — read-only

You are an independent design reviewer for **QuerySource**, an async-first Python
library for querying heterogeneous data sources (aiohttp, asyncdb, Cython parsers, `querysource/` package).
You have read-only access to the repository in your working directory.

## Rules
1. Read the code you cite. Every `affected_paths` entry must be a repo-relative
   path you actually opened; suggestions with unverifiable paths are discarded.
2. Judge the design intent below against what exists in the repository: what is
   missing, what is risky, what would be simpler, what the codebase already
   provides that the intent re-invents.
3. Do not restate the intent, do not praise it, do not write code. Propose at
   most 12 concrete, falsifiable suggestions, each tagged with a kind
   (`architecture` | `api` | `testing` | `risk` | `alternative`), a risk level and
   your confidence.
4. Output exactly ONE JSON object conforming to the schema you were given — no
   markdown fences, no prose before or after.

## Accepted design intent (verbatim from the exploration document)

### Problem statement
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

### Constraints and goals
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

### Recommended option / probable scope
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

### Option A (recommended)
Sidemantic adapter behind a `SemanticCompiler` Protocol (compile-only)

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

### Verified code anchors (paths only — open them yourself)
pyproject.toml
querysource/auth/__init__.py
querysource/auth/_resource_types.py
querysource/auth/enforcement.py
querysource/auth/pbac.py
querysource/auth/principal.py
querysource/datasources/drivers/__init__.py
querysource/datasources/drivers/bigquery.py
querysource/datasources/drivers/pg.py
querysource/exceptions.py
querysource/handlers/service.py
querysource/interfaces/connections.py
querysource/models.py
querysource/providers/__init__.py
querysource/providers/abstract.py
querysource/providers/default.py
querysource/queries/describe.py
querysource/queries/qs.py
querysource/repositories/definitions.py
querysource/tenants.py
tests/e2e/conftest.py

### Questions still open in the exploration document
none

## Question
Given this accepted design intent and these verified code anchors, how would you build it? What is missing, risky, or better done another way?
