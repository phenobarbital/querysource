# Feature Specification: PBAC Support in QuerySource

**Feature ID**: FEAT-091
**Date**: 2026-04-29
**Author**: Jesus Lara / Claude
**Status**: approved
**Target version**: 5.0.0

> Source brainstorm: `sdd/proposals/pbac-support.brainstorm.md`
> Recommended option: **A — Mirror ai-parrot's PBAC pattern + driver-layer credential resolver**

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource currently runs every query without consulting any policy. Any
authenticated caller of `QueryExecutor`, `QueryService`, or `QueryHandler`
(multi-query) can execute any registered slug, run arbitrary raw queries
against any registered datasource, and use any registered driver. The
`DatasourceView` and `DatasourceDrivers` endpoints likewise return the full
registry to every user. This is acceptable inside a tightly-controlled
deployment but breaks down when the same QS process serves users with
different privilege levels — engineers, analysts, embedded service tokens,
report consumers — through `navigator-api`.

Two coupled gaps:

1. **Authorization gap.** No mechanism filters what slugs/datasources/drivers a
   user can see or execute. Today `querysource.conf` defines two credential
   sets — `PG_*` (read-only) and `DB*` (full access) — but only the read-only
   one is exposed as a registered datasource, and there is no way to grant
   some users the full-access one.
2. **Per-user database identity.** Even within "read-only Postgres," operators
   want individual users to connect with their **own** database accounts
   (so DB-level audit, row-level security, and quota systems can attribute
   actions to the right person), with a fall-back to the default datasource
   credentials when no per-user account is configured.

`navigator-auth` already ships a production-ready PBAC engine
(`PDP`, `Guardian`, `PolicyEvaluator`, Rust-backed `rs_pep` extension) and
`ai-parrot` already wires it in for agent/tool filtering. QuerySource will
reuse the same engine, mirror the same YAML conventions, and stay compatible
with the rest of the suite.

### Goals

- Add **policy-based access control** to QuerySource's HTTP surface
  (`QueryExecutor`, `QueryService`, `QueryHandler`/multi-query, `DatasourceView`,
  `DatasourceDrivers`) using `navigator-auth`'s engine **directly**.
- Toggle enforcement via a single `QS_PBAC_ENABLED` setting in
  `querysource.conf`. Default `False` — zero behavioural change for existing
  deployments.
- On denial, **hide existence**: HTTP **404** for execution endpoints; silent
  list filtering for `DatasourceView` / `DatasourceDrivers`.
- Enforce **all-or-nothing** semantics for `MultiQS`: any one denied component
  rejects the entire multi-query before execution starts.
- Gate **raw inline queries** with an explicit `raw_query:execute` action so
  ad-hoc SQL/REST/GraphQL queries are admin-only by default.
- Add a **per-user credential resolver** at the driver layer with two-level
  override: `<PREFIX>_{USERNAME}_*` env vars (per-user) and
  `<PREFIX>_{PROFILE}_*` env vars (profile-from-policy), falling back to each
  registered datasource's default prefix.
- Register a new **`pg_admin`** datasource (full-access `DB*` credentials) so
  the existing `postgres` (read-only `PG_*`) and `pg_admin` are policy-gated
  as distinct entries in the registry.
- Bootstrap PBAC automatically from `QuerySource.setup(app)`. Consuming apps
  do not need to call any extra setup function.
- Ship default YAML policies under `policies/` (path configurable via
  `QS_POLICY_PATH`, default `BASE_DIR / 'policies'`).

### Non-Goals (explicitly out of scope)

- DB-backed (PostgreSQL) policy storage. YAML files only in v1.
- Mutating policy management endpoints inside QuerySource itself
  (`navigator-auth` already exposes `POST /api/v1/abac/reload` and
  policy CRUD endpoints when its `PDP.setup(app)` is called by the host app).
- Group-tier env-var lookup (e.g. `PG_{GROUP}_HOST`) — group precedence is
  the policy engine's job; only per-user and profile-from-policy overrides are
  in scope at the env-var layer.
- A second, QuerySource-private decision cache. Reuse navigator-auth's
  existing LRU + TTL cache.
- `DatasourceView` mutating verbs (POST/PUT/DELETE) — the v1 list-endpoint
  filtering covers `GET` only. (See Open Questions §8.)
- Rolling out the `params_for(session)` credential-resolver hook to every
  driver in the `SUPPORTED` registry. v1 ships only **Postgres** (`pg.py`)
  with the hook; other SQL/NoSQL/cloud drivers continue to use their existing
  `params()` until follow-up work. (See Open Questions §8.)
- Aiohttp middleware-based enforcement. QuerySource handlers enforce
  explicitly because resource extraction is dynamic.

---

## 2. Architectural Design

### Overview

A new `querysource/auth/` package owns PBAC bootstrap and credential
resolution. `QuerySource.setup(app)` (the existing Singleton-pattern startup
hook in `querysource/services.py:80`) gains a single new step: when
`QS_PBAC_ENABLED=True`, it calls `setup_pbac(app, policy_dir, cache_ttl)`,
which wires `PDP` + `PolicyEvaluator` + `Guardian` from navigator-auth onto
the aiohttp app.

`AbstractHandler` (the parent of every QuerySource handler) gains two
helpers — `_get_user_session(request)` and `_enforce_pbac(...)`. The three
execution handlers (`QueryService`, `QueryExecutor`, `QueryHandler`) call
`_enforce_pbac` at well-defined points (slug name in scope, datasource and
driver resolved, raw-query payload detected). Denials raise `web.HTTPNotFound`
to hide existence.

`MultiQS` accepts the user session in its constructor; the handler runs a
single `Guardian.filter_resources()` pre-flight per resource type before
entering the parallel execution loop. Any denied component fails the whole
request.

`DatasourceView.get()` and the `DatasourceDrivers` listing call
`Guardian.filter_resources()` to silently drop unauthorised entries before
serializing.

The driver layer gets a new `params_for(session)` hook. The Postgres driver
(`pg.py`) is the v1 implementer. It consults `app['credential_resolver']` to
build connection params: per-user `<PREFIX>_<USERNAME>_*` overrides → policy
`credential_profile` attribute → datasource default. A new `pg_admin`
registered datasource (using `DB*` env-var prefix) is added alongside
`postgres` (using `PG_*`) so they can be policy-gated as separate entries.

### Component Diagram

```
                       querysource/services.py
                           QuerySource.setup(app)
                                     │
            ┌────────────────────────┼─────────────────────────┐
            ▼                        ▼                         ▼
   connection.setup(app)   TemplateParser.setup(app)   setup_pbac(app, ...)
                                                         │  (only if QS_PBAC_ENABLED)
                                                         ▼
                                          ┌──────────────────────────────────┐
                                          │  navigator-auth                  │
                                          │   PDP / Guardian /               │
                                          │   PolicyEvaluator / rs_pep       │
                                          └──────────────────────────────────┘
                                                         │
                                                         ▼
                                          app['security']  ← Guardian
                                          app['abac']      ← PDP
                                          app['policy_evaluator'] ← PolicyEvaluator
                                          app['credential_resolver'] ← CredentialResolver

   ── Per-request enforcement ──────────────────────────────────────────────
   AbstractHandler._enforce_pbac(request, resource_type, resource_name, action)
        │
        ├── QueryService.query()    → SLUG / DATASOURCE / DRIVER checks
        ├── QueryExecutor.query()   → SLUG-or-RAW_QUERY / DATASOURCE / DRIVER checks
        └── QueryHandler.query()    → MultiQS pre-flight (batch filter_resources)

   ── Per-listing filtering ────────────────────────────────────────────────
   DatasourceView.get()      → Guardian.filter_resources(... DATASOURCE ...)
   DatasourceDrivers.get()   → Guardian.filter_resources(... DRIVER ...)

   ── Driver-layer credential resolution ──────────────────────────────────
   pgDriver.params_for(session) → app['credential_resolver'].resolve(...)
        ├─ 1. PG_<USERNAME>_HOST/PORT/USER/PASSWORD/DATABASE  (per-user override)
        ├─ 2. PG_<PROFILE>_*   (profile-from-policy attribute)
        └─ 3. PG_*             (datasource default)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QuerySource.setup(app)` (`querysource/services.py:80`) | extends | Calls `setup_pbac` after `connection.setup` and `TemplateParser.setup`, before route registrations, gated by `QS_PBAC_ENABLED`. |
| `AbstractHandler` (`querysource/handlers/abstract.py:18`) | extends | Adds `_get_user_session` + `_enforce_pbac` helpers used by all handlers. |
| `QueryService.query` (`querysource/handlers/service.py:134`) | modifies | Pre-execution PBAC checks for slug, datasource, driver. |
| `QueryExecutor.query` (`querysource/handlers/executor.py:51`) | modifies | Pre-execution PBAC checks; `raw_query:execute` for inline queries; datasource and driver checks. |
| `QueryExecutor.dry_run` (`querysource/handlers/executor.py:104`) | modifies | Same checks as `.query()` so dry-run cannot be used to probe slug existence. |
| `QueryHandler.query` (`querysource/handlers/multi.py:32`) | modifies | Pre-flight `Guardian.filter_resources()` over slugs, files, raw queries; all-or-nothing rejection. |
| `MultiQS.__init__` / `MultiQS.query` (`querysource/queries/multi/__init__.py:53,102`) | modifies | Accepts `user_session=...`; enforcement remains in the handler pre-flight. |
| `DatasourceView.get` (`querysource/datasources/handlers/datasource.py:74`) | modifies | Filters merged result list via `Guardian.filter_resources()` before responding. |
| `default_sources()` (`querysource/datasources/handlers/datasource.py:33`) | modifies | Driver-listing handler filters its return list the same way. |
| `pgDriver.params` (`querysource/datasources/drivers/pg.py:42`) | modifies | New `params_for(session)` hook; `params()` keeps current behaviour when no session is supplied. |
| `SUPPORTED` registry (`querysource/datasources/drivers/__init__.py:29`) | extends | Registers new `pg_admin` driver. |
| `pyproject.toml` | modifies | Bumps `navigator-auth` pin from `>=0.15.8` to `>=0.20.0`. |
| `querysource/conf.py` | extends | Adds `QS_PBAC_ENABLED`, `QS_POLICY_PATH`, `QS_PBAC_CACHE_TTL`. |
| `navigator-auth` (upstream) | extends | New `ResourceType` values (`SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`) and matching `ActionType` constants — additive enum extension. |

### Data Models

```python
# querysource/auth/credentials.py
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass(slots=True)
class ResolvedCredentials:
    """Connection params returned by CredentialResolver.resolve()."""
    host: str
    port: int
    user: str
    password: str
    database: str
    source: str            # "user-override" | "profile:<name>" | "default:<datasource>"

class CredentialResolver:
    """Resolves driver connection params from session + env using a 3-tier lookup."""
    def __init__(self, logger: Optional["logging.Logger"] = None) -> None: ...

    def resolve(
        self,
        prefix: str,                    # e.g. "PG", "DB"
        session: Optional["SessionData"],
        credential_profile: Optional[str] = None,
    ) -> Optional[ResolvedCredentials]: ...

    @staticmethod
    def sanitize(value: str) -> str:
        # value.upper().replace(".", "_").replace("-", "_").replace("@", "_")
        ...
```

```python
# Type-only contract for what QS reads from a session.
# This is what the policy engine and the credential resolver consume.
# Source: navigator_session.SessionData (already imported in
# querysource/interfaces/queries.py:18 as SessionData).
# Expected userinfo fields (no Pydantic model — dict-style access):
#   "username": str
#   "user_id":  str | int
#   "email":    str
#   "groups":   list[str]
#   "roles":    list[str]      # consumed by navigator-auth's evaluator
#   "programs": list[str]      # tenants
#   "organizations": list[str]
#   "clients":  list[str]      # divisions of organisations
#   "service":  bool           # True for service-token sessions
```

### New Public Interfaces

```python
# querysource/auth/pbac.py
def setup_pbac(
    app: "web.Application",
    policy_dir: str,
    cache_ttl: int = 300,
    default_effect: Optional["PolicyEffect"] = None,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    """Initialize navigator-auth PBAC and register on the aiohttp app.

    Side effects (only when initialization succeeds):
      app['security']           = Guardian
      app['abac']               = PDP
      app['policy_evaluator']   = PolicyEvaluator
      app['credential_resolver']= CredentialResolver

    Idempotent: if app['security'] is already populated by a parent stack
    (e.g. navigator-api invoked navigator_auth's PDP.setup(app) first),
    QuerySource reuses the existing instances instead of re-creating them.
    """

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):
    async def _get_user_session(
        self,
        request: "web.Request",
    ) -> "Optional[SessionData]":
        """Extract the user session via navigator_session.get_session().
        Memoizes on request['user_session']. Returns None on RuntimeError."""

    async def _enforce_pbac(
        self,
        request: "web.Request",
        resource_type: "ResourceType",
        resource_name: str,
        action: str,
    ) -> None:
        """Evaluate a single PBAC decision; raise web.HTTPNotFound on deny.

        Fast-path no-op when QS_PBAC_ENABLED is False or
        request.app.get('security') is None. Caches decisions via
        navigator-auth's PolicyEvaluator LRU."""
```

---

## 3. Module Breakdown

> Each module is an implementation unit and maps to one (or a small group of)
> tasks. Order follows dependency.

### Module 1: Settings & auth-package skeleton
- **Path**: `querysource/conf.py`, `querysource/auth/__init__.py`
- **Responsibility**: Add three settings (`QS_PBAC_ENABLED`, `QS_POLICY_PATH`,
  `QS_PBAC_CACHE_TTL`) following the existing `config.get*` convention; create
  the empty `querysource/auth/` package.
- **Depends on**: nothing.

### Module 2: PBAC bootstrap
- **Path**: `querysource/auth/pbac.py`
- **Responsibility**: `setup_pbac(app, policy_dir, cache_ttl, default_effect)`.
  Lazy-import `navigator-auth` so module loading doesn't fail when
  `QS_PBAC_ENABLED=False`. Build `YAMLStorage`, `PolicyEvaluator`, `PDP`,
  `Guardian`. Register on `app`. Idempotent re-use of pre-existing
  `app['security']` / `app['abac']`. Register `app['credential_resolver']`.
- **Depends on**: Module 1, Module 3 (for the resolver instance).

### Module 3: Credential resolver
- **Path**: `querysource/auth/credentials.py`
- **Responsibility**: `CredentialResolver` with `resolve(prefix, session,
  credential_profile)`, three-tier env-var lookup (per-user → profile →
  default), partial-set fall-through, username/profile sanitization, and
  `ResolvedCredentials` dataclass.
- **Depends on**: Module 1.

### Module 4: AbstractHandler helpers
- **Path**: `querysource/handlers/abstract.py`
- **Responsibility**: `_get_user_session(request)` (memoized) and
  `_enforce_pbac(request, resource_type, resource_name, action)` raising
  `web.HTTPNotFound` on deny. Fast-path no-op when PBAC is disabled.
- **Depends on**: Module 2.

### Module 5: QueryService enforcement
- **Path**: `querysource/handlers/service.py`
- **Responsibility**: Inside `QueryService.query()` (line 134), after the slug
  is read (line 173), call `_enforce_pbac` with `ResourceType.SLUG /
  "slug:execute"`. After `build_provider()` resolves the datasource and
  driver, run `ResourceType.DATASOURCE / "datasource:use"` and
  `ResourceType.DRIVER / "driver:use"` checks.
- **Depends on**: Module 4.

### Module 6: QueryExecutor enforcement
- **Path**: `querysource/handlers/executor.py`
- **Responsibility**: Inside `QueryExecutor.query()` and
  `QueryExecutor.dry_run()`, branch on payload: if `slug` present, run the
  slug-execute check; otherwise, run `raw_query:execute`. Then run
  datasource and driver checks before driver instantiation.
- **Depends on**: Module 4.

### Module 7: MultiQuery enforcement
- **Path**: `querysource/handlers/multi.py` and
  `querysource/queries/multi/__init__.py`
- **Responsibility**: `MultiQS.__init__` accepts `user_session` (optional).
  `QueryHandler.query()` extracts the session, builds resource lists for
  slugs (`_queries.keys()`), files (`_files.keys()`), and inline raw queries
  (if any), then calls `Guardian.filter_resources()` once per resource type.
  On any denial, return 404 immediately — no thread fan-out runs.
- **Depends on**: Module 4.

### Module 8: Datasource & driver list filtering
- **Path**: `querysource/datasources/handlers/datasource.py`
- **Responsibility**: `DatasourceView.get()` filters its merged result list via
  `Guardian.filter_resources(resources=names, request=request,
  resource_type=ResourceType.DATASOURCE, action="datasource:list")`. The
  drivers branch (using `default_sources()` at line 33) does the same with
  `ResourceType.DRIVER / "driver:list"`. Filtering is silent — no error on
  empty result.
- **Depends on**: Module 4.

### Module 9: pg_admin datasource registration
- **Path**: `querysource/datasources/drivers/pg_admin.py`,
  `querysource/datasources/drivers/__init__.py`
- **Responsibility**: New `pg_adminDriver(pgDriver)` declaring `DB*`
  credentials (host = `DBHOST`, etc.). Module-level `pg_admin_default`
  instance. Register in `SUPPORTED` so it appears in `default_sources()`.
- **Depends on**: nothing (orthogonal to handler streams).

### Module 10: Postgres driver `params_for(session)` hook
- **Path**: `querysource/datasources/drivers/pg.py`
- **Responsibility**: New `pgDriver.params_for(session, app)` method that
  consults `app['credential_resolver'].resolve(prefix, session,
  credential_profile)` and returns either the resolved params or the
  existing `self.params()` output. The driver's prefix is read from a class
  attribute (`pgDriver.credential_prefix = "PG"`, `pg_adminDriver.credential_prefix = "DB"`).
  Existing `params()` is unchanged — call sites that pass a session use
  `params_for(session)`; legacy call sites continue to work.
- **Depends on**: Module 3, Module 9.

### Module 11: Connect drivers to `params_for` at instantiation time
- **Path**: `querysource/datasources/drivers/__init__.py` and
  callers of `default_sources()`/`build_provider()` that produce live driver
  instances.
- **Responsibility**: Plumb the user session (already available on the
  handler thanks to `_get_user_session`) into the driver factory path so
  `params_for(session)` is invoked when the connection is opened. Postgres
  is the only driver that consults the resolver in v1; other drivers behave
  unchanged.
- **Depends on**: Module 10.

### Module 12: QuerySource.setup integration
- **Path**: `querysource/services.py`
- **Responsibility**: In `QuerySource.setup(app)` (line 80), after
  `connection.setup(app)` and `TemplateParser.setup(app)` and **before** the
  route registrations, read `QS_PBAC_ENABLED`. If `True`, call
  `setup_pbac(app, policy_dir=QS_POLICY_PATH, cache_ttl=QS_PBAC_CACHE_TTL)`.
  Log success or "PBAC disabled" at info level.
- **Depends on**: Module 2.

### Module 13: Default policy YAML
- **Path**: `policies/defaults.yaml`, `policies/slugs.yaml`,
  `policies/datasources.yaml`, `policies/drivers.yaml`,
  `policies/raw_queries.yaml`, `policies/superusers.yaml`
- **Responsibility**: Mirror ai-parrot's file layout. `defaults.yaml`
  declares `version: "1.0"`, `defaults.effect: deny`, and a single
  `superuser`/`admin` allow policy with priority 100 + `enforcing: true`.
  Other files ship empty (just `version` + `defaults` + `policies: []`) for
  operators to extend. See Open Questions §8 for the deny-vs-permissive
  baseline decision.
- **Depends on**: Module 14 (upstream ResourceType values must exist for
  YAMLStorage to parse `slug:execute`-style strings).

### Module 14: navigator-auth upstream — new ResourceType / ActionType values
- **Path**: `navigator_auth/abac/policies/resources.py` (separate repo PR)
- **Responsibility**: Add four `ResourceType` values (`SLUG = "slug"`,
  `DATASOURCE = "datasource"`, `DRIVER = "driver"`, `RAW_QUERY = "raw_query"`)
  and seven matching `ActionType` constants (`SLUG_EXECUTE = "slug:execute"`,
  `SLUG_LIST = "slug:list"`, `DATASOURCE_USE`, `DATASOURCE_LIST`,
  `DRIVER_USE`, `DRIVER_LIST`, `RAW_QUERY_EXECUTE`). Additive, non-breaking;
  follows the same precedent as `DATASET`. Released as `navigator-auth 0.20.0`.
- **Depends on**: nothing in QuerySource — this is upstream work that gates
  every other module.

### Module 15: Test harness
- **Path**: `tests/test_pbac.py` (or split: `tests/auth/test_credentials.py`,
  `tests/handlers/test_pbac_enforcement.py`,
  `tests/integration/test_pbac_e2e.py`)
- **Responsibility**: Unit tests for `CredentialResolver`; integration tests
  for handler-level enforcement, list filtering, multi-query pre-flight, and
  per-user credential resolution; a perf-regression smoke test for steady-state
  query execution with PBAC enabled (cache warm) versus disabled.
- **Depends on**: Modules 1-13.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_resolver_per_user_full_set` | Module 3 | All 5 `PG_JOHN_*` env vars set → returns user-override creds, `source="user-override"`. |
| `test_resolver_per_user_partial_set` | Module 3 | `PG_JOHN_HOST` set but `PG_JOHN_PASSWORD` missing → falls through to default; warns once. |
| `test_resolver_profile_from_policy` | Module 3 | No per-user vars; `credential_profile="tier1"` → resolves `PG_TIER1_*`. |
| `test_resolver_default` | Module 3 | No overrides → returns datasource default (`PG_*`). |
| `test_resolver_sanitization` | Module 3 | `john.doe@acme.com` → `JOHN_DOE_ACME_COM`. |
| `test_resolver_no_session` | Module 3 | `session=None` → returns None (caller falls back to legacy `params()`). |
| `test_setup_pbac_disabled` | Module 2 | `QS_PBAC_ENABLED=False` → `setup_pbac` not invoked; `app['security']` absent. |
| `test_setup_pbac_idempotent` | Module 2 | `app['security']` already set by parent → reused, not recreated. |
| `test_enforce_pbac_disabled_noop` | Module 4 | No `app['security']` → `_enforce_pbac` returns silently. |
| `test_enforce_pbac_deny_raises_404` | Module 4 | Guardian denies → `web.HTTPNotFound` raised. |
| `test_get_user_session_caches` | Module 4 | First call populates `request['user_session']`; second call reuses without re-invoking `get_session`. |

### Integration Tests
| Test | Description |
|---|---|
| `test_pbac_off_baseline` | `QS_PBAC_ENABLED=False`, no policies — every existing endpoint behaves identically to today (regression guard). |
| `test_qs_anonymous_denied` | `QS_PBAC_ENABLED=True`, request with no extractable session → 404 on every execution endpoint. |
| `test_slug_execute_allowed` | User in `analysts` group hits `GET /api/v2/services/queries/{allowed_slug}` → 200 with payload. |
| `test_slug_execute_denied_404` | Same user, slug they cannot run → 404 (not 403; body does not leak the slug). |
| `test_raw_query_blocked_without_permission` | User without `raw_query:execute` posting a raw query to `QueryExecutor` → 404. |
| `test_raw_query_allowed_with_permission` | User with `raw_query:execute` → executes successfully. |
| `test_datasource_use_denied` | User can run a slug but not the datasource it resolves to → 404. |
| `test_driver_use_denied` | User can run slug + datasource but not the driver → 404. |
| `test_dry_run_gated` | `dry_run` endpoint enforces same checks as `query` — denied user gets 404 (no slug-existence leak). |
| `test_multiquery_all_or_nothing` | MultiQuery referencing 3 slugs where one is denied → entire request returns 404; allowed slugs do not execute. |
| `test_multiquery_all_allowed` | All components allowed → executes as before, identical payload to today. |
| `test_datasource_list_filtered` | `GET /datasource` returns only the datasources the user is policy-allowed to see; denied entries are silently absent. |
| `test_driver_list_filtered` | Same for the drivers list. |
| `test_pg_admin_visible_to_admins_only` | `pg_admin` datasource appears in the list and is usable for `superuser` policy holders; absent and unusable for non-admins. |
| `test_per_user_credentials_used` | Set `PG_JOHN_HOST=...`; "john" runs a Postgres query → driver opens connection with overridden creds (asserted via fixture/spy on the driver factory). |
| `test_profile_from_policy_credentials` | Policy attaches `credential_profile="tier1"`; `PG_TIER1_*` set; user without per-user vars → driver uses tier1 creds. |
| `test_perf_regression` | 1,000 sequential warm-cache slug executions with PBAC on vs off; assert delta < 5% wall-clock at p95. |

### Test Data / Fixtures

```python
# tests/conftest.py additions

@pytest.fixture
def policies_dir(tmp_path):
    """Write minimal YAML policies into a tmp dir; yield the path."""
    (tmp_path / "defaults.yaml").write_text(
        "version: '1.0'\n"
        "defaults: { effect: deny }\n"
        "policies:\n"
        "  - name: superuser_all\n"
        "    effect: allow\n"
        "    resources: ['slug:*', 'datasource:*', 'driver:*', 'raw_query']\n"
        "    actions: ['slug:execute', 'slug:list', 'datasource:use',\n"
        "              'datasource:list', 'driver:use', 'driver:list',\n"
        "              'raw_query:execute']\n"
        "    subjects: { groups: ['superuser'] }\n"
        "    priority: 100\n"
        "    enforcing: true\n"
    )
    yield str(tmp_path)

@pytest.fixture
def qs_app_pbac_on(policies_dir, monkeypatch):
    """An aiohttp app with QS_PBAC_ENABLED=True and a tmp policies dir."""
    monkeypatch.setenv("QS_PBAC_ENABLED", "True")
    monkeypatch.setenv("QS_POLICY_PATH", policies_dir)
    # ... build aiohttp test client with QuerySource(...).setup(app)

@pytest.fixture
def session_with_groups():
    """Factory: build a SessionData-like dict with given userinfo."""
    def _build(username="john", groups=("analysts",), service=False, **extra):
        return {"username": username, "user_id": username,
                "groups": list(groups), "roles": [],
                "service": service, **extra}
    return _build
```

---

## 5. Acceptance Criteria

This feature is complete when **all** of the following are true:

- [ ] `QS_PBAC_ENABLED=False` (the default): every existing test in `tests/`
      passes unchanged (`pytest tests/ -v`). Zero behavioural delta.
- [ ] `QS_PBAC_ENABLED=True` with sample policies: every test in
      `tests/test_pbac.py` (or split path) passes (`pytest tests/test_pbac.py -v`).
- [ ] Denial paths return **HTTP 404** with no response-body leak of the
      denied resource's name or existence.
- [ ] MultiQuery rejects on first denial — verified by spy/log that no
      thread for any component started.
- [ ] `DatasourceView.get` and the `DatasourceDrivers` listing return only
      policy-allowed entries; the response shape is otherwise unchanged.
- [ ] `pg_admin` datasource is registered, listed for users with the matching
      policy, and usable when their policy permits — and silently absent for
      users without it.
- [ ] Per-user env-var override works: setting `PG_<USERNAME>_*` causes
      that user's Postgres connections to use those credentials (asserted by
      a driver-factory spy).
- [ ] Profile-from-policy works: a policy attribute `credential_profile=<name>`
      causes lookup of `PG_<NAME>_*` env vars.
- [ ] Performance: 1,000 warm-cache sequential executions with PBAC on are
      within **5% p95** wall-clock of PBAC off, on the CI runner.
- [ ] `pyproject.toml` pins `navigator-auth>=0.20.0` and the upstream PR
      adding the new `ResourceType`/`ActionType` values is merged & released.
- [ ] No breaking changes to existing public API. Existing handler signatures
      (`QueryService.query`, `QueryExecutor.query/.dry_run`,
      `QueryHandler.query`, `MultiQS.__init__`/`MultiQS.query`,
      `pgDriver.params`) remain callable as before; new behaviour is added
      via opt-in (settings flag, optional `params_for(session)`).
- [ ] `policies/` ships with `defaults.yaml` (deny baseline + admin allow)
      and four empty templated files for operators.
- [ ] No regressions across the four datasource-listing tests, the multi-query
      composition tests, and any existing slug-CRUD tests
      (`pytest tests/ -v -k 'datasource or multi or slug'`).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor.** Every implementation reference
> below is verified against the source files. Implementing agents MUST NOT
> reference imports, attributes, or methods not listed here without first
> verifying via `grep`/`read`.

### Verified Imports

```python
# QuerySource
from querysource.services import QuerySource                          # querysource/services.py:45
# Existing session helpers (already in use):
from navigator_session import get_session                             # querysource/handlers/log.py:8, querysource/interfaces/queries.py:17
from navigator_session import SessionData                             # querysource/interfaces/queries.py:18
# Existing config pattern:
from navconfig import BASE_DIR, config                                # querysource/conf.py:5

# navigator-auth (for the new auth package — to be added to QS imports)
from navigator_auth.abac.pdp import PDP                               # navigator_auth/abac/pdp.py:39
from navigator_auth.abac.guardian import Guardian                     # navigator_auth/abac/guardian.py:16
from navigator_auth.abac.policies.evaluator import (
    PolicyEvaluator, PolicyLoader,
)                                                                     # navigator_auth/abac/policies/evaluator.py
from navigator_auth.abac.policies.abstract import PolicyEffect        # navigator_auth/abac/policies/abstract.py
from navigator_auth.abac.storages.yaml_storage import YAMLStorage     # navigator_auth/abac/storages/yaml_storage.py
from navigator_auth.abac.policies.resources import ResourceType, ActionType  # navigator_auth/abac/policies/resources.py:15,28
from navigator_auth.abac.policies.environment import Environment     # navigator_auth/abac/policies/environment.py
from navigator_auth.abac.context import EvalContext                  # navigator_auth/abac/context.py:6
from navigator_auth.conf import AUTH_SESSION_OBJECT                  # navigator_auth/conf.py:195
from navigator_auth import rs_pep                                     # navigator_auth/__init__.py:14
```

### Existing Class Signatures

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/services.py:45
class QuerySource(metaclass=Singleton):
    jupyter_process = None
    def __init__(self, **kwargs):                                       # line 60
        self.lazy: bool = kwargs.get('lazy', False)
        self._loop: asyncio.AbstractEventLoop = kwargs.get('loop', asyncio.get_event_loop())
        self.connection = QueryConnection(loop=self._loop, lazy=self.lazy)
        # ...iterates providers, filters, variables...
    def setup(self, app: web.Application) -> web.Application:           # line 80
        # Calls connection.setup(app), TemplateParser.setup(app),
        # registers QueryService, QueryExecutor, QueryHandler, QueryManager,
        # LoggingService routes. The PBAC bootstrap call is added inside this
        # method, after the two .setup() calls and before route registrations,
        # gated by QS_PBAC_ENABLED.

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
    async def query(self, request): ...                               # line 51
    async def dry_run(self, request: web.Request = None): ...         # line 104

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/service.py:31
class QueryService(AbstractHandler):
    async def query(self, request):                                   # line 134
        # ...
        slug: str = args['slug']                                      # line 173
        # ...
        if query := await self.get_source(request, slug, conditions, driver=args):
            await query.build_provider()                              # line 101 (in qs.py call site)

# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/multi.py:22
class QueryHandler(AbstractHandler):
    async def query(self, request: web.Request) -> web.StreamResponse:  # line 32
        # ...
        qs = MultiQS(slug=slug, queries=_queries, files=_files,
                     query=options, conditions=data)                  # ~line 129
        result, options = await qs.query()                            # line 137

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
    async def query(self):                                            # line 102
        # iterates self._queries (line 140) and self._files (line 156)

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/handlers/datasource.py:23
class DatasourceView(BaseView):
    def default_sources(self) -> list: ...                            # line 33
    async def get(self) -> web.Response:                              # line 74
        # result = await DataSource.all(fields=fields)                # line 123
        # result = result + default                                   # line 130
        # return self.json_response(response=result, ...)             # line 131

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/__init__.py:29
SUPPORTED = {
    "postgres": {...}, "mysql": {...}, "oracle": {...}, ...           # 42 entries
}

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/postgres.py
class postgresDriver(pgDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    defaults: str = asyncpg_url
postgres_default = postgresDriver(
    dsn=asyncpg_url, host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
    user=PG_USER, password=PG_PWD,
)

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/pg.py:17
class pgDriver(SQLDriver):
    def params(self) -> dict:                                         # line 42
        return {
            "host": self.host, "port": self.port,
            "username": self.user, "password": self.password,
            "database": self.database,
        }

# /home/jesuslara/proyectos/parallel/querysource/querysource/conf.py
from navconfig import BASE_DIR, config                                # line 5
# Read-only Postgres credentials:
PG_HOST = config.get('PG_HOST', fallback='localhost')                 # line 38
PG_USER = config.get('PG_USER')                                       # line 39
PG_PWD  = config.get('PG_PWD')                                        # line 40
PG_DATABASE = config.get('PG_DATABASE', fallback='navigator')         # line 41
PG_PORT = config.get('PG_PORT', fallback=5432)                        # line 42
asyncpg_url = f'postgres://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'  # line 44
# Full-access credentials (for pg_admin):
DBHOST = config.get('DBHOST', fallback='localhost')                   # line 24
DBUSER = config.get('DBUSER')                                         # line 25
DBPWD  = config.get('DBPWD')                                          # line 26
DBNAME = config.get('DBNAME', fallback='navigator')                   # line 27
DBPORT = config.get('DBPORT', fallback=5432)                          # line 28
default_dsn = f'postgres://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'  # line 32

# Existing session helper (pattern to mirror in AbstractHandler):
# /home/jesuslara/proyectos/parallel/querysource/querysource/interfaces/queries.py:165
async def user_session(self, request: web.Request = None) -> SessionData:
    if not request:
        return None
    try:
        session = await get_session(request, new=False)               # line 174
    except RuntimeError:
        self._logger.error('QS: User Session system is not installed.')
        return None
    return session
```

```python
# navigator-auth surface (verified in /home/jesuslara/proyectos/navigator/navigator-auth/)
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/pdp.py:39
class PDP:
    def __init__(self, storage, policies=None, yaml_storage=None): ...
    async def reload_policies(self) -> int: ...
    def setup(self, app: web.Application): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/guardian.py:16
class Guardian:
    def __init__(self, pdp): ...
    async def filter_resources(
        self,
        resources: List[str],
        request: web.Request,
        resource_type: ResourceType = ResourceType.TOOL,
        action: str = "tool:execute",
    ) -> "FilteredResources": ...
    async def is_allowed(self, request: web.Request, **kwargs): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/evaluator.py:361
class PolicyEvaluator:
    def __init__(self, cache_size: int = 1024, cache_ttl_seconds: int = 300): ...
    def check_access(
        self, ctx: EvalContext, resource_type: ResourceType,
        resource_name: str, action: str, env: Environment = None,
        owner_reports_to: str = None,
    ) -> EvaluationResult: ...
    def filter_resources(
        self, ctx: EvalContext, resource_type: ResourceType,
        resource_names: List[str], action: str, env: Environment = None,
    ) -> FilteredResources: ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/context.py:6
class EvalContext(dict, MutableMapping):
    def __init__(self, request, user, userinfo, session, *args, **kwargs): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/resources.py:15
class ResourceType(Enum):
    TOOL = "tool";  KB = "kb";  VECTOR = "vector";  AGENT = "agent"
    MCP = "mcp";    URI = "uri";  DATASET = "dataset"
    WIDGET = "widget"; CARD = "card"
    # *** Module 14 (this spec) adds: ***
    # SLUG = "slug"; DATASOURCE = "datasource"; DRIVER = "driver"; RAW_QUERY = "raw_query"
```

```python
# ai-parrot reference (read-only — DO NOT import from QuerySource)
# /home/jesuslara/proyectos/navigator/ai-parrot/packages/ai-parrot/src/parrot/auth/pbac.py:35
def setup_pbac(
    app: "web.Application",
    policy_dir: str = "policies",
    cache_ttl: int = 30,
    default_effect: Optional[object] = None,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    ...

# /home/jesuslara/proyectos/navigator/ai-parrot/packages/ai-parrot/src/parrot/handlers/agent.py:378
async def _build_eval_context(self) -> Any:
    """Build an EvalContext from the current request session."""
    # ... lazy import of EvalContext + AUTH_SESSION_OBJECT
    # ... session = self.request.session OR await get_session(self.request)
    # ... userinfo = session.get(AUTH_SESSION_OBJECT, {})
    # ... user = session.decode('user') if hasattr(session, 'decode') else userinfo
    return EvalContext(request=self.request, user=user,
                       userinfo=userinfo, session=session)
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `setup_pbac()` (Module 2) | aiohttp app dict | `app['security']`, `app['abac']`, `app['policy_evaluator']`, `app['credential_resolver']` | navigator_auth/abac/pdp.py:208 (existing convention) |
| `setup_pbac()` (Module 2) | `QuerySource.setup` (Module 12) | direct call inside the existing method | querysource/services.py:80 |
| `_get_user_session` (Module 4) | `navigator_session.get_session` | function call | querysource/interfaces/queries.py:174 |
| `_enforce_pbac` (Module 4) | `app['security']` (Guardian) | `Guardian.is_allowed()` or `PolicyEvaluator.check_access()` | navigator_auth/abac/guardian.py:16, evaluator.py:361 |
| `QueryService.query` (Module 5) | `_enforce_pbac` | three sequential calls (slug, datasource, driver) | querysource/handlers/service.py:134,173 |
| `QueryExecutor.query/.dry_run` (Module 6) | `_enforce_pbac` | branched call (slug vs raw_query) + datasource + driver | querysource/handlers/executor.py:51,104 |
| `QueryHandler.query` (Module 7) | `Guardian.filter_resources` | one call per resource type, batched | querysource/handlers/multi.py:32; guardian.py |
| `MultiQS` (Module 7) | new `user_session` ctor arg | passed through from `QueryHandler.query` | querysource/queries/multi/__init__.py:53 |
| `DatasourceView.get` (Module 8) | `Guardian.filter_resources` | resource_type=`DATASOURCE`, action=`datasource:list` | querysource/datasources/handlers/datasource.py:74,123,130 |
| `DatasourceDrivers` listing (Module 8) | `Guardian.filter_resources` | resource_type=`DRIVER`, action=`driver:list` | querysource/datasources/handlers/datasource.py:33 |
| `pgDriver.params_for` (Module 10) | `app['credential_resolver'].resolve` | function call with prefix + session + profile | querysource/datasources/drivers/pg.py:42 |
| `pg_admin_default` (Module 9) | `SUPPORTED` registry | dict insertion under key `"pg_admin"` | querysource/datasources/drivers/__init__.py:29 |

### Does NOT Exist (Anti-Hallucination)

The following are **not** in the codebase. Implementing agents MUST NOT
import or reference them as if they were:

- ~~`ResourceType.SLUG`~~ — to be added by Module 14 (upstream `navigator-auth` PR).
- ~~`ResourceType.DATASOURCE`~~ — to be added by Module 14.
- ~~`ResourceType.DRIVER`~~ — to be added by Module 14.
- ~~`ResourceType.RAW_QUERY`~~ — to be added by Module 14.
- ~~`ActionType.SLUG_EXECUTE`~~ / `SLUG_LIST` / `DATASOURCE_USE` /
  `DATASOURCE_LIST` / `DRIVER_USE` / `DRIVER_LIST` / `RAW_QUERY_EXECUTE` —
  added by Module 14. Until that PR ships, action verbs may be passed as
  string literals (`"slug:execute"`, etc.); the constants are syntactic sugar.
- ~~`AbstractHandler._get_user_session`~~ / ~~`AbstractHandler._enforce_pbac`~~ —
  do not exist; introduced by Module 4. Closest existing helper is
  `AbstractQuery.user_session()` at `querysource/interfaces/queries.py:165`,
  but it is on a different class hierarchy (query interface, not handler base).
- ~~`pg_admin` datasource~~ / ~~`pg_adminDriver`~~ — not in `SUPPORTED`.
  Introduced by Module 9.
- ~~`pgDriver.params_for(session)`~~ — does not exist. Module 10 adds it
  alongside the existing `params()` (line 42).
- ~~`request.session`~~ — not a QuerySource convention. Use
  `await get_session(request)` plus memoization on `request['user_session']`.
- ~~`Guardian.filter_tools`~~ / ~~`Guardian.filter_datasets`~~ — referenced in
  the older ai-parrot brainstorm. The **actual current method** is the
  generic `Guardian.filter_resources(resources, request, resource_type, action)`
  (guardian.py:16). Use the generic API.
- ~~Group-tier env-var lookup (e.g. `PG_{GROUP}_HOST`)~~ — explicitly out of
  scope; resolution is per-user → profile → datasource-default only.
- ~~A QuerySource-private decision cache~~ — do not add one. Reuse
  `PolicyEvaluator`'s built-in LRU + TTL.
- ~~A QuerySource-private aiohttp middleware for PBAC~~ — do not install
  navigator-auth's `abac_middleware` from QuerySource. Handlers enforce
  explicitly because resources are dynamic.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Lazy imports of `navigator-auth`** inside `querysource/auth/pbac.py` —
  the module itself is always importable, but the heavy nav-auth imports
  happen only inside `setup_pbac()`. With `QS_PBAC_ENABLED=False`, no
  nav-auth code is loaded at startup.
- **Single `PolicyEvaluator` instance per process.** Stash on
  `app['policy_evaluator']`; never instantiate a second one inside
  `_enforce_pbac` or list-filtering paths.
- **Use the generic `Guardian.filter_resources()`** for batch operations
  (lists, MultiQuery pre-flight). Use `PolicyEvaluator.check_access()` only
  for single-resource gating where the per-call cache key matters.
- **Audit logging is free.** `Guardian` already records access decisions —
  no need to add explicit logging at the QS handler layer beyond an
  `info`-level "PBAC denied: <resource>" line for ops triage.
- **Resource identifier strings.** Construct as
  `f"slug:{name}"`, `f"datasource:{name}"`, `f"driver:{name}"`,
  literal `"raw_query"`. Always use the `ResourceType` enum for the
  `resource_type` param; pass the **string** form for `resource_name`.
- **Follow existing `config.get*` style** in `conf.py`:
  ```python
  QS_PBAC_ENABLED   = config.getboolean('QS_PBAC_ENABLED', fallback=False)
  QS_POLICY_PATH    = config.get('QS_POLICY_PATH', fallback=str(BASE_DIR / 'policies'))
  QS_PBAC_CACHE_TTL = config.getint('QS_PBAC_CACHE_TTL', fallback=300)
  ```
- **Respect `Singleton` semantics** of `QuerySource`. The PBAC bootstrap
  must be safe under repeat `setup()` calls (it shouldn't normally happen,
  but the class is a Singleton and tests sometimes re-enter).

### Known Risks / Gotchas

- **navigator-auth version coupling.** Module 14 (the upstream PR) gates
  the entire feature. Do **not** ship modules 5–8 with hard-coded
  `ResourceType.SLUG`-style attribute access until 0.20.0 is released — use
  the string-literal form (`"slug"`, `"datasource"`, `"driver"`,
  `"raw_query"`) inside `_enforce_pbac` calls so QS modules can land
  before the upstream constants exist. Switch to enum access only after
  the bump.
- **`navigator-auth` editable install path.** `pyproject.toml` line 198
  ships an editable local override (`navigator-auth = { path = "../../navigator/navigator-auth", editable = true }`).
  CI must check out matching versions of QS and navigator-auth, otherwise
  importing the new ResourceType values will fail at startup.
- **`QuerySource` is a `Singleton`.** Tests that re-instantiate it will
  hit `if hasattr(self, '__initialized__') and self.__initialized__ is True:
  return` (services.py:61). Re-running `setup_pbac` against an already-set
  `app['security']` must be idempotent (Module 2 requirement).
- **Driver factory call sites.** Modules 10–11 must not break callers of
  `pgDriver.params()` that pass no session (legacy tests, internal queue
  workers, the `default_sources()` enumeration). Keep `params()` unchanged
  and add `params_for(session)` as a separate method.
- **`pg_admin` registration without `DB*` env vars.** If an existing
  deployment has not configured `DBHOST/DBUSER/DBPWD/...`, the
  `pg_admin_default` instance creation may raise. Wrap construction in the
  same `try/except ValueError` pattern that `postgres_default` uses
  (`postgres.py`), assigning `pg_admin_default = None` on failure. The
  list-filtering path then naturally hides it.
- **404 vs 403 on the existing audit-log handler.** Other QS endpoints
  (e.g., `LoggingService`) are not on this spec's enforcement path. They
  remain unauthenticated/group-checked as today; a follow-up spec can
  bring them under PBAC if needed.
- **Cache invalidation across policy reloads.** `PolicyEvaluator.swap_index`
  clears the cache atomically (verified in evaluator.py). The
  `POST /api/v1/abac/reload` endpoint that triggers this is registered by
  navigator-auth's `PDP.setup(app)` — only available if the consuming app
  also wires nav-auth's PDP. QuerySource itself does not register that
  endpoint.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `navigator-auth` | `>=0.20.0` | PBAC engine: `PDP`, `Guardian`, `PolicyEvaluator`, `rs_pep`. The 0.20.0 release adds the new `ResourceType`/`ActionType` values for `SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY` (Module 14). Bumped from `>=0.15.8`. |
| `navigator-session` | `>=` current pin | `get_session(request)` and `SessionData`. Already in use. |
| `pyyaml` | `>=` current pin | YAML policy loading via `YAMLStorage`. Already a transitive dep. |

---

## Worktree Strategy

**Default isolation: `mixed`.** This feature has clear horizontal seams that
allow safe parallel work plus a vertical seam that must stay sequential.

| Task / Module | Worktree | Why |
|---|---|---|
| Module 14 (`navigator-auth` upstream) | **Separate repo** | Lives in another repo. Must merge & release 0.20.0 before any QS module lands. |
| Modules 1, 2, 3 (settings + auth pkg + bootstrap + resolver) | Worktree A | Closely-coupled foundation; no other module compiles without these. |
| Module 4 (AbstractHandler helpers) | Worktree A | Sits on top of Module 2; shipped together. |
| Modules 5, 6, 7 (handler enforcement: service, executor, multi) | Worktree A (sequential) | All three handlers share the same enforcement contract (resource-type + action vocabulary, 404 semantics, MultiQS pre-flight pattern). Doing them in parallel risks contract drift. |
| Module 8 (datasource & driver list filtering) | **Worktree B (parallel-friendly)** | Touches a single file (`datasources/handlers/datasource.py`); independent of handler streams once Module 4 is in place. |
| Module 9 (pg_admin datasource) | **Worktree C (parallel-friendly)** | New file + 1-line registration in `SUPPORTED`. Independent of handler streams. |
| Modules 10, 11 (driver `params_for` hook + factory plumbing) | Worktree A (sequential after Modules 3 & 9) | The hook depends on `CredentialResolver` (Module 3) and the second registered datasource (Module 9). |
| Module 12 (`QuerySource.setup` integration) | Worktree A | Last step before E2E. Needs Modules 1–11. |
| Module 13 (default policies YAML) | **Worktree D (parallel-friendly)** | Pure data files. Can be written in parallel; only blocks E2E tests. |
| Module 15 (test harness) | Worktree A | Must converge against final modules. |

**Cross-feature dependencies:**

- **`navigator-auth` 0.20.0 release** (Module 14) is the critical-path
  dependency. Until it ships, QS modules pin string literals in place of
  `ResourceType.SLUG`-style enum access (see Implementation Notes — known
  risks).
- No other in-flight QuerySource specs touch the affected files. The recently
  merged FEAT-090 (`querysource-slug-list-pagination`) modifies
  `handlers/manager.py`, not the three handlers we're touching here.

**Recommended execution order:**

1. Land Module 14 in `navigator-auth` and release 0.20.0.
2. Bump the pin in QS `pyproject.toml` to `navigator-auth>=0.20.0`.
3. Modules 1, 2, 3, 4 in Worktree A (sequential foundation).
4. Modules 8, 9, 13 in parallel worktrees B, C, D.
5. Modules 5, 6, 7 in Worktree A (sequential).
6. Modules 10, 11 in Worktree A.
7. Module 12 in Worktree A.
8. Module 15 wraps everything; final E2E pass.

---

## 8. Open Questions

> All open questions were resolved during /sdd-spec discovery on 2026-04-30.
> No questions remain blocking implementation.

- [x] ~~**Driver rollout scope**~~ — **Resolved (2026-04-30)**: Postgres only in
      v1 (`pg.py` / `postgres` / `pg_admin`). MySQL/Oracle/BigQuery/Mongo/REST
      will be a follow-up spec once the per-user credential pattern is
      validated end-to-end on Postgres. The Non-Goal in §1 stands.
- [x] ~~**Env-var prefix per datasource**~~ — **Resolved (2026-04-30)**: confirm
      `<DATASOURCE_PREFIX>_<USERNAME>_*` where the prefix is declared by each
      registered datasource as a `credential_prefix` class attribute on the
      driver model (e.g. `postgresDriver.credential_prefix = "PG"`,
      `pg_adminDriver.credential_prefix = "DB"`). The `CredentialResolver`
      reads this attribute when invoked from the driver's `params_for(session)`
      method. Per-user override key for `pg_admin` users is therefore
      `DB_<USERNAME>_*`.
- [x] ~~**`DatasourceView` mutating verbs**~~ — **Resolved (2026-04-30)**: out
      of scope for v1. POST/PUT/DELETE on `DatasourceView`, the QueryManager
      CRUD verbs, and any other admin endpoints are covered by a follow-up
      spec that addresses admin-write endpoints together with their audit
      story. v1 stays focused on read/execute paths only.
- [x] ~~**Default policy posture**~~ — **Resolved (2026-04-30)**: strict deny
      baseline. `defaults.yaml` ships `version: "1.0"`, `defaults.effect: deny`,
      and a single `superuser`/`admin` allow policy with priority 100 +
      `enforcing: true`. Operators must add allow policies for normal users
      before anything works with `QS_PBAC_ENABLED=True`. Mitigation: include a
      copy-pasteable example block (commented-out) in `slugs.yaml` showing
      how to grant `analysts` access to all slugs, so adopting operators have
      a starting point.
- [x] ~~**Dry-run gating**~~ — **Resolved (2026-04-30)**: `QueryExecutor.dry_run`
      enforces the same checks as `.query()`. Without this, an attacker could
      probe slug existence by attempting `dry_run` against guesses (200 vs 404
      leaks the slug catalog). The hide-existence guarantee requires both
      endpoints to behave identically on denial.
- [x] ~~**Performance budget — measurement methodology**~~ — **Resolved
      (2026-04-30)**: 1,000 sequential warm-cache slug executions on the CI
      runner against the existing fixture
      (`postgres://qs_data:12345678@127.0.0.1:5432/navigator_dev`, see
      `tests/test_api.py`). p95 wall-clock delta `< 5%` between PBAC-on and
      PBAC-off. Single-process, single-thread; cache is warmed by 10 throwaway
      requests before measurement starts. Test rig lives in
      `tests/perf/test_pbac_overhead.py`.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-29 | Jesus Lara / Claude | Initial draft from `pbac-support.brainstorm.md`. FEAT-091, target version 5.0.0, 15 modules, mixed worktree isolation across 4 parallel-friendly streams. |
| 1.0 | 2026-04-30 | Jesus Lara / Claude | All 6 open questions resolved (driver scope = Postgres-only v1; env-var prefix via `credential_prefix` class attr; `DatasourceView` mutating verbs out of scope; strict-deny default policy posture; `dry_run` gated identically to `.query()`; perf rig pinned). Status promoted to `approved`. |
