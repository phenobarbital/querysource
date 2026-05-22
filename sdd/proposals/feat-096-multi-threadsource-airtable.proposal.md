---
id: FEAT-096
title: Multi-Query ThreadSource — Airtable
type: feature
mode: investigation
status: review
source:
  kind: inline
  jira_key: null
  summary: New Airtable Source for MultiQuery extending ThreadSource, backed by AirtableInterface with OAuth2 + PAT auth
base_branch: dev
confidence: medium
research_state: sdd/state/FEAT-096/
---

# FEAT-096 — Multi-Query ThreadSource: Airtable

## §0 Origin

Inline feature request: add a new `AirtableSource` for MultiQuery that extends `ThreadSource` and exposes records from an Airtable table as a pandas DataFrame.

**Scope clarification (Phase 0 Q&A):** The `AirtableSource` (MultiQuery Source class) is **read-only** for this feature — `list_records(table)` → DataFrame. The supporting `AirtableInterface` is designed to encapsulate **all** Airtable-related code (read + write/create-table stubs) so a future feature can add write capability without re-touching the Source layer.

> ⚠️ **Security note:** the original prompt contained a real-looking Airtable Personal Access Token (`pat36EoFVW…`). It has been **redacted everywhere** in this proposal and the state files. The submitter must rotate the token in Airtable.

## §1 Synthesis Summary

### Problem

QuerySource has zero integration with Airtable today (F007). Customers' Airtable bases are a common data source for MultiQuery pipelines, but they currently have to manually export to CSV/S3 to feed querysource. Two auth modes are needed:

1. **End-user OAuth2** — interactive users who want to "connect their Airtable account" to QuerySource.
2. **Server-wide Personal Access Token** — service-account use, batch pipelines, and CI/CD jobs that run without a user session.

The FEAT-093 workstream (just landed — F008) introduced the `ThreadSource` base class and a clean Source registry. An Airtable source slots naturally into that framework.

### Approach

1. New `querysource/queries/multi/sources/airtable.py` containing `AirtableSource(ThreadSource)` — thin: parses the source URL/IDs, selects auth, delegates to interface.
2. New `querysource/interfaces/airtable.py` containing `AirtableInterface` — owns all Airtable API logic (auth header, paginated record list, future write methods, URL parser, refresh-token retry).
3. Register the new class in `SOURCE_REGISTRY` and `__all__` in `querysource/queries/multi/sources/__init__.py`.
4. Add two new aiohttp routes inside `QuerySource.setup()` in `querysource/services.py`:
   - `GET /api/v1/qs/integrations/airtable/connect` — serves a minimal HTML consent page (built into QuerySource for self-containment) that links to Airtable's OAuth2 authorize URL.
   - `GET /api/v1/qs/integrations/airtable/callback` — receives the redirect, exchanges the code for tokens, writes `{access_token, refresh_token, expires_at, scope}` into `session['airtable']`.
5. Gate both new routes behind a new env flag `QS_AIRTABLE_OAUTH_ENABLED` (default off) — keeps existing deployments unaffected.

### Confidence

**Overall: medium.** The Source pattern hooks are high-confidence — ThreadSource, registry, setup() are all directly observed in code (F001, F004, F005). The medium-confidence pieces are: (a) writing OAuth tokens to `navigator_session` has no precedent in querysource (F006) and (b) raw `aiohttp` vs `pyairtable` SDK is a deliberate implementation choice the spec must lock down. The three architectural unknowns (PAT scope, consent-page ownership, reauth UX) were resolved by user Q&A in §5 below.

## §2 Codebase Findings

### 2.1 Localization (where the changes go)

| Path | Symbol | Kind | Evidence |
|---|---|---|---|
| `querysource/queries/multi/sources/airtable.py` | `AirtableSource` | **NEW file** | F001, F002, F003 |
| `querysource/queries/multi/sources/__init__.py` | `SOURCE_REGISTRY` + `__all__` | modify (3 lines) | F004 |
| `querysource/interfaces/airtable.py` | `AirtableInterface`, `AirtableReauthRequired` | **NEW file** | F003, F007 |
| `querysource/handlers/integrations/__init__.py` | new package marker | **NEW file** | F005, F009 |
| `querysource/handlers/integrations/airtable.py` | `AirtableConnectView`, `AirtableCallbackView` | **NEW file** | F005, F009 |
| `querysource/services.py` | `QuerySource.setup` lines 97-310 | modify (register 2 routes + import) | F005 |
| `querysource/conf.py` | `QS_AIRTABLE_OAUTH_ENABLED`, `AIRTABLE_CLIENT_ID`, `AIRTABLE_CLIENT_SECRET`, `AIRTABLE_BASE_ID`, `AIRTABLE_ACCESS_TOKEN`, `AIRTABLE_REDIRECT_URI` | modify (new env constants) | F005 |
| `pyproject.toml` | `[project.optional-dependencies] airtable` | modify (optional) | F003 |

### 2.2 Constraints (must follow these patterns)

| ID | Constraint | Confidence | Evidence |
|---|---|---|---|
| C1 | All multi-query sources must inherit `ThreadSource` and implement `async def fetch(self) -> pd.DataFrame` | high | F001, F002, F003 |
| C2 | Constructor signature is fixed: `(name: str, options: dict, request: web.Request, queue: asyncio.Queue)` | high | F001 |
| C3 | Env-var-looking credential values must flow through `self.resolve_credential(key, value)` for navconfig resolution | high | F001, F002, F003 |
| C4 | The new Source must be registered in `SOURCE_REGISTRY` AND exported in `__all__` | high | F004 |
| C5 | Routes are registered inside `QuerySource.setup()` in `querysource/services.py` — there is **no** method called `configure()` (user prompt was wrong about the method name) | high | F005 |
| C6 | Sessions are read via `navigator_session.get_session(request, new=False)`, memoized on `request['user_session']`. There is no 'vault' abstraction — session storage IS the vault. | high | F006 |
| C7 | Heavy/optional SDK deps must be imported lazily inside `fetch()` and gated via `pyproject.toml` extras | high | F003 |
| C8 | `fetch()` returns `df.infer_objects()`; an empty Airtable table must yield a valid empty DataFrame, not `None` | medium | F002, F003 |

### 2.3 Recent History

The `multiquery-new-sources` workstream landed FEAT-093 commits between TASK-644 and TASK-652 (F008). It introduced `ThreadSource` and refactored existing sources to inherit it. No drift, no legacy patterns to worry about. The most recent commit on the area (`1d655c9 fix(multiquery-new-sources): address code review findings`) shows the code is freshly reviewed and stable.

## §3 Hypothesis & Scope

### Primary hypothesis

> Implement `AirtableSource(ThreadSource)` as a thin wrapper around a new `AirtableInterface`. The Interface owns all Airtable logic (URL parsing, auth selection, paginated record fetch, refresh-token handling). The Source:
> 1. Parses `options['source']['url']` (full Airtable URL) OR `options['source']['base_id'] + options['source']['table']` (explicit IDs) into `(base_id, table_id, view_id)`.
> 2. Selects auth: first checks `await navigator_session.get_session(self._request, new=False)` for `session['airtable']`; falls back to `AIRTABLE_ACCESS_TOKEN` PAT via `self.resolve_credential()`; raises `RuntimeError("No Airtable credentials available")` if neither.
> 3. Delegates to `interface.list_records(base_id, table_id, view_id)`.
> 4. Converts records to DataFrame via `pd.DataFrame.from_records([r["fields"] for r in records]).infer_objects()`.

**Confidence: medium.** All Source-side hooks are high-confidence (F001-F005); the session-vault interaction is the only net-new design.

### Risks

| Risk | Mitigation |
|---|---|
| No precedent for writing OAuth tokens into navigator_session in querysource | Spec must define a stable schema for `session['airtable']` and treat it as the contract between callback and Source |
| Refresh-token-on-401 retry has no template to copy | Build inside `AirtableInterface` (single retry, persist refreshed token back into session) |
| `navigator_session` may be uninstalled in some deployments (handlers/abstract.py:248 defensively logs this) | Source falls back to PAT-only; OAuth routes raise 503 if session backend is unavailable |
| Consent-page HTML serving puts a tiny UI concern inside an API-focused package | Keep the page minimal (≤30 lines of inline HTML); document as a fallback for self-hosted use |
| Airtable API rate limits (5 req/sec per base) — not exercised today | Use `aiohttp.ClientTimeout` + explicit 429 handling (mirror SmartSheetSource F002 line 80-86) |
| Real PAT leaked in original prompt | Caller informed; rotate token immediately. State files contain only the env-var name. |

### Scope

**In scope**
- `AirtableSource(ThreadSource)` — read-only, returns DataFrame
- `AirtableInterface` encapsulating: URL parsing, auth header selection, `list_records()`, refresh-on-401 retry, write-method stubs (for future use; not called by Source)
- Custom `AirtableReauthRequired` exception
- Dual auth: OAuth2 session token (preferred) → `AIRTABLE_ACCESS_TOKEN` PAT (server-wide fallback) → raise
- New aiohttp routes `/api/v1/qs/integrations/airtable/{connect,callback}` registered in `QuerySource.setup()`
- Minimal HTML consent page served by QuerySource at `/connect` (≈20 lines of inline template — keeps the feature self-contained per Phase 5 U2 answer)
- Pagination over Airtable's `offset` parameter
- Field-type normalization: flatten linked records, attachments, and lookups to JSON-serializable scalars (best-effort)
- New env vars: `AIRTABLE_CLIENT_ID`, `AIRTABLE_CLIENT_SECRET`, `AIRTABLE_BASE_ID`, `AIRTABLE_ACCESS_TOKEN`, `AIRTABLE_REDIRECT_URI`
- Feature flag `QS_AIRTABLE_OAUTH_ENABLED` (default `False`) — routes registered only when enabled
- Optional `pyproject.toml` `[airtable]` extras entry (only if SDK chosen over raw aiohttp — defer to spec)

**Out of scope**
- Writing records / creating tables via `AirtableSource` (Interface exposes stubs only — actual usage deferred to a future feature, per Phase 0 Q&A)
- Per-user PAT (FEAT-091-style env-var-by-username) — global PAT only (Phase 5 U1 answer)
- Persistent token storage outside `navigator_session` (no new database table)
- Multi-workspace selection UI
- Migrating FEAT-091 `CredentialResolver` to handle OAuth tokens

## §4 Confidence Map

| Claim | Confidence | Notes |
|---|---|---|
| ThreadSource pattern + constructor signature are correct and stable | high | Directly read in F001 |
| `SOURCE_REGISTRY` registration is the right hook | high | F004 |
| `QuerySource.setup()` (not `configure()`) is where to add routes | high | F005 — caller's terminology in the prompt was wrong |
| `navigator_session` is the right session backend | high | F006 |
| Per-user OAuth token writeback to session under key `airtable` | medium | Net-new design, but trivial |
| Raw `aiohttp.ClientSession` is sufficient — no need for `pyairtable` | medium | Spec should make this binary decision |
| Refresh-token-on-401 retry inside Interface | low | No precedent in codebase — design from scratch |
| Inline minimal HTML consent page is acceptable in an API package | low | Stylistic concern; user opted in via Phase 5 |

## §5 Open Questions

### Resolved (Phase 5 Q&A)

- [x] **U1 — PAT fallback scope.** Global server-wide PAT only (`AIRTABLE_ACCESS_TOKEN`). Matches FEAT-093 SmartSheet/S3 pattern. No per-user PAT env-var convention.
- [x] **U2 — Consent page ownership.** QuerySource serves a minimal HTML page at `/api/v1/qs/integrations/airtable/connect`. The feature is self-contained; no frontend dependency.
- [x] **U3 — Token-refresh failure UX.** Source raises a typed `AirtableReauthRequired` exception so the frontend (or CLI) can prompt the user to reconnect. No silent PAT fallback in this case — explicit reauth flow.

### Remaining (for the spec phase to decide)

- [ ] **Q-impl-1:** Use raw `aiohttp.ClientSession` (no new dep) or `pyairtable` SDK (cleaner pagination/field-typing, adds an optional extra)? Recommended default: raw aiohttp, matching SmartSheetSource (F002).
- [ ] **Q-impl-2:** Exact normalization rules for Airtable column types (linked-record IDs vs. expanded objects, attachments as URLs vs. binary fetch, formula vs. plain values). The spec must enumerate these.
- [ ] **Q-impl-3:** Where exactly does the `AirtableInterface` live — `querysource/interfaces/airtable.py` (alongside `http.py`, `credentials.py`) or a dedicated `querysource/integrations/airtable/` package? The proposal assumes `interfaces/` for consistency.

## §6 Recommended Next Step

**→ `/sdd-spec FEAT-096`**

**Rationale:** all three architectural unknowns were resolved by the user in Phase 5 Q&A. Localization is high-confidence on the source/registry/setup hooks. The remaining implementation questions (SDK vs. aiohttp, field normalization rules, interface file location) are small enough to be decided directly inside the spec.

**Alternatives:**

- `/sdd-brainstorm FEAT-096` — only if you want to explore an alternative architecture for QuerySource OAuth integrations more broadly (e.g. a uniform "integration credentials" subsystem that would also cover Google, Salesforce, etc.). The current single-feature path is fine if Airtable is the immediate priority.
- Direct `/sdd-task FEAT-096` — discouraged. Even with resolved unknowns, this feature spans ≥7 files and introduces a brand-new OAuth callback in the project. A spec gives task decomposition a stable contract.

## §7 Research Audit

- **State directory:** `sdd/state/FEAT-096/`
- **Findings (9):** F001 (ThreadSource), F002 (SmartSheet token pattern), F003 (SharePoint OAuth pattern), F004 (registry), F005 (`QuerySource.setup`), F006 (session + no vault), F007 (no prior Airtable), F008 (recent FEAT-093 history), F009 (no existing OAuth callback)
- **Budget consumed (default profile):** 11 files read / 12 grep / 1 git_log / depth 1 / ~270s (limits: 40 / 25 / 10 / 2 / 300)
- **Truncated:** false
- **Synthesis lint:** all `localization.path` entries are either net-new files (explicit `NEW file`) or correspond to paths cited in findings; every `evidence` finding ID exists.
