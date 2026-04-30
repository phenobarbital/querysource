# Brainstorm: PBAC Support in QuerySource

**Date**: 2026-04-29
**Author**: Jesus Lara (with Claude)
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

QuerySource currently runs every query without consulting any policy: any authenticated
caller of the HTTP handlers (`QueryExecutor`, `QueryService`, `QueryHandler`/multi-query) can
execute any registered slug, any raw query, against any registered datasource and driver.
The `DatasourceView` and `DatasourceDrivers` endpoints likewise return the full registry to
every user.

This is acceptable inside a tightly-controlled deployment but breaks down when the same
QuerySource process serves users with different privilege levels — e.g., engineers,
analysts, embedded service tokens, and report consumers — through `navigator-api`.

Two coupled problems:

1. **Authorization gap.** No mechanism filters what slugs/datasources/drivers a user can
   see or execute. Today the `querysource.conf` has two credential sets — `PG_*`
   (read-only) and `DB*` (full access) — but only the read-only one is exposed as a
   registered datasource, and there is no way to grant some users the full-access one.

2. **Per-user database identity.** Even within "read-only Postgres," operators want
   individual users to connect with their **own** database accounts (so DB-level audit and
   row-level security can attribute actions to the right person), with a fall-back to the
   default datasource credentials when no per-user account is configured.

`navigator-auth` already ships a production-ready PBAC engine (`PDP`, `Guardian`,
`PolicyEvaluator`, Rust-backed `rs_pep` extension) and `ai-parrot` already wires it in for
agent/tool filtering. QuerySource can reuse the same engine, mirror the same conventions,
and stay compatible with the rest of the suite.

**Who is affected.** End users (will see only the slugs/datasources/drivers they're
allowed), developers (declare YAML policies instead of hard-coding role checks), and ops
(centralize access rules and per-user DB credentials).

---

## Constraints & Requirements

- **Hard dependency on `navigator-auth`** (already an editable dep — bump pin to a version
  that exposes `Guardian.filter_resources()` and the `rs_pep` Rust evaluator). Use
  navigator-auth's PBAC engine **directly** — no parallel implementation.
- **Opt-in via setting.** A new `QS_PBAC_ENABLED` flag in `querysource.conf` toggles
  enforcement. Default: `False` (preserves current behavior for existing deployments).
- **Fail-closed when enabled.** With `QS_PBAC_ENABLED=True`, requests with no extractable
  session are denied by default; with `QS_PBAC_ENABLED=False`, they bypass.
- **Hide-existence on denial.** Failed checks return **HTTP 404** (not 403). Filter
  endpoints return shorter lists silently — the user never sees that denied resources
  exist.
- **Hard-deny on execution.** Execution endpoints (`QueryExecutor`, `QueryService`,
  `QueryHandler`/multi-query) reject the entire request on any policy violation.
  Superusers are handled by **a policy that grants them broad access**, not by a code
  bypass.
- **All-or-nothing for MultiQuery.** If a user is denied any of the slugs, files, or raw
  queries inside a `MultiQS`, the whole multi-query request is rejected.
- **Raw inline queries require explicit permission.** A separate
  `qs:raw_query:execute` action gates ad-hoc inline SQL/REST queries (no slug). Without
  it, only slug-based execution is allowed.
- **Policy authoring follows `ai-parrot`'s convention.** YAML files per resource type, in
  a directory configurable via `QS_POLICY_PATH` (default `BASE_DIR / 'policies'`). DB-backed
  policies are deferred to a follow-up spec.
- **Performance budget.** Zero measurable penalty in steady state. Reuse navigator-auth's
  built-in LRU + TTL decision cache; do not add a second cache layer in QuerySource.
- **Per-user credential resolution at the driver layer.** Two-level lookup:
  1. Per-user: `<PREFIX>_{USERNAME}_HOST/PORT/USER/PASSWORD/DATABASE` (env var override
     for one specific user).
  2. **Profile-from-policy:** policy decision may attach a `credential_profile` attribute;
     env vars looked up as `<PREFIX>_{PROFILE}_HOST/...`.
  3. Datasource default: each registered datasource declares its own env-var prefix
     (`postgres` → `PG_*`, `pg_admin` → `DB_*`).
  Username/profile sanitization: `value.upper().replace(".", "_").replace("-", "_").replace("@", "_")`.
  No group-tier env-var lookup — group precedence is handled inside the policy engine.
- **Service-token sessions** (no human user) carry `service: true` and are
  policy-evaluable like any other subject.
- **Backward compatibility.** With `QS_PBAC_ENABLED=False` the wiring must not run, no new
  imports may be required at startup, and zero behavioral change for existing deployments.
- **Resource-type taxonomy is additive in `navigator-auth`.** New `ResourceType` values
  (`SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`) must be added upstream — `navigator-auth` is
  under the same team's control, so this is acceptable (same pattern ai-parrot used to add
  `DATASET`).

---

## Options Explored

### Option A: Mirror `ai-parrot`'s PBAC pattern, add a driver-layer credential resolver

Follow ai-parrot's `parrot/auth/pbac.py` design point-for-point:

- New `querysource/auth/pbac.py` exposing `setup_pbac(app, policy_dir, cache_ttl)`,
  initializing `PDP` + `PolicyEvaluator` + `Guardian` and registering them on the aiohttp
  app under `app['security']`, `app['abac']`, `app['policy_evaluator']`. Invoked from
  `QuerySource.setup(app)` at `querysource/services.py:80` (after `connection.setup`
  and `TemplateParser.setup`, before handler route registrations), gated by
  `QS_PBAC_ENABLED`. Consuming apps get PBAC wired automatically — no extra call needed.
- New `AbstractHandler` helpers: `_get_user_session(request)` (extracts via
  `navigator_session.get_session()` and caches on `request['user_session']`) and
  `_enforce_pbac(request, resource_type, resource_name, action)` (calls
  `Guardian` / `PolicyEvaluator.check_access()`, raises `web.HTTPNotFound` on deny).
- Each consumer handler (`QueryExecutor`, `QueryService`, `QueryHandler`) calls
  `_enforce_pbac` explicitly with the right resource identifier built from the URL/payload.
  No decorators — resource names are dynamic (slug, datasource, driver) and decorators
  obscure that.
- `MultiQS` accepts the user session and runs a pre-flight policy check across every
  component (slug, file, raw query) before executing anything; first denial → reject the
  whole multi-query.
- `DatasourceView` and `DatasourceDrivers` use `Guardian.filter_resources()` to drop
  denied entries before responding.
- Per-user credentials live in a new `querysource/auth/credentials.py`
  (`CredentialResolver`), invoked from each driver's connection-params build path. Two
  lookup levels: per-user env override, then profile-from-policy, then datasource default.
- A new `pg_admin` datasource (file `querysource/datasources/drivers/pg_admin.py`) is
  registered alongside the existing `postgres` datasource. Each declares its own env-var
  prefix, so the "DB_* full / PG_* read-only" split is encoded in the datasource registry
  and gated by separate policies.
- Default policies ship inside `policies/` at the project root, mirroring ai-parrot's file
  layout: `defaults.yaml`, `slugs.yaml`, `datasources.yaml`, `drivers.yaml`,
  `raw_queries.yaml`, `superusers.yaml`.
- **navigator-auth upstream additions** (additive, non-breaking): new `ResourceType`
  values for `SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`, plus matching `ActionType`
  constants. This mirrors ai-parrot's `DATASET` precedent.

✅ **Pros:**
- Maximum compatibility with ai-parrot — operators authoring policies already know the
  format. A `superuser` policy that grants `tool:*` in ai-parrot translates directly into
  `slug:*` + `datasource:*` + `driver:*` for QuerySource.
- Reuses the **single** `PolicyEvaluator` instance per process (with built-in LRU + TTL),
  so the perf budget is met by reuse, not by inventing a new cache layer.
- Explicit `_enforce_pbac` calls in handlers keep resource-name extraction visible and
  trivially debuggable — important when a slug name comes from a URL match-info or a JSON
  payload.
- Per-user credential resolution is **bounded to the driver layer** and orthogonal to
  policy enforcement — they can be developed and tested independently.
- 404-on-denial is implemented in one place (`_enforce_pbac`), not duplicated per handler.
- Adding `pg_admin` as a separate datasource is a one-file addition, no driver rewrites.

❌ **Cons:**
- Touches many files (handlers, MultiQS, datasource view, driver-init paths) — broad
  surface area even though each touch is small.
- Requires upstream changes in `navigator-auth` (new `ResourceType` values). Coordinated
  release needed.
- Two enforcement code paths: explicit handler calls **and** the driver-layer credential
  resolver. The two must agree on what the user is authorized to do — the resolver must
  not silently override a policy decision.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `navigator-auth` | PBAC engine: `PDP`, `Guardian`, `PolicyEvaluator`, `rs_pep` Rust evaluator | Bump pin from `>=0.15.8` to a version exposing `Guardian.filter_resources()` (ai-parrot uses `>=0.19.0`); upstream PR adds new `ResourceType` values |
| `navigator-session` | Session extraction (`get_session(request)`) | Already used in `querysource/handlers/log.py:8` and `querysource/interfaces/queries.py:17–18` |
| `pyyaml` | YAML policy loading | Already a transitive dep via navigator-auth's `YAMLStorage` |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/abstract.py` — extend `AbstractHandler` with `_get_user_session` and `_enforce_pbac` helpers
- `querysource/interfaces/queries.py:165` — already implements `user_session(request)`; pattern to copy into the handler base
- `querysource/datasources/drivers/postgres.py` — template for `pg_admin.py`
- `querysource/datasources/drivers/__init__.py:29–114` — `SUPPORTED` registry, where new datasources register
- `querysource/datasources/handlers/datasource.py:33,74` — `default_sources()` and `DatasourceView.get()`, the filter point
- `querysource/queries/multi/__init__.py:53,102,140,156` — `MultiQS` and the loops over `_queries` and `_files`
- `parrot/auth/pbac.py` (ai-parrot, reference only) — `setup_pbac()` shape
- `parrot/auth/resolver.py` (ai-parrot, reference only) — `PBACPermissionResolver` pattern

---

### Option B: aiohttp middleware-driven enforcement + decorators

Use `navigator-auth`'s `abac_middleware` to enforce policies at the HTTP layer for every
request, plus a `@requires_qs_permission(resource_type, action, resource_name_param)`
decorator on individual handler methods to extract resource names from path params.

✅ **Pros:**
- More declarative — resource and action sit on the handler signature.
- Zero change to handler bodies; less surface area in the QS handlers themselves.

❌ **Cons:**
- The middleware enforces a **single** policy decision per request, but QuerySource
  handlers reference multiple resources per request (multi-query is the obvious one;
  even single-query touches a slug, a datasource, and a driver). The middleware can't
  see all of them.
- Slug names sometimes arrive in the JSON **body** (e.g., `QueryService` reads `slug`
  from match-info but `QueryHandler` reads slug + nested queries from body). A decorator
  that pulls the slug from `request.match_info` works for some endpoints and not others.
- `MultiQS`'s per-component check (the all-or-nothing rule) doesn't fit a single-decorator
  model — you'd need to call the engine in the body anyway.
- For `DatasourceView`/`DatasourceDrivers`, you need filter-not-deny semantics — a
  middleware/decorator that 404s on the whole list endpoint is wrong; you want the list
  filtered, not refused.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `navigator-auth` | `abac_middleware`, `requires_permission` decorator | Same version requirement as Option A |

🔗 **Existing Code to Reuse:**
- `navigator_auth.abac.middleware.abac_middleware`
- `navigator_auth.abac.decorators.requires_permission`

---

### Option C: Service-layer enforcement inside `Executor` / `QueryService` / `MultiQS`

Push policy enforcement **down** into the query layer — handlers just thread the session
through; the query objects (`Executor`, `MultiQS`, `BaseQuery`) themselves consult the
policy engine before resolving the slug/driver/datasource.

✅ **Pros:**
- Single enforcement layer, regardless of caller (HTTP handler, internal task, CLI).
- The resource names (slug, datasource, driver) are already known inside the query layer
  at the right moment — no payload re-parsing.

❌ **Cons:**
- Couples the query-execution layer to PBAC. Unit-testing query logic now needs PBAC
  fixtures (or a stub `Guardian`).
- Doesn't naturally cover `DatasourceView`/`DatasourceDrivers` — those are list endpoints
  that don't go through the query layer at all. Would need a parallel enforcement spot
  for those handlers, defeating the "single layer" claim.
- Background tasks and ETL jobs that use `Executor` directly would suddenly require a
  session-like object. This is a lot of breakage for QuerySource consumers.
- Returning 404-from-the-query-layer to the handler requires raising HTTP exceptions from
  non-HTTP code, which is awkward.

📊 **Effort:** High

📦 **Libraries / Tools:** Same as Option A.

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/__init__.py` — `MultiQS`
- `querysource/queries/qs.py` — `QS` base class

---

### Option D: Credential-resolver-first; policies as a thin visibility layer

Invert the priority: do per-user credential resolution at the driver layer (the
**hardest** part of this work) first, and rely on the **database's** own permission
system to enforce execution rights. Use PBAC only for visibility (`DatasourceView`,
`DatasourceDrivers`, slug catalog). No execution-time policy checks in handlers.

✅ **Pros:**
- Smallest blast radius. The database is already a policy engine — leveraging it is
  cheaper than duplicating its rules in YAML.
- Per-user DB accounts give native row-level / column-level security and DB audit logs.

❌ **Cons:**
- Doesn't deliver the user's stated requirements: slug-execution gating and raw-query
  gating cannot be expressed at the DB layer (a user with DB access could still hit any
  slug). The "explicit `raw_query` permission" requirement is impossible without an
  execution-time check.
- Cross-driver consistency is impossible — REST/Mongo/Cassandra/etc. drivers don't all
  have the same kind of permission system.
- The `pg_admin` vs `postgres` datasource split is meaningless if both end up at the same
  policy decision (they don't, because different DB accounts have different rights — but
  then the user's session must already carry distinct credentials, defeating the point).

📊 **Effort:** Low (smaller scope, but doesn't satisfy requirements)

📦 **Libraries / Tools:** Limited to credential resolver only; no PBAC engine wiring at
the handler layer.

🔗 **Existing Code to Reuse:** `querysource/datasources/drivers/pg.py` `params()` method
as the integration point.

---

## Recommendation

**Option A** is recommended.

It is the only option that delivers all the stated requirements: slug-level filtering,
per-handler hard-deny on execution, all-or-nothing multi-query enforcement, raw-query
gating with an explicit permission, list-endpoint silent filtering, and per-user
credential resolution. It also keeps QuerySource policy authoring **identical** to
ai-parrot's, so operators have one mental model across the suite.

The cost is breadth: many small touches across handlers, MultiQS, datasources, drivers,
and `conf.py`. The trade is acceptable because each touch is mechanically simple — call
the helper, return on deny — and the helper itself is centralized in
`querysource/auth/pbac.py` and `AbstractHandler`. The two enforcement points (handler
helpers + driver-layer credential resolver) are deliberately isolated from each other:
handlers say *can the user access this resource*; the resolver says *which credentials
should the driver use*. They share a session attribute (`credential_profile`) but
otherwise stay independent.

Option B fails on multi-resource requests and on filter-vs-deny semantics for list
endpoints. Option C breaks non-HTTP consumers of `Executor`/`MultiQS` and still requires
a parallel mechanism for list endpoints. Option D doesn't meet the explicit
requirements (slug gating, raw-query permission).

What we're trading off in (A): complexity of upstream coordination with `navigator-auth`
(new `ResourceType` values must land before QuerySource can pin to them). This is the
same coordination ai-parrot already does, so the muscle exists.

---

## Feature Description

### User-Facing Behavior

**HTTP API consumers (end users):**
- Authenticate via `navigator-auth` JWT, exactly as today. No new auth flow.
- With `QS_PBAC_ENABLED=False` (default in `querysource.conf`): nothing changes. Existing
  deployments continue to work.
- With `QS_PBAC_ENABLED=True`:
  - `GET/POST /query/{slug}` (handled by `QueryService`): if the user is denied
    `slug:execute` for that slug, the response is **HTTP 404** with no leak of the slug's
    existence.
  - `POST /query` (handled by `QueryExecutor`): if the body contains a raw inline query
    and the user lacks `raw_query:execute`, **404**. If it references a slug the user
    can't execute, **404**.
  - `POST /multiquery` (handled by `QueryHandler` / `MultiQS`): a pre-flight check
    enumerates every component (slugs in `queries`, files in `files`, any raw queries).
    If any one is denied, the whole multi-query returns **404** — no partial execution.
  - `GET /datasource` (handled by `DatasourceView`): the response list silently omits
    datasources the user can't see.
  - `GET /datasource/drivers` (handled by `DatasourceDrivers`): drivers the user can't
    see are silently omitted.
- Service tokens (sessions with `service: true`) are subject to the same rules and need
  matching policies.

**Operators / policy authors:**
- Place YAML policy files in `BASE_DIR / 'policies'` (override path with
  `QS_POLICY_PATH`):
  ```
  policies/
    defaults.yaml      # version + deny-by-default + admin allow
    slugs.yaml         # who can execute which slugs
    datasources.yaml   # which datasources are visible/usable
    drivers.yaml       # which drivers are usable
    raw_queries.yaml   # who can run raw inline queries
    superusers.yaml    # broad allows for service accounts and admins
  ```
- Schema and resource/action vocabulary are identical to ai-parrot's. Wildcards
  (`slug:fin_*`), priority, `enforcing: true` short-circuit, group/role/user `subjects`,
  and time-of-day `conditions` all carry over.
- A policy may attach `attributes: { credential_profile: <name> }` on its allow
  decision. When such a policy authorizes a user against a datasource, the driver-layer
  credential resolver looks for `<PREFIX>_<PROFILE>_*` env vars instead of the
  datasource's default prefix.
- Per-user credential overrides are operational config: set
  `PG_<USERNAME>_HOST/PORT/USER/PASSWORD/DATABASE` in the environment and that user's
  Postgres connections (against any Postgres-flavored datasource) use those values
  ahead of the default or profile.

**Developers:**
- A new helper on `AbstractHandler` makes the user session available to any subclass:
  ```
  session = await self._get_user_session(request)   # returns SessionData or None
  await self._enforce_pbac(request,
                          resource_type=ResourceType.SLUG,
                          resource_name=slug,
                          action="slug:execute")    # raises web.HTTPNotFound on deny
  ```
- Drivers gain a `params_for(session)` hook (in `pg.py` first, generalizable to other
  SQL drivers) that consults the credential resolver when a session is supplied.

### Internal Behavior

**Startup wiring (inside `QuerySource.setup(app)`):**
1. Consuming app instantiates and calls `QuerySource(lazy=False, loop=...).setup(app)`
   as today (see `querysource/services.py:80`).
2. Inside `QuerySource.setup`, after the existing `connection.setup(app)` and
   `TemplateParser.setup(app)` calls and **before** the route registrations for
   `QueryService` / `QueryExecutor` / `QueryHandler` / `QueryManager`, QS reads
   `QS_PBAC_ENABLED` from `querysource.conf`.
3. If `False`: skip PBAC setup entirely; `app['security']` is not registered;
   `_enforce_pbac` becomes a no-op fast-path. Existing deployments behave exactly as
   today.
4. If `True`: call `querysource.auth.pbac.setup_pbac(app, policy_dir=QS_POLICY_PATH,
   cache_ttl=QS_PBAC_CACHE_TTL)`. This:
   - Builds `YAMLStorage(policy_dir)`, `PolicyLoader.load_from_directory()`,
     `PolicyEvaluator(default_effect=DENY, cache_ttl_seconds=cache_ttl)`,
     `PDP(storage=...)`, and `Guardian(pdp)`.
   - Registers `app['security']` (Guardian), `app['abac']` (PDP),
     `app['policy_evaluator']` (PolicyEvaluator), and an `app['credential_resolver']`
     for the driver layer.
   - Idempotent on re-entry: if `app['security']` is already populated by a parent
     stack (e.g., `navigator-api` invoked navigator-auth's `PDP.setup(app)` first),
     QS reuses that instance instead of re-creating one.
   - Does **not** install the `abac_middleware`. QuerySource handlers enforce
     explicitly because resource extraction is dynamic (slug from URL, datasource
     from payload, etc.) and a single middleware decision per request is the wrong
     granularity. The consuming app may still install navigator-auth's middleware
     for its own routes — they coexist.

**Per-request (single-query — `QueryService` path):**
1. `QueryService.query()` extracts the slug from `request.match_info`.
2. Calls `await self._get_user_session(request)`, caches result in
   `request['user_session']`.
3. Calls `await self._enforce_pbac(request, ResourceType.SLUG, slug, "slug:execute")`.
4. On allow, the slug is resolved as today. The `Guardian` check has also recorded an
   audit entry (free, from navigator-auth).
5. Just before driver-instantiation in `QS.build_provider()`, an additional pair of
   checks runs against the resolved datasource and driver name:
   `_enforce_pbac(..., ResourceType.DATASOURCE, ds_name, "datasource:use")` and
   `_enforce_pbac(..., ResourceType.DRIVER, drv_name, "driver:use")`. These are
   typically already cached from the slug check.
6. The driver factory consults `app['credential_resolver']` with the session; the
   resolver returns connection params (per-user override → profile-from-policy →
   datasource default). The driver opens its connection with those params.

**Per-request (raw inline query — `QueryExecutor` path):**
1. `QueryExecutor.query()` parses the body. If a slug is present, follow the slug path
   above. If a raw query payload is present (no slug), call
   `_enforce_pbac(..., ResourceType.RAW_QUERY, "raw_query", "raw_query:execute")` first.
2. Then run the same datasource/driver checks before instantiating the driver.

**Per-request (multi-query — `QueryHandler` / `MultiQS` path):**
1. `QueryHandler.query()` parses the multi-query body, extracts the user session, and
   builds the dict of `_queries`, `_files`, plus any inline raw queries.
2. **Pre-flight loop:** before constructing/calling `MultiQS.query()`, the handler
   batches resource names by type and calls `Guardian.filter_resources()` once per type
   (slugs, files, raw_queries). If any single resource lands in `denied`, the handler
   returns **404** and never starts execution.
3. On allow-everything, `MultiQS.query()` proceeds as today. No change to the parallel
   thread-fan-out logic.

**Per-request (list endpoints — `DatasourceView`, `DatasourceDrivers`):**
1. The handler builds the candidate list as today (`default_sources()` for drivers,
   `DataSource.all()` + defaults for datasources).
2. Extracts a list of resource names.
3. Calls `Guardian.filter_resources(resources, request, resource_type, action)`.
4. Filters the candidate list to only those in `result.allowed`.
5. Returns the filtered list. The user has no way to tell denied entries existed.

**Driver-layer credential resolution (`CredentialResolver`):**
1. Driver subclass (e.g., `pgDriver.params_for(session)`) calls
   `app['credential_resolver'].resolve(driver_class, session, datasource_name)`.
2. Resolver canonicalizes the username (`upper().replace(".", "_").replace("-", "_").replace("@", "_")`).
3. Lookup order:
   1. `<PREFIX>_<USERNAME>_HOST/PORT/USER/PASSWORD/DATABASE` (full set must be present;
      partial sets fall through).
   2. If a `credential_profile` attribute is on the session's last allow decision, try
      `<PREFIX>_<PROFILE>_HOST/...`.
   3. Datasource-default env vars (the prefix declared by the registered datasource:
      `PG_*` for `postgres`, `DB_*` for `pg_admin`).
4. Resolver returns a dict; driver `params_for` returns it to AsyncDB.

### Edge Cases & Error Handling

- **`QS_PBAC_ENABLED=False`, no session attached.** `_get_user_session` returns `None`;
  `_enforce_pbac` is a no-op. Behavior identical to today.
- **`QS_PBAC_ENABLED=True`, no session attached.** `_get_user_session` returns `None`;
  `_enforce_pbac` denies → **404**.
- **Session present but `userinfo` empty (anonymous-like).** `EvalContext` builds with
  empty groups/roles. Only policies with `subjects.groups: ["*"]` (or anonymous-targeted)
  match. Most likely → deny → **404**.
- **Session has `service: true`.** Treated as a normal subject; matching policies must
  exist for service tokens (e.g., `superusers.yaml` granting service accounts).
- **Slug doesn't exist.** Return **404** as today (the existing path already produces a
  not-found). Note: this means denied slugs and missing slugs are indistinguishable —
  intentional, satisfies the "hide existence" requirement.
- **Multi-query with one denied slug among 10 allowed.** Pre-flight check returns
  `denied=[that_slug]`; handler returns **404** before any thread starts. No partial
  execution.
- **Per-user credential lookup with partial env vars** (e.g., `PG_JOHN_HOST` set,
  but `PG_JOHN_PASSWORD` missing). Resolver treats partial sets as "no override," falls
  through to profile/default. Operators get a warning logged once per missing key per
  process.
- **Username with non-ASCII characters.** Sanitization keeps only `[A-Z0-9_]`; non-matching
  chars become `_`. Documented in the auth/credentials module docstring.
- **`pg_admin` datasource registered but `DB_*` env vars unset.** The datasource appears
  in the registry (until the candidate list is filtered) but driver instantiation fails
  with a clear "credentials not configured" error. Mitigation: a small startup check that
  warns when a registered datasource has no resolvable default credentials.
- **Policy file YAML parse error.** `YAMLStorage` logs and skips the offending file (this
  is navigator-auth's existing behavior). Other policies still load. Operators see a
  startup warning.
- **`navigator-auth` not installed / version too old.** With `QS_PBAC_ENABLED=False`,
  the import is never attempted (lazy import inside `setup_pbac`). With `True`, the import
  fails fast at startup with a clear message — fail-closed.
- **Cache invalidation when a user's policy assignment changes mid-session.** Out of
  scope for v1: navigator-auth's evaluator cache TTL bounds staleness. A
  `POST /api/v1/abac/reload` endpoint already exists in navigator-auth (admin-only) and
  clears the cache.
- **Anonymous read-only health-check endpoints in QuerySource.** Are not enforced by
  PBAC (they are not under `QueryExecutor`/`QueryService`/`QueryHandler`).

---

## Capabilities

### New Capabilities
- `pbac-bootstrap` — `querysource.auth.pbac.setup_pbac()` invoked from
  `QuerySource.setup(app)` at `querysource/services.py:80`, gated by `QS_PBAC_ENABLED`.
  Adds settings (`QS_PBAC_ENABLED`, `QS_POLICY_PATH`, `QS_PBAC_CACHE_TTL`) and registers
  `app['security']` (Guardian) / `app['abac']` (PDP) / `app['policy_evaluator']` /
  `app['credential_resolver']`.
- `pbac-handler-helpers` — `AbstractHandler._get_user_session` and
  `AbstractHandler._enforce_pbac`; consistent 404-on-deny semantics.
- `pbac-query-execution-gate` — slug-execute, raw-query-execute, datasource-use, and
  driver-use checks wired into `QueryExecutor` and `QueryService`.
- `pbac-multiquery-preflight` — pre-execution all-or-nothing policy check for `MultiQS`
  components (slugs, files, raw queries).
- `pbac-datasource-filtering` — `Guardian.filter_resources()` integration in
  `DatasourceView` (datasources) and `DatasourceDrivers` (drivers).
- `pbac-default-policies` — default YAML policy files shipped under `policies/`.
- `pbac-credential-resolver` — `CredentialResolver` with per-user override + profile-from-
  policy + datasource-default lookup, surfaced through a driver `params_for(session)` hook.
- `pbac-pg-admin-datasource` — new `pg_admin` registered datasource using `DB_*` env
  prefix; `postgres` continues to use `PG_*`.

### Modified Capabilities
- `query-execution-handlers` (executor + service) — gain pre-execution PBAC checks.
- `multi-query-handler` — gains pre-execution all-or-nothing PBAC check.
- `datasource-listing` — gains visibility filtering.
- `database-driver-instantiation` (Postgres first; pattern for other SQL drivers) —
  gains optional `params_for(session)` path.

### Upstream additions to `navigator-auth` (not capabilities here, but required)
- New `ResourceType` enum values: `SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`.
- Matching `ActionType` constants:
  `SLUG_EXECUTE = "slug:execute"`, `SLUG_LIST = "slug:list"`,
  `DATASOURCE_USE = "datasource:use"`, `DATASOURCE_LIST = "datasource:list"`,
  `DRIVER_USE = "driver:use"`, `DRIVER_LIST = "driver:list"`,
  `RAW_QUERY_EXECUTE = "raw_query:execute"`.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/conf.py` | extends | Add `QS_PBAC_ENABLED`, `QS_POLICY_PATH`, `QS_PBAC_CACHE_TTL` |
| `querysource/services.py` | modifies | `QuerySource.setup(app)` calls `setup_pbac(app, ...)` after `connection.setup` and before route registrations, gated by `QS_PBAC_ENABLED` |
| `querysource/auth/__init__.py` | new | Module entry point |
| `querysource/auth/pbac.py` | new | `setup_pbac()`, lazy imports of navigator-auth |
| `querysource/auth/credentials.py` | new | `CredentialResolver` |
| `querysource/handlers/abstract.py` | extends | `_get_user_session`, `_enforce_pbac` |
| `querysource/handlers/executor.py` | modifies | Calls `_enforce_pbac` for slug/raw-query, datasource, driver |
| `querysource/handlers/service.py` | modifies | Calls `_enforce_pbac` for slug, datasource, driver |
| `querysource/handlers/multi.py` | modifies | Pre-flight `Guardian.filter_resources()` for all components |
| `querysource/queries/multi/__init__.py` | modifies | Accepts session in constructor; defers enforcement to handler pre-flight |
| `querysource/datasources/handlers/datasource.py` | modifies | `DatasourceView.get` and `DatasourceDrivers` use `Guardian.filter_resources()` |
| `querysource/datasources/drivers/postgres.py` | modifies | `params_for(session)` hook |
| `querysource/datasources/drivers/pg.py` | modifies | `params_for(session)` hook (the actual params-build logic) |
| `querysource/datasources/drivers/pg_admin.py` | new | `pg_admin` datasource declaration; uses `DB_*` env prefix |
| `querysource/datasources/drivers/__init__.py` | modifies | Register `pg_admin` in `SUPPORTED` |
| `policies/*.yaml` | new | `defaults.yaml`, `slugs.yaml`, `datasources.yaml`, `drivers.yaml`, `raw_queries.yaml`, `superusers.yaml` |
| `pyproject.toml` | modifies | Bump `navigator-auth` pin (e.g., `>=0.19.0`) |
| `tests/test_pbac.py` (or similar) | new | Integration tests with aiohttp test client + sample policies |
| `navigator-auth` (upstream) | extends | Add 4 `ResourceType` values, 7 `ActionType` constants |

**Breaking changes:** None for existing deployments (default `QS_PBAC_ENABLED=False`).
The new `pg_admin` datasource is additive — existing deployments without `DB_*` env vars
will see it fail to register (or be filtered out at runtime), with no impact on
`postgres`. Existing `navigator-auth` consumers of `ResourceType` are not affected by
new enum values.

---

## Code Context

### User-Provided Code

The user invoked the brainstorm with a scope description and answered four rounds of
discovery questions. No code snippets were pasted; all references below come from
codebase inspection.

### Verified Codebase References

#### `navigator-auth` — PBAC engine surface

```python
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/pdp.py:39
class PDP:
    def __init__(
        self,
        storage: AbstractStorage,
        policies: Optional[List[Policy]] = None,
        yaml_storage: Optional[YAMLStorage] = None,
    ): ...
    async def on_startup(self, app: web.Application): ...
    async def reload_policies(self) -> int: ...
    async def authorize(
        self,
        request: web.Request,
        session: SessionData = None,
        user: Any = None,
        effect: PolicyEffect = PolicyEffect.ALLOW,
    ): ...
    def setup(self, app: web.Application): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/guardian.py:16
class Guardian:
    def __init__(self, pdp: Callable): ...
    async def authorize(self, request: web.Request): ...
    async def is_allowed(self, request: web.Request, **kwargs): ...
    async def filter_resources(
        self,
        resources: List[str],
        request: web.Request,
        resource_type: ResourceType = ResourceType.TOOL,
        action: str = "tool:execute",
    ) -> "FilteredResources": ...
    async def filter_files(
        self, files: List[str], request: web.Request,
    ): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/evaluator.py:361
class PolicyEvaluator:
    def __init__(self, cache_size: int = 1024, cache_ttl_seconds: int = 300): ...
    def check_access(
        self,
        ctx: EvalContext,
        resource_type: ResourceType,
        resource_name: str,
        action: str,
        env: Environment = None,
        owner_reports_to: str = None,
    ) -> EvaluationResult: ...
    def filter_resources(
        self,
        ctx: EvalContext,
        resource_type: ResourceType,
        resource_names: List[str],
        action: str,
        env: Environment = None,
    ) -> FilteredResources: ...
    def invalidate_cache(self, user_id: str = None) -> None: ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/context.py:6
class EvalContext(dict, MutableMapping):
    def __init__(
        self,
        request: web.Request,
        user: Any,
        userinfo: Any,
        session: Any,
        *args, **kwargs,
    ): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/resources.py:15
class ResourceType(Enum):
    TOOL = "tool"
    KB = "kb"
    VECTOR = "vector"
    AGENT = "agent"
    MCP = "mcp"
    URI = "uri"
    DATASET = "dataset"
    WIDGET = "widget"
    CARD = "card"
    # *** NEW upstream additions required by this spec: ***
    # SLUG = "slug"
    # DATASOURCE = "datasource"
    # DRIVER = "driver"
    # RAW_QUERY = "raw_query"

# Rust evaluator entry points (PyO3-exposed):
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/rs_pep/src/lib.rs:502
# fn evaluate_single(...) -> PyResult<PyObject>  # returns {"allowed", "effect", "matched_policy", "reason"}
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/rs_pep/src/lib.rs:396
# fn filter_resources_batch(...) -> PyResult<PyObject>  # returns {"allowed": [...], "denied": [...]}
```

Verified `__init__.py` re-exports:
```python
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/__init__.py
from .auth import AuthHandler
from navigator_auth import rs_pep
__all__ = ("AuthHandler", "rs_pep")
```

Sample real policy file (verbatim) — to mirror format:
```yaml
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/default_policies/admin_full_access.yaml
version: "1.0"
defaults:
  effect: deny
policies:
  - name: admin_full_access
    effect: allow
    description: "Superuser and admin groups have unrestricted access..."
    resources:
      - "tool:*"
      - "kb:*"
      - "agent:*"
    actions:
      - "tool:execute"
      - "tool:list"
      - "agent:chat"
    subjects:
      groups:
        - superuser
        - admin
    priority: 100
    enforcing: true
```

#### `ai-parrot` — reference for the `setup_pbac` shape

```python
# /home/jesuslara/proyectos/navigator/ai-parrot/packages/ai-parrot/src/parrot/auth/pbac.py:35
def setup_pbac(
    app: web.Application,
    policy_dir: str = "policies",
    cache_ttl: int = 30,
    default_effect: Optional[object] = None,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    ...

# /home/jesuslara/proyectos/navigator/ai-parrot/packages/ai-parrot/src/parrot/handlers/agent.py:378
async def _build_eval_context(self) -> Any:
    """Build an EvalContext from the current request session."""
    from navigator_auth.abac.context import EvalContext
    from navigator_auth.conf import AUTH_SESSION_OBJECT
    session = self.request.session if hasattr(self.request, 'session') else None
    if session is None:
        session = await get_session(self.request)
    if session is None:
        return None
    userinfo = session.get(AUTH_SESSION_OBJECT, {}) if hasattr(session, 'get') else {}
    user = session.decode('user') if hasattr(session, 'decode') else None
    if user is None and isinstance(userinfo, dict) and userinfo:
        user = userinfo
    return EvalContext(request=self.request, user=user, userinfo=userinfo, session=session)
```

ai-parrot policy directory — file layout to mirror:
```
/home/jesuslara/proyectos/navigator/ai-parrot/policies/
  defaults.yaml         # baseline deny + admin allow
  agents.yaml           # agent access (agent:chat, agent:configure)
  tools.yaml            # tool visibility/execution (tool:execute, tool:list)
  mcp.yaml              # MCP server access
  superusers.yaml       # per-user codebase owner exemptions
  agents/.gitkeep       # subdirectory for per-agent policies
```

ai-parrot config settings:
```python
# /home/jesuslara/proyectos/navigator/ai-parrot/app.py:233
policy_dir = self.app.get('policy_dir') or config.get('POLICY_DIR', fallback='policies')
# /home/jesuslara/proyectos/navigator/ai-parrot/app.py:237
cache_ttl = int(config.get('PBAC_CACHE_TTL', fallback=30))
```

#### `querysource` — integration points

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/services.py:45
class QuerySource(metaclass=Singleton):
    def __init__(self, **kwargs):                                       # line 60
        self.lazy: bool = kwargs.get('lazy', False)
        self._loop: asyncio.AbstractEventLoop = kwargs.get('loop', asyncio.get_event_loop())
        self.connection = QueryConnection(loop=self._loop, lazy=self.lazy)
        # ...iterates providers, filters, variables...
    def setup(self, app: web.Application) -> web.Application:           # line 80
        # registers QueryService, QueryExecutor, QueryHandler, QueryManager,
        # LoggingService routes. PBAC bootstrap is added here, after
        # connection.setup(app) and TemplateParser.setup(app), before route
        # registrations, gated by QS_PBAC_ENABLED.

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/abstract.py:18
class AbstractHandler(BaseHandler):  # BaseHandler from navigator.views
    def post_init(self, *args, **kwargs):
        self.logger = logging.getLogger('QS.Handler')
        self._lasterr = None
        self.slug: str = None
        self._compression: str = None
        self._columns: list = []
        self.debug: bool = DEBUG

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/executor.py:19
class QueryExecutor(AbstractHandler):
    async def get_payload(self, request: web.Request) -> dict: ...   # line 29
    def get_executor(self, data, request: web.Request) -> Executor: ...  # line 42
    async def query(self, request): ...     # line 51 — main entry
    async def dry_run(self, request: web.Request = None): ...  # line 104

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/service.py:31
class QueryService(AbstractHandler):
    async def query(self, request):  # line 134
        # ...
        slug: str = args['slug']  # line 173 — slug name read here

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/multi.py:22
class QueryHandler(AbstractHandler):
    async def query(self, request: web.Request) -> web.StreamResponse:  # line 32
        # ...
        qs = MultiQS(slug=slug, queries=_queries, files=_files,
                     query=options, conditions=data)  # ~line 129
        result, options = await qs.query()  # line 137

# /home/jesuslara/proyectos/parallel/querysource/querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(
        self,
        slug: str = None,
        queries: Optional[list] = None,
        files: Optional[list] = None,
        query: Optional[dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        **kwargs,
    ): ...
    async def query(self):  # line 102
        # iterates self._queries (line 140) and self._files (line 156)

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/handlers/datasource.py:23
class DatasourceView(BaseView):
    def default_sources(self) -> list: ...  # line 33 — built-in datasources from SUPPORTED registry
    async def get(self) -> web.Response:    # line 74 — list endpoint
        # result = await DataSource.all(fields=fields)            # line 123
        # result = result + default                               # line 130
        # return self.json_response(response=result, ...)         # line 131

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/__init__.py:29
SUPPORTED = {
    "postgres": {...}, "mysql": {...}, "oracle": {...}, ...   # 42 entries
}

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/postgres.py
class postgresDriver(pgDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    defaults: str = asyncpg_url
postgres_default = postgresDriver(
    dsn=asyncpg_url,
    host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
    user=PG_USER, password=PG_PWD,
)

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/pg.py:17
class pgDriver(SQLDriver):
    def params(self) -> dict:  # line 42 — currently returns hardcoded self.* attrs
        return {
            "host": self.host, "port": self.port,
            "username": self.user, "password": self.password,
            "database": self.database,
        }

# /home/jesuslara/proyectos/parallel/querysource/querysource/conf.py
from navconfig import BASE_DIR, config  # line 5
# Read-only DB credentials:
PG_HOST = config.get('PG_HOST', fallback='localhost')   # line 38
PG_USER = config.get('PG_USER')                          # line 39
PG_PWD  = config.get('PG_PWD')                           # line 40
PG_DATABASE = config.get('PG_DATABASE', fallback='navigator')  # line 41
PG_PORT = config.get('PG_PORT', fallback=5432)          # line 42
asyncpg_url = f'postgres://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'  # line 44
# Full-access DB credentials:
DBHOST = config.get('DBHOST', fallback='localhost')      # line 24
DBUSER = config.get('DBUSER')                            # line 25
DBPWD  = config.get('DBPWD')                             # line 26
DBNAME = config.get('DBNAME', fallback='navigator')      # line 27
DBPORT = config.get('DBPORT', fallback=5432)             # line 28
default_dsn = f'postgres://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'  # line 32
```

Existing session usage in QuerySource (pattern to extend):
```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/log.py:8
from navigator_session import get_session
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/log.py:71
return await get_session(request)

# /home/jesuslara/proyectos/parallel/querysource/querysource/interfaces/queries.py:17
from navigator_session import get_session
from navigator_session import SessionData
# /home/jesuslara/proyectos/parallel/querysource/querysource/interfaces/queries.py:165
async def user_session(self, request: web.Request = None) -> SessionData:
    if not request:
        return None
    try:
        session = await get_session(request, new=False)   # line 174
    except RuntimeError:
        self._logger.error('QS: User Session system is not installed.')
        return None
    return session
```

Dependency declaration:
```toml
# /home/jesuslara/proyectos/parallel/querysource/pyproject.toml:114
"navigator-auth>=0.15.8",
# line 198 (editable local override):
navigator-auth = { path = "../../navigator/navigator-auth", editable = true }
```

#### Verified Imports

```python
# Confirmed working imports for QuerySource integration:
from navigator_session import get_session                                          # already in querysource/handlers/log.py
from navigator_session import SessionData                                          # already in querysource/interfaces/queries.py
from navigator_auth.abac.pdp import PDP                                            # navigator-auth, used by ai-parrot
from navigator_auth.abac.guardian import Guardian                                  # navigator-auth
from navigator_auth.abac.policies.evaluator import PolicyEvaluator, PolicyLoader   # navigator-auth, used by ai-parrot
from navigator_auth.abac.policies.abstract import PolicyEffect                     # navigator-auth, used by ai-parrot
from navigator_auth.abac.storages.yaml_storage import YAMLStorage                  # navigator-auth, used by ai-parrot
from navigator_auth.abac.policies.resources import ResourceType, ActionType        # navigator-auth
from navigator_auth.abac.policies.environment import Environment                   # navigator-auth
from navigator_auth.abac.context import EvalContext                                # navigator-auth
from navigator_auth.conf import AUTH_SESSION_OBJECT                                # navigator-auth
from navigator_auth import rs_pep                                                  # navigator-auth Rust extension
```

#### Key Attributes & Constants

- `ResourceType` enum currently exposes: `TOOL`, `KB`, `VECTOR`, `AGENT`, `MCP`, `URI`,
  `DATASET`, `WIDGET`, `CARD` (see resources.py:15–26). The new values for QuerySource
  must be **added upstream** — see "Does NOT Exist" below.
- `Guardian.filter_resources()` returns `FilteredResources(allowed: List[str],
  denied: List[str], policies_applied: List[str])`. Verify field names against the
  current navigator-auth version before consumption.
- `PolicyEvaluator` defaults: `cache_size=1024`, `cache_ttl_seconds=300`. ai-parrot
  overrides to 30s for time-of-day policies. QuerySource should default to 300s and
  expose `QS_PBAC_CACHE_TTL`.
- After `pdp.setup(app)`: `app['security']` → Guardian, `app['abac']` → PDP,
  `app['policy_evaluator']` → PolicyEvaluator.
- aiohttp request session conventions: ai-parrot reads `request.session` if present and
  falls back to `await get_session(request)`. QuerySource has no `request.session`
  convention today; use `request['user_session']` as a per-request memoization key.

### Does NOT Exist (Anti-Hallucination)

- ~~`ResourceType.SLUG`~~ — not in navigator-auth's enum at the time of writing. Must
  be **added upstream** as `SLUG = "slug"`.
- ~~`ResourceType.DATASOURCE`~~ — not present. Must be added as `DATASOURCE = "datasource"`.
- ~~`ResourceType.DRIVER`~~ — not present. Must be added as `DRIVER = "driver"`.
- ~~`ResourceType.RAW_QUERY`~~ — not present. Must be added as `RAW_QUERY = "raw_query"`.
- ~~`ActionType.SLUG_EXECUTE`~~, `SLUG_LIST`, `DATASOURCE_USE`, `DATASOURCE_LIST`,
  `DRIVER_USE`, `DRIVER_LIST`, `RAW_QUERY_EXECUTE` — none of these exist yet. They are
  the new constants to add upstream alongside the new ResourceType values.
- ~~`AbstractHandler._get_user_session`~~ / ~~`AbstractHandler._enforce_pbac`~~ — do not
  exist; they are introduced by this spec. The closest existing helper is
  `AbstractQuery.user_session()` at `querysource/interfaces/queries.py:165`, but that
  lives on a *query interface*, not the *handler base*.
- ~~`pg_admin` datasource~~ — not registered in `SUPPORTED`. Must be added in
  `querysource/datasources/drivers/__init__.py` and a new
  `querysource/datasources/drivers/pg_admin.py` file created.
- ~~`pgDriver.params_for(session)`~~ — does not exist. The current
  `pgDriver.params()` (pg.py:42) takes no args. The new hook is added by this spec.
- ~~`request.session`~~ — not a convention in QuerySource today (it is used by ai-parrot,
  via the auth-handler stack). Do not assume it; use `await get_session(request)` plus
  caching on `request['user_session']`.
- ~~`querysource.auth` package~~ — does not exist; introduced by this spec.
- ~~`Guardian.filter_tools` / `Guardian.filter_datasets`~~ — referenced in the older
  ai-parrot brainstorm, but the **actual current method** is the generic
  `Guardian.filter_resources(resources, request, resource_type, action)`. Use the
  generic API.
- ~~Per-group env var lookup (e.g., `PG_{GROUP}_HOST`)~~ — explicitly out of scope; the
  resolution order has only per-user → profile-from-policy → datasource-default. Group
  precedence is handled inside the policy engine.

---

## Parallelism Assessment

- **Internal parallelism:** Mixed.
  - **Stream 1 (`navigator-auth` upstream):** add 4 new `ResourceType` values + 7
    `ActionType` constants. Small, additive PR. Must land first (or at least be available
    in the editable local checkout) before QuerySource can import them.
  - **Stream 2 (querysource — bootstrap & helpers):** new `querysource/auth/` package,
    `setup_pbac()`, `AbstractHandler` helpers, settings. Independent of the handler
    edits.
  - **Stream 3 (querysource — handler integration):** edits to `executor.py`,
    `service.py`, `multi.py`, `MultiQS`. These touch overlapping but distinct files;
    can be parallel-friendly with care.
  - **Stream 4 (querysource — datasource/driver):** `pg_admin` registration,
    `params_for(session)` hook, `CredentialResolver`. Independent of handler streams.
  - **Stream 5 (querysource — list filtering):** `DatasourceView` /
    `DatasourceDrivers` `Guardian.filter_resources()` integration. Touches one file.
- **Cross-feature independence:** No conflicts with in-flight specs in
  `sdd/proposals/`. The two existing `policy-based-access-control.brainstorm.md` and
  `user-based-credentials.brainstorm.md` documents are **for ai-parrot**, not
  QuerySource — they share concepts and YAML conventions but no code. The
  `pyproject.toml` and `conf.py` edits are tiny enough that they're trivial to rebase.
- **Recommended isolation:** **mixed.**
  - Streams 2, 4, and 5 (bootstrap/helpers; driver-layer; list filtering) each work in
    their own isolated areas and could ship in parallel worktrees.
  - Stream 3 (handler integration) should be a single sequential effort because the
    three handlers (`executor`, `service`, `multi`) share patterns and tests; doing
    them in parallel risks divergence.
  - Stream 1 (navigator-auth upstream) is its own repo PR.
- **Rationale:** the feature has clear horizontal seams (auth bootstrap, drivers, list
  endpoints) and one vertical seam (the three execution handlers). Parallelizing the
  horizontal seams gains real time; parallelizing the vertical seam loses consistency
  across the three handlers, which must implement the exact same enforcement contract.

---

## Open Questions

- [x] ~~Exact `navigator-auth` version pin~~ — **Resolved**: pin to
      `navigator-auth>=0.20.0` in `pyproject.toml`. The upstream PR adding the new
      `ResourceType`/`ActionType` values targets that version.
- [x] ~~Where is `setup_pbac()` invoked?~~ — **Resolved**: QuerySource invokes it
      itself. QuerySource (the library) is distinct from any aiohttp integration —
      its central startup hook is `QuerySource.setup(app)` at
      `querysource/services.py:80` (a `Singleton`-pattern class instantiated as
      `QuerySource(lazy=False, loop=...).setup(app)`). PBAC bootstrap is called from
      inside that method, after `connection.setup(app)` and `TemplateParser.setup(app)`
      and before the handler route registrations, so any consuming app gets PBAC
      wired automatically when `QS_PBAC_ENABLED=True`. The consuming app does not
      need to call `setup_pbac()` itself.
- [x] For non-Postgres drivers (MySQL, Oracle, BigQuery, REST, Mongo, etc.), do we
      ship the `params_for(session)` credential-resolver hook in **this** spec, or
      apply it only to Postgres in v1 and roll out per driver afterwards? — *Owner:
      Jesus Lara*.: resolve on this spec.
- [x] Confirm the env-var prefix convention for the credential resolver. The proposal
      uses `<DATASOURCE_PREFIX>_<USERNAME>_*` where prefix comes from the registered
      datasource (e.g., `PG_*` for `postgres` → user override is `PG_<USER>_*`). For
      `pg_admin` whose default prefix is `DB_*`, the user override would be
      `DB_<USER>_*`. Acceptable? — *Owner: Jesus Lara*.: Acceptable
- [x] How are admin operations in `DatasourceView` (POST/PUT/DELETE — not just GET)
      gated? In scope for this spec or covered separately? The current brainstorm only
      addresses the `GET` list filtering — *Owner: Jesus Lara*.: covered separately.
- [x] Default policy content: should `defaults.yaml` ship a hard `deny` baseline
      (forcing operators to add their own allow policies before any user can run any
      slug), or include a permissive "any authenticated user → execute non-sensitive
      slugs" policy? Hard deny is safer; permissive is friendlier for upgrade paths —
      *Owner: Jesus Lara*.: for current v1 a permissive "any authenticated user → execute non-sensitive
      slugs"
- [x] Should the PBAC enforcement layer also gate the existing `dry_run` endpoint
      (`QueryExecutor.dry_run` at executor.py:104)? It does not execute the query but
      does parse and validate it; an attacker could use it to probe slug existence. — *Owner: Jesus Lara*.: enforce also dry_run-
