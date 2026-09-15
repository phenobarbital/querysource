---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
---

# Brainstorm: Describe Query-Slug REST Endpoints

**Date**: 2026-09-15
**Author**: Jesús Lara / Claude brainstorm
**Status**: exploration
**Recommended Option**: A

> Merged on 2026-09-15 with the parallel brainstorm `queryslug-describe`
> (same day, same author), which contributed the typed-columns sub-resource,
> the relative-date keyword vocabulary endpoint, the parameter typing
> rules, the downstream ai-parrot consumers and the UDF typed-resolution fix.
> That file was removed; this document is the single source. All open
> questions were resolved with the author the same day.

---

## Problem Statement

Today, a consumer can get information about query slugs in only two ways:

1. **Running SQL against the definitions table through the query executor.** For example:
   `SELECT query_slug, provider, description, program_slug, updated_at FROM public.queries ORDER BY updated_at DESC NULLS LAST`
   sent to `POST /api/v1/queries/run`. Anything richer needs another hand-written raw query: `cond_definition`, default `conditions`, `attributes`, `fields`/`filtering`/`ordering`/`grouping`, or `query_raw` itself.
   - This path requires `raw_query:execute` + `datasource:use` + `driver:use`. That is far more power than "tell me what this slug is".
   - Every client has to know the physical table.
   - Nothing filters the result by what the user may actually see.
2. **Calling `QueryManager` at `GET /api/v1/management/queries[/{slug}]`** (`querysource/handlers/manager.py`). It is an admin CRUD view that **enforces no PBAC at all**. It returns every column, including `query_raw`, `dwh_info` and `cache_options`, to anyone who reaches the route.

Neither is a safe, consumer-oriented discovery API. Two groups are affected:
- **Frontend and dashboard developers** building query pickers and parameter forms.
- **Integrators and agents** that need to know which variables and conditions a slug accepts before executing it.

Three concrete consumers are waiting on this contract (all in `ai-parrot`):
- **FEAT-567 `QuerySourceToolkit.qs_describe`** (replaces `QSourceTool`, which today can only *execute* `query_slug` + `conditions` and has no way to discover a slug's parameters or output columns).
- **A2UI "linked surfaces"**: an agent emits a widget/dashboard that carries a *data-source descriptor* (`slug` + `conditions` + declared parameters) instead of the data; the Navigator Svelte 5 renderer re-fetches through `POST /api/v2/services/queries/{slug}` with the viewer's JWT and renders a filter bar from the parameter contract.
- **`DatasetManager.QuerySlugSource.prefetch_schema()`**, which today executes the slug with `querylimit=1` only to learn column names and dtypes.

Two further gaps surfaced while researching them:
- **Column types are discarded.** `providers/pg.py:44-56` already prepares the statement and reads `stmt.get_attributes()`, but keeps only `a.name`; the `.type` is available for free.
- **The relative-date keyword vocabulary is not discoverable.** `TODAY`, `YESTERDAY`, `FDOM`, `LDOM`, `CURRENT_YEAR`, `CURRENT_MONTH`, `LAST_YEAR` are accepted as condition *values* (`rust/src/validators.rs:20-35`, env-overridable mirror in `querysource/types/validators.pyx:27-33`), and ~50 date helpers live in `querysource/utils/functions.pyx` without docstrings. An LLM or a filter bar has nothing to read the vocabulary from.

The platform already has PBAC (`slug:execute`, policies under `policies/`), and slugs are logically owned by programs (`program_slug`). A describe API should respect both:
- a **pre-filter** by the user's programs, pushed into SQL;
- an **ABAC post-filter**, evaluated per slug.

## Constraints & Requirements

- **Routes:** `GET /api/v1/queries/describe` returns the list. `GET /api/v1/queries/{slug}/describe` returns the detail.
  - The detail URL shape (`{slug}/describe`) was chosen by the user during brainstorming, replacing the originally proposed `/describe/{query_slug}`.
  - Both are read-only (GET, plus HEAD/OPTIONS as aiohttp/CORS require).
  - They do not collide with the existing POST-only `/api/v1/queries/{test,run,schema}`.
  - The detail route has one more path segment than those routes, so no match is ambiguous.
  - A slug literally named `describe` resolves to `/api/v1/queries/describe/describe`, which only the detail route matches.
  - The `{slug}/describe` shape leaves room for sibling sub-resources; this feature adds one: `GET /api/v1/queries/{slug}/columns`.
  - `GET /api/v1/queries/vocabulary` exposes the relative-date keyword vocabulary. It is one path segment shorter than the detail route, so a slug named `vocabulary` still resolves to `/api/v1/queries/vocabulary/describe`.
- **Typed columns live in the sibling sub-resource, not in describe (user decision, 2026-09-15).** `GET /api/v1/queries/{slug}/columns` is the **only** route in this feature that opens a connection to the slug's datasource: it prepares the statement (`conn.prepare`, no execution) where the provider supports it and falls back to the slug's declared `attributes.columns`. It never executes the query and never samples rows (`querylimit=1` sampling is out of scope).
- **Parameter typing in `derived.variables`:** each variable carries a canonical lowercase `type` from the set the validator understands (`literal, integer, float, numeric, decimal, epoch, boolean, string, field, date, datetime, timestamp, uuid, array, json, numrange, int4range, int8range`; the DB stores hints such as `"STRING"` and the Rust validator is case-insensitive), the verbatim `raw_type`, a `source` (`cond_definition` | `conditions` | `placeholder`) and `accepts_keywords` (true for `date`/`datetime`/`timestamp` and untyped variables).
- **Structural placeholders are never user variables.** The reserved set is the union of the parser tokens (`{schema}`, `{table}`, `{tablename}`, `{fields}`, `{filter}`, `{where_cond}`, `{and_cond}`, `{grouping}`, `{group_by}`, `{ordering}`, `{order_by}`, `{offset}`, `{_offset}`, `{limit}`, `{_limit}`, `{querylimit}`) and the provider `replacement` defaults (`providers/sql.py:37-45`: `firstdate`/`lastdate`/`filterdate` default to `current_date`, which makes them *defaulted* variables, not structural ones).
- **Vocabulary reflects runtime configuration.** The endpoint lists the process's effective `UDF_LIST`, `PG_CONSTANTS` and `PG_UDF` (environment overrides included), enriched from a curated registry; a keyword accepted by the validator but missing from the registry is still listed, with `description: null`.
- **UDF typed-resolution fix, first task of this feature (ships in `4.6.0`):** keyword resolution is inconsistent for typed conditions. In `rust/src/validators.rs:296-298` a `date`/`datetime`/`timestamp` hint quotes the value verbatim (`'FDOM'`) before the UDF check at `:320-328` is reached, and the Rust generic branch returns the quoted keyword "for the Python layer to resolve" while only the Cython path (`querysource/types/validators.pyx:552-553`) calls `to_udf`. Because describe will *advertise* `accepts_keywords`, the fix must land before the endpoints in the same branch: typed `date`/`datetime`/`timestamp` values that are a known UDF keyword resolve through `to_udf` on both the Rust and the Cython paths, with parity tests.
- **Per-tenant variant included (FEAT-176):** `GET /api/v1/{tenant}/queries/describe`, `GET /api/v1/{tenant}/queries/{slug}/describe` and `GET /api/v1/{tenant}/queries/{slug}/columns` are served by the same handler, resolving `(schema, table)` from the tenant through FEAT-176's data-access layer, with the program pre-filter deriving its context from the tenant instead of `program_slug`.
- **Admin visibility rule:** `userinfo.superuser` OR membership in one of `QS_DESCRIBE_ADMIN_GROUPS` (default `admin,superuser`). No new PBAC action for the admin-only fields.
- **No session, no sessionless authz:** `401`, fail-closed, on list, detail and columns.
- **Scan cap:** `QS_DESCRIBE_MAX_SCAN` (default `10000`); beyond it the first N rows in SQL order are processed and the response carries `X-Truncated: true` (never `400`).
- **Handler:** a new dedicated read-only handler. `QueryManager` is **not modified** and stays the admin CRUD tool.
- **Pre-filter (SQL, list and detail)** by the session's `userinfo["programs"]`:
  - `userinfo["superuser"] is True`: no program restriction.
  - Slugs with `program_slug = 'default'` are always included for users who have programs.
  - An authenticated user **without** `programs` (missing or empty) sees **nothing**: 204 on the list. Fail-closed; `default` is not granted either.
  - Sessionless authz requests (the `authorized` synthetic identity, via `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` + `request[AUTHZ_BACKEND_KEY]`) skip the pre-filter and rely only on ABAC.
- **ABAC post-filter:**
  - List: a slug is visible if `slug:list` **OR** `slug:execute` is allowed.
  - Detail: allowed if `slug:describe` **OR** `slug:execute` is allowed ("execute implies describe").
  - Detail denial → **404**, the same semantics as `AbstractHandler._enforce_pbac`, which hides existence.
- **Pagination:** order is pre-filter SQL → ABAC batch filter → paginate in memory, so `X-Total-Count` stays exact.
  - Accepts `page`, `page_size`, `sort`, `q`/`search`, plus equality filters such as `provider` and `program_slug`.
  - Reuses `querysource/handlers/_pagination.py` allowlists and envelope.
  - Default order is `updated_at DESC NULLS LAST`.
- **Detail payload:** every `QueryModel` attribute plus **derived metadata**:
  - `query_raw` variables (placeholders), marked required or defaulted;
  - the effective `cond_definition` as the parser merges it;
  - normalized query capabilities (`fields`, `filtering`, `ordering`, `grouping`, `h_filtering`, `qry_options`).
- **Redaction (fields omitted, never nulled):**
  - `query_raw` needs an extra permission, `slug:describe_raw`.
  - `dwh_info`, `cache_options`, `created_by` and `updated_by` are hidden from non-admins.
  - The response carries `"redacted": [<field names>]` so clients know what was withheld.
- **No caching:** definitions are read from Postgres on each request, as `get_query_slug` does today. No Redis layer.
- **No execution:** describing a slug (list and detail) must never run it or connect to its source datasource. It only touches the definitions table. The `/{slug}/columns` sub-resource is the sole, explicit exception: it may *prepare* the statement on the datasource, but never executes it.
- **Never leak `query_raw` by accident:** the columns sub-resource returns column metadata only; the rendered statement stays server-side.
- **Configurable table:** use `QS_QUERIES_SCHEMA` / `QS_QUERIES_TABLE`, via `QueryModel.Meta`. Never hardcode `public.queries` (the scheduler's mistake at `scheduler/scheduler.py:523-542`).
- **Safety:** no string-built SQL from user input. The program list goes in as a **bound parameter** (`program_slug = ANY($1)`). Column names come only from the `_pagination` allowlists.
- **Conventions:** async-only, `self.logger`, Google docstrings, type hints, and `pytest` + `pytest-asyncio` tests under `tests/handlers/`.

---

## Options Explored

### Option A: Dedicated `QueryDescribe` handler + slug-visibility service + pure describer

This adds three small, separately testable pieces:

1. **A visibility service** (for example `querysource/auth/slug_visibility.py`) answers "which slugs may this request see":
   - It resolves the request's *principal*: session user, superuser, sessionless authz identity, or anonymous.
   - From that principal it builds the program **pre-filter predicate**, returned as SQL fragment plus bound args, or "deny all".
   - It runs the **ABAC batch post-filter**:
     - `PolicyEvaluator.filter_resources` for `slug:list`, then again for `slug:execute` on the denied remainder only; the result is the union.
     - The same pattern serves detail, with `slug:describe` falling back to `slug:execute`.
     - A non-raising single-slug check covers `slug:describe_raw` and the admin-only fields.
   - It builds the `EvalContext` the same way `_enforce_pbac` does, including the synthetic `authorized` identity. That matters because `Guardian.filter_resources` calls `is_authenticated` and does not support the sessionless path.
2. **A pure describer** (for example `querysource/queries/describe.py`) turns a `QueryModel` into the describe payload. It is a function of the model and the parser class, with no I/O:
   - It extracts `{placeholders}` from `query_raw` with `string.Formatter().parse`, only for brace-template parsers (the SQL family).
   - It drops parser-reserved names and classifies each variable as `required` or `default` (present in `conditions`).
   - It emits the effective `cond_definition` and the normalized capabilities.
   - It applies redaction rules given a set of granted "view" flags.
3. **A read-only handler** (`querysource/handlers/describe.py`, class `QueryDescribe`) is registered in `QuerySource.setup()` next to the `QueryExecutor` routes:
   - **List:** validate `PaginationParams` → pre-filter `WHERE` (programs + search + equality filters) → fetch the lightweight projection (`query_slug, provider, description, program_slug, updated_at`) for **all** matching rows in SQL order → ABAC filter preserving order → slice the page → `PaginatedResponse` plus the `X-*` headers.
   - **Detail:** same pre-filter predicate + `query_slug = $n` → `QueryModel` → ABAC describe check (404) → optional raw/admin checks → describer → JSON.
   - **Columns:** same pre-filter + ABAC check as detail → `QS(slug=slug, conditions=<defaults>, request=request)` → `build_provider()` → `await provider.describe_columns()` under a short timeout → on failure fall back to `attributes.columns` → JSON with `columns_source`.
   - **Vocabulary:** authenticated; composes the effective `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF` with the curated registry → JSON.
4. **An additive provider hook** `BaseProvider.describe_columns() -> list[dict]` (`providers/abstract.py`), defaulting to `[{"name": n, "type": None} for n in await self.columns()]`, overridden on `pgProvider` to keep `attribute.type.name` from the prepared statement. `columns()` itself is untouched, so `HEAD`/`PATCH /api/v2/services/queries/{slug}` keep their payloads.
5. **A curated vocabulary registry** (`querysource/utils/vocabulary.py`): explicit entries (`name`, `category`, `returns`, `description`, `example`, `args`) over `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF` and a hand-picked subset of `utils/functions.pyx`. It is the single source the ai-parrot prompt and the frontend filter bar are generated from.

✅ **Pros:**
- Matches every decision taken in discovery exactly. `QueryManager` stays untouched and admin behaviour does not regress.
- The visibility service can be reused later:
  - by `QueryManager` hardening;
  - by `/api/v2/services/queries` listing;
  - by the FEAT-176 per-tenant variant (`/api/v1/{tenant}/queries/describe`), because its data access takes `(schema, table)` rather than mutating `QueryModel.Meta`.
- The describer is pure, so it is exhaustively unit-testable without Postgres or PBAC.
- Uses the Rust batch evaluator (`PolicyEvaluator.filter_resources`), so post-filtering thousands of slugs stays cheap.
- Exact totals and stable ordering, because filtering happens before slicing.

❌ **Cons:**
- More new modules (3) than a quick patch.
- In-memory pagination scales with the *pre-filtered* slug count. A superuser, or the sessionless `authorized` identity, scans the whole table projection. That is fine at thousands of rows; it needs a guard at hundreds of thousands (see Open Questions).
- The existing `_pagination.build_where_clause` only builds literal-quoted equality/ILIKE predicates. A bound `ANY($1)` program predicate needs a small companion builder, or an extension of it.
- Placeholder extraction is heuristic for non-SQL parsers, whose `query_raw` is JSON (Mongo, Elastic, ArangoDB…). It must be explicitly marked unsupported there instead of guessed.
- `prepare` needs a syntactically complete statement: slugs with variables that have no default cannot be prepared and fall back to declared columns (`columns_source: "declared"` or `"unavailable"`) with a warning.
- The vocabulary registry is curated by hand (no docstrings to introspect) and must be kept in sync when `functions.pyx` gains helpers.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | Handler / routing | already the stack |
| `navigator-api` (`navigator.views.BaseView` / `BaseHandler`) | `json_response`, `no_content`, CORS | already used by `QueryManager` / `AbstractHandler` |
| `navigator-auth` (`abac.policies.evaluator.PolicyEvaluator`, `EvalContext`, `ResourceType.SLUG`) | ABAC batch + single checks | installed; Rust-backed `filter_resources` |
| `navigator-session` (`get_session`) | session / `userinfo["programs"]`, `superuser` | via `AbstractHandler._get_user_session` |
| `asyncdb` (`Model`, pg pool via `app['qs_connection']`) | read definitions with bound params | same path as `QueryManager` |
| `pydantic` v2 | `PaginationParams`, `PaginatedResponse`, describe response model | already used in `_pagination.py` |
| stdlib `string.Formatter` | placeholder extraction from `query_raw` | no new dependency |
| `asyncpg` (via asyncdb) | `stmt.get_attributes()` → `attr.type.name` for typed columns | already the pg driver |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/service.py:359-439` `QueryService.get_columns`: the build-provider → `columns()` → fallback-to-`attributes.columns` sequence the columns sub-resource mirrors (and its `SlugNotFound` → 404 handling).
- `querysource/providers/pg.py:44-56` `pgProvider.columns`: the prepared-statement discovery to extend with types in `describe_columns()`.
- `querysource/providers/abstract.py:128-146` `get_definition()` / `_get_cond_definition()`; `:190` `columns()` (raises `NotImplementedError` by default).
- `querysource/providers/sql.py:37-62` `replacement` defaults and `_PARSER_PLACEHOLDERS`.
- `rust/src/safe_dict.rs:116-120` placeholder key grammar (`[A-Za-z0-9_.]`), to keep extraction consistent with substitution.
- `rust/src/validators.rs:20-35` `UDF_LIST` / `PG_CONSTANTS`; `querysource/types/validators.pyx:27-33` env-overridable mirror; `rust/src/validators.rs:280-305` the case-insensitive `cond_definition` type dispatch.
- `querysource/utils/functions.pyx` date helpers documented by the vocabulary (`today:94`, `previous_month:111`, `fdom:133`, `fdow:147`, `ldow:150`, `last_year:162`, `ldom:183`, `yesterday:217`, `days_ago:264`, `date_diff:454`, `date_sum:503`).
- `querysource/handlers/_pagination.py`:
  - `PaginationParams.from_query_string`, `build_where_clause`, `build_order_by`, `PaginatedResponse`, `PaginationMeta`;
  - the allowlists `SORTABLE_COLUMNS`, `SEARCHABLE_COLUMNS`, `FILTERABLE_COLUMNS`.
- `querysource/handlers/manager.py:145` `_paginate_list`: reference for the header set and the 204-on-empty behaviour.
- `querysource/handlers/abstract.py:289` `_get_user_session` and `:317` `_enforce_pbac`: session memoization, sessionless synthetic identity and `EvalContext` construction (to be factored or mirrored non-raising).
- `querysource/datasources/handlers/datasource.py:190` `DatasourceView._pbac_filter`: listing-filter pattern. Note that it fails **open** on Guardian errors; this feature must fail **closed**.
- `querysource/datasources/handlers/datasource.py:48` `_redact_datasource`: redaction pattern.
- `querysource/auth/_resource_types.py`: `ResourceType.SLUG` with the fallback shim.
- `querysource/interfaces/connections.py:444` `get_query_slug`: `NoDataFound`/`ValidationError` → `SlugNotFound` mapping.
- `tests/handlers/conftest.py`: `FakeQSConnection`, `test_client`, `seeded_query_slugs` fixtures.

---

### Option B: Extend `QueryManager` with describe routes and PBAC

Register `/api/v1/queries/describe` and `/api/v1/queries/{slug}/describe` as extra `add_view` routes on the existing `QueryManager`. Branch inside `get()` on the matched route (describe vs management), add PBAC plus redaction to the describe branch, and reuse `_paginate_list` directly with a post-filter hook.

✅ **Pros:**
- Least new code: `_paginate_list`, the model loading and the `:meta` handling are already there.
- One place to reason about "reading query definitions".

❌ **Cons:**
- Explicitly rejected in discovery: `QueryManager` must stay an untouched admin tool.
- It mixes two security postures, unrestricted CRUD and consumer describe, in one class keyed on route shape. Any refactor could leak the unrestricted branch.
- `_paginate_list` paginates in SQL, so ABAC post-filtering would need to restructure it and would affect `/management` pagination behaviour and its tests (`test_querymanager_pagination.py`).
- `QueryView` is class-based with `self.request`, while PBAC helpers live on `AbstractHandler`. That means a mixin or duplication.

📊 **Effort:** Low–Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `navigator-api` `BaseView` | existing base of `QueryManager` | — |
| `navigator-auth` | ABAC checks | — |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/manager.py:71` `QueryManager.get`, `:145` `_paginate_list`
- `querysource/utils/handlers.py:67` `QueryView`

---

### Option C: "System slug" — describe executed through the QS pipeline (unconventional)

Seed a built-in, non-editable slug, for example `qs.describe`, whose `query_raw` selects from `{schema}.{table}` with the session programs injected as conditions. Expose `/api/v1/queries/describe` as a thin alias that runs it through `QS` with `output_format=json`. An output post-processor then applies the ABAC filter, redaction and derived metadata, row by row.

✅ **Pros:**
- Reuses the full QS machinery: parser, provider, output writers. Describe results could then be exported as CSV/Excel for free.
- Conceptually "dogfoods" slugs: describing slugs is itself a slug.
- The same ABAC checks as execution (`slug:execute` on `qs.describe`) gate access to the endpoint as a whole.

❌ **Cons:**
- The pre-filter depends on injecting session-derived values into slug conditions. That is exactly the raw-substitution surface that the malforming-queryslug security work hardened. It is high risk.
- Row-level ABAC and redaction in an output writer is the wrong layer. Writers serialize; they do not authorize, and streaming or cached results would bypass the filter.
- Result caching (`is_cached`) would serve one user's filtered view to another unless carefully disabled.
- Exact pagination totals after post-filtering are awkward inside the QS pipeline.
- The system slug must be seeded or migrated in every deployment and protected from `QueryManager` edits.

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `querysource.queries.qs.QS` | execution pipeline | existing |
| `querysource.outputs` writers | serialization / post-processing | would need an authorizing hook (not present) |

🔗 **Existing Code to Reuse:**
- `querysource/queries/qs.py:135` `QS.build_provider`
- `querysource/handlers/abstract.py` `AbstractHandler.get_source`

---

## Recommendation

**Option A** is recommended because:

- It is the only option that satisfies every discovery decision without side effects:
  - a new read-only handler, with `QueryManager` untouched;
  - the program pre-filter pushed into SQL with bound parameters;
  - ABAC `list|execute` and `describe|execute` post-filtering;
  - exact in-memory pagination;
  - redaction by omission, and no cache.
- **Option B** is cheaper, but it couples an unrestricted admin surface with a permissioned consumer surface in one class. It would also force a pagination restructure that changes `/management` behaviour, which was explicitly excluded.
- **Option C** is elegant as dogfooding, but puts authorization in the output layer. It also reintroduces session-to-SQL substitution risk and fights result caching. Wrong layer, higher risk, higher effort.
- **What A trades off:**
  - Three new modules instead of a patch.
  - In-memory pagination cost proportional to the pre-filtered slug set, which is unbounded for superusers and sessionless callers.

  Both are acceptable. Modules are small and pure or testable, and slug tables are in the thousands, not millions. A configurable scan cap is proposed as an open question.
- The visibility service is a strategic building block. `QueryManager` hardening, the v2 listing and FEAT-176 per-tenant describe can all reuse it.

---

## Feature Description

### User-Facing Behavior

**`GET /api/v1/queries/describe`** returns the list of slugs visible to the caller.

- **Query params:**
  - `page` (default 1) and `page_size` (default 50, max 200);
  - `sort=<field>[:asc|desc]` (default `updated_at:desc`, nulls last), where the field must be in `SORTABLE_COLUMNS`;
  - `q`/`search` (ILIKE over `query_slug`, `description`, `program_slug`, `source`);
  - equality filters such as `provider=pg` and `program_slug=walmart`.
    - A `program_slug` filter narrows *within* the user's allowed programs and never widens them.
- **200 response:**
  ```json
  {"data": [{"query_slug": "...", "provider": "pg", "description": "...",
             "program_slug": "walmart", "updated_at": "..."}],
   "meta": {"page": 1, "page_size": 50, "total": 132, "total_pages": 3}}
  ```
  with headers `X-Total-Count`, `X-Page`, `X-Page-Size` and `X-Total-Pages`.
- **Other responses:**
  - `204 No Content` when nothing is visible: no programs, everything denied, or no matches.
  - `400` for invalid pagination, sort or filter keys, with the same error payload style as `QueryManager`.

**`GET /api/v1/queries/{slug}/describe`** returns the full description of one slug.

- **200 response** contains:
  - **identity and ownership:** `query_slug`, `description`, `program_slug`, `program_id`, `provider`, `parser`, `source`;
  - **definition:** `query_raw` (only with `slug:describe_raw`), `is_raw`, `params`, `attributes`, `conditions` (defaults), `cond_definition`;
  - **capabilities:** `fields`, `filtering`, `ordering`, `grouping`, `h_filtering`, `qry_options`;
  - **cache and DWH summary:** `is_cached`, `cache_timeout`, `cache_refresh`, `dwh`, `dwh_driver`, plus `dwh_info`, `dwh_scheduler` and `cache_options` for admins only;
  - **audit:** `created_at`, `updated_at`, plus `created_by` and `updated_by` for admins only;
  - **`derived`:**
    - `variables`: a list of `{name, type, raw_type, default, required, source, accepts_keywords}` extracted from `query_raw` and `cond_definition`, or `null` with `variables_supported: false` for non-template parsers. Example:
      ```json
      {"name": "firstdate", "type": "date", "raw_type": "date", "default": "current_date",
       "required": false, "source": "cond_definition", "accepts_keywords": true}
      ```
    - `structural_placeholders`: the reserved names found in `query_raw` (e.g. `["and_cond", "fields", "querylimit"]`), listed so clients know they are not inputs;
    - `effective_cond_definition`;
    - `capabilities`: normalized `fields`/`filtering`/`ordering`/`grouping` "what you may request" block, plus `refresh_param: "refresh"` (the condition that bypasses the result cache, `providers/abstract.py:78-82`);
    - `links`: `{"columns": "/api/v1/queries/{slug}/columns", "vocabulary": "/api/v1/queries/vocabulary"}`;
  - **`redacted`:** the list of field names withheld from this caller.
- **Other responses:**
  - `404` when the slug does not exist, is outside the caller's programs, or ABAC denies describe/execute. The three cases are indistinguishable.
  - Describing never executes the query and never opens a connection to the slug's datasource.

**`GET /api/v1/queries/{slug}/columns`** returns the output columns of one slug, typed when possible.

- Same visibility rules as the detail endpoint (program pre-filter + `slug:describe OR slug:execute`; denial → 404).
- Query-string conditions are accepted and merged exactly as `get_columns` does (`{**json_body, **query_params}`), validated through `cond_definition` by the Rust substitution.
- **200 response:**
  ```json
  {"slug": "epson_field_activity",
   "columns": [{"name": "visit_date", "type": "date"}, {"name": "store_id", "type": "integer"},
               {"name": "visits", "type": "integer"}],
   "columns_source": "prepare",
   "warnings": []}
  ```
  - `columns_source` is `prepare` (typed, from the prepared statement), `declared` (names from `attributes.columns`, types `null`) or `unavailable` (neither); each non-`prepare` value comes with an explanatory `warnings` entry.
  - Query-string conditions let a caller supply values for variables without defaults and make `prepare` possible.
- The statement is prepared, never executed; no rows are read and the rendered SQL is not returned.

**`GET /api/v1/queries/vocabulary`** returns the relative-date keyword vocabulary accepted as condition values.

- Authenticated; no slug resource involved (PBAC action: see Open Questions).
- **200 response:**
  ```json
  {"version": "1.0", "case_insensitive": true,
   "keywords": [
     {"name": "TODAY", "category": "date", "returns": "date",
      "description": "Current date in the server timezone (UTC by default).", "example": "2026-09-15"},
     {"name": "FDOM", "category": "date", "returns": "date",
      "description": "First day of the current month.", "example": "2026-09-01"},
     {"name": "LDOM", "category": "date", "returns": "date", "description": "…", "example": "2026-09-30"},
     {"name": "YESTERDAY", "category": "date", "returns": "date", "description": "…", "example": "2026-09-14"},
     {"name": "CURRENT_YEAR", "category": "date", "returns": "integer", "description": "…", "example": 2026},
     {"name": "CURRENT_MONTH", "category": "date", "returns": "string", "description": "…", "example": "…"},
     {"name": "LAST_YEAR", "category": "date", "returns": "integer", "description": "…", "example": 2025}],
   "constants": ["CURRENT_DATE", "CURRENT_TIMESTAMP"],
   "functions": [{"name": "date_diff", "invocable": false,
                  "args": [{"name": "value"}, {"name": "diff", "default": 1}, {"name": "mode", "default": "days"}],
                  "description": "…"}],
   "usage": "Pass a keyword as the condition value, e.g. {\"firstdate\": \"FDOM\", \"lastdate\": \"TODAY\"}."}
  ```
  - `keywords` is exactly the process's effective `UDF_LIST` (environment override included).
  - `functions` is informational in v1 (`date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow`, `ldow`): documented args, `invocable: false`, because HTTP condition values cannot call them yet.
- Authenticated only; no PBAC action.

**Per-tenant variants (FEAT-176):** `GET /api/v1/{tenant}/queries/describe`, `GET /api/v1/{tenant}/queries/{slug}/describe`, `GET /api/v1/{tenant}/queries/{slug}/columns` behave identically over the tenant's `(schema, table)`.

### Internal Behavior

1. **Principal resolution** (visibility service):
   - Get the session with the `_get_user_session` semantics and read `session[AUTH_SESSION_OBJECT]`.
   - The principal is one of four kinds:
     - `superuser`, when `userinfo.superuser` is true;
     - `programs`, when `userinfo.programs` is a non-empty list;
     - `authz`, for a sessionless authorized backend (only when `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`);
     - `none`.
   - Normalize `programs` to a list of slugs, since the Token backend may provide `Program` objects.
2. **Pre-filter predicate:**
   - `superuser` and `authz`: no predicate.
   - `programs`: `program_slug = ANY($n)` with `programs ∪ {'default'}` bound.
   - `none`: short-circuit to empty. The list returns 204 and detail returns 404, without touching the DB.
3. **List flow:**
   - Validate params and combine the pre-filter with search/equality predicates and `ORDER BY <allowlisted> NULLS LAST`.
   - Fetch the lightweight projection for all matches via `app['qs_connection']`.
   - Batch ABAC: allowed = `filter(slug:list)`, then `filter(slug:execute)` over the denied remainder; keep SQL order.
   - Slice to the page and build `PaginatedResponse` plus headers. An empty result gives 204.
4. **Detail flow:**
   - Fetch one row with pre-filter AND `query_slug = $n`. No row gives 404.
   - Check ABAC `slug:describe`, falling back to `slug:execute`. Denied gives 404.
   - Compute view grants with non-raising checks:
     - `raw`: `slug:describe_raw` (and nothing else; `raw_query:execute` does not imply it);
     - `admin`: `userinfo.superuser` OR a session group in `QS_DESCRIBE_ADMIN_GROUPS`.
   - The describer builds the payload, applies redaction and fills `redacted`.
5. **PBAC disabled** (`app['security']` / `app['policy_evaluator']` absent): ABAC steps are no-ops, as with `_enforce_pbac`. The program pre-filter still applies whenever a session exists; with no session and no sessionless authz the request is denied with `401` before any DB access.
6. **Describer:**
   - For brace-template parsers (SQL family: `SQLParser`, pgSQL, MS SQL, BigQuery, CQL, SOQL…), run `string.Formatter().parse(query_raw)` to collect field names.
   - Drop parser-reserved names (the structural set from Constraints: `fields`, `tablename`, `schema`, `table`, `filter`, `where_cond`, `and_cond`, `grouping`, `group_by`, `ordering`, `order_by`, `offset`, `_offset`, `limit`, `_limit`, `querylimit`); report them under `structural_placeholders`. A reserved name that is *also* in `cond_definition` (misconfigured slug) stays structural and emits a warning.
   - Mark each name `required` unless it has a default in `conditions` **or** an implicit provider default (`replacement`: `firstdate`/`lastdate`/`filterdate` → `current_date`); record `default` and `source`.
   - Set `type` from `cond_definition` normalised to the canonical lowercase set, `raw_type` verbatim, `null` when absent; `accepts_keywords` for `date`/`datetime`/`timestamp`/untyped.
   - Malformed braces (`ValueError`) give `variables: null` plus a `variables_error` note, never a 500.
   - `effective_cond_definition` mirrors `AbstractParser._col_definition_sync` merge order (conditions-level, then definition-level).
7. **Columns flow:**
   - Same pre-filter + ABAC check as detail; instantiate `QS(slug=slug, conditions=<query-string conditions or {}>, request=request)` and `await build_provider()` (`SlugNotFound` → 404, `ParserError` → 406).
   - `await provider.describe_columns()` under a short timeout (e.g. 5 s). On `ParserError` / `DriverError` / timeout, fall back to `definition.attributes.columns`; record `columns_source` and a warning. A datasource that is down still yields `200` with `columns_source: "unavailable"` so the contract stays useful to agents.
8. **Vocabulary flow:**
   - Read the effective `UDF_LIST`, `PG_CONSTANTS`, `PG_UDF` from `querysource.types.validators` (env overrides included) and join them with the curated registry in `querysource/utils/vocabulary.py`; serialise. No I/O.

### Edge Cases & Error Handling

- **Slug names:** the path parameter is validated against a safe slug pattern before any DB call. An invalid name gives 404, not 400, so existence is not leaked.
- **Missing row data:** `query_raw` empty or `None` gives `variables: []`. JSON-type `query_raw` (Mongo/Elastic/Arango) gives `variables_supported: false`.
- **Rows that fail model validation:** a `QueryModel` `ValidationError` on a legacy row is logged at warning and returned as 404 on detail. On the list only the projection is read, so it is unaffected.
- **Program data:** `programs` in the session are normalised to lowercase slugs (`Program` objects → `.slug`, falling back to `.name` / `str()`) and deduplicated before binding; the comparison against `program_slug` is lowercase on both sides.
- **ABAC failures:** evaluator exceptions **fail closed**: 404 on detail, and on the list the affected slugs are excluded with an error log. This deliberately differs from `DatasourceView._pbac_filter`, which fails open.
- **`execute` fallback:** it is evaluated only on the denied remainder, which avoids double evaluation cost.
- **Large pre-filtered sets:** superuser/authz principals can produce large scans. `QS_DESCRIBE_MAX_SCAN` (default `10000`) bounds the projection fetch; beyond it the first N rows in SQL order are kept, `X-Truncated: true` is set and a warning is logged. The response is never a `400`.
- **FEAT-176 tenant rows (no `program_slug` column):** the tenant routes derive program context from the tenant; the pre-filter predicate builder is pluggable so the same handler serves both shapes.
- **Transport:** CORS/OPTIONS follow `QueryView`/`BaseView` conventions. HEAD on the list returns the headers only.
- **Columns on non-SQL providers** (REST, Mongo, BigQuery without `prepare`): the default `describe_columns()` yields untyped names when the provider implements `columns()`, else the declared fallback. `is_raw` slugs follow the same path. MultiQuery (v3) slugs are out of scope for v1 (open question).
- **Columns with unresolved variables:** `prepare` is skipped and the response carries `columns_source: "declared"|"unavailable"` with `warnings: ["prepare_skipped: unresolved placeholders [...]"]`.
- **Vocabulary under `UDF_LIST` override:** the list reflects the override; unknown keywords appear with `description: null`.
- **`previous_year()`** (`functions.pyx:107`) returns a `datetime` despite its `cpdef int` signature; it is excluded from the curated `functions` block until fixed.

---

## Capabilities

### New Capabilities
- `query-describe-api`: read-only REST endpoints `GET /api/v1/queries/describe` (list) and `GET /api/v1/queries/{slug}/describe` (detail).
- `slug-visibility`: principal resolution, the program pre-filter predicate, and the ABAC batch/single slug checks (`list|execute`, `describe|execute`, `describe_raw`), all fail-closed.
- `slug-describer`: pure transformation `QueryModel` → describe payload (variables, effective cond_definition, capabilities, redaction).
- `slug-columns`: `GET /api/v1/queries/{slug}/columns`, typed output columns via an additive `BaseProvider.describe_columns()` hook (prepared statement on pg, declared fallback elsewhere).
- `date-keyword-vocabulary`: `GET /api/v1/queries/vocabulary` and the curated registry `querysource/utils/vocabulary.py` over `UDF_LIST` / `PG_CONSTANTS` / `functions.pyx`.

### Modified Capabilities
- `querysource-slug-list-pagination` (`sdd/specs/querysource-slug-list-pagination.spec.md`): reused, and possibly extended with a bound-parameter `ANY` predicate builder and `NULLS LAST` ordering, without changing `QueryManager` behaviour.
- `pbac-support` (`sdd/specs/pbac-support.spec.md`): new actions `slug:describe` and `slug:describe_raw` (plus existing `slug:list`) added to `policies/defaults.yaml` grants for admin/superuser only.
- `malforming-queryslug-issue` / Rust validators (`rust/src/validators.rs`, `querysource/types/validators.pyx`): UDF keyword resolution extended to `date`/`datetime`/`timestamp`-typed conditions on both paths.
- `per-tenant-queries` (FEAT-176, in proposal): consumed by the tenant describe/columns routes.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/services.py` (`QuerySource.setup`, ~L160-167) | modifies | register the 2 GET routes next to the `QueryExecutor` routes |
| `querysource/handlers/__init__.py` | modifies | export the new handler |
| `querysource/handlers/describe.py` | new | `QueryDescribe` read-only handler |
| `querysource/auth/slug_visibility.py` | new | principal + pre-filter + ABAC checks |
| `querysource/queries/describe.py` | new | pure describer + redaction |
| `querysource/handlers/_pagination.py` | extends | bound `ANY` predicate helper and/or `NULLS LAST` option (backward compatible) |
| `querysource/handlers/abstract.py` | depends on / light refactor | possibly factor `EvalContext` construction out of `_enforce_pbac` for reuse (no behaviour change) |
| `querysource/conf.py` | extends | `QS_DESCRIBE_MAX_SCAN` (default 10000), `QS_DESCRIBE_ADMIN_GROUPS` (default `admin,superuser`) |
| `policies/defaults.yaml` | modifies | grant `slug:describe`, `slug:describe_raw` to admin/superuser only; regular users rely on the `slug:execute` fallback |
| `rust/src/validators.rs`, `querysource/types/validators.pyx` | modifies (first task) | UDF keyword resolution for `date`/`datetime`/`timestamp`-typed conditions on both paths, with parity tests |
| `querysource/handlers/manager.py` (`QueryManager`) | none | explicitly untouched |
| `querysource/providers/abstract.py` | extends | additive `describe_columns()` default (names only, from `columns()`) |
| `querysource/providers/pg.py` | extends | `describe_columns()` keeps `attribute.type.name`; `columns()` unchanged |
| `querysource/utils/vocabulary.py` | new | curated keyword/function registry |
| `CHANGES.rst`, `querysource/version.py` | modifies | changelog; bump to `4.6.0` (new public endpoints → minor) |
| `tests/handlers/`, `tests/test_route_registration.py`, `tests/policies/` | extends | new tests, incl. pg typed-columns test and vocabulary test under `UDF_LIST` override |
| API consumers | new API | additive, no breaking changes |
| FEAT-176 per-tenant queries (`sdd/proposals/` proposal, same day) | depends on | the tenant routes need its `(schema, table)` data-access layer; sequence FEAT-176's data-access tasks first |
| ai-parrot FEAT-567 `QuerySourceToolkit.qs_describe` | depends on (downstream, other repo) | consumes detail + columns + vocabulary |
| ai-parrot A2UI linked surfaces (next feature) | depends on (downstream) | parameter contract + vocabulary feed the data-source descriptor |
| ai-parrot `QuerySlugSource.prefetch_schema` | depends on (later) | can replace the `querylimit=1` probe with `/columns` |

---

## Code Context

### User-Provided Code

```sql
-- Source: user-provided (current way of listing slugs, via the query executor)
SELECT query_slug, provider, description, program_slug, updated_at
FROM public.queries
ORDER BY updated_at DESC NULLS LAST
```

```text
# Source: user-provided (conversation, 2026-09-15) — how the Navigator frontend calls a slug today
POST /api/v2/services/queries/{query_slug}
payload: {"lastdate": "2026-08-15", "firstdate": "2026-08-09"}
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/models.py:48-107
class QueryModel(asyncdb.models.Model):
    query_slug: str            # primary_key
    description: str
    source: Optional[str]
    params: dict               # jsonb
    attributes: dict           # jsonb
    conditions: dict           # jsonb
    cond_definition: dict      # jsonb
    fields: List[str]          # array
    filtering: dict            # jsonb
    ordering: List[str]        # array
    grouping: List[str]        # array
    qry_options: dict          # jsonb
    h_filtering: bool          # default False
    query_raw: str
    is_raw: bool               # default False
    is_cached: bool            # default True
    provider: str              # default 'db'
    parser: str                # default 'SQLParser'
    cache_timeout: int         # 3600
    cache_refresh: int         # 0
    cache_options: dict        # jsonb
    program_id: int            # 1
    program_slug: str          # 'default'
    dwh: bool
    dwh_driver: str
    dwh_info: dict             # jsonb
    dwh_scheduler: dict        # jsonb
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int
    class Meta:                # L101-107
        driver = 'pg'; name = QS_QUERIES_TABLE; schema = QS_QUERIES_SCHEMA
        strict = True; frozen = False; remove_nulls = True

# From querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):                              # L27
    async def _get_user_session(self, request: web.Request) -> Optional[SessionData]:  # L289
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None:  # L317
        # no-op if app['security'] missing; raises web.HTTPNotFound on deny;
        # sessionless synthetic user {'username': 'authz:<backend>', 'groups': ['authorized', backend]}
        # when QS_PBAC_ALLOW_SESSIONLESS_AUTHZ and request[AUTHZ_BACKEND_KEY]

# From querysource/handlers/manager.py
class QueryManager(QueryView):                                   # L32
    async def get(self):                                         # L71
    async def _paginate_list(self, qp: dict, default_args: dict):  # L145

# From querysource/utils/handlers.py:67
class QueryView(navigator.views.BaseView): ...

# From querysource/handlers/_pagination.py
DEFAULT_PAGE_SIZE: int = 50; MAX_PAGE_SIZE: int = 200            # L40-41
DEFAULT_SORT_FIELD = "updated_at"; DEFAULT_SORT_DIRECTION = "desc"
FILTERABLE_COLUMNS: frozenset[str]                               # L48
SORTABLE_COLUMNS: frozenset[str]   # query_slug, description, program_slug, provider, is_cached, created_at, updated_at  (L52-62)
SEARCHABLE_COLUMNS: tuple[str, ...]  # query_slug, description, program_slug, source  (L65-70)
class PaginationParams(BaseModel):                               # L73
    page: int; page_size: int; sort_field: str; sort_direction: SortDirection
    search: Optional[str]; fields: Optional[list[str]]
    @classmethod
    def from_query_string(cls, qs: dict) -> "PaginationParams":  # L122
class PaginationMeta(BaseModel): page: int; page_size: int; total: int; total_pages: int  # L188
class PaginatedResponse(BaseModel): data: list[dict]; meta: PaginationMeta               # L197
def build_where_clause(params: PaginationParams, extra_filters: dict) -> str:            # L251 (literal-quoted equality/ILIKE)
def build_order_by(params: PaginationParams) -> str:   # L309 -> 'ORDER BY "<col>" <DIR>' (no NULLS LAST)
def build_count_sql(schema: str, table: str, where: str) -> str:                         # L332
def build_page_sql(schema: str, table: str, fields: list[str], where: str,
                   order_by: str, limit: int, offset: int) -> str:                       # L352

# From querysource/datasources/handlers/datasource.py
def _redact_datasource(record: dict) -> dict:                    # L48
async def _check_datasource_read(request: web.Request, logger=None) -> None:  # L84
@user_session()                                                   # L185
class DatasourceView(BaseView):                                   # L186
    async def _pbac_filter(self, request: web.Request, items: list, name_key: str,
                           resource_type, action: str) -> list:   # L190 (fail-OPEN on errors)
    async def get(self) -> web.Response:                          # L291

# From querysource/interfaces/connections.py
async def get_query_slug(self, slug, evt=None, max_retries=3) -> BaseModel:  # L444
    # QueryModel.get(query_slug=slug, _connection=conn); NoDataFound/ValidationError -> SlugNotFound
async def get_slug(self, slug, program=None, evt=None):          # L492

# From querysource/queries/qs.py:43
class QS(...):
    def __init__(self, slug: str = '', conditions: dict = None,
                 request: web.Request = None, loop=None, **kwargs): ...  # L43
    async def columns(self): ...                          # L126 — delegates to self._qs.columns()
    async def build_provider(self): ...                   # L134 — resolves QueryModel + provider
    def get_source(self): return self._qs                 # L123

# From querysource/providers/abstract.py
class BaseProvider:
    def get_definition(self) -> Union[QueryModel, dict]: ...   # L128
    def _get_cond_definition(self) -> dict: ...                # L133 — normalises None → {}
    async def columns(self): ...                               # L190 — raises NotImplementedError
    def refresh(self): return self._refresh                    # L220 — 'refresh' popped from conditions at L78-82

# From querysource/providers/pg.py:44
class pgProvider(...):
    async def columns(self):
        async with await self._connection.connection() as conn:
            stmt, _ = await conn.prepare(self._query)          # L50
            self._columns = [a.name for a in stmt.get_attributes()]   # L51 — .type discarded

# From querysource/handlers/service.py
class QueryService(AbstractHandler):
    async def get_columns(self, request): ...             # L359 — HEAD, X-Columns header, fallback attributes.columns at L406-409
    async def columns(self, request): ...                 # L444 — PATCH, bare list JSON

# From querysource/parsers/abstract.pyx
cdef class AbstractParser:                                        # L28
    cdef void define_conditions(self, object conditions):         # L96
    cdef void _col_definition_sync(self):                         # L307 (cond_definition merge order)
# From querysource/parsers/sql.pyx:97-98 (reserved template placeholders)
#   self.tablename = '{schema}.{table}'
#   self._base_sql = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'
# sql.pyx:242-246 also substitutes '{where_cond}', '{and_cond}', '{filter}'

# From .venv/.../navigator_auth/abac/guardian.py:209
async def filter_resources(self, resources: List[str], request: web.Request,
                           resource_type: ResourceType = ResourceType.TOOL,
                           action: str = "tool:execute") -> "FilteredResources":
    # calls is_authenticated(request) -> does NOT support QS sessionless 'authorized' identity

# From .venv/.../navigator_auth/abac/policies/evaluator.py
class EvaluationResult: allowed: bool; effect; matched_policy; reason; ...   # L60
class FilteredResources: allowed: List[str]; denied: List[str]; policies_applied: List[str]  # L74
def check_access(self, ctx: EvalContext, resource_type: ResourceType, resource_name: str,
                 action: str, env: Environment = None, owner_reports_to: str = None,
                 org_id: int = 1, ...)                                        # L405
def filter_resources(self, ctx: EvalContext, resource_type: ResourceType,
                     resource_names: List[str], action: str, env: Environment = None,
                     org_id: int = 1, client_id: int = 1)                     # L516

# From .venv/.../navigator_auth/decorators.py:462 — allowed_programs(programs: list, ...)
#   reads session[AUTH_SESSION_OBJECT]["programs"] (reference for program membership semantics)
# navigator_auth/conf.py: DEFAULT_MAPPING "superuser": "is_superuser" (L271);
#   DJANGO_USER_MAPPING "programs": "programs" (L345); AUTH_SESSION_OBJECT default "session"
```

#### Verified Imports
```python
from querysource.models import QueryModel                         # querysource/models.py:48
from querysource.handlers import QueryManager, QueryExecutor      # querysource/handlers/__init__.py
from querysource.handlers.abstract import AbstractHandler         # querysource/handlers/abstract.py:27
from querysource.handlers._pagination import (
    PaginationParams, PaginatedResponse, PaginationMeta,
    build_where_clause, build_order_by, build_count_sql, build_page_sql,
    SORTABLE_COLUMNS, SEARCHABLE_COLUMNS, FILTERABLE_COLUMNS,
)                                                                 # querysource/handlers/_pagination.py
from querysource.auth._resource_types import ResourceType        # querysource/auth/_resource_types.py (SLUG w/ shim)
from querysource.conf import (
    QS_QUERIES_SCHEMA, QS_QUERIES_TABLE,                          # conf.py:353-354
    QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL,            # conf.py:429-431
    QS_PBAC_ALLOW_SESSIONLESS_AUTHZ,                              # conf.py:441
)
from navigator.views import BaseView, BaseHandler
from navigator_session import get_session, SessionData
from navigator_auth.conf import AUTH_SESSION_OBJECT, AUTHZ_BACKEND_KEY   # lazily imported in abstract.py:370,410
from navigator_auth.abac.policies.resources import ResourceType
```

#### Key Attributes & Constants
- `app['qs_connection']` → `QueryConnection` (set in `querysource/connections.py:153`); `acquire()` at L123.
- `app['security']` → `Guardian`; `app['abac']` → `PDP`; `app['policy_evaluator']` → `PolicyEvaluator` (set by `querysource/auth/pbac.py:28-149` `setup_pbac`, only when `QS_PBAC_ENABLED`).
- Existing routes `POST /api/v1/queries/test|run|schema` → `QueryExecutor` (`querysource/services.py:160-167`).
- `QueryManager` routes: `/api/v1/management/queries/{slug}` and `/api/v1/management/queries{meta:\:?.*}` (`services.py:181-188`).
- PBAC actions in use: `slug:execute`, `slug:read` (components), `raw_query:execute`, `datasource:use|list|read`, `driver:use|list`. `slug:list` granted in `policies/defaults.yaml:20` but unused in code.
- Test fixtures: `tests/handlers/conftest.py` (`FakeConn`, `FakeQSConnection`, `test_client`, `seeded_query_slugs`); PBAC unit-test style in `tests/handlers/test_abstract_pbac_helpers.py`; route assertions in `tests/test_route_registration.py`.
- `UDF_LIST` → `["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]` (`rust/src/validators.rs:21-35`; env-overridable mirror `querysource/types/validators.pyx:27-33`); `PG_CONSTANTS` → `["CURRENT_DATE", "CURRENT_TIMESTAMP"]`; `PG_UDF` → `["now()"]`.
- `replacement` → `{"fields": "*", "filterdate": "current_date", "firstdate": "current_date", "lastdate": "current_date", "where_cond": "", "and_cond": "", "filter": ""}` (`querysource/providers/sql.py:37-45`); `_PARSER_PLACEHOLDERS` → `where_cond, and_cond, filter, fields, group_by, grouping, order_by, ordering, querylimit, _limit, _offset, schema, table` (`sql.py:48-62`).
- Placeholder key grammar → non-empty, no `{`, chars in `[A-Za-z0-9_.]` (`rust/src/safe_dict.rs:116-120`).
- `cond_definition` type dispatch (case-insensitive) → `literal | int | integer | float | numeric | decimal | epoch | boolean | string | varchar | field | date | datetime | timestamp | uuid | array | json` (`rust/src/validators.rs:280-305`); `numrange` / `int4range` / `int8range` in `rust/src/pgsql_parser.rs:371-393`.
- Keyword set accepted as a `date_diff` / `date_sum` input value → `current_date | now | today | yesterday | tomorrow | fdom | ldom | fdcw` (`querysource/utils/functions.pyx:475, 535`).
- Result-cache key → `sha256(rendered_query)` (`providers/abstract.py:229`, `utils/functions.pyx:39-40`); `?refresh=1` bypasses it.
- Ledger → next feature id `FEAT-147`, next task id `TASK-716` (`sdd/tasks/.id_ledger.json`); version `4.5.16` (`querysource/version.py:9`).

### Does NOT Exist (Anti-Hallucination)
- ~~`GET /api/v1/queries/describe` / `/api/v1/queries/{slug}/describe`~~: no route (no GET routes exist under `/api/v1/queries` at all); no `describe` handler anywhere. `outputs/writers/describe.py` `DescribeWriter` is commented out and unrelated. `queries/multi/_introspect.py` `describe_class` is for components.
- ~~`QueryDescribe` / describe response model~~: does not exist.
- ~~SQL pre-filtering by `program_slug` / session groups~~: none in any handler.
- ~~PBAC in `QueryManager`~~: it performs no auth or PBAC checks.
- ~~`slug:describe`, `slug:describe_raw` actions~~: not defined in policies or code.
- ~~Cache of slug definitions (Redis or memory)~~: only query *results* are cached. Definitions hit the DB each time.
- ~~Placeholder/variable extractor for `query_raw`~~: none in Cython or Rust. `_qs_parsers` exposes `safe_format_map[_validated]` (substitution) only.
- ~~Bound-parameter (`$n`) support in `_pagination.build_where_clause`~~: it emits literal-quoted predicates; no `ANY($n)`.
- ~~`NULLS LAST` in `build_order_by`~~: not emitted.
- ~~Guardian sessionless support matching QS `authorized` identity~~: `Guardian.filter_resources` requires `is_authenticated`.
- ~~A "system slug" exposing the queries table~~: none. `troc.queries` is referenced nowhere.
- ~~`{today}` / `{today-7}` / `{fdom}` placeholder expressions~~: the Rust substitution replaces simple identifiers only; date keywords are resolved as condition **values**, never as placeholder names or arithmetic.
- ~~`QS.get_definition()`~~: only `BaseProvider.get_definition()` exists (`providers/abstract.py:128`); reach it via `qs.get_source().get_definition()`.
- ~~`BaseProvider.describe_columns()`~~: does not exist yet (this feature adds it).
- ~~Docstrings on `utils/functions.pyx` date helpers~~: `today`, `fdom`, `ldom`, `previous_month`, … have none; the vocabulary cannot be introspected and must be a curated registry.
- ~~`:describe` / `:meta` suffix on `/api/v2/services/queries/{slug}`~~: the `:` suffix there is the output format (`slug:csv`); `:meta` exists only on `/api/v1/management/queries`.
- ~~`QueryHandler.columns` (v3) returning data~~: `handlers/multi.py:101-107` always answers 204 "No Columns available".
- ~~`aiohttp_swagger` / generated OpenAPI~~: absent; documentation is docstring YAML (`service.py:444-469`) or Google-style (`components.py`).
- ~~`slug:read` granted in `policies/defaults.yaml`~~: only `slug:execute` and `slug:list` are granted today (`slug:read` is used by `handlers/components.py:41-46` only).

---

## Parallelism Assessment

- **Internal parallelism:** moderate. Two strands are independent until the handler task:
  - `slug-describer`: a pure function with its own unit tests and no shared files;
  - `slug-visibility`: auth plus a `_pagination` helper.

  The handler, route registration and integration tests depend on both, and the policy YAML grants are a small independent task.
  Two more strands are independent of everything above until route registration: `describe_columns()` on `providers/abstract.py` + `providers/pg.py` (with its asyncpg test), and the vocabulary registry + endpoint.
- **Cross-feature independence:**
  - The **UDF typed-resolution fix** is the first task of this branch (`rust/src/validators.rs`, `querysource/types/validators.pyx`, parity tests); nothing else in flight touches those files.
  - Including the **tenant routes now** turns FEAT-176 from a merge-conflict risk into a **hard dependency**: the tenant variants cannot be implemented until FEAT-176's `(schema, table)` data-access layer exists. Sequence: FEAT-176 data-access tasks first, then this feature's tenant routes as its last task; the non-tenant routes do not wait.
  - **FEAT-176 `per-tenant-queries`** (proposal in review, same day) touches `QueryModel`, `_pagination.py` allowlists (drops `program_slug` for tenant rows), `QueryManager` and route registration in `services.py`. Expect **merge conflicts in `services.py` and `_pagination.py`**, and a **semantic dependency**: tenant rows have no `program_slug`, so the pre-filter must be pluggable.
  - `pbac-support` and `querysource-slug-list-pagination` specs are complete; they are extended, not in flight.
  - No other pending tasks in `sdd/tasks/index/`.
- **Recommended isolation:** `per-spec`
- **Rationale:** the feature is medium-sized, around 8-9 tasks (UDF fix, describer, visibility, handler + routes, columns hook, vocabulary, policies/config, tenant routes), and the independent strands are small. One worktree with sequential tasks avoids coordination overhead, and keeps the `_pagination.py` / `services.py` edits in one branch that can be rebased cleanly against FEAT-176.

---

## Open Questions

- [x] Flow type and base branch — *Owner: Jesús Lara*: `feature`, base `dev`.
- [x] Detail URL shape — *Owner: Jesús Lara*: `GET /api/v1/queries/{slug}/describe` (list stays at `GET /api/v1/queries/describe`).
- [x] New handler vs extending `QueryManager` — *Owner: Jesús Lara*: new read-only handler; `QueryManager` untouched.
- [x] Meaning of "pre-filtering based on permissions" — *Owner: Jesús Lara*: SQL pre-filter by session `programs`; superuser sees all; `program_slug='default'` always included for users with programs; user without programs sees nothing (fail-closed); sessionless authz skips the pre-filter and relies on ABAC.
- [x] Detail payload scope — *Owner: Jesús Lara*: full `QueryModel` + derived metadata (query_raw variables, effective cond_definition, query capabilities); no source-schema introspection, no cache/DWH derived block.
- [x] Redaction — *Owner: Jesús Lara*: `query_raw` gated by extra permission; `dwh_info`/`cache_options` and `created_by`/`updated_by` hidden from non-admins; fields omitted, with a `redacted` list.
- [x] List pagination strategy — *Owner: Jesús Lara*: pre-filter SQL → ABAC batch → paginate in memory (exact totals).
- [x] PBAC actions and denial semantics — *Owner: Jesús Lara*: `slug:list` (list), `slug:describe` (detail), `slug:describe_raw` (query_raw); detail deny → 404.
- [x] Does execute imply visibility? — *Owner: Jesús Lara*: yes, list = `slug:list OR slug:execute`, detail = `slug:describe OR slug:execute`.
- [x] List parameters — *Owner: Jesús Lara*: page/page_size/sort/search + equality filters (provider, program_slug) reusing `_pagination`.
- [x] Caching — *Owner: Jesús Lara*: no cache; always read from DB.
- [x] Typed columns: inside describe, or never touching the datasource? — *Owner: Jesús Lara*: neither; a sibling sub-resource `GET /api/v1/queries/{slug}/columns` (prepared statement + declared fallback, never executes). Describe stays pure over `QueryModel`.
- [x] Where the relative-date keyword vocabulary lives — *Owner: Jesús Lara*: its own endpoint `GET /api/v1/queries/vocabulary`, referenced from describe via `derived.links`.
- [x] Inconsistent UDF resolution for `date`-typed conditions (Rust quotes before the UDF check; only Cython calls `to_udf`) — *Owner: Jesús Lara*: initially "separate hotfix on `main`"; **revised the same day** to ship inside `4.6.0` as the first task of this feature (see below).
- [x] Sampling rows (`querylimit=1`) to infer column types on providers without `prepare` — *Owner: Jesús Lara*: out of scope for v1.
- [x] PBAC for the new sub-resources — *Owner: Jesús Lara*: `/columns` under `slug:describe OR slug:execute` exactly like detail; `/vocabulary` requires authentication only (no slug resource, no PBAC action).
- [x] Should `/columns` accept query-string conditions so callers can render variables without defaults and make `prepare` possible? — *Owner: Jesús Lara*: yes, same merge rules as `get_columns` (`{**json_body, **query_params}`), validated through `cond_definition` by the Rust substitution.
- [x] Which `functions.pyx` helpers enter the curated `functions` block of the vocabulary in v1? — *Owner: Jesús Lara*: keywords plus a minimal, **informational** set (`date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow`, `ldow`) with documented args; each entry carries `invocable: false` until a way to call them as HTTP condition values exists (today only `masks`/`fnExecutor` in MultiQuery use them).
- [x] Does the UDF typed-resolution fix ship as `4.5.17` before `4.6.0`, or inside `4.6.0`? — *Owner: Jesús Lara*: inside `4.6.0`, as the **first task** of this feature's branch (supersedes the earlier "separate hotfix on `main`" decision below).
- [x] Does the program pre-filter also apply to the **detail** endpoint? — *Owner: Jesús Lara*: yes, same as the list; a slug outside the caller's programs is 404 even if ABAC would allow it. Superuser and sessionless authz stay unfiltered.
- [x] What defines "admin" for `dwh_info`/`cache_options`/`created_by`/`updated_by`? — *Owner: Jesús Lara*: `userinfo.superuser` OR membership in an admin group (group names configurable, `QS_DESCRIBE_ADMIN_GROUPS`, default `admin,superuser`); no new PBAC action.
- [x] Behaviour with **no session and no sessionless authz** (`QS_PBAC_ENABLED=False`, auth middleware absent) — *Owner: Jesús Lara*: deny, fail-closed: `401` on list, detail and columns. `QueryManager` remains the unauthenticated admin path.
- [x] Should `query_raw` also be visible to callers holding `raw_query:execute`? — *Owner: Jesús Lara*: no; only `slug:describe_raw` reveals `query_raw`.
- [x] Cap for in-memory pagination scans — *Owner: Jesús Lara*: `QS_DESCRIBE_MAX_SCAN`, default `10000`; when exceeded, process the first N rows in SQL order, set `X-Truncated: true`, log a warning. Never `400`.
- [x] Program matching case-sensitivity and `Program` object normalization — *Owner: Jesús Lara*: normalise both sides to lowercase (`program_slug` is lowercase by convention); Token-backend `Program` objects map to `.slug`, falling back to `.name` / `str()`.
- [x] Coordination with FEAT-176 (per-tenant queries) — *Owner: Jesús Lara*: include the tenant variant **now**: `GET /api/v1/{tenant}/queries/describe`, `/{tenant}/queries/{slug}/describe` and `/{tenant}/queries/{slug}/columns`, served by the same handler over the FEAT-176 `(schema, table)` data access, with the pre-filter deriving program context from the tenant. This makes FEAT-176's data-access layer a hard dependency (see Parallelism).
- [x] `policies/defaults.yaml` grant for `slug:describe` — *Owner: Jesús Lara*: admins/superuser only; regular users describe exactly the slugs they may execute (the `execute` fallback). Program-level policies may widen it.
