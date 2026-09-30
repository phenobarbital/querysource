---
id: FEAT-178
title: OneDriveSource for MultiQS — download a CSV/Excel file from OneDrive (Business + personal) via Microsoft Graph
slug: onedrive-multiqs-source
type: feature
mode: enrichment
status: discussion
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
instead of building a new token flow.

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
  request's session (`session["_vault"]`). Scheduled or CLI runs don't have
  one (see U7).
  *Evidence*: F012

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
- **Delegated token resolver**: a small helper that runs on the request
  loop. It reads `identity:{provider}` from the session vault
  (`navigator_auth.identity.store.cached_credential`), falls back to
  `IdentityStore.get_by_provider`, refreshes through the backend's
  `refresh_identity_tokens` when the token is about to expire, persists the
  rotated token (`update_tokens` + `cache_credential`), and passes the access
  token into the source. If there is no linked identity, it raises an
  actionable error pointing to `/api/v1/user/identities/link/azure`.
- **Login UI**: reuse navigator-auth's `/api/v1/user/identities/manage` and
  `/link/{provider}` pages. No new UI in querysource, except linking to them
  from the error and the docs.
- **Credentials**: `ONEDRIVE_APP_ID`, `ONEDRIVE_APP_SECRET` and
  `ONEDRIVE_TENANT_ID`, each falling back to its `SHAREPOINT_*` counterpart
  when unset (app-only mode). Delegated mode needs no source-level secrets,
  only deployment config: the Azure backend's app registration, with
  `Files.Read` in `AZURE_IDENTITY_SCOPES` and personal accounts allowed.
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
- **Deployment prerequisites.** The Azure identity backend, the vault master
  keys and the `auth.user_identities` tables must all be enabled, but none of
  that is visible from this repo (U6). *Evidence*: F014

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

Distribution: **7** high, **4** medium, **0** low.

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

### Unresolved (defer to spec)

- [ ] **U6 — Deployment enablement.** Is the navigator-auth Azure identity
  link flow (`/api/v1/user/identities/link/azure`) enabled where querysource
  runs? Can the Azure app registration and `AZURE_IDENTITY_SCOPES` include
  `Files.Read` and personal Microsoft accounts (`common` authority)? Or does
  OneDrive need its own provider name (e.g. `onedrive`)? *Owner*: tbd
  *Blocks claims*: C8
  *Plausible answers*: a) reuse the `azure` provider and add scopes ·
  b) register a dedicated `onedrive` backend or provider in navigator-auth
- [ ] **U7 — Runs without a session.** When a MultiQS definition with
  delegated OneDrive runs from the scheduler or CLI, there's no session and
  so no vault. *Owner*: tbd
  *Plausible answers*: a) fall back to `IdentityStore.get_by_provider(user_id)`
  (the DB copy of the same tokens) when the run carries an owner user_id ·
  b) delegated mode is HTTP-only, and non-session runs fail with a clear
  error

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-178`**. *Rationale*: localization is high-confidence
(C1–C3, C5), the pattern is a direct mirror of SharePoint, and the design
forks have been resolved, including the login/vault flow (U5, F013). The
spec needs to settle U6/U7, the base-class contract, and where the
delegated token is resolved (C10).

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
| Findings (digests) | `sdd/state/FEAT-178/findings/F001-*.md` … `F014-*.md` (F012–F014 added after the U5 answer) |
| Synthesis (JSON) | `sdd/state/FEAT-178/synthesis.json` |

**Budget consumed** (default profile):
- Files read: 17 / 40
- Grep calls: 15 / 25
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
