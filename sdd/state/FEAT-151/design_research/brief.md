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

### Constraints and goals
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
- **Multi columns**: add a `columns_definition` array column to the tenant `queries` table (and
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

### Recommended option / probable scope
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
- The `columns_definition` column is the price of giving `HEAD/PATCH` something meaningful for
  a pipeline without executing it. It is additive (`SELECT *` on a store without the
  column just yields the default), and the follow-up "describe the resulting frame"
  can populate it.

---

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

`columns()` uses the same peek: multi → declared `columns_definition` list or 204; single →
`QueryService.get_columns` / `columns`. `test_slug()` uses the same peek: multi → a new
validate-only dry-run on `QueryHandler`; single → `QueryService.test_slug`.

The `columns_definition` array is added to `TenantQueryDefinition` (`tenant_models.py:24`) **and**
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
- Introduces a schema addition (`columns_definition`) that must be rolled out to every tenant
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

### Verified code anchors (paths only — open them yourself)
docs/PER_TENANT_QUERIES.md
querysource/handlers/__init__.py
querysource/handlers/abstract.py
querysource/handlers/multi.py
querysource/handlers/service.py
querysource/handlers/tenant.py
querysource/interfaces/connections.py
querysource/interfaces/queries.py
querysource/models.py
querysource/queries/__init__.py
querysource/queries/base.py
querysource/queries/multi/__init__.py
querysource/queries/qs.py
querysource/repositories/definitions.py
querysource/scheduler/scheduler.py
querysource/tenant_models.py
querysource/tenants.py
tests/tenants/conftest.py
tests/tenants/test_tenant_http_routes.py

### Questions still open in the exploration document
- [ ] Should the legacy `public.queries` table also gain the `columns_definition` column, or stay read-only-compatible (reads default to `[]`, writes must not include it)? — *Owner: Jesus Lara*
- [ ] Dry-run report envelope for multi: reuse `QueryService.test_slug`'s keys (`works`, `generated_at`, ...) with an added `children` list, or a distinct multi-specific shape? — *Owner: Jesus Lara*
- [ ] `provider='multi'` with non-multi `query_raw`: keep the v3/scheduler fallback to single execution, or reject with 422 on the tenant route only? — *Owner: Jesus Lara*
- [ ] Should the stashed `LoadedDefinition` request key be shared with the FEAT-148 describe handler (`handlers/describe.py:302-339`) so it also stops re-loading the definition? — *Owner: Jesus Lara*
- [ ] Test strategy: unit tests with the existing MagicMock/monkeypatch pattern only, or also an opt-in integration test (`QS_TEST_POSTGRES_DSN`, `tests/tenants/test_integration.py`) with a real stored multi definition? — *Owner: Jesus Lara*

## Question
Given this accepted design intent and these verified code anchors, how would you build it? What is missing, risky, or better done another way?
