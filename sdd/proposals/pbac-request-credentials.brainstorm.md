---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [querysource, auth, handlers, multiquery]
tags: [pbac, authorization, principal, tenancy, library-api]
---

# Brainstorm: PBAC for Request-less (Programmatic) QS Callers

**Date**: 2026-09-24
**Author**: Jesus Lara / Claude
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

QuerySource enforces PBAC **only in the aiohttp handlers**
(`AbstractHandler._enforce_pbac` / `_enforce_owned_slug`,
`querysource/handlers/abstract.py:323,463`). `QS` itself never evaluates a
policy. When `QS` is constructed with `request=None`, `build_provider()` skips
the session/app extraction (`querysource/queries/qs.py:147-165`) and the slug
runs with the trusted service credentials and **no authorization at all**.
FEAT-147 kept this on purpose ("PBAC-disabled execution remains supported.
Programmatic/scheduled/worker calls preserve their existing trusted-service
credential model", `sdd/specs/per-tenant-queries.spec.md:235-237`).

That model breaks when a library caller runs a slug **on behalf of a user**.
The main case is ai-parrot:

- `QuerySlugSource.fetch` / `prefetch_schema` call
  `QS(slug=..., conditions=...)` with no request and no tenant
  (`ai-parrot/packages/ai-parrot/src/parrot/tools/dataset_manager/sources/query_slug.py:110,150`).
- The UI-surface `_refresh` (ai-parrot FEAT-535) replays a stored surface as
  its owner using `build_principal_context(record.user_id, channel="ui_surfaces")`.
  That helper defaults `tenant_id` to the principal (`auth/permission.py:188-202`),
  and `record.tenant` is ignored.

The result is that a user who could not run slug X over HTTP can get X's rows
through an agent tool or a surface refresh. Nothing tells QuerySource which
user is asking, so no slug policy is evaluated.

**Affected:** operators who rely on PBAC slug policies (the policies are silently
bypassed through library callers), and ai-parrot developers (who have no
supported way to say "run this as user U").

## Constraints & Requirements

- **Opt-in and backward compatible.** With no principal, behaviour is exactly as
  today: scheduler jobs (`querysource/scheduler/jobs.py:87,144,190`), workers
  and existing library callers keep trusted-service execution with no PBAC.
- **Credentials stay trusted-service.** This feature authorizes; it does not switch
  to per-user datasource credentials (`pgDriver.params_for`, FEAT-091). *(Round 1 decision.)*
- **Identity is an explicit argument** on `QS` / `MultiQS`. It is not a synthetic
  request and not a contextvar. *(Round 1 decision.)*
- **The evaluator comes from QuerySource's own singleton state.** A library caller
  does not need to hold the aiohttp app. *(Round 2 decision.)*
- **PBAC not configured means no-op**, logged at debug, mirroring the handler
  fast path (`abstract.py:346-348`). *(Round 2 decision.)*
- **`tenant=` stays the only store-routing key.** The principal's tenant is carried
  into the evaluation context and logs only. No membership check is introduced
  (FEAT-147 L225-226). *(Round 2 decision.)*
- **Numeric auth tenants are never inferred from schema tenant names** (FEAT-147
  L235). The principal's string tenant must not be copied into the EvalContext
  `org_id`/`client_id`.
- **Tenant-isolated decisions:** each check uses a detached evaluator copy with a
  fresh `_cache` (the same rule as `_enforce_owned_slug`, FEAT-147 L227-234). The
  app evaluator's cache, TTL or policy index is never mutated.
- **Deny before any data or cache access.** The gate runs inside `build_provider()`,
  which `query()` calls before the result-cache lookup (`qs.py:384` vs `:395`).
- **MultiQS:** every stored child is checked before any child executes (FEAT-147
  no-partial-batch rule). *(Round 3 decision.)*
- **Denial raises a dedicated `QueryAccessDenied(QueryException)`.** Its message
  never reveals slug existence to end users. *(Round 3 decision.)*
- **One evaluation core:** handlers and `slug_visibility` delegate to the same
  request-optional helper, and existing handler tests pin their behaviour.
  *(Round 3 decision.)*
- Async, no blocking I/O. navigator-auth stays lazily imported (never at module
  import time when `QS_PBAC_ENABLED=False`, `auth/pbac.py:6-9`).

---

## Options Explored

### Option A: Request-optional evaluation core + `QSPrincipal` kwarg

Add a small, QuerySource-owned, frozen dataclass `QSPrincipal`. It has user id,
username, groups, roles, programs, a superuser flag, optional numeric
`org_id`/`client_id`, an informational `tenant_id` and a `channel`. `QS` and
`MultiQS` accept it as a keyword-only `principal=` argument.

Extract one **request-optional PBAC evaluation core** in `querysource/auth/`.
It takes a normalized identity (userinfo, user, session), an optional
request, a resource type/name/action and a "detached" flag. It returns an
allow/deny decision with the matched policy and reason. The core works out
evaluator availability, builds the `EvalContext` (with a request-free
stand-in when no request exists), makes the detached copy and awaits a
coroutine result defensively.

- `_enforce_pbac`, `_enforce_owned_slug` and `slug_visibility.can_access` /
  `filter_visible` delegate to the core and keep their current raise/return
  contracts.
- `QS.build_provider()` calls the core after the definition loads and before
  `get_provider()`: `slug:execute` for slugs, `raw_query:execute` for raw
  queries.
- `MultiQS.query()` calls it inside its existing child pre-flight loop
  (`multi/__init__.py:288-336`).
- The evaluator is found through the QuerySource singleton (the app it was
  `setup()` on, `services.py:146-150`) or a module-level runtime registered
  by `setup_pbac()`.

✅ **Pros:**
- Closes the bypass at the only place every caller passes through (`QS`), including cached results.
- Removes three near-identical copies of the EvalContext + detach + check logic (`abstract.py:323-461`, `:463-580`; `slug_visibility.py:227-282`).
- No dependency on ai-parrot types: ai-parrot maps `PermissionContext` → `QSPrincipal` on its side.
- Fully opt-in. `principal=None` takes today's path byte-for-byte.

❌ **Cons:**
- Touches security-critical handler code, so it needs strong regression tests for the handler 404 semantics.
- Needs a request-free `EvalContext`. navigator-auth 0.26.0's `EvalContext.__init__` reads `request.remote/.method/.headers/.path_qs/.path/.rel_url` without guarding them.
- Policies that match on IP, headers or path cannot match a request-free context. They default-deny, which is safe but may surprise operators.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `navigator-auth` | `EvalContext`, `PolicyEvaluator.check_access`, `Environment`, `ResourceType` | 0.26.0 installed; `check_access(ctx, resource_type, resource_name, action, env=None, owner_reports_to=None, org_id=1, client_id=1)` |
| stdlib `dataclasses`, `copy` | `QSPrincipal`; detached evaluator | already used in `slug_visibility.py` |

🔗 **Existing Code to Reuse:**
- `querysource/auth/slug_visibility.py:227-282` — `_evaluator_state` (detach) and `_eval_context`; the natural core to generalize.
- `querysource/handlers/abstract.py:323-580` — sessionless-authz synthetic identity and fail-closed rules to preserve.
- `querysource/queries/multi/__init__.py:288-336` — existing per-child tenant pre-flight loop.
- `querysource/services.py:65,146-150` — QuerySource singleton and `setup_pbac` wiring.

---

### Option B: Synthetic-request adapter inside QS

Leave the handlers alone. When `QS` receives a principal, it builds a mocked
`web.Request` (for example with `aiohttp.test_utils.make_mocked_request`) bound to the
QuerySource app and places a fake `user_session` on it. It then runs the same
enforcement code the handlers use, lifted into a mixin.

✅ **Pros:**
- `EvalContext` receives a real-looking request, so there is no navigator-auth workaround.
- Smallest change to the evaluation semantics.

❌ **Cons:**
- `make_mocked_request` is a test utility. Using it in production is fragile, and the Round 1 answer rejected a synthetic request as the transport.
- The `_enforce_*` helpers are `AbstractHandler` methods, so reusing them from `QS` means refactoring them anyway. It carries Option A's work and adds a fake request on top.
- Fake request attributes (IP, path, headers) would be visible to policies and could satisfy IP/path conditions they should not.
- Leaves the duplicate enforcement logic in place.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp.test_utils` | `make_mocked_request` | test utility, not a stable runtime API |

🔗 **Existing Code to Reuse:**
- `querysource/handlers/abstract.py:295-580` — enforcement helpers (would need extraction).

---

### Option C: Public `authorize()` API, caller-enforced (capability check)

QuerySource does not gate `QS` at all. It exposes a public
`querysource.auth.authorize(principal, slug, *, tenant=None, action="slug:execute")`
coroutine that returns or raises a decision. Callers such as ai-parrot must
call it before running `QS`. A stricter variant has `authorize()` return an
opaque, single-use grant that `QS` requires whenever PBAC is enabled.

✅ **Pros:**
- Zero change to `QS` / `MultiQS` execution paths and to the handlers.
- Very small surface. The grant variant also makes a forgotten check fail loudly.

❌ **Cons:**
- The plain variant is opt-in per caller, and forgetting to call it is exactly today's bug.
- The grant variant breaks every existing request-less caller (scheduler, workers) unless they are exempted, which reopens the bypass.
- Nested MultiQS children would need callers to authorize each child themselves, so the no-partial-batch rule moves outside QuerySource.
- It still needs the same request-free EvalContext core as Option A.

📊 **Effort:** Low (plain) / Medium (grant)

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `navigator-auth` | evaluation | same as A |

🔗 **Existing Code to Reuse:**
- `querysource/auth/slug_visibility.py:353` — `can_access` is already a non-raising single-slug check.

---

## Recommendation

**Option A** is recommended because:

- It is the only option that enforces PBAC **inside** `QS`/`MultiQS`, the path every caller shares. A caller cannot forget it, and cached results are covered because the gate runs in `build_provider()` before the cache lookup.
- It follows all Round 1-3 decisions: an explicit `QSPrincipal` argument, the evaluator from singleton state, a no-op when PBAC is off, `tenant=` as the routing key, MultiQS included, a dedicated denial exception, and one shared core.
- Extracting the core removes three duplicated enforcement blocks, so a fourth copy for the library path is never added.

**What we trade off:** the handler paths become a refactor, not just an addition, so the blast radius is larger. We accept that because handler behaviour (404 on deny, fail-closed on a missing session or evaluator, sessionless authz) is already pinned by tests. It also gives one place to fix the next policy-evaluation bug. The request-free `EvalContext` is a navigator-auth compatibility risk. It is kept behind one adapter in the core, and the long-term fix is an upstream `EvalContext` factory (Open Question).

---

## Feature Description

### User-Facing Behavior

A library caller can now say "run this as user U":

- `QS(slug="sales_by_store", conditions={...}, tenant="client_b", principal=QSPrincipal(user_id="35", username="jdoe", groups=("sales",), programs=("walmart",), tenant_id="client_b", channel="ui_surfaces"))`
- `MultiQS(slug=..., tenant=..., principal=...)`

Outcomes:
- **PBAC active and allowed:** the query runs exactly as today, with trusted-service credentials, from the store that `tenant=` selects.
- **PBAC active and denied:** `QueryAccessDenied` is raised from `query()` / `columns()` before any provider, connection or cache access. The message is generic ("query not available"). Details (matched policy, reason, slug, principal) go to the logs only.
- **PBAC not configured:** no-op, and a debug log notes that the principal was ignored.
- **No principal:** unchanged. The scheduler, workers and existing callers are unaffected.
- **Both `request=` and `principal=`:** rejected with a `ValueError` at construction, because the identity would be ambiguous.

HTTP API users see no change. Handler responses (404 on deny) stay identical.

### Internal Behavior

1. **`QSPrincipal`** (`querysource/auth/`): a frozen dataclass and the only public identity type. It has a converter to the normalized userinfo shape the evaluator reads (`username`, `user_id`, `groups`, `roles`, `programs`, `superuser`; plus `org_id`/`client_id` only when set explicitly). Its `tenant_id` is **never** mapped into `org_id`/`client_id`.
2. **Evaluation core** (`querysource/auth/`, name decided at spec time): it takes the normalized identity, an optional request, the resource and action, and `detached`.
   - It finds the evaluator: from `request.app` when a request is present, otherwise from QuerySource singleton state.
   - It builds the `EvalContext`: the normal constructor when a request exists, otherwise a request-free adapter.
   - On the principal path it always uses a detached copy with a fresh `_cache` (see Open Questions for the handler path).
   - It awaits coroutine results and returns a typed decision.
   - A misconfiguration (guardian present, evaluator missing) or an evaluator exception is a **deny** (fail-closed), matching `abstract.py:400-406`.
3. **Handlers:** `_enforce_pbac` and `_enforce_owned_slug` turn their request into the core's input. The existing session lookup and sessionless-authz branch stay handler-side, because they need the request. They map a deny to `web.HTTPNotFound`. `slug_visibility.can_access` / `filter_visible` delegate the same way and keep returning bool/list.
4. **`QS.build_provider()`:**
   - Slug type: after `repo.get(identity)` succeeds and before `connection.get_provider()`, it calls the core with `slug:execute` on the resolved slug, using a detached evaluator.
   - Raw-query type: it checks `raw_query:execute`.
   - It logs the principal's user and tenant, the selector tenant, the slug and the decision, which carries the owner into logs (FEAT-147).
5. **`MultiQS.query()`:** inside the existing child pre-flight loop (`multi/__init__.py:288-336`), after each child's store resolves, it checks `slug:execute` for the child slug. Any deny aborts the whole batch before execution. Raw inline children follow the handler's `has_raw_query` rule. The stored parent multi slug is checked first. The unused `user_session` kwarg (`multi/__init__.py:105,147`) is left untouched and documented as separate from `principal`.
6. **Singleton evaluator lookup:** `QuerySource.setup()` already runs `setup_pbac(self.app, ...)` (`services.py:146-150`). The core reads `security` and `policy_evaluator` from that app, or from a small runtime handle recorded by `setup_pbac()`, decided at spec time. If QuerySource was never set up, there is no PBAC, which is a no-op.

### Edge Cases & Error Handling

- **A principal with an empty identity** (no user id or username): `ValueError` at construction. It must never evaluate as anonymous.
- **Principal given, PBAC enabled, evaluator missing:** deny (fail-closed), logged at error, matching the handlers.
- **Principal given, `QS_PBAC_ENABLED=False` or QuerySource not set up:** no-op, debug log (accepted risk: a caller that expected enforcement gets none; see Open Questions).
- **Policies with IP, header or path conditions:** they cannot match a request-free context and default-deny. This must be documented for operators.
- **Missing slug and principal denied:** `SlugNotFound` is raised before the gate, so a library caller can tell "missing" from "denied". That is acceptable for trusted in-process code, but the ai-parrot layer must not relay the distinction to end users (Open Question).
- **Cross-tenant decision leakage:** avoided by the detached evaluator per check.
- **`MultiQS` with a denied child:** the whole batch fails with `QueryAccessDenied` naming no data. No child runs.
- **Cached result for a slug:** still gated, because the gate runs before the cache lookup.
- **navigator-auth upgrade changes `EvalContext` internals:** the adapter is isolated in one function and a contract test pins the expected keys.

---

## Capabilities

### New Capabilities
- `qs-principal`: public `QSPrincipal` identity type and `principal=` argument on `QS` / `MultiQS`.
- `pbac-evaluation-core`: request-optional, detached-evaluator slug/raw-query policy evaluation shared by handlers, describe visibility and QS.
- `qs-programmatic-pbac`: PBAC enforcement in `QS.build_provider()` and the `MultiQS` child pre-flight when a principal is supplied.

### Modified Capabilities
- `pbac-support` (FEAT-091, `sdd/specs/pbac-support.spec.md`): enforcement is no longer handler-only.
- `per-tenant-queries` (FEAT-147, `sdd/specs/per-tenant-queries.spec.md`): the "programmatic calls keep the trusted-service model" rule is refined. Credentials stay trusted-service, but authorization applies when a principal is given.
- `describe-queryslug` (FEAT-148, `sdd/specs/describe-queryslug.spec.md`): `slug_visibility` delegates to the shared core with no behaviour change.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/queries/qs.py` (`QS.__init__`, `build_provider`) | modifies | new kwarg; gate before `get_provider` |
| `querysource/queries/multi/__init__.py` (`MultiQS`) | modifies | new kwarg; per-child gate in pre-flight loop |
| `querysource/interfaces/queries.py` (`AbstractQuery.__init__`) | modifies | store principal next to `_request` / `_tenant_selector` |
| `querysource/handlers/abstract.py` | modifies (refactor) | `_enforce_pbac` / `_enforce_owned_slug` delegate to core |
| `querysource/auth/slug_visibility.py` | modifies (refactor) | `_evaluator_state` / `_eval_context` fold into core |
| `querysource/auth/` (new module(s)) | extends | `QSPrincipal`, evaluation core, request-free EvalContext adapter |
| `querysource/auth/pbac.py` / `services.py` | modifies (maybe) | expose evaluator via singleton runtime |
| `querysource/exceptions.py` | extends | `QueryAccessDenied(QueryException)` |
| ai-parrot `QuerySlugSource`, `ui_surfaces._refresh` | depends on (follow-up, other repo) | map `PermissionContext` to `QSPrincipal`; pass `record.tenant` as `tenant=` |
| Public API | extends, non-breaking | new optional kwarg; no signature removals |

---

## Code Context

### User-Provided Code
None. The user supplied the references only (qs.py:147-165; FEAT-147 spec L225; ai-parrot `auth/permission.py:188-202`; FEAT-535).

### Verified Codebase References

#### Classes & Signatures
```python
# querysource/queries/qs.py:42-57
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None,
                 request: web.Request = None, loop: asyncio.AbstractEventLoop = None,
                 *, tenant: str | None = None, **kwargs): ...
    async def build_provider(self):            # :137
        # :147-165 — _pbac_app/_pbac_session extracted ONLY when self._request is not None
        # :169-178 — repo = await self.get_definition_repository();
        #            store = repo.registry.resolve(self._tenant_selector);
        #            loaded_def = await repo.get(QueryIdentity(store=store, slug=self._query))
        # :188 — self._conn, self._provider = await self.connection.get_provider(objquery, session=_pbac_session, app=_pbac_app)
    async def query(self, output_format: str | None = None):   # :376 — build_provider() at :384, cache lookup from :395

# querysource/interfaces/queries.py:41
class AbstractQuery(Connection):
    def __init__(..., tenant: str | None = None, ...)   # :48-55
    # self._request = request            :91
    # self._tenant_selector = tenant     :104

# querysource/queries/multi/__init__.py:90
class MultiQS(BaseQuery):
    def __init__(self, slug=None, ..., request: web.Request = None, loop=None,
                 user_session: object | None = None, *, tenant: str | None = None, **kwargs)  # :96-118
    # self._user_session = user_session   :147 — stored, never read elsewhere in multi/
    async def query(self):                 # :203
        # :288-336 — child pre-flight: child_tenant inherit/override, repo.registry.resolve(child_tenant),
        #            repo.get(QueryIdentity(store=child_store, slug=child_slug)); raw children skipped

# querysource/handlers/abstract.py
_SENTINEL = object()                                                     # :30
async def _get_user_session(self, request) -> SessionData | None         # :295 (memoizes request['user_session'])
async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None   # :323
    # no-op if request.app.get('security') is None (:346-348); 404 on missing name/session/evaluator/deny;
    # sessionless authz via QS_PBAC_ALLOW_SESSIONLESS_AUTHZ + request[AUTHZ_BACKEND_KEY];
    # EvalContext(request=, user=, userinfo=, session=) :425; evaluator.check_access(...) :443; iscoroutine guard
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None      # :463
    # detached = copy.copy(evaluator); detached._cache = {}; detached._stats = dict(evaluator._stats)

# querysource/auth/slug_visibility.py
class PrincipalKind(str, Enum): SUPERUSER, PROGRAMS, AUTHZ, NO_PROGRAMS, NONE    # :25
@dataclass(frozen=True)
class Principal:  kind, userinfo: dict, groups: tuple, programs: tuple, session: Any   # :36 (describe-only, request-derived)
async def resolve_principal(request, session) -> Principal                        # :99
def _evaluator_state(request, *, detached: bool = False) -> tuple[bool, Any]      # :227
def _eval_context(request, principal: Principal) -> Any                           # :260
async def filter_visible(request, principal, slugs, ...)                          # :283
async def can_access(request, principal, slug, primary_action, fallback_action=None, *, detached=False) -> bool  # :353

# querysource/auth/pbac.py:28
def setup_pbac(app: web.Application, policy_dir: str = "policies", cache_ttl: int = 300)
    -> tuple[PDP | None, PolicyEvaluator | None, Guardian | None]
    # registers app['security'], app['abac'], app['policy_evaluator'], app['credential_resolver']; idempotent

# querysource/services.py:65
class QuerySource(metaclass=Singleton):
    def __init__(self, *, tenant_allowlist=_UNSET, **kwargs)
    def setup(self, app) -> web.Application   # sets self.app; :146-150 calls setup_pbac(self.app, ...) when QS_PBAC_ENABLED

# querysource/interfaces/connections.py
async def get_provider(self, entry: dict, session=None, app=None)   # :262
async def get_definition_repository(self) -> DefinitionRepository  # :438

# querysource/datasources/drivers/pg.py:59
def params_for(self, session, app=None) -> dict   # per-user creds; unchanged by this feature

# querysource/scheduler/jobs.py — request-less callers that must stay unchanged
QS(slug=slug, tenant=tenant)        # :87, :190
MultiQS(slug=slug, tenant=tenant)   # :144

# navigator-auth 0.26.0 (installed)
EvalContext.__init__(self, request: web.Request, user, userinfo, session, *args, org_id=None, client_id=None, **kwargs)
    # dereferences request.remote/.method/.headers/.path_qs/.path/.rel_url unguarded; tenant pair via _resolve_tenant(request, userinfo, org_id, client_id)
PolicyEvaluator.check_access(ctx, resource_type, resource_name, action, env=None,
                             owner_reports_to=None, org_id=1, client_id=1) -> EvaluationResult
```

#### Verified Imports
```python
from querysource.queries.qs import QS                          # querysource/queries/qs.py
from querysource.queries.multi import MultiQS                  # querysource/queries/multi/__init__.py:90
from querysource.exceptions import QueryException, SlugNotFound, QueryNotFound, DriverError  # exceptions.py:6,34,52,58
from querysource.auth.pbac import setup_pbac                   # querysource/auth/pbac.py:28
from querysource.auth.slug_visibility import Principal, PrincipalKind, can_access  # slug_visibility.py:25,36,353
from querysource.handlers.abstract import _SENTINEL            # used by qs.py:160
from querysource.tenants import QueryIdentity                  # used by qs.py:173
from navigator_auth.abac.context import EvalContext            # lazy import only
from navigator_auth.abac.policies.environment import Environment
```

#### Key Attributes & Constants
- `QS_PBAC_ENABLED` (bool, default False): `querysource/conf.py:441`
- `QS_POLICY_PATH`: `querysource/conf.py:442`
- `QS_PBAC_CACHE_TTL` (default 300): `querysource/conf.py:443`
- `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` (default False): `querysource/conf.py:453`
- App keys: `'security'`, `'abac'`, `'policy_evaluator'`, `'credential_resolver'`, `'qs_tenant_registry'`, `'qs_definition_repository'`
- Request keys: `'user_session'` (memoized session), `'qs_tenant'` (handler tenant selector, `handlers/service.py:201`)

#### ai-parrot references (other repo, `4f8a0bfa1`; informational, not modified here)
- `packages/ai-parrot/src/parrot/auth/permission.py:166` `build_principal_context(principal, *, channel, tenant_id=None, roles=None)` sets `tenant_id=tenant_id or principal` (:201).
- `permission.py:209-252` `to_eval_context()` builds an `EvalContext` through `__new__`, a hack pinned to navigator-auth 0.19.x with a TODO for an upstream `EvalContext.from_userinfo()`.
- `tools/dataset_manager/sources/query_slug.py:110,150` `QuerySlugSource` builds `QS(slug=..., conditions=...)` with no request, tenant or identity.
- ai-parrot `sdd/specs/ui-surfaces-tenant-visibility.spec.md` (FEAT-535) L143, L434: `_refresh` uses `build_principal_context(record.user_id, channel="ui_surfaces")`.

### Does NOT Exist (Anti-Hallucination)
- ~~`QS(..., principal=...)` / `MultiQS(..., principal=...)`~~: no identity argument exists today.
- ~~`QSPrincipal`~~: new. Do not confuse it with `slug_visibility.Principal`, which is describe-only and request-derived.
- ~~`QueryAccessDenied`~~: not in `querysource/exceptions.py`.
- ~~Any PBAC check inside `QS` / `MultiQS` / `QueryObject`~~: enforcement is handler-only today.
- ~~`EvalContext.from_userinfo()` / any request-free `EvalContext` constructor in navigator-auth 0.26.0~~: only `__init__(request, ...)` exists.
- ~~A module-level accessor for the policy evaluator~~: it is only on `app['policy_evaluator']`.
- ~~Use of `MultiQS._user_session`~~: stored at `multi/__init__.py:147`, never read.
- ~~`wikitoolkit` CLI in this shell~~: not on PATH during this brainstorm, so research used grep/read.

---

## Parallelism Assessment

- **Internal parallelism**: moderate. (1) `QSPrincipal` + exception + evaluation core come first. After that: (2a) handler + `slug_visibility` delegation and (2b) `QS` + `MultiQS` integration touch disjoint files and could run in parallel. (3) Docs and operator notes last.
- **Cross-feature independence**: shares `handlers/abstract.py`, `auth/slug_visibility.py` and `queries/qs.py` with the recently merged FEAT-147 (per-tenant-queries) and FEAT-148 (describe-queryslug, PR #601). No other in-flight spec touches them. Rebase on current `dev` before starting.
- **Recommended isolation**: `per-spec`
- **Rationale**: the core refactor is security-critical and the handler delegation must land together with its regression tests. A single sequential worktree keeps the enforcement semantics in one reviewable line of commits. The 2a/2b split is small enough that parallel worktrees would add merge risk without saving much time.

---

## Open Questions

- [x] Flow type / base branch — *Owner: Jesus Lara*: feature → dev.
- [x] Goal — *Owner: Jesus Lara*: PBAC + tenant routing; credentials stay trusted-service.
- [x] Identity transport — *Owner: Jesus Lara*: explicit principal argument (not a synthetic request or contextvar).
- [x] Scope — *Owner: Jesus Lara*: QuerySource side only; ai-parrot changes are a separate follow-up.
- [x] Evaluator source without request — *Owner: Jesus Lara*: QuerySource singleton/global state.
- [x] Principal shape — *Owner: Jesus Lara*: new QS-owned dataclass (`QSPrincipal`).
- [x] PBAC not configured — *Owner: Jesus Lara*: no-op with debug log.
- [x] Principal tenant vs `tenant=` — *Owner: Jesus Lara*: `tenant=` wins; principal tenant only carried into context/logs.
- [x] MultiQS — *Owner: Jesus Lara*: included; every stored child checked before any executes.
- [x] Deny surface — *Owner: Jesus Lara*: new `QueryAccessDenied(QueryException)`, non-leaking message.
- [x] Handler refactor — *Owner: Jesus Lara*: extract the core; handlers and slug_visibility delegate.
- [ ] Request-free `EvalContext`: a local duck-typed stand-in request (neutral `remote`/`method`/`headers`/`path`), a `__new__`-populated store (ai-parrot's pinned hack), or an upstream `EvalContext.from_userinfo()` in navigator-auth first? — *Owner: Jesus Lara*
- [ ] Singleton lookup mechanics: read `QuerySource().app[...]`, or have `setup_pbac()` record a module-level runtime handle (which also covers a parent navigator-api stack that ran `PDP.setup` first)? — *Owner: Jesus Lara*
- [ ] Should the principal path always use a detached evaluator (no decision cache at all), or only when a tenant registry is active, like `handlers/service.py:201-223`? — *Owner: Jesus Lara*
- [ ] The no-op when PBAC is off is silent at debug level. Should a principal plus `QS_PBAC_ENABLED=True` with a failed bootstrap be a warning, or fail-closed? — *Owner: Jesus Lara*
- [ ] Missing slug vs denied: should `QS` with a principal collapse `SlugNotFound` into `QueryAccessDenied` so existence never leaks through library layers? — *Owner: Jesus Lara*
- [ ] Does `QSPrincipal` need a sessionless-authz form (`authz:<backend>` identity with groups `authorized`/backend) for worker-to-QS calls, or is that handler-only? — *Owner: Jesus Lara*
- [ ] ai-parrot follow-up (tracked in the ai-parrot repo): map `PermissionContext` to `QSPrincipal` in `QuerySlugSource`, and make `_refresh` pass `record.tenant` as `tenant=` instead of letting `build_principal_context` default the tenant to the user id. — *Owner: ai-parrot maintainers*
