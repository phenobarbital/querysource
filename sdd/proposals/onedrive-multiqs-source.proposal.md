---
id: FEAT-178
title: OneDriveSource for MultiQS — download a CSV/Excel file from OneDrive (Business + personal) via Microsoft Graph
slug: onedrive-multiqs-source
type: feature
mode: enrichment
status: accepted
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-09-30
  summary_oneline: MS OneDrive source for MultiQS, modelled on the existing SharePoint source
overall_confidence: medium
base_branch: dev
projects: [multiquery]
tags: [onedrive, sharepoint, microsoft-graph, multiquery-source, session-vault, oauth-identity]
research_state: sdd/state/FEAT-178/
created: 2026-09-30
updated: 2026-09-30
---

# FEAT-178 — OneDriveSource for MultiQS

> **Mode**: enrichment
> **Confidence**: medium
> **Source**: `inline`
> **Audit**: [`sdd/state/FEAT-178/`](../state/FEAT-178/)

---

## 0. Origin

> onedrive-multiqs-source -- Using the Sharepoint source as example for creating a MS OneDrive source for MultiQS

**Initial signals** (extracted, not interpreted):
- Verbs: "creating" → new capability (enrichment)
- Named entities: "Sharepoint source" (reference implementation), "MS OneDrive", "MultiQS"
- Components / labels: none
- Acceptance criteria provided: no

---

## 1. Synthesis Summary

MultiQS needs a `OneDriveSource` that downloads a single CSV/Excel file from
OneDrive and exposes it as a named DataFrame, just as
`querysource/queries/multi/sources/sharepoint.py::SharepointSource` does for
SharePoint libraries. Research shows that everything in `SharepointSource`
except site→drive resolution is drive-agnostic: app-only Graph auth, `/shares`
URL resolution, download via `@microsoft.graph.downloadUrl`, and parsing
(`_parse_file_content`). The recommendation is to extract that shared logic
into a Graph drive-item base that both sources extend. `OneDriveSource`
resolves either a user's drive (Business, app-only) or the signed-in user's
drive (personal, delegated OAuth), and is registered in `SOURCE_REGISTRY`.
For personal OneDrive, the user logs in through a UI and the captured
refresh token is saved into their session vault. querysource doesn't
implement either piece today, but the `navigator-auth` dependency already
provides both. Its "Identity Vault" link flow
(`/api/v1/user/identities/link/{provider}` plus the `/manage` page) runs the
Microsoft OAuth login. It stores the tokens encrypted and caches them in the
`navigator_session` session vault under `identity:{provider}`. It also
refreshes and persists rotated tokens (`IdentityCredentialHandler`).
`OneDriveSource`'s delegated mode therefore consumes that linked identity
instead of building a new token flow. querysource does not enable the
existing `azure` provider, and that provider is the corporate SSO login, so
a new link-only **`onedrive` identity provider** is added to navigator-auth
(cross-repo work). Scheduled runs have no session. They read the tokens from
`auth.user_identities` for the user who registered the job. That
`user_id` is saved in a dedicated `scheduler_run_as_user_id` column, and
every change to it is recorded in an append-only audit table.

---

## 2. Codebase Findings

### 2.1 Localization

| # | Path | Symbol | Lines | Role | Evidence |
|---|------|--------|-------|------|----------|
| 1 | `querysource/queries/multi/sources/sharepoint.py` | `SharepointSource` | 20-332 | reference implementation; source of the shared base | F002, F011 |
| 2 | `querysource/queries/multi/sources/sharepoint.py` | `SharepointSource._encode_share_url` | 127-140 | `/shares` id encoding — drive-agnostic, reusable | F002 |
| 3 | `querysource/queries/multi/sources/sharepoint.py` | `SharepointSource._parse_file_content` | 142-179 | CSV/Excel parsing — reusable as-is | F002 |
| 4 | `querysource/queries/multi/sources/sharepoint.py` | `SharepointSource.fetch` | 181-332 | auth + resolve + download; only 237-310 is SharePoint-specific | F002 |
| 5 | `querysource/queries/multi/sources/base.py` | `ThreadSource` | 14-163 | base contract (`fetch`, `resolve_credential`, `resolve_masks`) | F003 |
| 6 | `querysource/queries/multi/sources/__init__.py` | `SOURCE_REGISTRY` | 1-35 | registration + `__all__` | F004 |
| 7 | `querysource/queries/multi/__init__.py` | sources dispatch | 541-556 | generic registry lookup — no change needed | F006 |
| 8 | `querysource/queries/multi/_introspect.py` | `extract_source_schema` | 950-958 | docs introspection of `creds.get`/`source.get` | F007 |
| 9 | `pyproject.toml` | `sharepoint` extra | 151-155 | `msgraph-sdk`, `azure-identity`, `httpx` | F008 |
| 10 | `querysource/queries/multi/destinations/sharepoint.py` | `ToSharepoint._build_graph_client` | 108-148 | a second, independent copy of the Graph setup | F009 |
| 11 | `tests/test_source_sharepoint.py` | `TestSharepointSource` | 12-103 | offline test pattern to mirror | F010 |
| 12 | `navigator_session/vault/session_vault.py` *(dependency)* | `SessionVault` | 132-409 | the user's session vault (Redis + `auth.user_vault_secrets`) | F012 |
| 13 | `navigator_auth/vault/integration.py` *(dependency)* | `VAULT_SESSION_KEY`, `get_session_vault` | 24-151 | how a request/session reaches its vault (`session["_vault"]`) | F012 |
| 14 | `navigator_auth/handlers/user_identities.py` *(dependency)* | `IdentityLinkHandler`, `IdentitiesManageView`, `IdentityCredentialHandler` | 171-247 | login UI, token capture, auto-refresh + rotation, vault caching | F013 |
| 15 | `navigator_auth/identity/store.py` *(dependency)* | `IdentityStore`, `cached_credential`, `IDENTITY_VAULT_KEY` | 19, 112-400 | encrypted identity storage + session-vault cache helpers | F013 |
| 16 | `navigator_auth/backends/azure.py` *(dependency)* | `AzureAuth.authorize_identity` / `refresh_identity_tokens` | 292-380 | MSAL-based Microsoft link flow | F013 |
| 17 | `pyproject.toml` | `navigator-auth>=0.15.8` | 118 | pin predates the identity/vault features (0.28.2 installed) | F014 |
| 18 | `settings/settings.py` | `AUTHENTICATION_BACKENDS` | 146-148 | only `BasicAuth` enabled; the new provider must be added here | F015 |
| 19 | `navigator_auth/backends/azure.py` *(dependency)* | `AzureAuth` | 86, 102-108 | corporate SSO, tenant-bound; **not** reused | F015 |
| 20 | `navigator_auth/backends/external.py` *(dependency)* | `ExternalAuth` identity hooks | 80-157, 495-611 | generic link flow a new `onedrive` provider inherits | F016 |
| 21 | `querysource/scheduler/jobs.py` | `scheduled_multiqs_job` | 104-158 | sessionless MultiQS run; must receive `run_as_user_id` | F017 |
| 22 | `querysource/scheduler/scheduler.py` | `QSScheduler` add_job / `register_slug` | 330-360, 586 | job kwargs rebuilt from DB rows | F017 |
| 23 | `querysource/handlers/scheduler.py` | `SchedulerJobsView.post` | 208-260 | authenticated registration point that captures the registrant | F017 |
| 24 | `docs/PER_TENANT_QUERIES.md` | Provisional DDL gate / legacy migration | 35-75 | where the new column + audit DDL is documented (FEAT-151 precedent) | F018 |
| 25 | `querysource/models.py` | `QueryModel` (`dwh_scheduler`, `created_by`) | 93-106 | model gains `scheduler_run_as_user_id` | F017, F018 |
| 26 | `querysource/scheduler/scheduler.py` | `attributes.get("scheduler")` loader | 295-301 | startup loader must also read the run-as column | F018 |

### 2.2 Constraints Discovered

- **App-only auth has no `/me`.** `SharepointSource` authenticates with
  `ClientSecretCredential` (client credentials). Under app-only auth,
  OneDrive for Business has to be addressed as `/users/{upn|id}/drive` or
  `/drives/{id}`, or through a sharing URL (`/shares`). Personal (consumer)
  OneDrive cannot be reached with client credentials at all. It needs
  delegated auth: the `consumers` authority, `Files.Read offline_access`
  scopes, and a refresh token.
  *Implication*: two auth strategies in one source.
  *Evidence*: F002

- **Docs come from introspection.** `generated/SharepointSource.json` has no
  companion catalog YAML. It is derived from the
  `options.get('credentials')` / `source.get(...)` idiom by
  `extract_source_schema`, and regenerated by the `generate-multiquery-docs`
  pre-commit hook.
  *Implication*: keep the same idiom (including in the base class), then
  regenerate `generated/`.
  *Evidence*: F007

- **Inherit the SharePoint hardening.** Several fixes came from production:
  the `platform.version()` trailing-space patch for kiota's User-Agent, the
  600 s download timeout, stringified header names, `masks`, and
  `sheet_name`/`pd_args`.
  *Implication*: the extracted base must carry all of them. A copy that
  drifts would lose them.
  *Evidence*: F002, F011

- **Graph setup is already duplicated.** `ToSharepoint` re-implements
  client construction and drive resolution independently.
  *Implication*: the new base is a natural place to converge later.
  Refactoring `ToSharepoint` is out of scope here.
  *Evidence*: F009

- **Reuse the login flow; don't rebuild it.** `navigator-auth` already has
  the login UI, the refresh-token capture, encrypted storage in
  `auth.user_identities`, auto-refresh that persists rotated tokens, and the
  session-vault cache under `identity:{provider}`.
  *Implication*: querysource adds no OAuth handlers. Delegated mode reads
  the linked Microsoft identity from the vault (with `IdentityStore` as the
  fallback) and applies the same refresh rule as `IdentityCredentialHandler`.
  *Evidence*: F012, F013

- **Resolve the vault on the request loop.** `SessionVault` uses Redis and
  asyncpg pools that belong to the aiohttp event loop, while `ThreadSource.run`
  creates a fresh loop per thread.
  *Implication*: fetch (and refresh, if needed) the delegated access token on
  the request loop before the thread starts, then hand only the access token
  to `fetch()`. Using the vault from inside the thread is unsafe.
  *Evidence*: F003, F012

- **Vault access needs a session.** The vault is reached through the
  request's session (`session["_vault"]`). Scheduled and CLI runs don't have
  one, so they read `auth.user_identities` directly through
  `IdentityStore(app["authdb"], IdentityCipher())`, keyed by the stored
  `run_as_user_id`, and apply the same refresh and persist rules.
  *Evidence*: F012, F017

- **Don't reuse the corporate `azure` provider.** querysource enables only
  `BasicAuth`, so `/identities/link/azure` returns 404. `AzureAuth` is the
  corporate SSO, bound to `AZURE_ADFS_TENANT_ID`/`AZURE_ADFS_CLIENT_ID` with
  one global `AZURE_IDENTITY_SCOPES`. Reusing it would turn on corporate
  login here, widen that app's consented scopes, and force it to accept
  personal accounts.
  *Implication*: add a new `onedrive` provider with its own app
  registration and the `common` authority (work and personal accounts),
  with scopes `Files.Read User.Read offline_access`.
  *Evidence*: F015

- **A link-only provider must not become a login method.**
  `ExternalAuth.configure` registers login routes as well as the callback.
  The `onedrive` provider must override that, or navigator-auth needs a
  link-only flag.
  *Evidence*: F016

- **Jobs are rebuilt from the DB.** The scheduler re-registers jobs from
  `public.queries` rows, so an in-memory kwarg would not survive a restart.
  `run_as_user_id` has to be persisted with the slug's scheduler definition.
  It is taken from the authenticated registrant's session and never from
  the request body or the definition, so nobody can point a job at another
  user's OneDrive.
  *Evidence*: F017

- **Registration is enough for dispatch.** MultiQS looks up `sources:`
  entries by type name in `SOURCE_REGISTRY`.
  *Evidence*: F004, F006

### 2.3 Recent History (Relevant)

| Commit | When | Author | Message |
|--------|------|--------|---------|
| `c112ad97` | 2026-07-01 | Juan2coder | fix(SharepointSource): make source.masks optional in schema |
| `6d8681b6` | 2026-06-29 | Juan2coder | refactor(sources): use flowtask-style `masks` dict |
| `b640ea23` | 2026-06-25 | Juan2coder | refactor(SharepointSource): resolve full url via Graph /shares |
| `3337c7c9` | 2026-06-16 | Juan2coder | feat(SharepointSource): add sheet_name and pd_args |
| `70d030a5` | 2026-06-16 | Juan2coder | fix(SharepointSource): directory parsing, subsite support, large file timeout |
| `07e2cd37` | 2026-06-16 | Juan2coder | fix(SharepointSource): patch platform.version() trailing space on Linux |
| `2710e3fe` | 2026-05-19 | Jesus | feat(multiquery-new-sources): TASK-647 — SourceSharepoint Component |

No activity since 2026-07-01, so extracting a base now won't conflict with any in-flight work. (Evidence: F011)

---

## 3. Probable Scope

### What's New

- **Graph drive-item base** (e.g. `sources/_graph.py`, a `ThreadSource`
  subclass). It holds credential handling, Graph client construction and the
  platform.version patch, `/shares` resolution, the download, and
  `_parse_file_content`. Subclasses implement one hook that resolves the
  target `DriveItem`.
- **`OneDriveSource`** (`sources/onedrive.py`). Addressing modes:
  - `url`: a OneDrive for Business or personal sharing URL, resolved through `/shares`.
  - `user` + `directory` + `filename`: Business, app-only, via `/users/{user}/drive/root:/<path>:`.
  - delegated / personal mode (`auth: delegated`, optional
    `provider: azure`): Graph calls use the access token of the requesting
    user's linked Microsoft identity, against `/me/drive/root:/<path>:` or a
    sharing URL.
  - `masks`, `sheet_name` and `pd_args` behave as in SharePoint.
- **navigator-auth: `OneDriveAuth` provider** (cross-repo, lands first).
  It subclasses `ExternalAuth` with `_service_name = "onedrive"`, its own
  `ONEDRIVE_CLIENT_ID` and `ONEDRIVE_CLIENT_SECRET`, the `common` authority,
  and `identity_scopes()` = `Files.Read User.Read offline_access`. It is
  link-only: no login routes. It is then enabled in querysource's
  `AUTHENTICATION_BACKENDS`. The login UI becomes
  `/api/v1/user/identities/link/onedrive` and `/manage`.
- **Delegated token resolver**: a small helper that runs on the request
  loop, or on the scheduler's loop. It reads `identity:onedrive` from the
  session vault (`navigator_auth.identity.store.cached_credential`) when a
  session exists. Otherwise, or on a cache miss, it calls
  `IdentityStore.get_by_provider(user_id, "onedrive")`, where `user_id` is
  the session user or the job's `run_as_user_id`. Then it refreshes through the backend's
  `refresh_identity_tokens` when the token is about to expire, persists the
  rotated token (`update_tokens` + `cache_credential`), and passes the access
  token into the source. If there is no linked identity, it raises an
  actionable error pointing to `/api/v1/user/identities/link/onedrive`.
- **Login UI**: navigator-auth's `/api/v1/user/identities/manage` and
  `/link/onedrive` pages. No new UI in querysource, except linking to them
  from the error and the docs.
- **Scheduler `run_as_user_id`**: `SchedulerJobsView.post` records the
  authenticated registrant's `user_id` in the new column (below). It is
  reloaded at startup and passed `QSScheduler` → `scheduled_multiqs_job` →
  `MultiQS` → `OneDriveSource`.
- **Dedicated column and audit trail (U8)**, shipped as documented DDL in
  `docs/PER_TENANT_QUERIES.md`, following the FEAT-151 pattern:
  ```sql
  ALTER TABLE "{schema}".queries
      ADD COLUMN IF NOT EXISTS scheduler_run_as_user_id INTEGER;
  CREATE TABLE IF NOT EXISTS "{schema}".queries_run_as_audit (
      audit_id      BIGSERIAL PRIMARY KEY,
      query_slug    VARCHAR NOT NULL,
      old_user_id   INTEGER,
      new_user_id   INTEGER,
      operation     VARCHAR NOT NULL,        -- set | change | clear
      changed_by    INTEGER NOT NULL,        -- session user who made the change
      changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
      request_info  JSONB                    -- from LoggingService.request_info
  );
  ```
  - The code tolerates un-migrated stores: a missing column reads as
    `NULL`, and a job with no run-as user fails delegated sources with a
    clear message instead of crashing the loader.
  - Every write of the column appends an audit row in the **same
    transaction**. The table is append-only (the grant gives INSERT and
    SELECT only).
  - The same DDL goes into the "Provisional DDL gate" for new tenant stores.
  - The final table and column names are for the spec to confirm.
- **Credentials**: `ONEDRIVE_APP_ID`, `ONEDRIVE_APP_SECRET` and
  `ONEDRIVE_TENANT_ID`, each falling back to its `SHAREPOINT_*` counterpart
  when unset (app-only mode). Delegated mode needs no source-level secrets,
  only deployment config for the `onedrive` provider: an app registration
  that allows work and personal accounts, with a redirect to
  `/auth/onedrive/callback/`.
- **Tests**: `tests/test_source_onedrive.py` (offline: config parsing, the
  credential fallback, drive-path building per mode, parsing, ImportError),
  plus registry assertions.
- **Docs**: `generated/OneDriveSource.json`, regenerated.

### What Changes

- **`querysource/queries/multi/sources/sharepoint.py`::`SharepointSource`**
  is rebased on the Graph base and keeps only site→drive resolution. Its
  public config shape and behaviour must not change, and
  `tests/test_source_sharepoint.py` must stay green unmodified. *Evidence*: F002, F010
- **`querysource/queries/multi/sources/__init__.py`**: import `OneDriveSource`
  and add it to `__all__` and `SOURCE_REGISTRY`. *Evidence*: F004
- **`pyproject.toml`**: add a `onedrive` extra (the same packages as
  `sharepoint`), and raise the `navigator-auth` minimum to the release that
  ships `identity` and `vault`. *Evidence*: F008, F014
- **`settings/settings.py`**::`AUTHENTICATION_BACKENDS`: add the
  `onedrive` provider. *Evidence*: F015
- **`querysource/models.py`**::`QueryModel`: add
  `scheduler_run_as_user_id: int` (optional). **`docs/PER_TENANT_QUERIES.md`**:
  add the ALTER, audit table DDL and grants. *Evidence*: F018
- **`querysource/handlers/scheduler.py`**::`SchedulerJobsView.post`,
  **`querysource/scheduler/scheduler.py`** (add_job multi path, startup
  loader) and **`querysource/scheduler/jobs.py`**::`scheduled_multiqs_job`:
  capture, persist, reload and pass `run_as_user_id`. *Evidence*: F017
- **`querysource/queries/multi/__init__.py`** (sources dispatch, 541-556):
  possibly a pre-start hook, so a source can resolve request-bound
  credentials on the loop before `t.start()`. The spec decides between that
  and resolving in `__init__`. *Evidence*: F006, F003

### What's Untouched (Non-Goals)

- Folder/glob or multi-file fetch. One entry fetches one file, the same as SharePoint.
- A `ToOneDrive` destination, and refactoring `ToSharepoint` onto the new base.
- A new OAuth login UI or token endpoint in querysource (navigator-auth owns these).
- Changes to MultiQS dispatch beyond an optional pre-start credential hook.

### Patterns to Follow

- `ThreadSource` subclass with `async fetch()`. Optional imports go inside `fetch()`, and a missing package raises an `ImportError` that names the extra. *Evidence*: F002, F003
- `resolve_credential` for ALL_CAPS navconfig names, `resolve_masks` on directory and filename. *Evidence*: F003
- Offline unit tests in the style of `TestSharepointSource`. *Evidence*: F010

### Integration Risks

- **SharePoint regression from the extraction.** `SharepointSource` is in
  production use and has a history of subtle fixes. Mitigation: behaviour-
  preserving refactor, existing tests unchanged, and a new test that pins
  drive resolution for the `directory` parsing cases. *Evidence*: F002, F011
- **Introspection losing attributes.** If credential parsing moves into the
  base `__init__`, `extract_source_schema` may stop seeing the per-class
  `creds.get(...)` calls. Mitigation: check `generated/*.json` diffs for both
  sources. *Evidence*: F007
- **Refresh token rotation.** Rotation is already handled by
  navigator-auth's `update_tokens` and `cache_credential`. The risk is that
  querysource re-implements refresh and diverges from it. Mitigation: call the
  same store/backend functions, or the `/credential` handler's logic, rather
  than a separate copy. *Evidence*: F013
- **Cross-loop vault use.** Touching `SessionVault` from the worker thread's
  loop would break or deadlock on the loop-bound pools. Mitigation: resolve
  the token before the thread starts (see §2.2). *Evidence*: F003, F012
- **Cross-repo sequencing.** querysource cannot ship delegated mode until a
  navigator-auth release with `OneDriveAuth` exists and the pin is raised.
  Mitigation: split the work into (1) navigator-auth provider, (2)
  querysource app-only and `url` modes (no dependency on 1), then (3)
  querysource delegated mode and the scheduler changes. *Evidence*: F014, F016
- **Impersonation through `run_as_user_id`.** If the value could come from
  a request body or a definition, a user could read someone else's
  OneDrive. Mitigation: set it only from the authenticated session at
  registration, and audit-log it. *Evidence*: F017
- **Schema rollout across tenant stores.** The column and audit table must
  be added to `public.queries` and to every tenant schema, and the repo
  holds no migrations for these tables. Mitigation: follow FEAT-151 (deploy
  the code first, tolerate the missing column, document the ALTER, and add
  both to the DDL gate). *Evidence*: F018
- **Silent run-as changes.** Mitigation: the audit row is written in the
  same transaction as the column update, and the audit table is
  append-only. *Evidence*: F018
- **Revoked or expired link in scheduled runs.** A refresh failure makes
  the job fail. Mitigation: a clear "re-link at /link/onedrive" error sent
  through the existing `notification_manager`. *Evidence*: F013, F017
- **Deployment prerequisites.** The vault master keys, the
  `auth.user_identities` tables and the OneDrive app registration must all
  exist, and none of that is visible from this repo. *Evidence*: F014, F015

---

## 4. Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | No OneDrive support exists in the repo | F005 | high | empty grep over code, tests, docs, pyproject |
| C2 | A `SOURCE_REGISTRY` entry is sufficient for dispatch | F004, F006 | high | direct read of the dispatch loop |
| C3 | Only SharePoint's site→drive block is SharePoint-specific | F002 | high | direct read of `fetch` |
| C4 | The `/shares` url mode works unchanged for OneDrive for Business URLs | F002 | medium | Graph `/shares` is drive-agnostic; not exercised against a OneDrive URL here |
| C5 | The existing `sharepoint` extra covers Business-mode deps | F008 | high | direct read of pyproject |
| C6 | Docs JSON is generated by introspection, no catalog YAML needed | F007 | medium | observed for SharepointSource; base-class extraction untested |
| C7 | Personal OneDrive needs delegated auth | F002, F013 | high | platform fact; the delegated flow exists in navigator-auth |
| C8 | navigator-auth already provides login UI, token capture, auto-refresh with rotation, and the session-vault cache | F013 | high | direct read of handlers and store |
| C9 | The session vault is `navigator_session.SessionVault`, reached via `session["_vault"]` | F012 | high | direct read; querysource has no vault code |
| C10 | The delegated token must be resolved on the request loop, before the thread's own loop | F003, F012 | medium | inferred from loop-bound pools; not exercised |
| C11 | The navigator-auth pin must be raised to a release with identity/vault | F014 | medium | installed 0.28.2 has them; exact first release not verified |

| C12 | The Azure identity link is not enabled for querysource; reusing `AzureAuth` would couple OneDrive to corporate SSO | F015 | high | direct read of settings, backend lookup and AzureAuth |
| C13 | A new `ExternalAuth` subclass inherits the identity-link flow but must suppress its login routes | F016 | medium | generic flow read; link-only override not yet tried |
| C14 | `run_as_user_id` must be persisted with the slug's scheduler definition | F017 | medium | jobs are rebuilt from DB rows; exact storage slot open (U8) |

| C15 | Run-as is a dedicated column plus an append-only audit table, shipped as documented DDL (FEAT-151 pattern) | F018 | high | user decision; precedent read directly |

Distribution: **9** high, **6** medium, **0** low.

---

## 5. Open Questions

### Resolved (during proposal phase)

- [x] **Which OneDrive flavour?** *Resolved*: Business + personal.
  *Resolves claims*: C7
- [x] **How should the code relate to SharepointSource?** *Resolved*: extract a shared Graph drive-item base.
- [x] **Default credential names?** *Resolved*: `ONEDRIVE_*`, falling back to `SHAREPOINT_*`.
- [x] **Scope of one entry?** *Resolved*: a single file (parity with SharePoint).

- [x] **U5 — Personal OneDrive token lifecycle.** *Resolved*: "we need a UI
  interface for user's login and capture the refresh token that is saved
  into the user's session vault." Grounding: navigator-auth's Identity Vault
  link flow already does this (F013). The source consumes the linked
  identity from the session vault.
  *Resolves claims*: C7, C8

- [x] **U6 — Is the Azure identity link enabled?** *Resolved*: "lets
  research, if no, we need a new provider in navigator-auth." Research
  answer: **no**. Only `BasicAuth` is enabled, and `AzureAuth` is the
  tenant-bound corporate SSO (F015). Decision: a new link-only `onedrive`
  provider in navigator-auth.
  *Resolves claims*: C12, C13
- [x] **U7 — Runs without a session.** *Resolved*: "read the stored tokens
  from user identity table by user_id, registering in some place the
  user_id." Follow-up answer: the `user_id` is the **scheduler job's
  registrant**, taken from the session at registration and never from the
  body or the definition.
  *Resolves claims*: C14

- [x] **U8 — Where is `run_as_user_id` persisted?** *Resolved*: "dedicated
  column with an audit trail if possible." It is possible: a
  `scheduler_run_as_user_id` column on `{schema}.queries` plus an
  append-only `{schema}.queries_run_as_audit` table, written in the same
  transaction and shipped as documented DDL following the FEAT-151 pattern
  (F018).
  *Resolves claims*: C14, C15

### Unresolved (defer to spec)

None blocking. Spec-level details remain: the final table and column
names; whether a slug edit through the management API (as opposed to an
explicit scheduler registration) may change the run-as user (recommended:
no); and where the delegated token is resolved (C10).

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-178`**. *Rationale*: localization is high-confidence
(C1–C3, C5), the pattern is a direct mirror of SharePoint, and the design
forks have been resolved: the login/vault flow (U5), the new provider
(U6), sessionless runs (U7) and run-as persistence (U8). The spec needs to
settle the base-class contract and where the delegated token is resolved
(C10). It should also
split the work into three phases: the navigator-auth provider (cross-repo),
the querysource app-only/url modes, and the querysource delegated mode plus
scheduler changes.

### Alternatives

- **`/sdd-brainstorm FEAT-178`**: if the personal-OneDrive token lifecycle (U5) needs an options analysis first.
- **Manual review**: none needed. Research was not truncated.

---

## 7. Research Audit

| Artifact | Path |
|----------|------|
| State checkpoints | `sdd/state/FEAT-178/state.json` |
| Source (raw) | `sdd/state/FEAT-178/source.md` |
| Research plan | `sdd/state/FEAT-178/research_plan.json` |
| Findings (digests) | `sdd/state/FEAT-178/findings/F001-*.md` … `F018-*.md` (F012–F014 after the U5 answer; F015–F017 after U6/U7; F018 after U8) |
| Synthesis (JSON) | `sdd/state/FEAT-178/synthesis.json` |

**Budget consumed** (default profile):
- Files read: 29 / 40
- Grep calls: 24 / 25
- Git calls: 1 / 10
- Truncated: **no**

**Mode determination**: `auto`, resolved to `enrichment` (the source asks for a new capability).

---

## 8. Provenance

| Field | Value |
|-------|-------|
| Generated by | `/sdd-proposal v1.0` |
| Synthesis prompt | `sdd/templates/synthesis.prompt.md v1.0` |
| Plan prompt | `sdd/templates/research_plan.prompt.md v1.0` |
| Schema versions | state=1.0, synthesis=1.0, research_plan=1.0 |
| Operator | Claude Code (Opus 5.5) for Jesus Lara |
