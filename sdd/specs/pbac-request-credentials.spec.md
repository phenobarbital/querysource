---
type: feature
base_branch: dev
projects: [querysource, auth, handlers, multiquery]
tags: [pbac, authorization, principal, tenancy, library-api]
---

# Feature Specification: PBAC for Request-less (Programmatic) QS Callers

**Feature ID**: FEAT-150
**Date**: 2026-09-24
**Author**: Jesus Lara / Claude
**Status**: draft
**Target version**: 5.1.0
**Exploration**: `sdd/proposals/pbac-request-credentials.brainstorm.md` (Option A)

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource enforces PBAC **only in the aiohttp handlers**
(`AbstractHandler._enforce_pbac` / `_enforce_owned_slug`,
`querysource/handlers/abstract.py:323,463`). `QS` itself never evaluates a
policy. When `QS` is built with `request=None`, `build_provider()` skips the
session/app extraction (`querysource/queries/qs.py:147-165`) and the slug runs
with the trusted service credentials and **no authorization at all**. FEAT-147
kept this on purpose ("PBAC-disabled execution remains supported.
Programmatic/scheduled/worker calls preserve their existing trusted-service
credential model", `sdd/specs/per-tenant-queries.spec.md:235-237`).

That breaks when a library caller runs a slug **on behalf of a user**. The main
case is ai-parrot. Its `QuerySlugSource.fetch` / `prefetch_schema` build
`QS(slug=..., conditions=...)` with no request and no tenant. Its UI-surface
`_refresh` (ai-parrot FEAT-535) replays a stored surface as its owner through
`build_principal_context(record.user_id, channel="ui_surfaces")`, whose
`tenant_id` defaults to the user id (`parrot/auth/permission.py:188-202`),
and `record.tenant` is ignored. A user who cannot run slug X over HTTP can
get X's rows through an agent tool or a surface refresh, because nothing tells
QuerySource who is asking.

### Goals
- A library caller can run `QS` / `MultiQS` **as a user** by passing an explicit
  `principal=QSPrincipal(...)`. The same slug policies that protect the HTTP API
  are then evaluated.
- **Opt-in and backward compatible.** `principal=None` keeps today's path exactly
  (scheduler, workers, existing callers).
- Credentials stay trusted-service. This feature authorizes; it does not switch to
  per-user datasource credentials.
- `tenant=` stays the only store-routing key. The principal's tenant is carried
  into logs only.
- A denied or unavailable query raises `QueryAccessDenied` **before** any store
  resolution, definition load, provider, connection or cache access. With a
  principal, "missing" and "denied" look the same.
- `MultiQS` checks every stored child (and its own stored slug) before any child runs.
- One request-optional PBAC evaluation core replaces the three duplicated
  EvalContext + detach + `check_access` blocks. Handlers and describe visibility
  delegate to it with no behaviour change.

### Non-Goals (explicitly out of scope)
- Per-user datasource credentials on the principal path (`pgDriver.params_for`, FEAT-091). This was rejected in brainstorm Round 1.
- Any change in ai-parrot. Mapping `PermissionContext` to `QSPrincipal` and passing `record.tenant` belong to a separate ai-parrot follow-up (§8).
- A tenant-membership check between `principal.tenant_id` and `tenant=` (FEAT-147 L225-226: "No tenant membership check is introduced").
- Implementing `EvalContext.from_userinfo()` itself. That lands in the navigator-auth repo (`../navigator-auth`) under its own spec, using the contract fixed in §2. FEAT-150 feature-detects it and does not wait for its release.
- A synthetic `web.Request` or contextvar identity transport. Both were rejected in brainstorm (Options B/C; Round 1).
- Numeric `org_id` / `client_id` on `QSPrincipal`. Handlers never pass a tenant pair to `check_access` (defaults 1, `abstract.py:443-449`), so the principal path does not either (refinement of the brainstorm description; see §7).
- MultiQS `sources` entries (the non-query `sources:` list). Handlers do not PBAC-check them today, and this feature keeps parity.

---

## 2. Architectural Design

### Overview

**Option A: a request-optional evaluation core plus a `QSPrincipal` kwarg.**

1. **`QSPrincipal`** (new, `querysource/auth/principal.py`) is a frozen dataclass
   and the only public identity type. It is independent of ai-parrot. It
   converts to the navigator-auth *userinfo* dict the evaluator reads (`username`,
   `user_id`, `groups`, `roles`, `programs`, `superuser`). `tenant_id` and
   `channel` are **never** placed into userinfo. They are for logs only. An empty
   `user_id` raises `ValueError` at construction, so a principal never evaluates as
   anonymous.

   **Sessionless form** (resolved Q2): `QSPrincipal.for_authz(backend)` builds exactly
   the handler's synthetic identity (`handlers/abstract.py:382-386`). That is
   `user_id=username='authz:<backend>'`, `groups=('authorized', <backend>)` and no
   roles, programs or superuser, and it is evaluated with `user=None`, as the handler
   does (`abstract.py:418-421`). `__post_init__` rejects an `authz_backend` principal
   whose other claims differ from that shape, so the form cannot carry extra
   privileges. The form is honoured **only** when `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`
   is true. Otherwise, with PBAC active, it is denied (`QueryAccessDenied`), just as
   the handler denies a session-less request when the flag is off (`abstract.py:395-403`).
2. **Evaluation core** (new, `querysource/auth/enforcement.py`):
   - `resolve_evaluator(request, *, detached)` generalizes
     `slug_visibility._evaluator_state`. With a request it reads
     `request.app['security' | 'policy_evaluator']`. Without one it reads the
     process-wide **PBAC runtime handle** that `setup_pbac()` records.
   - `build_eval_context(...)`: with a request it uses the real `EvalContext`
     constructor (unchanged handler path). Without a request it **prefers
     `EvalContext.from_userinfo(...)`** when the installed navigator-auth provides it
     (feature-detected with `hasattr`, resolved Q1). Otherwise, on 0.26.0, it passes a
     neutral stand-in request (`_ServiceRequest`) to the real constructor. Both are
     safe because navigator-auth 0.26.0's `check_access` reads only `ctx.userinfo` /
     `ctx.user` (cache key + `_build_user_context`), never request fields.
   - **Upstream contract** for the navigator-auth spec (verbatim target):
     `@classmethod EvalContext.from_userinfo(cls, userinfo: dict, *, user: Any = None,
     session: Any = None, org_id: Any = None, client_id: Any = None) -> EvalContext`.
     It builds the same store keys `__init__` sets, with `request=None`,
     `ip_addr=None`, `method=None`, `referer=None`, `path_qs=None`, `path=None`,
     `headers={}`, `url=None` and `is_authenticated = user is not None or bool(userinfo)`.
     `org_id`/`client_id` are resolved through `_resolve_tenant(None, userinfo, org_id,
     client_id)`, whose header branch must tolerate `request=None` (it already guards
     `if request and hasattr(request, 'headers')`).
   - `evaluate(...)` runs `check_access` with the `iscoroutine` guard and turns
     evaluator exceptions into a **deny** (fail-closed).
   - `enforce_principal(...)` is the QS entrypoint. It always uses a detached
     evaluator copy, treats "PBAC off" as a no-op, and raises `QueryAccessDenied`
     on deny.
3. **PBAC runtime handle** (modifies `querysource/auth/pbac.py`): both
   `setup_pbac()` success branches (fresh bootstrap and reuse of a parent
   navigator-api stack) record `(guardian, evaluator)` in a module-level handle
   that `get_pbac_runtime()` reads. This is the "QuerySource singleton" evaluator
   source from brainstorm Round 2. It does **not** read `QuerySource().app`, because
   calling `QuerySource()` in a library process that never set it up would
   construct the heavy singleton (provider scan) as a side effect.
4. **Handlers** (`_enforce_pbac`, `_enforce_owned_slug`) and **`slug_visibility`**
   (`_evaluator_state`, `_eval_context`) delegate to the core. Session lookup,
   sessionless authz and the `web.HTTPNotFound` mapping stay handler-side
   (they need the request). Handler decisions are unchanged: `_enforce_pbac` keeps
   the shared evaluator, and `_enforce_owned_slug` and describe keep the detached copy.
5. **`QS`** gets keyword-only `principal=`, stored on `AbstractQuery`. Passing both
   `request=` and `principal=` raises `ValueError` (ambiguous identity). In
   `build_provider()`, when a principal is set:
   - Slug type: `slug:execute` on the slug name **first**, then store
     resolution and definition load. A `TenantError` whose `error_code` is
     `query_not_found` or `tenant_not_available` becomes `QueryAccessDenied`
     (existence collapse, clarified at spec time).
   - Types `query` / `raw` / `driver`: `raw_query:execute` on `"raw_query"`
     before `get_provider()` (parity with `handlers/executor.py`).
   - Because `query()` calls `build_provider()` before the cache lookup
     (`qs.py:384` vs `:395`), cached results are gated too.
6. **`MultiQS`** gets the same kwarg. In `query()`:
   - When `self.slug` is set, the pipeline's own slug is checked before
     `get_slug()`, and `SlugNotFound` / not-found `TenantError` collapse to
     `QueryAccessDenied`.
   - After the pipeline is expanded and **before** the source-count guard's DB
     pre-flight, every stored child slug is checked for `slug:execute`, every
     `files` entry for `slug:execute`, and any raw inline child once for
     `raw_query:execute`, all before any child runs.
   - The per-child not-found in the existing pre-flight loop is re-raised as
     `QueryAccessDenied`, not wrapped by `self.Error`.
   - The existing, unused `user_session` kwarg is untouched.
7. **Logging** carries the owner (FEAT-147): each decision logs principal
   `user_id`, `principal.tenant_id`, the `tenant=` selector, the resource, the
   action, and allow/deny with the matched policy and reason. Denials log at
   info. The no-op when PBAC is off logs at debug. When `QS_PBAC_ENABLED=True`
   but no runtime is registered (bootstrap failed or QuerySource was never set
   up), the principal path logs a **warning** once per process and proceeds
   (no-op, as resolved).

User-facing behaviour:

| Situation | Result |
|---|---|
| `principal=None` | unchanged (trusted-service, no PBAC) |
| principal, PBAC active, allowed | runs as today, trusted-service creds, store chosen by `tenant=` |
| principal, PBAC active, denied | `QueryAccessDenied` from `query()` / `columns()` / `build_provider()`; generic message, details only in logs |
| principal, slug or tenant missing | `QueryAccessDenied` (indistinguishable from denied) |
| principal, PBAC runtime absent | no-op; debug log (warning once if `QS_PBAC_ENABLED=True`) |
| principal, guardian present but evaluator missing, or evaluator raises | `QueryAccessDenied` (fail-closed) |
| `request=` **and** `principal=` | `ValueError` at construction |
| HTTP API | unchanged (404 on deny) |

### Component Diagram
```
library caller (ai-parrot, scripts)             HTTP handlers (aiohttp)
  QS/MultiQS(..., principal=QSPrincipal)          _enforce_pbac / _enforce_owned_slug
        │                                          slug_visibility.can_access/filter_visible
        ▼                                                   │
  enforce_principal(principal, rtype, name, action)         │  (request, session → userinfo)
        │                                                   │
        └──────────────► auth/enforcement.py ◄──────────────┘
                           resolve_evaluator(request|None, detached)
                             ├─ request.app['security'/'policy_evaluator']
                             └─ get_pbac_runtime()  ◄── setup_pbac() records it
                           build_eval_context(userinfo, user, session, request|None)
                             └─ _ServiceRequest stand-in when request is None
                           evaluate(...) → AccessDecision  (fail-closed on error)
        │
        ▼ allowed
  store resolve → DefinitionRepository.get → get_provider → cache / execute
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QS.__init__` / `QS.build_provider` (`queries/qs.py:42,137`) | modifies | `principal=` kwarg; gate before store resolution / `get_provider` |
| `AbstractQuery.__init__` (`interfaces/queries.py:48`) | modifies | stores `self._principal`; request+principal → `ValueError` |
| `BaseQuery.__init__` (`queries/base.py:21`) | modifies | forwards `principal=` |
| `MultiQS.__init__` / `MultiQS.query` (`queries/multi/__init__.py:96,203`) | modifies | kwarg; parent + children + files + raw pre-check |
| `AbstractHandler._enforce_pbac` / `_enforce_owned_slug` (`handlers/abstract.py:323,463`) | modifies (refactor) | delegate to core; HTTP semantics unchanged |
| `slug_visibility._evaluator_state` / `_eval_context` (`auth/slug_visibility.py:227,260`) | modifies (refactor) | thin wrappers over core; names kept |
| `setup_pbac` (`auth/pbac.py:28`) | modifies | records runtime handle in both success branches |
| `querysource/exceptions.py` | extends | `QueryAccessDenied(QueryException)`, code 404 |
| `querysource/auth/__init__.py` | extends | export `QSPrincipal`, `get_pbac_runtime` |
| `DefinitionRepository.get` (`repositories/definitions.py:161`) | depends on | `TenantError(error_code="query_not_found")` collapsed |
| `TenantRegistry.resolve` (`tenants.py`) | depends on | `TenantError(error_code="tenant_not_available")` collapsed |
| `scheduler/jobs.py:87,144,190` | unchanged | no principal, regression-tested |

### Data Models
```python
@dataclass(frozen=True)
class QSPrincipal:
    user_id: str
    username: str | None = None
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    superuser: bool = False
    tenant_id: str | None = None      # informational only — never routes, never enters userinfo
    channel: str = "library"          # informational only — logs
    authz_backend: str | None = None  # set only by for_authz(); sessionless-authz form

@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    pbac_enabled: bool
    matched_policy: str | None = None
    reason: str | None = None

@dataclass(frozen=True)
class PBACRuntime:
    guardian: Any
    evaluator: Any
```

### New Public Interfaces
```python
from querysource.auth import QSPrincipal, get_pbac_runtime
from querysource.exceptions import QueryAccessDenied

qs = QS(slug="sales_by_store", conditions={...}, tenant="client_b",
        principal=QSPrincipal(user_id="35", username="jdoe", groups=("sales",)))
result, error = await qs.query()          # raises QueryAccessDenied on deny/missing

mq = MultiQS(slug="pipeline", tenant="client_b", principal=principal)
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: principal + exception | yes | `QSPrincipal` fields/validation/`to_userinfo()`; `QueryAccessDenied(message=None)` code 404 | — |
| M2: PBAC runtime handle | yes | module global, `_set_pbac_runtime` / `get_pbac_runtime` / `clear_pbac_runtime`; set in both success branches | — |
| M3: evaluation core | no | — | fail-closed/no-op matrix and the stand-in's navigator-auth contract are security-critical; needs thinking-model review |
| M4: handler + slug_visibility delegation | no | — | refactor of security-critical code; handler 404/sessionless semantics must be preserved exactly |
| M5: QS integration | yes | kwarg plumbing, gate placement, collapse set, `ValueError` rule all fixed below | — |
| M6: MultiQS integration | no | — | gate ordering relative to expansion, the source-count guard and `self.Error` wrapping needs judgement |
| M7: docs | yes | new `docs/PBAC_PROGRAMMATIC.md` + link from `docs/PER_TENANT_QUERIES.md` | — |

### Module 1: Principal & denial exception
- **Path**: `querysource/auth/principal.py` (new), `querysource/exceptions.py`, `querysource/auth/__init__.py`
- **Responsibility**: Public caller identity. Converts to evaluator userinfo. Defines the denial exception.
- **Depends on**: nothing in this spec
- **Interface Skeleton**:
  ```python
  # querysource/auth/principal.py  (new)
  @dataclass(frozen=True)
  class QSPrincipal:
      """Identity of the user a library caller acts on behalf of.

      Only identity/claims used by PBAC policies. ``tenant_id`` and ``channel``
      are informational (logs); they never select a store and never enter the
      evaluation userinfo. Raises ValueError when ``user_id`` is empty/blank, or when
      ``authz_backend`` is set and the other claims differ from the for_authz() shape.
      Sequences passed for groups/roles/programs are normalized to tuples of str.
      """
      user_id: str
      username: str | None = None
      groups: tuple[str, ...] = ()
      roles: tuple[str, ...] = ()
      programs: tuple[str, ...] = ()
      superuser: bool = False
      tenant_id: str | None = None
      channel: str = "library"
      authz_backend: str | None = None

      @classmethod
      def for_authz(cls, backend: str, *, tenant_id: str | None = None,
                    channel: str = "library") -> "QSPrincipal":
          """Sessionless-authz identity, identical to the handler's synthetic one
          (verified: handlers/abstract.py:382-386): user_id=username='authz:<backend>',
          groups=('authorized', backend), no roles/programs/superuser. Blank backend → ValueError."""

      @property
      def is_authz(self) -> bool:
          """True when authz_backend is set."""

      def to_userinfo(self) -> dict[str, Any]:
          """User form: username (username or user_id), user_id, groups, roles, programs
          (lists), superuser (bool). Authz form: exactly {'username', 'groups', 'roles'} as the
          handler builds it. Nothing else in either form."""

      def log_fields(self) -> dict[str, Any]:
          """Return {'principal': user_id, 'principal_tenant': tenant_id, 'channel': channel} for logs."""

  # querysource/exceptions.py  (modifies; insert after QueryNotFound — verified: querysource/exceptions.py:52)
  class QueryAccessDenied(QueryException):
      """Principal may not run this query, or the query/tenant is not available to it.
      Message is generic and never names the matched policy; code 404."""
      def __init__(self, message: str = None): ...

  # querysource/auth/__init__.py  (modifies __all__ — verified: querysource/auth/__init__.py:21)
  from querysource.auth.principal import QSPrincipal
  from querysource.auth.pbac import get_pbac_runtime
  ```

### Module 2: PBAC runtime handle
- **Path**: `querysource/auth/pbac.py`
- **Responsibility**: A process-wide handle to the guardian and evaluator, recorded by `setup_pbac()` and read by request-less callers.
- **Depends on**: nothing in this spec
- **Interface Skeleton**:
  ```python
  # querysource/auth/pbac.py  (modifies querysource/auth/pbac.py:28)
  @dataclass(frozen=True)
  class PBACRuntime:
      """Guardian + evaluator registered by the last successful setup_pbac()."""
      guardian: Any
      evaluator: Any

  def get_pbac_runtime() -> PBACRuntime | None:
      """Return the registered runtime, or None when setup_pbac() never succeeded in this process."""

  def clear_pbac_runtime() -> None:
      """Forget the registered runtime (tests / app shutdown)."""

  def _set_pbac_runtime(guardian: Any, evaluator: Any) -> None:
      """Record the runtime; called by setup_pbac() in the reuse branch (verified: pbac.py:62-68)
      and after app registration in the bootstrap branch (verified: pbac.py:137-140).
      Never called on a failed bootstrap (the three `return (None, None, None)` paths)."""
  ```

### Module 3: Request-optional evaluation core
- **Path**: `querysource/auth/enforcement.py` (new)
- **Responsibility**: The one implementation of evaluator lookup, detaching, EvalContext construction, evaluation and fail-closed handling, plus the principal-path entrypoint.
- **Depends on**: Module 1 (`QSPrincipal`, `QueryAccessDenied`), Module 2 (`get_pbac_runtime`)
- **Interface Skeleton**:
  ```python
  # querysource/auth/enforcement.py  (new)
  @dataclass(frozen=True)
  class AccessDecision:
      """Outcome of one evaluation. pbac_enabled=False means 'no PBAC configured' (allowed=True)."""
      allowed: bool
      pbac_enabled: bool
      matched_policy: str | None = None
      reason: str | None = None

  class _ServiceRequest:
      """Neutral stand-in satisfying navigator_auth EvalContext.__init__ for request-less
      evaluation: remote=None, method='INTERNAL', empty CIMultiDictProxy headers,
      path='', path_qs='', rel_url=yarl.URL(''), get(key, default=None) -> default.
      Carries no caller-controlled data."""

  def resolve_evaluator(request: web.Request | None, *, detached: bool) -> tuple[bool, Any]:
      """(pbac_enabled, evaluator). Request → request.app['security'/'policy_evaluator']
      (verified: slug_visibility.py:239-243); None → get_pbac_runtime(). Guardian without
      evaluator → (True, None) and an error log. detached=True → shallow copy with fresh
      _cache and copied _stats (verified: abstract.py:496-499)."""

  def build_eval_context(*, userinfo: dict, user: Any, session: Any,
                         request: web.Request | None = None) -> Any:
      """request given → EvalContext(request=request, user=, userinfo=, session=) (unchanged).
      request None → EvalContext.from_userinfo(userinfo, user=, session=) when hasattr(EvalContext,
      'from_userinfo'), else EvalContext(request=_ServiceRequest(), ...). Lazy-imports navigator-auth."""

  async def evaluate(evaluator: Any, ctx: Any, resource_type: Any, resource_name: str,
                     action: str) -> AccessDecision:
      """check_access(ctx=, resource_type=, resource_name=, action=, env=Environment());
      awaits coroutine results (verified: abstract.py:450-451); any exception → deny
      (allowed=False, reason='evaluator error'), logged. Empty resource_name → deny."""

  async def enforce_principal(principal: QSPrincipal, resource_type: Any, resource_name: str,
                              action: str, *, tenant: str | None = None,
                              logger: logging.Logger | None = None) -> AccessDecision:
      """Principal-path gate. Always detached. PBAC off → allowed (debug log; one warning per
      process when QS_PBAC_ENABLED is True). Authz-form principal with PBAC active and
      QS_PBAC_ALLOW_SESSIONLESS_AUTHZ false → QueryAccessDenied; allowed flag → evaluated with user=None. Evaluator missing → QueryAccessDenied.
      Deny → QueryAccessDenied. Logs principal.log_fields(), tenant selector, resource, action,
      decision, matched policy and reason."""
  ```

### Module 4: Handler & describe delegation
- **Path**: `querysource/handlers/abstract.py`, `querysource/auth/slug_visibility.py`
- **Responsibility**: Remove the duplicated evaluation blocks. HTTP behaviour stays identical.
- **Depends on**: Module 3
- **Interface Skeleton** *(public signatures unchanged)*:
  ```python
  # querysource/handlers/abstract.py  (modifies :323 and :463 — signatures unchanged)
  async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None:
      """Unchanged contract: no-op when app['security'] is None; 404 on missing name, no
      session (unless sessionless authz), missing evaluator, or deny. Uses
      resolve_evaluator(request, detached=False), build_eval_context(..., request=request)
      and evaluate(...)."""
  async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None:
      """Unchanged contract; resolve_evaluator(request, detached=True)."""

  # querysource/auth/slug_visibility.py  (modifies :227 and :260 — names and signatures kept)
  def _evaluator_state(request: web.Request, *, detached: bool = False) -> tuple[bool, Any]:
      """Delegates to enforcement.resolve_evaluator(request, detached=detached)."""
  def _eval_context(request: web.Request, principal: Principal) -> Any:
      """Delegates to enforcement.build_eval_context(...) with the AUTHZ/regular split preserved."""
  async def can_access(request, principal, slug, primary_action, fallback_action=None, *, detached=False) -> bool:
      """Unchanged contract (verified: slug_visibility.py:353); its primary/fallback check_access calls
      (verified: :380, :398) go through enforcement.evaluate(). filter_visible keeps filter_resources."""
  ```

### Module 5: QS integration
- **Path**: `querysource/interfaces/queries.py`, `querysource/queries/base.py`, `querysource/queries/qs.py`
- **Responsibility**: Accept and store the principal. Gate `build_provider()`. Collapse not-found errors.
- **Depends on**: Module 1, Module 3
- **Interface Skeleton**:
  ```python
  # querysource/interfaces/queries.py  (modifies AbstractQuery.__init__ — verified: interfaces/queries.py:48-56,104)
  def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
               loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None,
               principal: "QSPrincipal | None" = None, **kwargs):
      """... Stores self._principal. Raises ValueError when both request and principal are given."""

  # querysource/queries/base.py  (modifies BaseQuery.__init__ — verified: queries/base.py:21-40)
  #   adds keyword-only `principal: QSPrincipal | None = None`, forwarded to super().__init__.

  # querysource/queries/qs.py  (modifies QS.__init__ :42-57 and build_provider :137)
  def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
               loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
               principal: "QSPrincipal | None" = None, **kwargs): ...
  async def build_provider(self):
      """When self._principal is set: slug → enforce_principal(..., ResourceType.SLUG, slug,
      'slug:execute', tenant=self._tenant_selector) BEFORE repo.registry.resolve (verified: qs.py:175);
      TenantError(error_code in {'query_not_found','tenant_not_available'}) from resolve/get →
      QueryAccessDenied. query/raw/driver → enforce_principal(..., ResourceType.RAW_QUERY,
      'raw_query', 'raw_query:execute') before get_provider. principal None → unchanged."""
  ```

### Module 6: MultiQS integration
- **Path**: `querysource/queries/multi/__init__.py`
- **Responsibility**: Accept the principal. Check the pipeline slug, stored children, files and raw children before anything runs.
- **Depends on**: Module 5 (the `AbstractQuery._principal` storage and the `BaseQuery` forwarding)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/__init__.py  (modifies MultiQS.__init__ :96-118 and query :203)
  def __init__(self, slug: str = None, queries: list | None = None, files: list | None = None,
               query: dict | None = None, conditions: dict = None, request: web.Request = None,
               loop: asyncio.AbstractEventLoop = None, user_session: object | None = None, *,
               tenant: str | None = None, principal: "QSPrincipal | None" = None, **kwargs): ...

  async def _preflight_principal(self) -> None:
      """With a principal: enforce slug:execute for every stored child slug in self._queries,
      slug:execute for every self._files entry, and raw_query:execute once when any child
      carries an inline 'query'. Any deny raises QueryAccessDenied before any child runs.
      No-op when self._principal is None."""
  # query(): with a principal, enforce slug:execute on self.slug before get_slug (verified: :225),
  # collapsing SlugNotFound / not-found TenantError into QueryAccessDenied; call
  # _preflight_principal() after pipeline expansion and before the source-count guard (verified: :269);
  # in the child pre-flight loop (verified: :327-335) re-raise not-found as QueryAccessDenied
  # instead of wrapping it in self.Error when a principal is set.
  ```

### Module 7: Documentation
- **Path**: `docs/PBAC_PROGRAMMATIC.md` (new), `docs/PER_TENANT_QUERIES.md` (link)
- **Responsibility**: Operator and integrator guide: `QSPrincipal` usage, the decision matrix, the existence collapse, and that credentials stay trusted-service. Documents that request-derived policy conditions do not exist on this path.
- **Depends on**: Module 5, Module 6

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_principal_requires_user_id` | M1 | empty/blank `user_id` → `ValueError` |
| `test_principal_to_userinfo_shape` | M1 | exact key set; `tenant_id`/`channel` absent; username falls back to user_id |
| `test_principal_normalizes_sequences` | M1 | lists → tuples of str |
| `test_principal_for_authz_shape` | M1 | `for_authz('ip')` → user_id `authz:ip`, groups `('authorized','ip')`; `to_userinfo()` equals the handler's dict |
| `test_principal_authz_rejects_extra_claims` | M1 | `authz_backend` with extra groups/roles/programs/superuser → `ValueError`; blank backend → `ValueError` |
| `test_query_access_denied_is_query_exception` | M1 | subclass of `QueryException`, code 404, generic default message |
| `test_setup_pbac_records_runtime_bootstrap` | M2 | successful bootstrap → `get_pbac_runtime()` returns guardian/evaluator |
| `test_setup_pbac_records_runtime_reuse` | M2 | reuse branch records the pre-existing instances |
| `test_setup_pbac_failure_leaves_runtime_none` | M2 | import/policy failure → runtime stays None |
| `test_resolve_evaluator_request_and_runtime` | M3 | request path reads app; None path reads runtime; guardian w/o evaluator → `(True, None)` |
| `test_resolve_evaluator_detached_copy` | M3 | fresh `_cache`, copied `_stats`, app evaluator untouched |
| `test_build_eval_context_without_request` | M3 | real `EvalContext` built with `_ServiceRequest` on 0.26.0; `userinfo` preserved |
| `test_build_eval_context_prefers_from_userinfo` | M3 | when `EvalContext.from_userinfo` exists (monkeypatched), it is called and `_ServiceRequest` is not built |
| `test_enforce_principal_authz_flag` | M3 | authz principal: flag off → `QueryAccessDenied`; flag on → evaluated with `user=None` and the handler's userinfo |
| `test_evaluate_exception_is_deny` | M3 | evaluator raising → `allowed=False` |
| `test_evaluate_awaits_coroutine` | M3 | async `check_access` result is awaited, not truthy-bypassed |
| `test_enforce_principal_matrix` | M3 | PBAC off → allowed; missing evaluator → denied; deny → `QueryAccessDenied`; allow → decision |
| `test_enforce_principal_warns_once_when_enabled_but_absent` | M3 | `QS_PBAC_ENABLED=True`, no runtime → single warning |
| `test_real_evaluator_contract` | M3 | skip-if-no-Rust (pattern of `tests/policies/test_authorized_policy.py:17`): allow/deny policy decided for a request-less context from userinfo groups alone |
| existing `tests/handlers/test_abstract_pbac_helpers.py`, `test_*_pbac_smoke.py`, `tests/auth/test_slug_visibility.py`, `tests/tenants/test_tenant_policy_preflight.py` | M4 | pass unmodified (behaviour pin) |
| `test_qs_request_and_principal_is_value_error` | M5 | both given → `ValueError` |
| `test_qs_principal_denied_before_store_resolution` | M5 | deny → `QueryAccessDenied`; `registry.resolve`, `repo.get`, `get_provider` and the cache never called |
| `test_qs_principal_missing_slug_collapses` | M5 | `TenantError(query_not_found)` → `QueryAccessDenied` |
| `test_qs_principal_unknown_tenant_collapses` | M5 | `TenantError(tenant_not_available)` → `QueryAccessDenied` |
| `test_qs_principal_store_unavailable_not_collapsed` | M5 | `tenant_store_unavailable` propagates unchanged |
| `test_qs_principal_raw_query_action` | M5 | `query`/`raw`/`driver` types check `raw_query:execute` |
| `test_qs_principal_tenant_not_routing` | M5 | `principal.tenant_id` differs from `tenant=` → store chosen by `tenant=` |
| `test_qs_no_principal_unchanged` | M5 | no enforcement call at all when principal is None |
| `test_multiqs_principal_denied_child_runs_nothing` | M6 | one denied child → `QueryAccessDenied`, no child executed |
| `test_multiqs_principal_parent_slug_checked` | M6 | the pipeline's own slug is gated before `get_slug` |
| `test_multiqs_principal_files_and_raw` | M6 | files → `slug:execute`; inline query child → `raw_query:execute` |
| `test_multiqs_principal_missing_child_not_wrapped` | M6 | not-found child → `QueryAccessDenied`, not `self.Error` |
| `test_scheduler_jobs_unchanged` | M5/M6 | `scheduler/jobs.py` constructions still execute with no enforcement |

### Integration Tests
| Test | Description |
|---|---|
| `test_qs_principal_end_to_end_policy_dir` | real `setup_pbac` on an app with a temp policy dir (allow group `sales` on slug A only); `QS(principal=sales_user)` runs A with a mocked provider and gets `QueryAccessDenied` on B |

### Test Data / Fixtures
```python
@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", username="jdoe", groups=("sales",), tenant_id="client_b")

@pytest.fixture(autouse=True)
def _reset_runtime():
    clear_pbac_runtime()
    yield
    clear_pbac_runtime()
```

---

## 5. Acceptance Criteria

- [ ] `QS(..., principal=QSPrincipal(...))` and `MultiQS(..., principal=...)` evaluate `slug:execute` (and `raw_query:execute` for raw/query/driver types and inline raw children) through the configured PBAC policies.
- [ ] With `principal=None`, `QS`/`MultiQS` never call the enforcement core; the scheduler (`scheduler/jobs.py:87,144,190`) is unchanged.
- [ ] Credentials on the principal path are the trusted-service ones: `get_provider` still receives `session=None, app=None` when no request is given.
- [ ] A deny raises `QueryAccessDenied` (a `QueryException`, code 404) before store resolution, definition load, `get_provider` and any cache access.
- [ ] With a principal, `TenantError` `query_not_found` / `tenant_not_available` and `SlugNotFound` become `QueryAccessDenied`. `tenant_store_unavailable` is not collapsed.
- [ ] `tenant=` alone selects the store. `principal.tenant_id` never enters userinfo, never selects a store and is never used as `org_id`/`client_id`.
- [ ] PBAC not configured (no runtime) + principal → no-op with a debug log. With `QS_PBAC_ENABLED=True`, exactly one warning per process.
- [ ] Guardian registered without an evaluator, or an evaluator exception → deny (fail-closed).
- [ ] Without a request, `build_eval_context` uses `EvalContext.from_userinfo` when navigator-auth provides it, and `_ServiceRequest` otherwise. Both paths are tested.
- [ ] `QSPrincipal.for_authz(backend)` yields exactly the handler's synthetic userinfo. It is honoured only when `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` is true, and denied otherwise when PBAC is active. An authz principal with extra claims cannot be constructed.
- [ ] Every principal-path check uses a detached evaluator copy, and the app evaluator's `_cache`, `_stats` and TTL are unchanged afterwards.
- [ ] `QS`/`MultiQS` given both `request=` and `principal=` → `ValueError`.
- [ ] `MultiQS`: the pipeline slug, every stored child, every file and inline raw children are all checked before any child executes; one deny means none run.
- [ ] Decision logs contain principal user id, principal tenant, `tenant=` selector, resource, action and outcome. The `QueryAccessDenied` message names no policy.
- [ ] `_enforce_pbac`, `_enforce_owned_slug`, `slug_visibility._evaluator_state` / `_eval_context` / `can_access` delegate to `querysource/auth/enforcement.py`; neither `querysource/handlers/abstract.py` nor `querysource/auth/slug_visibility.py` calls `check_access(` directly any more (`grep -c "check_access(" <file>` → 0 for both). (`filter_resources` in `slug_visibility.filter_visible` and `handlers/multi.py` is out of scope.)
- [ ] Existing PBAC suites pass unmodified: `pytest tests/handlers tests/auth tests/tenants tests/policies tests/scheduler tests/multi -q`.
- [ ] New tests from §4 pass: `pytest tests/auth/test_principal.py tests/auth/test_enforcement.py tests/auth/test_pbac_runtime.py tests/queries/test_qs_principal.py tests/multi/test_multiqs_principal.py -q`.
- [ ] navigator-auth imports stay lazy (no top-level import in `enforcement.py`/`principal.py`); importing `querysource.auth` with `QS_PBAC_ENABLED=False` imports no `navigator_auth.abac` module.
- [ ] `ruff check` clean on all changed paths.
- [ ] `docs/PBAC_PROGRAMMATIC.md` written and linked from `docs/PER_TENANT_QUERIES.md`.
- [ ] No breaking change to any existing public signature (new kwargs are keyword-only with default `None`).

---

## 6. Codebase Contract

> Verified against: `a525718` (dev, 2026-09-24). Line numbers re-checked at spec time; `/sdd-task` must re-run `grep -c` for every Edit Site row.

### Verified Imports
```python
from querysource.queries.qs import QS                              # querysource/queries/qs.py
from querysource.queries.multi import MultiQS                      # querysource/queries/multi/__init__.py:90
from querysource.queries.base import BaseQuery                     # querysource/queries/base.py:19
from querysource.interfaces.queries import AbstractQuery           # querysource/interfaces/queries.py:41
from querysource.exceptions import QueryException, SlugNotFound, QueryNotFound  # exceptions.py:6,34,52
from querysource.tenant_errors import TenantError, OWNERSHIP_STATUS  # tenant_errors.py:15,5
from querysource.tenants import QueryIdentity                      # tenants.py:46
from querysource.auth import setup_pbac, ResourceType              # auth/__init__.py:18-19
from querysource.auth.slug_visibility import Principal, PrincipalKind, can_access, filter_visible  # :36,:25,:353,:283
from querysource.conf import QS_PBAC_ENABLED, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ  # conf.py:441,453
# lazy only (inside functions):
from navigator_auth.abac.context import EvalContext                # navigator-auth 0.26.0
from navigator_auth.abac.policies.environment import Environment
from navigator_auth.conf import AUTH_SESSION_OBJECT
```

### Existing Class Signatures
```python
# querysource/interfaces/queries.py:41
class AbstractQuery(Connection):
    def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None, **kwargs)  # :48-56
    # self._request = request            :91
    # self._tenant_selector = tenant     :104
    def Error(...)                       # :425

# querysource/queries/base.py:19
class BaseQuery(AbstractQuery):
    def __init__(self, slug=None, conditions=None, request=None, loop=None, *, tenant=None, **kwargs)  # :21-40

# querysource/queries/qs.py
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None, **kwargs)  # :42-57
    # _type: 'slug' (:73) | 'query' (:78) | 'raw' (:84) | 'driver' (:90)
    async def build_provider(self)                                   # :137
        # :151-165 request-only _pbac_app/_pbac_session extraction
        # :167 if self._type == 'slug' ; :173-178 resolve store + repo.get ; :188 get_provider
        # :248 elif 'query' ; :291 elif 'raw' ; :296 elif 'driver'
    async def query(self, output_format: str | None = None)          # :376 ; build_provider :384 ; cache :395+

# querysource/queries/multi/__init__.py:90
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None,
                 request=None, loop=None, user_session: object | None = None, *,
                 tenant: str | None = None, **kwargs)                 # :96-118
    # self._user_session = user_session  :147 (never read)
    async def query(self)                                             # :203
        # :225 get_slug(slug=self.slug, tenant=self._tenant_selector) ; :269 source-count guard
        # :288-336 child pre-flight; :324 registry.resolve(child_tenant); :327-335 repo.get wrapped in self.Error

# querysource/interfaces/connections.py
async def get_provider(self, entry: dict, session=None, app=None)   # :262
async def get_definition_repository(self) -> DefinitionRepository  # :438
async def get_slug(self, slug, program=None, evt=None, *, tenant=None)  # :526 ; raises SlugNotFound when None

# querysource/repositories/definitions.py:56
class DefinitionRepository:
    async def get(self, identity: QueryIdentity) -> LoadedDefinition   # :161 ; :166 TenantError(error_code="query_not_found")

# querysource/tenants.py:80 TenantRegistry.resolve → TenantError(error_code="tenant_not_available") (:422)
# querysource/tenant_errors.py:5 OWNERSHIP_STATUS {invalid_tenant:400, tenant_not_available:404, query_not_found:404,
#                                                 tenant_store_unavailable:503, tenant_write_forbidden:403, tenant_worker_unsupported:502}

# querysource/handlers/abstract.py
_SENTINEL = object()                                                   # :30
async def _get_user_session(self, request)                             # :295
async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None  # :323
    # :346-348 no-op; :359-365 missing name; :367-398 sessionless authz; :400-406 evaluator missing
    # :431 EvalContext(...); :443 evaluator.check_access(...); :450-451 iscoroutine
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None  # :463
    # :496-499 detached copy ; :543 EvalContext ; :551 detached.check_access

# querysource/auth/slug_visibility.py
class PrincipalKind(str, Enum)                                         # :25
@dataclass(frozen=True) class Principal: kind, userinfo, groups, programs, session   # :36
async def resolve_principal(request, session) -> Principal             # :99
def _evaluator_state(request, *, detached=False) -> tuple[bool, Any]   # :227
def _eval_context(request, principal: Principal) -> Any                # :260
async def filter_visible(request, principal, slugs, ...)               # :283
async def can_access(request, principal, slug, primary_action, fallback_action=None, *, detached=False) -> bool  # :353

# querysource/auth/pbac.py:28
def setup_pbac(app, policy_dir="policies", cache_ttl=300) -> tuple[PDP|None, PolicyEvaluator|None, Guardian|None]
    # :62-68 reuse branch ; :137-140 registration ; failure returns (None, None, None)

# querysource/services.py:146-150 — QuerySource.setup() calls setup_pbac(self.app, ...) when QS_PBAC_ENABLED

# navigator-auth 0.26.0 (installed, verified via inspect)
EvalContext.__init__(self, request, user, userinfo, session, *args, org_id=None, client_id=None, **kwargs)
    # reads request.remote/.method/.headers/.path_qs/.path/.rel_url unguarded; is_authenticated via try/AttributeError
PolicyEvaluator.check_access(ctx, resource_type, resource_name, action, env=None, owner_reports_to=None,
                             org_id=1, client_id=1) -> EvaluationResult
    # uses ONLY ctx.userinfo (username/user_id, groups, scopes, client_id) for the cache key and
    # _build_user_context(ctx) → {username, groups, roles}; request fields never reach the Rust engine
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `enforce_principal` | `PolicyEvaluator.check_access` | `evaluate()` | `handlers/abstract.py:443` (same call shape) |
| `resolve_evaluator(None, …)` | `get_pbac_runtime()` | function call | new (M2) |
| `setup_pbac` | `_set_pbac_runtime` | call in both success branches | `auth/pbac.py:62-68`, `:137-140` |
| `QS.build_provider` | `enforce_principal` | call before `repo.registry.resolve` | `queries/qs.py:175` |
| `MultiQS.query` | `enforce_principal` / `_preflight_principal` | calls before `get_slug` and the source guard | `queries/multi/__init__.py:225`, `:269` |
| `_enforce_pbac` / `_enforce_owned_slug` | `resolve_evaluator` / `build_eval_context` / `evaluate` | delegation | `handlers/abstract.py:405,431,443,543,551` |

### Does NOT Exist (Anti-Hallucination)
- ~~`QS(..., principal=...)` / `MultiQS(..., principal=...)` / `AbstractQuery._principal`~~: added by this spec.
- ~~`querysource.auth.principal` / `querysource.auth.enforcement`~~: new modules.
- ~~`QueryAccessDenied`~~: not in `querysource/exceptions.py`.
- ~~`get_pbac_runtime()` or any module-level evaluator accessor~~: the evaluator lives only on `app['policy_evaluator']` today.
- ~~Any PBAC check inside `QS` / `MultiQS` / `QueryObject`~~: enforcement is handler-only today.
- ~~`EvalContext.from_userinfo()` or another request-free constructor in navigator-auth 0.26.0~~. It is planned upstream (§2 contract), so always feature-detect it with `hasattr` and never assume it exists.
- ~~`querysource/tenants/` package~~: tenancy is `querysource/tenants.py` + `querysource/tenant_errors.py`.
- ~~A `check_access(..., bypass_cache=...)` argument~~: detaching is the only isolation mechanism (FEAT-147 L227-234).
- ~~Use of `MultiQS._user_session`~~: stored at `:147`, never read; do not repurpose it.
- ~~`QuerySource().app` as a safe lookup~~: calling `QuerySource()` constructs the singleton if absent.

### Edit Sites (Blueprint Anchors)

Verified against: `a525718`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/auth/principal.py` | CREATE | — | — | — |
| `querysource/auth/enforcement.py` | CREATE | — | — | — |
| `querysource/exceptions.py` | MODIFY | `class QueryNotFound(QueryException):` | `exceptions.py:52` | 1 |
| `querysource/auth/__init__.py` | MODIFY | `__all__ = (` | `auth/__init__.py:21` | 1 |
| `querysource/auth/pbac.py` | MODIFY | `        return (app.get("abac"), existing_evaluator, existing_guardian)` | `auth/pbac.py:68` | 1 |
| `querysource/auth/pbac.py` | MODIFY | `    app["policy_evaluator"] = evaluator` followed by `    app["credential_resolver"] = CredentialResolver(logger=_log)` (the unindented-branch pair, not the `if "credential_resolver" not in app:` one at :66-67) | `auth/pbac.py:139-140` | 2 (`credential_resolver` line) |
| `querysource/interfaces/queries.py` | MODIFY | `        self._tenant_selector = tenant` | `interfaces/queries.py:104` | 1 |
| `querysource/queries/base.py` | MODIFY | `            tenant=tenant,` | `queries/base.py:39` | 1 |
| `querysource/queries/qs.py` | MODIFY | `            tenant: str \| None = None,` | `qs.py:49` | 1 |
| `querysource/queries/qs.py` | MODIFY | `            store = repo.registry.resolve(self._tenant_selector)` | `qs.py:175` | 1 |
| `querysource/queries/qs.py` | MODIFY | `        elif self._type == 'query':` | `qs.py:248` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `            user_session: object \| None = None,` | `multi/__init__.py:105` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `            query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)` | `multi/__init__.py:225` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `        total_sources = (` | `multi/__init__.py:269` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                child_store = repo.registry.resolve(child_tenant)` | `multi/__init__.py:324` | 1 |
| `querysource/handlers/abstract.py` | MODIFY | `        result = evaluator.check_access(` | `handlers/abstract.py:443` | 1 |
| `querysource/handlers/abstract.py` | MODIFY | `        result = detached.check_access(` | `handlers/abstract.py:551` | 1 |
| `querysource/auth/slug_visibility.py` | MODIFY | `def _evaluator_state(request: web.Request, *, detached: bool = False) -> tuple[bool, Any]:` | `slug_visibility.py:227` | 1 |
| `querysource/auth/slug_visibility.py` | MODIFY | `def _eval_context(request: web.Request, principal: Principal) -> Any:` | `slug_visibility.py:260` | 1 |
| `querysource/auth/slug_visibility.py` | MODIFY | `        primary_result = evaluator.check_access(` (inside `can_access`; also `            fallback_result = evaluator.check_access(` at :398) | `slug_visibility.py:380` | 1 |
| `docs/PBAC_PROGRAMMATIC.md` | CREATE | — | — | — |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | (append a "Programmatic callers" link section at end of file) | — | — |
| `tests/auth/test_principal.py`, `tests/auth/test_enforcement.py`, `tests/auth/test_pbac_runtime.py`, `tests/queries/test_qs_principal.py`, `tests/multi/test_multiqs_principal.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Lazy navigator-auth imports inside functions, as in `handlers/abstract.py:411-413` and `auth/pbac.py:6-9`.
- Detached evaluator: `copy.copy(evaluator)`, then `_cache = {}` and `_stats = dict(evaluator._stats)` (`abstract.py:496-499`). Never mutate the original.
- The `iscoroutine` guard on every `check_access` result (`abstract.py:450-451`).
- Logging via `logging.getLogger(__name__)` in new modules, and `self._logger` in QS/MultiQS.
- Google-style docstrings and strict type hints; keyword-only new kwargs with default `None`.
- Tests: `pytest-asyncio` (`asyncio_mode = auto`). Mock providers and the repository, following `tests/tenants/test_tenant_policy_preflight.py`. Gate the real-evaluator contract test with `_evaluator_available()` (pattern `tests/policies/test_authorized_policy.py:17`).

### Known Risks / Gotchas
- **navigator-auth internals.** The stand-in relies on `EvalContext.__init__` touching only the attributes listed in §6. A navigator-auth upgrade may break it. Mitigation: `test_real_evaluator_contract` plus `test_build_eval_context_without_request`. Once the upstream `from_userinfo` factory is released, it is preferred automatically. A later cleanup can drop `_ServiceRequest` and raise the navigator-auth floor.
- **Request-derived conditions.** None reach the 0.26.0 Rust engine (`_build_user_context` returns only username, groups and roles, and the env comes from `Environment()`). If a later navigator-auth version adds request-based conditions, they will not match on the principal path. Document this in M7.
- **Detached = no decision cache** on the principal path. Every check is a fresh evaluation. That is acceptable (in-process Rust, a small number of checks per query) and required for tenant isolation (FEAT-147 L227-234).
- **Handler refactor regressions.** The 404 mapping, the sessionless-authz synthetic identity (`abstract.py:367-398`), the fail-closed path for a missing evaluator (`:400-406`) and the missing-name short-circuit (`:359-365`) must stay byte-for-byte equivalent in outcome. The existing suites are the pin and must pass unmodified.
- **`self.Error` wrapping in MultiQS** (`multi/__init__.py:330-335`) would turn `QueryAccessDenied` into a generic error. With a principal, re-raise it before wrapping.
- **`get_slug` raises `SlugNotFound`**, not `TenantError` (`interfaces/connections.py:545-548`). Both must be collapsed for the MultiQS parent.
- **The runtime handle is process-global.** If two apps call `setup_pbac` in one process (tests), the last one wins. Tests clear it with `clear_pbac_runtime()` in an autouse fixture.
- **Existence collapse is principal-only.** Without a principal, `SlugNotFound` / `TenantError` keep their current types and HTTP codes.
- **Brainstorm refinement:** optional numeric `org_id`/`client_id` were dropped from `QSPrincipal`, because no QuerySource path passes a tenant pair to `check_access` today. This avoids a field that silently does nothing (see Non-Goals).
- **`MultiQuerySlugSource` in ai-parrot** runs one `QS` per slug, so it gets per-slug enforcement for free once ai-parrot passes the principal.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `navigator-auth` | `0.26.0` (installed; no pin change) | `EvalContext`, `PolicyEvaluator.check_access`, `Environment` |
| `multidict`, `yarl` | already transitive via aiohttp | `_ServiceRequest` headers / `rel_url` |

---

## 8. Open Questions

- [x] Flow type / base branch — *Resolved in brainstorm*: feature → dev.
- [x] Goal — *Resolved in brainstorm*: PBAC + tenant routing; credentials stay trusted-service.
- [x] Identity transport — *Resolved in brainstorm*: explicit principal argument (not a synthetic request or contextvar).
- [x] Scope — *Resolved in brainstorm*: QuerySource side only; ai-parrot changes are a separate follow-up.
- [x] Evaluator source without request — *Resolved in brainstorm*: QuerySource singleton/global state → realized as the `setup_pbac()`-recorded runtime handle (§2 item 3).
- [x] Principal shape — *Resolved in brainstorm*: new QS-owned dataclass (`QSPrincipal`).
- [x] PBAC not configured — *Resolved in brainstorm*: no-op with debug log.
- [x] Principal tenant vs `tenant=` — *Resolved in brainstorm*: `tenant=` wins; principal tenant only carried into context/logs → logs only (not userinfo), per FEAT-147 L235.
- [x] MultiQS — *Resolved in brainstorm*: included; every stored child checked before any executes.
- [x] Deny surface — *Resolved in brainstorm*: new `QueryAccessDenied(QueryException)`, non-leaking message.
- [x] Handler refactor — *Resolved in brainstorm*: extract the core; handlers and slug_visibility delegate.
- [x] Missing slug vs denied — *Resolved at spec time (Jesus Lara)*: collapse to denied when a principal is supplied (`query_not_found`, `tenant_not_available`, `SlugNotFound` → `QueryAccessDenied`).
- [x] Request-free `EvalContext` — *Resolved at spec time from research*: a local neutral `_ServiceRequest` stand-in with the real constructor; navigator-auth 0.26.0 `check_access` reads only userinfo/user.
- [x] Singleton lookup mechanics — *Resolved at spec time from research*: module-level runtime handle recorded by `setup_pbac()` in both success branches; never `QuerySource()`.
- [x] Always detached on the principal path — *Resolved at spec time*: yes; handler paths keep their current shared/detached choice.
- [x] PBAC enabled but bootstrap failed/absent with principal — *Resolved at spec time*: stays a no-op (brainstorm decision) with one warning per process.
- [x] Should navigator-auth gain an `EvalContext.from_userinfo()` factory? — *Resolved (Jesus Lara, 2026-09-24)*: yes, in the navigator-auth repo (`../navigator-auth`) under its own spec, with the contract in §2. FEAT-150 feature-detects it and falls back to `_ServiceRequest` on 0.26.0.
- [x] Does `QSPrincipal` need a sessionless-authz form? — *Resolved (Jesus Lara, 2026-09-24)*: yes. `QSPrincipal.for_authz(backend)` mirrors the handler identity and is gated by `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` (§2 item 1, M1, M3).
- [x] ai-parrot follow-up — *Resolved (Jesus Lara, 2026-09-24)*: accepted as a separate ai-parrot feature after FEAT-150 ships in QuerySource 5.1.0. It maps `PermissionContext` → `QSPrincipal` in `QuerySlugSource`/`MultiQuerySlugSource`, and makes `_refresh` pass `record.tenant` as `tenant=` instead of the `build_principal_context` default. Out of scope here.

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (exploration doc status is `exploration`, not `accepted`) · Transcript: —

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation**: one feature worktree (`feat-FEAT-150-pbac-request-credentials`); the `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph** (edges = imports/uses):
  - M3 → M1 (imports `QSPrincipal`, `QueryAccessDenied`)
  - M3 → M2 (imports `get_pbac_runtime`)
  - M4 → M3 (delegates to `resolve_evaluator` / `build_eval_context` / `evaluate`)
  - M5 → M1, M3 (kwarg type; calls `enforce_principal`)
  - M6 → M5 (relies on `AbstractQuery._principal` storage and the `BaseQuery` forwarding added by M5)
  - M7 → M5, M6 (documents their behaviour)
  - M1 ∥ M2 run concurrently; M4 ∥ M5 run concurrently after M3.
- **Shared files**: `querysource/auth/__init__.py` (M1 exports `QSPrincipal`, M2 exports `get_pbac_runtime`), so serialize M1/M2 on that file or let M2 add both exports.
- **Exclusive resources**: none (no Cython/Rust rebuild, no lockfile or migration).
- **Cross-feature dependencies**: FEAT-147 (per-tenant-queries) and FEAT-148 (describe-queryslug) are merged on `dev`. No in-flight spec touches these files; FEAT-149 (falsy-refresh) lists `QS.query()` (`qs.py:385,414`) as a dependency it leaves unchanged, so there is no file overlap. Still rebase on `dev` before M5. **navigator-auth** `EvalContext.from_userinfo` (separate spec in `../navigator-auth`) is a soft dependency: FEAT-150 feature-detects it and does not wait for it.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-24 | Jesus Lara / Claude | Initial draft from brainstorm Option A; FEAT-150 reserved; spec-time decisions: existence collapse, `_ServiceRequest` stand-in, `setup_pbac` runtime handle, always-detached principal path. |
| 0.2 | 2026-09-24 | Jesus Lara / Claude | Resolved the last 3 open questions: upstream `EvalContext.from_userinfo` contract + feature detection; `QSPrincipal.for_authz` sessionless form gated by `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`; ai-parrot follow-up accepted as a separate feature. |
