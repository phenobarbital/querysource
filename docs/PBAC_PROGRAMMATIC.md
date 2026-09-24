# Programmatic PBAC: running queries on behalf of a user

FEAT-150 lets a library caller (a Python process that never has an aiohttp
`web.Request`) run a `QS`/`MultiQS` slug **as a user**, so the same PBAC
policies that protect the HTTP API also protect programmatic callers. Before
this feature, `QS(request=None)` always ran with trusted-service credentials
and **no authorization at all** — a caller such as ai-parrot could run any
slug on behalf of any user, regardless of what that user could do over HTTP.

## When you need this

Use `principal=` whenever a library call executes a stored slug **because a
specific end user asked for it** — an agent tool, a scheduled refresh
triggered by a user action, a background job that replays a saved surface,
etc. You do **not** need it for:

- Scheduler jobs (`querysource/scheduler/jobs.py`) — these stay
  request-less and principal-less by design; see [Operator notes](#operator-notes).
- Purely internal/service-to-service calls that are not acting on behalf of
  any particular user.
- HTTP handlers — they already enforce PBAC via `request=` and need no
  change.

`principal=` is opt-in and fully backward compatible: `principal=None` (the
default) keeps today's trusted-service behaviour exactly.

## QSPrincipal

`QSPrincipal` (`querysource.auth.QSPrincipal`) is the one public, ai-parrot
independent identity type a library caller constructs to say "run this as
this user." It is a frozen dataclass:

```python
from querysource.auth import QSPrincipal

QSPrincipal(
    user_id: str,
    username: str | None = None,
    groups: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
    programs: tuple[str, ...] = (),
    superuser: bool = False,
    tenant_id: str | None = None,   # informational only — see "Tenants" below
    channel: str = "library",       # informational only — logs
    authz_backend: str | None = None,  # set only by for_authz()
)
```

`user_id` may not be empty or blank — construction raises `ValueError`, so a
principal can never accidentally evaluate as anonymous. Sequences passed for
`groups`/`roles`/`programs` are normalized to tuples of `str`.

### User principal

The common case: identify the human on whose behalf the call runs.

```python
from querysource.auth import QSPrincipal

principal = QSPrincipal(
    user_id="35",
    username="jdoe",
    groups=("sales",),
    tenant_id="client_b",   # for logs only — does not route
)
```

### Sessionless-authz principal (`QSPrincipal.for_authz`)

Some callers are **authorized but not authenticated** — the same concept
`AbstractHandler._enforce_pbac` uses for requests approved by a
navigator-auth `authz_*` backend (IP / host / User-Agent allowlists), with
no user session behind them. `QSPrincipal.for_authz(backend)` builds the
identical synthetic identity a handler would:

```python
principal = QSPrincipal.for_authz("authz_useragent")
# user_id == username == "authz:authz_useragent"
# groups == ("authorized", "authz_useragent")
# roles == (), programs == (), superuser == False
```

`__post_init__` rejects an `authz_backend` principal whose other claims
differ from that exact shape, so the sessionless form can never carry extra
privileges. It is only ever honoured when `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ`
is true — see [Operator notes](#operator-notes).

## Running QS and MultiQS as a principal

Pass `principal=` as a keyword argument, exactly like `tenant=`:

```python
from querysource.auth import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.queries.qs import QS
from querysource.queries.multi import MultiQS

principal = QSPrincipal(user_id="35", username="jdoe", groups=("sales",))

qs = QS(slug="sales_by_store", conditions={...}, tenant="client_b", principal=principal)
result, error = await qs.query()   # raises QueryAccessDenied on deny/missing

mq = MultiQS(slug="pipeline", tenant="client_b", principal=principal)
result, options = await mq.query()
```

`request=` and `principal=` are mutually exclusive: passing both raises
`ValueError` at construction (ambiguous identity — a query is either
request-driven or principal-driven, never both).

For `MultiQS`, the pipeline's own stored slug, every stored child slug,
every `files` entry, and any inline raw child are **all** checked — once,
up front — before any child executes. One denied entry means the entire
pipeline is rejected; nothing partially runs.

## What happens (decision table)

This mirrors spec §2 exactly:

| Situation | Result |
|---|---|
| `principal=None` | unchanged (trusted-service, no PBAC) |
| principal, PBAC active, allowed | runs as today, trusted-service creds, store chosen by `tenant=` |
| principal, PBAC active, denied | `QueryAccessDenied` from `query()` / `columns()` / `build_provider()`; generic message, details only in logs |
| principal, slug or tenant missing | `QueryAccessDenied` (indistinguishable from denied) |
| principal, PBAC runtime absent | no-op; debug log (warning once per process if `QS_PBAC_ENABLED=True`) |
| principal, guardian present but evaluator missing, or evaluator raises | `QueryAccessDenied` (fail-closed) |
| `request=` **and** `principal=` | `ValueError` at construction |
| HTTP API | unchanged (404 on deny) |

## Missing vs. denied

With a principal, "the slug doesn't exist" and "you may not run this slug"
are deliberately indistinguishable — both raise `QueryAccessDenied`. A
`TenantError` with `error_code` `query_not_found` or `tenant_not_available`,
and a `SlugNotFound`, all collapse into `QueryAccessDenied` when a principal
is set. This prevents a denied caller from probing which slugs exist by
comparing error types. Without a principal, these errors keep their
original type and HTTP status exactly as before — this collapse is
principal-only.

`TenantError` with `error_code="tenant_store_unavailable"` is **not**
collapsed — that is an infrastructure problem, not an authorization
decision, and it propagates unchanged.

## Tenants: `tenant=` routes, `principal.tenant_id` is logged

`tenant=` remains the only store-routing key, exactly as before FEAT-150.
`principal.tenant_id` is carried into log lines only — it never selects a
store, never enters the evaluator's `userinfo`, and is never used as
`org_id`/`client_id`. There is no membership check between
`principal.tenant_id` and `tenant=`; if you need one, enforce it in your own
calling code before constructing the principal.

## Credentials stay trusted-service

`principal=` is an **authorization** mechanism only — it does not switch to
per-user datasource credentials. `get_provider()` still receives
`session=None, app=None` on the principal path, exactly as it does for any
other request-less call. If a policy allows the principal to run a slug,
the query executes with the same trusted-service credentials any
request-less `QS` call has always used.

## Operator notes

- **Detached evaluator, no decision cache.** Every principal-path check
  uses a detached copy of the evaluator (`resolve_evaluator(..., detached=True)`):
  a shallow copy with a fresh, empty decision cache and copied stats. The
  shared app evaluator's cache is never read or written by a principal
  check. This costs a fresh evaluation per call, which is acceptable given
  the small number of checks per query and is required for tenant
  isolation (see `sdd/specs/per-tenant-queries.spec.md` §2 for the
  original rationale).
- **Request-derived policy conditions cannot match.** Without a request,
  the evaluation context carries no `remote`, `headers`, `path`, or similar
  request-derived data — `PolicyEvaluator.check_access` on the installed
  navigator-auth reads only `ctx.userinfo`/`ctx.user` (username, groups,
  roles) plus a neutral `Environment()`. Any policy that conditions on
  request fields will simply never match on the principal path.
- **`EvalContext.from_userinfo` feature detection.** When building the
  evaluation context without a request, `querysource.auth.enforcement.build_eval_context`
  prefers `EvalContext.from_userinfo(...)` when the installed navigator-auth
  provides it (checked with `hasattr`), and otherwise falls back to a
  neutral internal stand-in (`_ServiceRequest`) passed to the real
  `EvalContext` constructor. Both paths are functionally equivalent; the
  `from_userinfo` factory is a planned upstream navigator-auth addition
  that this code adopts automatically once available — no QuerySource
  change is required either way.
- **One warning per process, not per call.** When `QS_PBAC_ENABLED=True`
  but no PBAC runtime was ever registered in this process (bootstrap
  failed, or `setup_pbac()` was never called), the principal path logs a
  single warning the first time it is hit, then proceeds as a silent no-op
  for every subsequent call. This is intentional — it surfaces a real
  misconfiguration once without flooding logs on every query.
- **Scheduler jobs are unchanged.** `querysource/scheduler/jobs.py` never
  passes `principal=` — scheduled and cache-refresh jobs keep running with
  trusted-service credentials and no principal-path PBAC evaluation, exactly
  as before FEAT-150.
- **`QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` gates the authz form.** With PBAC
  active and this flag false, a `QSPrincipal.for_authz(...)` principal is
  denied outright, without ever reaching the evaluator — mirroring how a
  handler denies a session-less HTTP request when the same flag is off.

## Example: mapping an ai-parrot `PermissionContext`

Mapping ai-parrot's own `PermissionContext` (or an equivalent caller-side
identity) onto `QSPrincipal` is the **integrator's responsibility** — it is
not part of this feature. A minimal, illustrative mapping:

```python
from querysource.auth import QSPrincipal

def to_qs_principal(ctx) -> QSPrincipal:
    """Map an ai-parrot PermissionContext to a QSPrincipal (integrator code)."""
    return QSPrincipal(
        user_id=ctx.user_id,
        username=getattr(ctx, "username", None),
        groups=tuple(ctx.groups or ()),
        roles=tuple(getattr(ctx, "roles", ()) or ()),
        programs=tuple(getattr(ctx, "programs", ()) or ()),
        superuser=bool(getattr(ctx, "superuser", False)),
        tenant_id=getattr(ctx, "tenant", None),
        channel="ai-parrot",
    )
```

The full ai-parrot-side change (wiring this into `QuerySlugSource` /
`MultiQuerySlugSource` and passing `record.tenant` as `tenant=`) is tracked
as a separate ai-parrot feature, out of scope for FEAT-150.
