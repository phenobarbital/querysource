---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [multiquery, auth, scheduler]
tags: [onedrive, sharepoint, microsoft-graph, multiquery-source, session-vault, oauth-identity]
---

# Feature Specification: OneDrive Source for MultiQS

**Feature ID**: FEAT-159
**Date**: 2026-09-30
**Author**: Jesus Lara (spec drafted by Claude Code, Opus 5.5)
**Status**: draft
**Target version**: next minor after the navigator-auth `onedrive` provider release
**Research**: proposal `sdd/proposals/onedrive-multiqs-source.proposal.md` (research id FEAT-178, state `sdd/state/FEAT-178/`)

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQS can read a CSV/Excel file from a SharePoint document library
(`SharepointSource`), but it cannot read from **Microsoft OneDrive**. Two kinds
of OneDrive need to be supported:

- **OneDrive for Business**, reached with the same app-only (client-credentials)
  Azure application that SharePoint already uses.
- **Personal / delegated OneDrive**, which app credentials cannot reach at all.
  A user has to log in once through a UI, and the captured refresh token is
  kept in that user's **session vault**.

Scheduled MultiQS runs have no user session. For those runs, the stored tokens
must be read from the user-identity table for a well-defined user: the person
who registered the schedule, recorded with an audit trail.

### Goals

- G1 — A `OneDriveSource` MultiQS source that returns one CSV/Excel file as a
  DataFrame. It supports three addressing modes: app-only per user drive,
  sharing URL, and delegated `/me/drive`.
- G2 — A shared Graph drive-item base (`GraphDriveSource`) that both
  `SharepointSource` and `OneDriveSource` extend. `SharepointSource` must keep
  its behaviour, error messages and generated schema exactly as they are.
- G3 — Delegated tokens come from navigator-auth's Identity Vault.
  - With an HTTP session: the session-vault cache (`identity:onedrive`).
  - Without one: `auth.user_identities`, through
    `IdentityProvider.get_user_identity_credential`.
  - Expiring tokens are refreshed and rotated tokens are persisted by
    navigator-auth. querysource does not re-implement refresh.
- G4 — Delegated tokens are always resolved on the caller's event loop
  (request or scheduler), **before** the source thread starts.
- G5 — A scheduled pipeline acts as the user who created or changed its
  schedule. That user is stored in a dedicated `scheduler_run_as_user_id`
  column, and every change to it writes an append-only audit row in the **same
  transaction**.
- G6 — No generic write path (PATCH, upsert, create, export) can set or leak
  the run-as user.

### Non-Goals (explicitly out of scope)

- Changes to the **navigator-auth repository**. The link-only `onedrive`
  identity provider is an external prerequisite with its own spec in
  `/home/jesuslara/proyectos/navigator-auth` (see §2, P1). This spec states
  only the contract querysource relies on.
- Folder or glob fetches, or several files per source entry. One entry returns
  one file, the same as `SharepointSource`.
- A `ToOneDrive` destination, and refactoring `ToSharepoint`
  (`querysource/queries/multi/destinations/sharepoint.py`) onto the new base.
- A new OAuth login UI or token endpoint in querysource. navigator-auth's
  `/api/v1/user/identities/link/onedrive` and `/manage` pages are the UI.
- Reusing the corporate `azure` provider (`AzureAuth`). It was rejected in
  research (proposal U6; F015) because it is tenant-bound corporate SSO.
- Adding `scheduler_run_as_user_id` to `QueryModel` or `TenantQueryDefinition`.
  It is deliberately kept repository-only (see §8).

---

## 2. Architectural Design

### Overview

**Graph base.** A new `GraphDriveSource(ThreadSource)` in
`sources/graph.py` takes over everything in today's `SharepointSource` that is
not specific to SharePoint:

- parsing the shared `source` keys (`filename`, `directory`, `url`,
  `sheet_name`, `pd_args`, `masks`), written as literal `.get()` calls so
  `extract_source_schema` still sees them;
- the lazy Graph imports, raising `ImportError` that names the extra;
- `/shares` resolution for a `url`;
- the `@microsoft.graph.downloadUrl` download through httpx with a 600 s
  timeout;
- `_parse_file_content`;
- a **scoped** kiota `platform.version` patch that is reference-counted under a
  lock and restored in `finally`.

Subclasses implement two hooks: `_credential()` and `_resolve_drive_item()`.

`SharepointSource` is rebased on this base. Its credential parsing and the
site→library→path resolution stay in the subclass, and its error messages are
kept verbatim.

**OneDriveSource.** `OneDriveSource(GraphDriveSource)` has an explicit mode
matrix:

| `auth` | Addressing | Graph path | Credential |
|---|---|---|---|
| `app` (default) | `user` + `directory`/`filename` | `/users/{user}/drive` → `root:/<path>:` | `ClientSecretCredential` (`ONEDRIVE_*`, falling back to `SHAREPOINT_*`) |
| `app` | `url` | `/shares/u!<b64url>` | same |
| `delegated` | `directory`/`filename` | `/me/drive` → `root:/<path>:` | `StaticTokenCredential` (the user's access token) |
| `delegated` | `url` | `/shares/u!<b64url>` | same |

The identity provider is fixed to `onedrive`. There is no `provider:` override
(design research S2). Invalid combinations raise `ValueError` at construction:

- app-only path mode without `user`;
- `delegated` together with `user`;
- an unknown `auth` value;
- neither `url` nor `filename`.

**Pre-thread credential phase.** `ThreadSource` gains a no-op
`async prepare(context)`. `MultiQS.query()` calls it on its own loop for every
dispatched source, right after the source is constructed and before any thread
starts (S3). `OneDriveSource.prepare()` in delegated mode calls
`resolve_delegated_token(context)`, which:

1. with a session: reads `identity:onedrive` from the session vault through
   navigator-auth `cached_credential` and uses it unless it is expiring;
2. otherwise, or on a cache miss or expiring token: calls
   `IdentityProvider.get_user_identity_credential(user_id, "onedrive",
   auto_refresh=True)`, which reads `auth.user_identities`, decrypts, refreshes
   if needed and persists the rotated token;
3. with a session: re-caches the result through `cache_credential`.

Only the resulting access token (plus its expiry) crosses into the worker
thread, wrapped in a `StaticTokenCredential`. `SessionVault`, Redis and the
asyncpg pools are never touched from the thread's loop.

A missing link, a disabled backend or a missing user raises
`DelegatedIdentityError`. It carries the link URL
`/api/v1/user/identities/link/onedrive`, is raised before any thread starts,
and the MultiQS handler maps it to **HTTP 409**.

**Identity context.** A frozen `SourceIdentityContext(user_id, session, auth,
origin)` is built by the two callers that have that information:

- `handlers/multi.py`, from `request.get('user_session')` and
  `request.app.get("auth")`;
- `scheduled_multiqs_job`, from `run_as_user_id` and the auth handler the
  scheduler kept at startup (S4).

It is passed to `MultiQS(..., identity_context=...)`.

**Run-as user (the resolved U7/U8, refined by S6).** A new column
`scheduler_run_as_user_id INTEGER` on each store's queries table, and an
append-only `{table}_run_as_audit` table in the same schema.

- `DefinitionRepository.patch()` and `.upsert()` gain `run_as_actor: int |
  None`. Inside **one transaction** they:
  1. lock the row and read its previous `attributes.scheduler`;
  2. apply the write;
  3. compare the new `attributes.scheduler` with the previous one;
  4. if the schedule was created or changed and an actor is given, set the
     column to the actor; if it was removed, clear the column;
  5. insert the audit row.
- Ordinary edits that leave the schedule alone keep the column untouched.
- `QueryManager` (`patch`/`put`/`post`) passes the authenticated session's
  numeric `user_id` as `run_as_actor`.
- `SchedulerJobsView.post` stays a pure re-sync and **never** writes run-as.
  Otherwise anyone who can re-sync a slug could take over its identity (S6).
- The key is rejected from every payload (the PATCH/upsert guard, like
  `program_slug`) and is popped from rows before model validation. Rows are
  validated with `TenantQueryDefinition(**data)`, which raises `TypeError` on an
  unknown column, as verified.
- Reads tolerate un-migrated stores. `schedulable()` retries without the column
  on `UndefinedColumnError`, and `get_run_as()` returns `None`.

### Component Diagram

```
 HTTP  POST /api/v3/queries/multi …            APScheduler (QSScheduler loop)
   │ handlers/multi.py                            │ scheduled_multiqs_job(run_as_user_id, identity_auth)
   │  SourceIdentityContext(session,user,auth)    │  SourceIdentityContext(user=run_as, auth)
   └──────────────┬───────────────────────────────┘
                  ▼
          MultiQS(identity_context=…)
          query(): for each `sources:` entry
             t = cls(name, config, request, queue)
             await t.prepare(context)   ◄── caller loop: resolve_delegated_token()
             │                                ├─ session vault  identity:onedrive  (cached_credential)
             │                                └─ IdentityProvider.get_user_identity_credential()
             │                                     (auth.user_identities; refresh+rotate in navigator-auth)
             ▼
          thread.start() → fresh loop → GraphDriveSource.fetch()
             ├─ credential: ClientSecretCredential | StaticTokenCredential(access_token)
             ├─ item: /shares (url) | _resolve_drive_item()  (SharePoint site/library | /users/{u}/drive | /me/drive)
             └─ download (httpx, 600s) → _parse_file_content → DataFrame

 QueryManager.patch/put/post ──run_as_actor──► DefinitionRepository.patch/upsert
                                               └─ one tx: UPDATE queries (+ scheduler_run_as_user_id)
                                                          INSERT {table}_run_as_audit
 QSScheduler.startup/register_slug ◄── schedulable()/get_run_as() (tolerant of missing column)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `ThreadSource` (`sources/base.py:14`) | extends + modifies | new no-op `async prepare(context)`; `fetch()`/`run()` unchanged |
| `SharepointSource` (`sources/sharepoint.py:20`) | refactors | rebased on `GraphDriveSource`; behaviour, messages and schema unchanged |
| `SOURCE_REGISTRY` / `__all__` (`sources/__init__.py:1-35`) | modifies | adds `OneDriveSource`; **shared with FEAT-158** |
| `MultiQS.__init__` / `query()` (`multi/__init__.py:107,556`) | modifies | `identity_context` kw-only param; `await t.prepare(...)` after construction |
| `handlers/multi.py:493` | modifies | builds the context; maps `DelegatedIdentityError` to 409 |
| `DefinitionRepository` (`repositories/definitions.py:56`) | modifies | run-as column handling, audit, tolerant reads |
| `QueryManager.patch/put/post` (`handlers/manager.py:415,671,780`) | modifies | passes `run_as_actor` from the session |
| `QSScheduler` (`scheduler/scheduler.py:120`) | modifies | keeps `app["auth"]`; forwards run-as into multi job kwargs |
| `scheduled_multiqs_job` (`scheduler/jobs.py:104`) | modifies | builds the identity context |
| navigator-auth `IdentityProvider.get_user_identity_credential` | uses | sessionless read + refresh + rotation |
| navigator-auth `cached_credential` / `cache_credential` | uses | session-vault cache under `identity:onedrive` |
| **P1 — navigator-auth `onedrive` provider** | external prerequisite | see below |

**P1 — required navigator-auth contract.** This is the cross-repo
prerequisite, delivered by its own spec in the navigator-auth repo:

- `OneDriveAuth(ExternalAuth)` with `_service_name = "onedrive"`, so
  `AuthHandler.get_external_backend("onedrive")` resolves it.
- **Link-only.** Its `configure()` registers only the identity-link callback
  (`/auth/onedrive/callback/`). There are no login, logout or API-login routes,
  and the middleware never treats it as an authentication method (S1).
- `get_identity_client()` returns `(ONEDRIVE_CLIENT_ID, ONEDRIVE_CLIENT_SECRET)`.
- The authority is `https://login.microsoftonline.com/common/oauth2/v2.0/{authorize,token}`,
  which accepts both work and personal accounts.
- `identity_scopes()` returns `["Files.Read", "User.Read", "offline_access"]`.
- `refresh_identity_tokens()` handles rotated refresh tokens. The generic
  `ExternalAuth` implementation is enough.
- *Nice to have:* a public `AuthHandler.identity_provider` property. querysource
  falls back to `AuthHandler._idp` when it is absent (§6).
- It is released as a navigator-auth version **V**. §8 Q2 tracks V.

### Data Models

```python
# querysource/auth/identity_tokens.py
@dataclass(frozen=True)
class SourceIdentityContext:
    user_id: int | None
    session: Any | None = None        # navigator_session SessionData (request path only)
    auth: Any | None = None           # navigator_auth AuthHandler (request.app["auth"])
    origin: str = "request"           # "request" | "scheduler"

@dataclass(frozen=True)
class DelegatedToken:
    access_token: str
    expires_at: datetime | None       # aware UTC; None = unknown

# querysource/repositories/definitions.py
@dataclass(frozen=True)
class RunAsChange:
    query_slug: str
    old_user_id: int | None
    new_user_id: int | None
    operation: str                    # "set" | "change" | "clear"
```

DDL, documented in `docs/PER_TENANT_QUERIES.md` and applied to `public` and to
every tenant schema (`{table}` = the store's queries table):

```sql
ALTER TABLE "{schema}"."{table}" ADD COLUMN IF NOT EXISTS scheduler_run_as_user_id INTEGER;

CREATE TABLE IF NOT EXISTS "{schema}"."{table}_run_as_audit" (
    audit_id     BIGSERIAL PRIMARY KEY,
    query_slug   VARCHAR     NOT NULL,
    old_user_id  INTEGER,
    new_user_id  INTEGER,
    operation    VARCHAR     NOT NULL CHECK (operation IN ('set', 'change', 'clear')),
    changed_by   INTEGER     NOT NULL,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_info JSONB
);

CREATE OR REPLACE FUNCTION "{schema}".qs_run_as_audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'run-as audit is append-only'; END $$;

CREATE TRIGGER qs_run_as_audit_no_mutation
    BEFORE UPDATE OR DELETE ON "{schema}"."{table}_run_as_audit"
    FOR EACH ROW EXECUTE FUNCTION "{schema}".qs_run_as_audit_immutable();

REVOKE UPDATE, DELETE, TRUNCATE ON "{schema}"."{table}_run_as_audit" FROM <app_role>;
GRANT  INSERT, SELECT          ON "{schema}"."{table}_run_as_audit" TO   <app_role>;
GRANT  USAGE                   ON SEQUENCE "{schema}"."{table}_run_as_audit_audit_id_seq" TO <app_role>;
```

### New Public Interfaces

The MultiQS YAML/JSON component:

```yaml
sources:
  - OneDriveSource:
      auth: app                       # app (default) | delegated
      user: "someone@contoso.com"     # app mode + path addressing only
      credentials:                    # app mode only; each falls back to SHAREPOINT_*
        client_id: ONEDRIVE_APP_ID
        client_secret: ONEDRIVE_APP_SECRET
        tenant_id: ONEDRIVE_TENANT_ID
      source:
        directory: "Reports/2026"     # or: url: "https://…-my.sharepoint.com/…" / "https://1drv.ms/…"
        filename: "extract_{filedate}.xlsx"
        sheet_name: 0
        pd_args: {skiprows: 1}
        masks: {"{filedate}": ["today", {"mask": "%Y%m%d"}]}
```

Python: `OneDriveSource`, `GraphDriveSource`, `SourceIdentityContext`,
`resolve_delegated_token`, `DelegatedIdentityError`, and
`DefinitionRepository.get_run_as`.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Graph drive base | yes | skeleton below; messages copied verbatim from `sharepoint.py`; scoped patch | — |
| M2: SharepointSource rebase | yes | behaviour-preserving; `tests/test_source_sharepoint.py` unchanged; schema snapshot | — |
| M3: Delegated identity resolver | yes | exact resolution order + errors below; navigator-auth symbols verified in §6 | — |
| M4: `prepare()` hook + MultiQS/handler wiring | yes | call site `multi/__init__.py:556`; 409 mapping | — |
| M5: OneDriveSource | yes | mode matrix §2; msgraph builders verified in §6 | — |
| M6: Run-as persistence | no | — | transactional diff-and-audit inside `patch`/`upsert` touches tenant write semantics; needs a thinking-model implementer |
| M7: Manager + scheduler run-as wiring | yes | kwargs names and flow fixed below | — |
| M8: Registration, extra, docs, generated schema | yes | file list fixed; generator command | — |
| M9: Enable the `onedrive` provider + pin | yes, **blocked on P1** | settings entry + `navigator-auth>=V` | blocked by the external release |

### Module 1: Graph drive-item base
- **Path**: `querysource/queries/multi/sources/graph.py` (new)
- **Responsibility**: the shared machinery listed in §2 Overview: the scoped
  kiota patch, `StaticTokenCredential`, and `fetch()` as a template method.
- **Depends on**: `ThreadSource` (existing)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/graph.py  (new)
  from .base import ThreadSource  # verified: querysource/queries/multi/sources/base.py:14
  from .file import excel_based   # verified: querysource/queries/multi/sources/sharepoint.py:17

  @contextmanager
  def kiota_platform_version_patch() -> Iterator[None]:
      """Strip platform.version()'s trailing space for the kiota User-Agent.
      Reference-counted under a module threading.Lock: the first entry installs the
      patch, the last exit restores the original in `finally` (never leaks)."""

  class StaticTokenCredential:
      """AsyncTokenCredential adapter over an already-resolved delegated access token.
      verified: GraphServiceClient(credentials: TokenCredential | AsyncTokenCredential)."""
      def __init__(self, access_token: str, expires_at: datetime | None) -> None: ...
      async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
          """Return AccessToken(token, expires_on epoch s); unknown expiry → now+300."""
      async def close(self) -> None:
          """No-op — nothing to release (never treated as an owned Azure credential)."""
      async def __aenter__(self) -> "StaticTokenCredential": ...
      async def __aexit__(self, *exc: Any) -> None: ...

  class GraphDriveSource(ThreadSource):
      """Download one CSV/Excel file through Microsoft Graph and return a DataFrame."""
      _extra_name: str = "sharepoint"   # pip extra named in ImportError messages

      def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
          """Parse the shared `source` keys with literal .get() calls (introspection):
          filename, directory, sheet_name (default 0), pd_args ({}), url (source or
          options), masks (merged into self._masks)."""

      @staticmethod
      def _encode_share_url(url: str) -> str:
          """'u!' + unpadded base64url(url) — moved verbatim from sharepoint.py:127-140."""

      def _parse_file_content(self, content: bytes) -> pd.DataFrame:
          """Moved verbatim from sharepoint.py:142-179."""

      def _import_graph(self) -> tuple[type, type]:
          """Return (ClientSecretCredential[aio], GraphServiceClient); raise ImportError whose
          message contains 'msgraph-sdk' and 'pip install querysource[<_extra_name>]'."""

      async def _credential(self) -> Any:
          """Credential for this fetch. Default: aio ClientSecretCredential from
          self._tenant_id/_client_id/_client_secret (set by the subclass)."""

      def _owns_credential(self, credential: Any) -> bool:
          """True when fetch() created it and must close() it (False for StaticTokenCredential)."""

      async def _resolve_shared_item(self, client: Any) -> Any:
          """GET /shares/{u!…}/driveItem for self._url; RuntimeError if unresolved; sets
          self._filename from item.name when empty."""

      @abstractmethod
      async def _resolve_drive_item(self, client: Any) -> Any:
          """Path-mode resolution (after resolve_masks on directory/filename)."""

      async def _download(self, item: Any) -> bytes:
          """@microsoft.graph.downloadUrl via httpx.AsyncClient(timeout=600.0)."""

      async def fetch(self) -> pd.DataFrame:
          """Template: imports → credential → client (inside kiota_platform_version_patch)
          → shared or path item → download → parse; closes only owned credentials."""
  ```

### Module 2: SharepointSource rebase
- **Path**: `querysource/queries/multi/sources/sharepoint.py` (modifies)
- **Responsibility**: subclass `GraphDriveSource`, keep `creds.get(...)`
  parsing (literal defaults `SHAREPOINT_*`, `tenant_name`/`tenant_host`, `site`)
  in its own `__init__`, and move lines 237-310 into `_resolve_drive_item`.
  `_encode_share_url` stays reachable as `SharepointSource._encode_share_url`
  through inheritance. Every error message is kept byte-for-byte.
- **Depends on**: M1
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/sharepoint.py  (modifies :20)
  class SharepointSource(GraphDriveSource):  # was ThreadSource — verified: sharepoint.py:20
      def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
          """Credentials + tenant/site parsing exactly as sharepoint.py:86-107."""
      async def _resolve_drive_item(self, client: Any) -> Any:
          """Site → drives → library/subfolder → root:/path: (sharepoint.py:237-310, unchanged)."""
  ```

### Module 3: Delegated identity resolver
- **Path**: `querysource/auth/identity_tokens.py` (new)
- **Responsibility**: `SourceIdentityContext`, `DelegatedToken`,
  `DelegatedIdentityError`, extracting the user id from a session, and the
  resolution order given in §2.
- **Depends on**: navigator-auth (existing dependency)
- **Interface Skeleton**:
  ```python
  # querysource/auth/identity_tokens.py  (new)
  from ..exceptions import QueryException  # verified: querysource/exceptions.py:6

  ONEDRIVE_PROVIDER: str = "onedrive"
  IDENTITY_LINK_PATH: str = "/api/v1/user/identities/link/{provider}"

  class DelegatedIdentityError(QueryException):
      """No usable linked identity. Attributes: provider, user_id, link_url, reason
      ('no_user' | 'auth_unavailable' | 'not_linked' | 'refresh_failed')."""

  def user_id_from_session(session: Any) -> int | None:
      """Numeric user id from session[AUTH_SESSION_OBJECT]['user_id'] or session['user_id'];
      None when absent or non-integer (never a username)."""

  def identity_provider(auth: Any) -> Any | None:
      """auth.identity_provider if present (P1 nice-to-have) else auth._idp, else None."""

  @dataclass(frozen=True)
  class SourceIdentityContext:
      user_id: int | None
      session: Any | None = None
      auth: Any | None = None
      origin: str = "request"
      @classmethod
      def from_request(cls, request: web.Request, session: Any) -> "SourceIdentityContext":
          """user_id from session, auth = request.app.get('auth')."""
      @classmethod
      def for_scheduler(cls, run_as_user_id: int | None, auth: Any) -> "SourceIdentityContext": ...

  async def resolve_delegated_token(
      context: SourceIdentityContext | None, provider: str = ONEDRIVE_PROVIDER
  ) -> DelegatedToken:
      """1) vault cache via cached_credential(session, provider) unless is_expiring(
      IDENTITY_REFRESH_LEEWAY); 2) identity_provider(auth).get_user_identity_credential(
      user_id, provider, auto_refresh=True); 3) cache_credential(session, …) when a session
      exists. Maps UserNotFound → not_linked, ConfigError/AuthException → refresh_failed,
      missing user → no_user, missing auth/idp → auth_unavailable. Must run on the
      caller's loop."""
  ```

### Module 4: `prepare()` hook and MultiQS/handler wiring
- **Paths**: `querysource/queries/multi/sources/base.py`,
  `querysource/queries/multi/__init__.py`, `querysource/handlers/multi.py` (all modify)
- **Responsibility**: the no-op hook; `MultiQS` accepts and stores
  `identity_context` and awaits `t.prepare(...)` for each `sources:` entry
  before the run loop; the handler builds the context and maps
  `DelegatedIdentityError` to 409 `{"error": str, "link": link_url}`.
- **Depends on**: M3
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/base.py  (modifies — insert before :132 `def run`)
  async def prepare(self, context: "SourceIdentityContext | None") -> None:
      """Resolve caller-loop-bound credentials before the thread starts. Default: no-op."""

  # querysource/queries/multi/__init__.py  (modifies :107 signature, :162, :556)
  def __init__(self, …, *, tenant=None, definition=None, principal=None,
               identity_context: "SourceIdentityContext | None" = None, **kwargs): ...
  # after `t = cls(name, config, self._request, self._queue)` (:556):
  #     await t.prepare(self._identity_context)
  ```

### Module 5: OneDriveSource
- **Path**: `querysource/queries/multi/sources/onedrive.py` (new)
- **Responsibility**: the mode matrix, credential fallback, `prepare()`
  (delegated), `_credential()` and `_resolve_drive_item()`.
- **Depends on**: M1, M3, M4
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/onedrive.py  (new)
  class OneDriveSource(GraphDriveSource):
      """Download one CSV/Excel file from OneDrive (Business app-only, sharing URL, or the
      requesting/run-as user's own drive via a linked `onedrive` identity)."""
      _extra_name = "onedrive"

      def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
          """Literal options.get('auth', 'app'), options.get('user', ''),
          creds.get('client_id', 'ONEDRIVE_APP_ID') / 'ONEDRIVE_APP_SECRET' / 'ONEDRIVE_TENANT_ID'
          each falling back to SHAREPOINT_APP_ID / _SECRET / _TENANT_ID when the navconfig name
          does not resolve; then _validate_mode()."""
      def _validate_mode(self) -> None:
          """ValueError for: unknown auth; app path-mode without user; delegated with user;
          no url and no filename."""
      async def prepare(self, context: "SourceIdentityContext | None") -> None:
          """delegated only: self._token = await resolve_delegated_token(context)."""
      async def _credential(self) -> Any:
          """delegated → StaticTokenCredential(self._token…); app → super()._credential()."""
      async def _resolve_drive_item(self, client: Any) -> Any:
          """app: client.users.by_user_id(user).drive.get(); delegated: client.me.drive.get();
          then client.drives.by_drive_id(id).items.by_drive_item_id(f'root:/{path}:').get();
          RuntimeError("File '<f>' not found in OneDrive directory '<d>'.") when missing."""
  ```

### Module 6: Run-as persistence (repository + DDL)
- **Paths**: `querysource/repositories/definitions.py` (modifies),
  `docs/PER_TENANT_QUERIES.md` (modifies),
  `tests/tenants/conftest.py` (modifies fixtures)
- **Responsibility**:
  - pop the column in `_row_to_persisted`;
  - reject it in `patch`/`upsert`/`create` payloads;
  - `patch(..., run_as_actor=)` and `upsert(..., run_as_actor=)` do the
    transactional diff-and-audit;
  - `get_run_as()`;
  - `schedulable()` tolerates a store without the column;
  - document the DDL, both in the Provisional DDL gate and as a migration
    section for existing stores.
- **Depends on**: —
- **Interface Skeleton**:
  ```python
  # querysource/repositories/definitions.py  (modifies :49, :88-109, :331, :399, + new methods before :476)
  RUN_AS_COLUMN: str = "scheduler_run_as_user_id"

  def _run_as_audit_table(self, store: QueryStore) -> str:
      """quote_identifier(store.schema) + '.' + quote_identifier(f'{store.table}_run_as_audit')."""

  async def get_run_as(self, identity: QueryIdentity) -> int | None:
      """Stored run-as user id; None when unset, row missing, or column absent (un-migrated)."""

  async def upsert(self, identity: QueryIdentity, data: Mapping[str, Any], *,
                   run_as_actor: int | None = None, request_info: Mapping[str, Any] | None = None,
                   ) -> tuple[Mapping[str, Any], bool]:  # verified: definitions.py:331
      """Existing semantics + one transaction: SELECT … FOR UPDATE previous attributes.scheduler,
      write, then _apply_run_as(...) when the schedule was created/changed/removed."""

  async def patch(self, identity: QueryIdentity, data: Mapping[str, Any], *,
                  run_as_actor: int | None = None, request_info: Mapping[str, Any] | None = None,
                  ) -> Mapping[str, Any]:  # verified: definitions.py:399
      """Same as upsert. Raises TenantError('invalid_tenant') when data contains RUN_AS_COLUMN."""

  async def _apply_run_as(self, conn: Any, store: QueryStore, slug: str, previous: Any,
                          current: Any, actor: int | None,
                          request_info: Mapping[str, Any] | None) -> RunAsChange | None:
      """Inside the caller's transaction: created/changed schedule + actor → set/change;
      removed schedule → clear; unchanged or no actor → None (no write, no audit).
      UndefinedColumnError (un-migrated store) → log WARNING once per store, return None."""
  ```

### Module 7: Manager + scheduler run-as wiring
- **Paths**: `querysource/handlers/manager.py`, `querysource/scheduler/scheduler.py`,
  `querysource/scheduler/jobs.py` (all modify); `querysource/handlers/scheduler.py`
  (**no functional change**, gets a regression test only)
- **Responsibility**:
  - `QueryManager.patch/put/post` read the session's numeric user
    (`user_id_from_session`) and pass `run_as_actor` and `request_info`.
  - `QSScheduler.startup` keeps `self._auth = app.get("auth")`.
  - `_fetch_slug_row` adds `scheduler_run_as_user_id` through `get_run_as`.
  - The `schedulable()` rows already carry it.
  - The multi `add_job` kwargs gain `run_as_user_id` and `identity_auth`.
  - `scheduled_multiqs_job` builds `SourceIdentityContext.for_scheduler` and
    passes it to `MultiQS`.
- **Depends on**: M3, M6
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/jobs.py  (modifies :104-112, :144)
  async def scheduled_multiqs_job(slug: str, notification_manager: NotificationManager | None = None, *,
                                  owner: TenantOwnerEnvelope | None = None, job_id: str | None = None,
                                  run_as_user_id: int | None = None, identity_auth: Any = None,
                                  **kwargs: Any) -> None:
      """…existing contract…; builds SourceIdentityContext.for_scheduler(run_as_user_id,
      identity_auth) and passes identity_context= to MultiQS. A DelegatedIdentityError is
      reported through notification_manager.notify like any other job failure."""
  ```

### Module 8: Registration, extra, docs, generated schema
- **Paths**: `querysource/queries/multi/sources/__init__.py`, `pyproject.toml`
  (`onedrive` extra only), `docs/PER_TENANT_QUERIES.md` (a OneDrive usage and
  identity-link section), `generated/OneDriveSource.json` (created by
  `generate-multiquery-docs`), `tests/test_source_registry.py`,
  `tests/multi/sources/test_registry.py`
- **Depends on**: M5 (and M6 for the DDL doc cross-reference)
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/sources/__init__.py  (modifies :7, :16, :31)
  from .onedrive import OneDriveSource
  SOURCE_REGISTRY["OneDriveSource"] = OneDriveSource   # literal dict entry, not a mutation
  ```
  ```toml
  # pyproject.toml  (modifies near :151)
  onedrive = ["msgraph-sdk>=1.0", "azure-identity>=1.0", "httpx>=0.24"]
  ```

### Module 9: Enable the `onedrive` provider and raise the pin (**blocked on P1**)
- **Paths**: `settings/settings.py` (`AUTHENTICATION_BACKENDS` adds P1's dotted
  path), `pyproject.toml` (`navigator-auth>=V`), `uv.lock`
- **Responsibility**: turn on the link-only provider and require the
  navigator-auth release that ships it, plus a startup test that the backend
  resolves and exposes no login route.
- **Depends on**: P1 released; M8 (shares `pyproject.toml`)

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_kiota_patch_restores_and_nests` | M1 | nested and concurrent entries install once and restore the original `platform.version` on the last exit, including when an exception is raised |
| `test_static_token_credential_get_token` | M1 | returns `AccessToken` with an epoch expiry; unknown expiry → now+300; `close()` is a no-op |
| `test_graph_import_error_names_extra` | M1 | the ImportError message contains `msgraph-sdk` and the subclass's extra name |
| `test_fetch_closes_only_owned_credentials` | M1 | `ClientSecretCredential` is closed; `StaticTokenCredential` is not |
| `tests/test_source_sharepoint.py` (all, **unchanged**) | M2 | behaviour preserved |
| `test_sharepoint_schema_unchanged` | M2 | `extract_source_schema(SharepointSource)` equals the committed `generated/SharepointSource.json` attributes |
| `test_sharepoint_resolve_drive_item_paths` | M2 | mocked Graph: library normalisation (`Shared Documents`→`Documents`), single-segment dir, fallback to the first drive, not-found message |
| `test_resolve_token_vault_hit` | M3 | a non-expiring cached credential is used; the IdP is not called |
| `test_resolve_token_vault_expiring_falls_back` | M3 | an expiring cache entry → IdP called → re-cached |
| `test_resolve_token_sessionless_uses_idp` | M3 | scheduler context → `get_user_identity_credential(user, "onedrive", auto_refresh=True)` |
| `test_resolve_token_errors` | M3 | no user / no auth / `UserNotFound` / `ConfigError` → `DelegatedIdentityError` with the right `reason` and `link_url` |
| `test_user_id_from_session_numeric_only` | M3 | ignores username; handles both session shapes |
| `test_prepare_called_before_start` | M4 | `MultiQS.query()` awaits `prepare()` for every source before any `start()`; a failing prepare starts no thread |
| `test_multi_handler_maps_delegated_error_409` | M4 | the response body carries `link` |
| `test_onedrive_mode_matrix` | M5 | every invalid combination raises `ValueError`; valid ones construct |
| `test_onedrive_credential_fallback` | M5 | an unresolved `ONEDRIVE_*` falls back to `SHAREPOINT_*` |
| `test_onedrive_app_user_drive_path` | M5 | mocked Graph: `users.by_user_id(u).drive` → `root:/dir/file:` |
| `test_onedrive_delegated_me_drive` | M5 | mocked Graph: `me.drive`, `StaticTokenCredential` used |
| `test_onedrive_share_url_both_modes` | M5 | `/shares` id encoding and filename inference |
| `test_row_to_persisted_pops_run_as` | M6 | a row with the column validates; the value is not in the persisted dict |
| `test_patch_rejects_run_as_key` / `test_upsert_rejects_run_as_key` | M6 | `TenantError(invalid_tenant)` |
| `test_run_as_set_change_clear_audited` | M6 | created → set, changed → change, removed → clear; one audit row each, in the same transaction (rollback on audit failure leaves the definition unchanged) |
| `test_run_as_untouched_on_non_schedule_edit` | M6 | editing `description` writes neither the column nor an audit row |
| `test_schedulable_tolerates_missing_column` | M6 | `UndefinedColumnError` → retry → rows with `scheduler_run_as_user_id=None` |
| `test_manager_passes_session_actor` | M7 | patch/put/post pass `run_as_actor` from the session |
| `test_scheduler_sync_never_writes_run_as` | M7 | `SchedulerJobsView.post` makes no run-as write |
| `test_multi_job_kwargs_carry_run_as` | M7 | `add_job` kwargs include `run_as_user_id` and `identity_auth`; no token material |
| `test_scheduled_multiqs_job_builds_context` | M7 | `MultiQS` receives `identity_context` with `origin="scheduler"` |
| `test_registry_contains_onedrive` | M8 | registry and `__all__` membership |

### Integration Tests
| Test | Description |
|---|---|
| `test_onedrive_delegated_end_to_end_mocked` | HTTP MultiQS with a session whose vault holds `identity:onedrive` → prepare → thread → mocked Graph download → DataFrame |
| `test_scheduled_delegated_run_as` | a definition PATCHed with a schedule by user 42 → column = 42 + audit → the scheduler loads the row → the job resolves user 42's identity through a mocked IdP |
| `test_run_as_audit_append_only` *(requires PG, marked)* | UPDATE/DELETE on the audit table raises |

### Test Data / Fixtures
```python
@pytest.fixture
def graph_client_mock():
    """MagicMock GraphServiceClient with async .get() on shares/users/me/drives builders."""

@pytest.fixture
def vault_session():
    """Dict-like session with AUTH_SESSION_OBJECT={'user_id': 42} and '_vault' fake exposing
    async exists/get/set for 'identity:onedrive'."""
```

---

## 5. Acceptance Criteria

- [ ] `OneDriveSource` is registered in `SOURCE_REGISTRY`, and every mode in the §2 matrix passes its mocked-Graph test.
- [ ] Invalid mode combinations raise `ValueError` at construction. The identity provider is fixed to `onedrive`, and no `provider:` option exists.
- [ ] `SharepointSource` behaviour is unchanged: `tests/test_source_sharepoint.py` passes **without edits**, and its introspected schema equals `generated/SharepointSource.json`.
- [ ] The kiota `platform.version` patch is always restored (a test proves it, including under an exception).
- [ ] Delegated tokens are resolved in `prepare()` on the caller's loop. No `SessionVault`, Redis or pool access happens inside a source thread (proved by `test_prepare_called_before_start` and by the thread receiving only a `StaticTokenCredential`).
- [ ] The session path uses the vault cache `identity:onedrive`. The sessionless path uses `IdentityProvider.get_user_identity_credential(..., auto_refresh=True)`. querysource contains no refresh-token grant code.
- [ ] A missing or unusable identity gives HTTP 409 with the link URL on the request path, and a notification (not a crash) on the scheduler path.
- [ ] `scheduler_run_as_user_id` is set, changed or cleared **only** when `attributes.scheduler` is created, changed or removed through `QueryManager`. Each change writes exactly one audit row in the same transaction. Non-schedule edits and `POST /api/v1/qs/scheduler/jobs` never change it.
- [ ] `scheduler_run_as_user_id` in a PATCH or upsert payload is rejected with `TenantError(invalid_tenant)`. It never appears in list, get, export or `:insert` output.
- [ ] Un-migrated stores keep working: reads, `schedulable()` and the scheduler startup succeed, and run-as reads as `None`.
- [ ] The DDL (column, audit table, immutability trigger, grants) is documented for the legacy store and added to the tenant Provisional DDL gate.
- [ ] `pytest tests/test_source_sharepoint.py tests/test_source_registry.py tests/multi tests/tenants tests/scheduler -q` and the new tests pass. `ruff check` is clean on the changed paths.
- [ ] M9 is merged only after P1 is released. With M9 applied, no `/auth/onedrive/login` or `/api/v1/auth/onedrive/` route is registered.

---

## 6. Codebase Contract

> Verified against base commit **7e6916e** (dev) on 2026-09-30; navigator-auth
> 0.28.2, msgraph-sdk, azure-identity and asyncdb as installed in `.venv`.

### Verified Imports
```python
from querysource.queries.multi.sources.base import ThreadSource          # sources/base.py:14
from querysource.queries.multi.sources.file import excel_based           # used at sharepoint.py:17
from querysource.queries.multi.sources import SOURCE_REGISTRY            # sources/__init__.py:29
from querysource.queries.multi._introspect import extract_source_schema  # _introspect.py:990
from querysource.exceptions import QueryException, DataNotFound          # exceptions.py:6, :53
from querysource.repositories.definitions import DefinitionRepository    # definitions.py:56
from querysource.tenants import QueryIdentity, QueryStore, LoadedDefinition, quote_identifier  # verified import
from querysource.tenant_errors import TenantError                        # definitions.py imports it
from asyncdb.drivers.pg import UndefinedTableError, UndefinedColumnError, pg   # verified import
from azure.core.credentials import AccessToken                           # verified import
from azure.identity.aio import ClientSecretCredential                    # sharepoint.py:189
from msgraph import GraphServiceClient                                   # sharepoint.py:190
from navigator_session import get_session                                # handlers/abstract.py:8
from navigator_auth.conf import AUTH_SESSION_OBJECT, IDENTITY_REFRESH_LEEWAY   # "session", 120
from navigator_auth.vault import VAULT_SESSION_KEY                       # "_vault"
from navigator_auth.identity.store import cached_credential, cache_credential, IDENTITY_VAULT_KEY  # "identity:{provider}"
from navigator_auth.identity.types import TokenResponse
from navigator_auth.exceptions import UserNotFound, ConfigError, AuthException
```

### Existing Class Signatures
```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                                   # :14
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:  # :25
    self._masks: dict  # popped from options                                  # :40-42
    def resolve_credential(self, key: str, value: str) -> str:               # :45  (ALL_CAPS → navconfig, else literal)
    def resolve_masks(self, text: str) -> str:                               # :72
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                                   # :116-117
    def run(self) -> None:                                                   # :132 (new loop; DataNotFound/NoDataFound → info)

# querysource/queries/multi/sources/sharepoint.py
class SharepointSource(ThreadSource):                                        # :20
    def __init__(...)                                                        # :78-125 (creds :86-107, source :108-125)
    @staticmethod
    def _encode_share_url(url: str) -> str:                                  # :127-140
    def _parse_file_content(self, content: bytes) -> pd.DataFrame:           # :142-179
    async def fetch(self) -> pd.DataFrame:                                   # :181-332
    # platform.version patch without restore: :208-210 ; site/drive resolution: :237-310

# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):                                                    # :101
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None,
                 request=None, loop=None, user_session=None, *, tenant=None,
                 definition=None, principal=None, **kwargs):                 # :107-121
    self._user_session = user_session                                        # :162
    async def query(self):                                                   # :278
    # sources dispatch: :541-557 ; `t = cls(name, config, self._request, self._queue)` :556

# querysource/interfaces/queries.py — AbstractQuery.__init__ accepts **kwargs   # :52-63; self._request :107, self._principal :129

# querysource/handlers/multi.py — `_user_session = request.get('user_session')` :483; MultiQS(...) :493-501 (no request=)

# querysource/repositories/definitions.py
_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"   # :49
class DefinitionRepository:                                                  # :56
    def __init__(self, registry: TenantRegistry, connection_factory: Callable[[], Awaitable[AbstractAsyncContextManager[pg]]]) -> None:  # :59
    def _qualified_table(self, store: QueryStore) -> str:                    # :70
    def _row_to_persisted(self, row, store) -> tuple[dict, str | None]:      # :88 (TenantQueryDefinition(**data) :105)
    async def _fetch_row(self, store, slug) -> Mapping | None:               # :121 (SELECT *)
    async def get(self, identity: QueryIdentity) -> LoadedDefinition:        # :161
    async def schedulable(self, store: QueryStore) -> tuple[Mapping, ...]:   # :282
    async def create(self, store, data) -> Mapping:                          # :292
    async def upsert(self, identity, data) -> tuple[Mapping, bool]:          # :331
    async def patch(self, identity, data) -> Mapping:                        # :399 (program_slug guard :421; SET from raw keys :433-440)
    async def delete(self, identity) -> bool:                                # :476

# querysource/tenant_models.py
class TenantQueryDefinition(BaseModel):  # :24 — unknown kwarg ⇒ TypeError (verified: scheduler_run_as_user_id rejected)

# querysource/handlers/manager.py
class QueryManager(QueryView):                                               # :36
    async def patch(self):   # :415 → repo.patch(identity, data) :463
    async def put(self):     # :671 → repo.upsert(identity, data) :718
    async def post(self):    # :780 → repo.upsert(identity, data) :834
    # no field allowlist before repo.patch (body keys become SQL identifiers)

# querysource/scheduler/scheduler.py
class QSScheduler:                                                           # :120
    def _register_query_row(self, row: dict, store=None) -> Optional[str]:   # :280 (multi add_job :344-360)
    async def _fetch_slug_row(self, slug, *, tenant=None) -> dict | None:    # :492 (row dict :549-556)
    async def register_slug(self, slug, *, tenant=None) -> dict:             # :586
    async def startup(self, app: web.Application) -> None:                   # :659 (repository :684; schedulable loop :718-745)

# querysource/scheduler/jobs.py
async def scheduled_multiqs_job(slug, notification_manager=None, *, owner=None, job_id=None, **kwargs) -> None:  # :104
    # MultiQS(slug=slug, tenant=tenant) :144 ; notify on failure :151-158

# querysource/handlers/scheduler.py
class SchedulerJobsView(BaseView):                                           # :70
    async def post(self):  # :208 — re-sync only; register_slug :259

# navigator_auth (site-packages, 0.28.2)
class AuthHandler:                                   # auth.py — app[self.name]=self (:680, name "auth"); self._idp (:136)
    def get_external_backend(self, service: str):    # auth.py:353
class IdentityProvider:                              # backends/idp/__init__.py:39
    async def get_user_identity_credential(self, user_id, provider: str, *, auto_refresh: bool = True) -> dict:  # :84
async def cached_credential(session: Any, provider: str) -> Optional[dict]   # identity/store.py (vault helpers :358-400)
async def cache_credential(session: Any, provider: str, credential: dict) -> None
class TokenResponse:                                 # identity/types.py:8 — access_token, refresh_token, expires_at, …
    def is_expiring(self, leeway: int = 0) -> bool
    def credential(self) -> dict                     # {"access_token","token_type","refresh_token","id_token","expires_at"(iso),"scopes","provider_user_id"}
    @classmethod
    def from_credential(cls, data: dict) -> "TokenResponse"
class ExternalAuth(BaseAuthBackend):                 # backends/external.py:80 — configure() registers login routes :114-157

# msgraph
GraphServiceClient(credentials: TokenCredential | AsyncTokenCredential | None = None, scopes=None, request_adapter=None)
client.shares.by_shared_drive_item_id(id).drive_item.get()          # sharepoint.py:226-229
client.users.by_user_id(u).drive.get()                              # verified builder attr
client.me.drive.get()                                               # client.me → UserItemRequestBuilder (verified)
client.drives.by_drive_id(d).items.by_drive_item_id("root:/p:").get()   # sharepoint.py:300-304
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `GraphDriveSource` | `ThreadSource` | subclass | `sources/base.py:14` |
| `MultiQS.query` | `ThreadSource.prepare` | `await t.prepare(ctx)` after construction | `multi/__init__.py:556` |
| `resolve_delegated_token` | `IdentityProvider.get_user_identity_credential` | method call via `auth._idp` | `navigator_auth/backends/idp/__init__.py:84`, `auth.py:136` |
| `resolve_delegated_token` | session vault | `cached_credential` / `cache_credential` | `navigator_auth/identity/store.py:358-400` |
| `QueryManager.patch/put/post` | `DefinitionRepository.patch/upsert` | `run_as_actor=` kwarg | `manager.py:463,718,834` |
| `QSScheduler._register_query_row` | `scheduled_multiqs_job` | add_job kwargs | `scheduler.py:344-360` |
| `QSScheduler._fetch_slug_row` | `DefinitionRepository.get_run_as` | method call | `scheduler.py:549-556` |

### Does NOT Exist (Anti-Hallucination)
- ~~Any OneDrive code in querysource~~: grep of querysource/, tests/, docs/ and pyproject finds nothing (F005).
- ~~`msgraph.generated.me`~~: there is no such module. Use `client.me` (it returns a `UserItemRequestBuilder`).
- ~~`TenantQueryDefinition.scheduler_run_as_user_id` / `QueryModel.scheduler_run_as_user_id`~~: deliberately not added.
- ~~A vault module inside querysource~~: the vault lives in `navigator_session.vault` / `navigator_auth.vault`.
- ~~An enabled `azure`/`onedrive` backend in querysource~~: `settings/settings.py:146-148` enables only `BasicAuth`.
- ~~`AuthHandler.identity_provider`~~ in navigator-auth 0.28.2: only the private `_idp` exists (the public property is a P1 nice-to-have).
- ~~Canonical DDL for `public.queries` in the repo~~: schema changes ship as documented SQL (`docs/PER_TENANT_QUERIES.md:35-75`).
- ~~A field allowlist in `QueryManager.patch`~~: body keys reach `DefinitionRepository.patch` unfiltered.
- ~~`CredentialResolver._extract_username` as a user-id source~~: it prefers `username` (`auth/credentials.py:78-93`). Do not reuse it for run-as.
- ~~Using `QSPrincipal` for run-as~~: it is a PBAC identity (`auth/principal.py:21`), and scheduled jobs intentionally run without a principal (`tests/scheduler/test_jobs_no_principal.py`).

### Edit Sites (Blueprint Anchors)

Verified against: **7e6916e**

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/queries/multi/sources/graph.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/onedrive.py` | CREATE | — | — | — |
| `querysource/auth/identity_tokens.py` | CREATE | — | — | — |
| `querysource/queries/multi/sources/sharepoint.py` | MODIFY | `class SharepointSource(ThreadSource):` | `sharepoint.py:20` | 1 |
| `querysource/queries/multi/sources/sharepoint.py` | MODIFY | `    async def fetch(self) -> pd.DataFrame:` | `sharepoint.py:181` | 1 |
| `querysource/queries/multi/sources/base.py` | MODIFY | `    def run(self) -> None:` | `base.py:132` | 1 |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | `from .sharepoint import SharepointSource` | `__init__.py:7` | 1 |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | `    "SharepointSource": SharepointSource,` | `__init__.py:31` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `        self._user_session = user_session` | `__init__.py:162` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                    t = cls(name, config, self._request, self._queue)` | `__init__.py:556` | 1 |
| `querysource/handlers/multi.py` | MODIFY | `            user_session=_user_session,` | `multi.py:499` | 1 |
| `querysource/repositories/definitions.py` | MODIFY | `_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"` | `definitions.py:49` | 1 |
| `querysource/repositories/definitions.py` | MODIFY | `        legacy_program_slug = data.pop("program_slug", None) if store.contract == "legacy" else None` | `definitions.py:104` | 1 |
| `querysource/repositories/definitions.py` | MODIFY | `        if "program_slug" in data:` | `definitions.py:421` | 1 |
| `querysource/repositories/definitions.py` | MODIFY | `    async def delete(self, identity: QueryIdentity) -> bool:` | `definitions.py:476` | 1 |
| `querysource/handlers/manager.py` | MODIFY | `                result = await repo.patch(identity, data)` | `manager.py:463` | 1 |
| `querysource/handlers/manager.py` | MODIFY | `result, is_created = await repo.upsert(identity, data)` — in `put` (after `async def put(self):` :671) and in `post` (after `async def post(self):` :780) | `manager.py:718, 834` | 2 |
| `querysource/scheduler/scheduler.py` | MODIFY | `        self._repository = app.get("qs_definition_repository")` | `scheduler.py:684` | 1 |
| `querysource/scheduler/scheduler.py` | MODIFY | `                    "job_id": job_id,` | `scheduler.py:358` | 1 |
| `querysource/scheduler/scheduler.py` | MODIFY | `            "query_raw": getattr(runtime, "query_raw", None),` | `scheduler.py:555` | 1 |
| `querysource/scheduler/jobs.py` | MODIFY | `        qs = MultiQS(slug=slug, tenant=tenant)` | `jobs.py:144` | 1 |
| `querysource/scheduler/jobs.py` | MODIFY | `    job_id: str \| None = None,` — the one inside `async def scheduled_multiqs_job(` | `jobs.py:109` | 3 |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | `    updated_at TIMESTAMPTZ DEFAULT now()` | `PER_TENANT_QUERIES.md:50` | 1 |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | `### Legacy store migration (FEAT-151)` | `PER_TENANT_QUERIES.md:54` | 1 |
| `pyproject.toml` | MODIFY | `sharepoint = [` | `pyproject.toml:151` | 1 |
| `pyproject.toml` (M9) | MODIFY | `"navigator-auth>=0.15.8",` | `pyproject.toml:118` | 1 |
| `settings/settings.py` (M9) | MODIFY | `    'navigator_auth.backends.BasicAuth',` | `settings.py:147` | 1 |
| `tests/test_source_registry.py` | MODIFY | `        assert len(SOURCE_REGISTRY) == 5` | `test_source_registry.py:35` | 1 |
| `generated/OneDriveSource.json` | CREATE | — (generated) | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- The `ThreadSource` contract: optional Graph imports go inside `fetch()`, and a
  missing package raises `ImportError` that names the extra (`sharepoint.py:188-203`).
- Constructors keep **literal** `creds.get('…', 'DEFAULT')`,
  `source.get('…')` and `options.get('…')` calls, because
  `extract_source_schema` regex-walks `__init__` bodies across the MRO (S10).
- Credentials go through `ThreadSource.resolve_credential` (ALL_CAPS
  navconfig names). OneDrive falls back to the `SHAREPOINT_*` name when the
  `ONEDRIVE_*` name returns itself unresolved.
- DDL is added the FEAT-151 way (`docs/PER_TENANT_QUERIES.md:54-75`): deploy
  the code first, tolerate the missing column, then run the documented ALTER.
- Logging goes through `self.logger`. No token, refresh token or credential
  dict is ever logged. Log the user id, provider and reason only.

### Known Risks / Gotchas
- **An un-migrated store would break every read.** `TenantQueryDefinition(**row)`
  raises `TypeError` on an unknown column (verified). Popping the column in
  `_row_to_persisted` must land **before** any DDL is applied. M6 goes first
  and the DDL doc says so.
- **PATCH is an open column writer.** `QueryManager.patch` sends body keys
  unfiltered to `DefinitionRepository.patch`, which turns them into SQL
  identifiers. The explicit `RUN_AS_COLUMN` rejection is the only guard.
  Tests cover both PATCH and upsert.
- **Cross-loop resources.** `SessionVault`, Redis and asyncpg pools belong to
  the request or scheduler loop, and `ThreadSource.run()` creates a new loop.
  Resolution happens only in `prepare()`.
- **Token lifetime vs slow sources.** An access token resolved in `prepare()`
  can expire during a long queue wait (MultiQS concurrency caps). Mitigation:
  `get_user_identity_credential` refreshes with `IDENTITY_REFRESH_LEEWAY`
  (120 s), and `StaticTokenCredential` reports the real expiry. A
  mid-download expiry surfaces as a Graph 401 and fails the source. It is not
  retried in-thread.
- **Process-global patch.** The kiota `platform.version` fix is global. The
  reference-counted scoped patch (S9) avoids leaking it, or restoring it early,
  across concurrent Graph sources.
- **Registrant semantics (S6).** Run-as is "who last created or changed the
  schedule", taken from the session in `QueryManager`. A caller with no
  numeric session user (for example a sessionless authz principal) leaves it
  unchanged. Delegated sources in that job then fail with `not_linked` or
  `no_user`, and a notification is sent.
- **Backfill.** Slugs scheduled before this feature have `NULL` run-as until
  someone re-saves their schedule (§8 Q1).
- **Cross-repo sequencing.** M9 and the real delegated flow need the P1
  release. M1–M8 are mergeable before it: the delegated mode is fully unit
  tested with mocks, and with the backend disabled it fails cleanly with
  `auth_unavailable`.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `msgraph-sdk` | `>=1.0` (existing `sharepoint` extra) | Graph request builders |
| `azure-identity` | `>=1.0` | app-only `ClientSecretCredential` (aio) |
| `azure-core` | transitive | `AccessToken` for `StaticTokenCredential` |
| `httpx` | `>=0.24` (core dep `httpx[http2]>=0.26.0`) | file download |
| `navigator-auth` | `>=V` (P1 release; currently `>=0.15.8`, 0.28.2 installed) | identity link, vault helpers, `onedrive` backend |

---

## 8. Open Questions

- [x] Which OneDrive flavour? — *Resolved in proposal (U1)*: "Business + personal".
- [x] How does the code relate to SharepointSource? — *Resolved in proposal (U2)*: "Extract shared base".
- [x] Credential defaults? — *Resolved in proposal (U3)*: "ONEDRIVE_* → SHAREPOINT_* fallback".
- [x] Scope of one entry? — *Resolved in proposal (U4)*: "Single file".
- [x] Personal OneDrive token lifecycle? — *Resolved in proposal (U5)*: "we need a UI interface for user's login and capture the refresh token that is saved into the user's session vault" → navigator-auth Identity Vault link flow + session-vault cache.
- [x] Is the Azure identity link enabled? — *Resolved in proposal (U6)*: "lets research, if no, we need a new provider in navigator-auth". Research answer: no, so the new link-only `onedrive` provider becomes external prerequisite P1.
- [x] Runs without a session? — *Resolved in proposal (U7)*: "read the stored tokens from user identity table by user_id, registering in some place the user_id". Follow-up answer: the scheduler job's registrant. **Refined at spec time (design research S6)**: the registrant is captured where the schedule is created or changed (`QueryManager` writes), not in the `SchedulerJobsView.post` re-sync, which would let any re-sync caller take over the identity.
- [x] Where is run-as persisted? — *Resolved in proposal (U8)*: "dedicated column with an audit trail if possible" → `scheduler_run_as_user_id` + append-only `{table}_run_as_audit`.
- [x] Model field or repository-only? — *Resolved at spec time*: "Repository-only". The field is kept out of `QueryModel` and `TenantQueryDefinition`, and only `DefinitionRepository` reads and writes it. This deliberately departs from the proposal's "QueryModel gains the field".
- [x] Cross-repo scope? — *Resolved at spec time*: "External prerequisite". This spec is querysource-only, and P1 gets its own spec in the navigator-auth repo.
- [ ] **Q1 — Backfill** for slugs already scheduled before this feature. Options: leave them `NULL` until the schedule is re-saved (default, and the safest), or add an admin-only explicit "claim run-as" endpoint that writes an audit row. — *Owner: Jesus Lara*
- [ ] **Q2 — navigator-auth version V** that ships `OneDriveAuth`, and whether it adds the public `AuthHandler.identity_provider`. This blocks M9 only. — *Owner: Jesus Lara*
- [ ] **Q3 — `<app_role>`** name for the audit-table grants in production and tenant schemas. It is a deployment value filled in the DDL doc. — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-159. The `sdd-coder` engine
  gives each task its own sub-worktree inside it.
- **Module dependency graph** (an edge means "imports or needs a symbol from"):
  - M2 → M1 (`GraphDriveSource`)
  - M4 → M3 (`SourceIdentityContext` type in `prepare`)
  - M5 → M1, M3, M4 (base class, resolver, `prepare` hook)
  - M7 → M3 (`user_id_from_session`, `SourceIdentityContext.for_scheduler`)
  - M7 → M6 (`run_as_actor=`, `get_run_as`)
  - M8 → M5 (registry import)
  - M9 → M8 (same `pyproject.toml`) + **P1 external**

  M1, M3 and M6 have no incoming edges and can run concurrently. After them,
  M2 ∥ M4, and M7 can start once M6 is done.
- **Shared files**:
  - `pyproject.toml`: M8 and M9, so serialize them.
  - `docs/PER_TENANT_QUERIES.md`: M6 (DDL) and M8 (usage section), so serialize them.
- **Exclusive resources**:
  - M8 regenerates `generated/` (`generate-multiquery-docs -o generated`) and
    edits `pyproject.toml`. Mark it `parallel: false`.
  - M9 changes `uv.lock`. Mark it `parallel: false`.
- **Cross-feature dependencies**:
  - **FEAT-158 (parquet-multiqs-source)** edits the same
    `sources/__init__.py` registry, `pyproject.toml`/`uv.lock` extras,
    `tests/test_source_registry.py` (the `== 5` count) and `generated/`.
    Whichever lands second rebases these four files. The registry-count
    assertion should become a membership check to stop the count conflict
    from recurring.
  - **P1 (navigator-auth `onedrive` provider)** must be released before M9.

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: `gpt-5.6-luna` · Status: completed
> · Transcript: `sdd/state/FEAT-159/design_research/`

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Add a first-class link-only backend contract (architecture) | CONFIRM | `ExternalAuth.configure` registers login routes; P1 must be link-only, and there is a querysource test that no login route exists | §2 P1, §5, M9 |
| S2 | Remove the ambiguous Azure provider fallback (api) | CONFIRM | the provider is fixed to `onedrive`; an explicit mode matrix rejects invalid combinations | §2 Overview, M5, §5 |
| S3 | Explicit pre-thread credential phase + token adapter (architecture) | CONFIRM | `GraphServiceClient` takes only (Async)TokenCredential; `prepare()` + `StaticTokenCredential` | M1, M4 |
| S4 | Inject scheduler auth context, not request state (architecture) | CONFIRM | the scheduler keeps `app["auth"]`; job kwargs carry `run_as_user_id` + the service reference, never tokens | M7 |
| S5 | Extend persistence boundary + tolerate old schemas (risk) | CONFIRM (partial) | tolerant reads and DDL-gate/fixture updates adopted; "add the field to both models" rejected by the user's repository-only decision | M6, §7 |
| S6 | Assign run-as in the definition transaction, not the scheduler sync (risk) | CONFIRM | capturing it in the re-sync endpoint would allow identity takeover; captured in `QueryManager` patch/upsert in one transaction | §2, M6, M7, §5, §8 |
| S7 | DB-enforced audit immutability (risk) | CONFIRM | trigger + REVOKE + per-store audit table, rolled out to public and tenant stores | §2 Data Models, M6 |
| S8 | Test the Graph contract; preserve SharePoint (testing) | CONFIRM | mocked builder tests per mode; SharePoint tests unchanged; deliberate registry update | §4 |
| S9 | Don't leak the process-wide platform.version patch (risk) | CONFIRM | scoped, ref-counted, restored in `finally`; close only owned credentials | M1, §7 |
| S10 | Keep introspection-visible field access (testing) | CONFIRM | literal `.get()` calls in constructors + SharePoint schema snapshot test | M1/M2/M5, §4 |

Summary: **10** confirmed (1 partial) · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-30 | Jesus Lara / Claude Code | Initial draft from accepted proposal FEAT-178 research + codex design research |
