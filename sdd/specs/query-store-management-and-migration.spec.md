---
type: feature
base_branch: dev
projects: [querysource, handlers, scheduler, auth]
tags: [query-stores, legacy-compatibility, explicit-migration, frontend-contract]
---

# Feature Specification: Query Store Management and Explicit Migration

**Feature ID**: FEAT-176
**Date**: 2026-10-15
**Author**: Codex, from decisions agreed with Juanfran
**Status**: draft
**Target version**: Next compatible release; no package version bump prescribed
**Companion**: `navigator-frontend-next/sdd/specs/querysource-store-selection-and-migration.spec.md` (frontend **FEAT-252**, independently allocated).

## 1. Motivation & Business Requirements

### Problem Statement
Saving an existing pipeline sends `program_slug` in PATCH and receives 400
`program_slug cannot be modified`. The new repository rejects the field for
both tenant and legacy stores, although FEAT-147 explicitly preserves the
legacy stored-program contract. Create/upsert, metadata and INSERT export also
use the tenant persistence model irrespective of contract. This spec restores
that boundary and gives the frontend an explicit way to select a definition
store and move a single saved definition out of the legacy pool.

The agreed invariant is: **save keeps the current store; create chooses a
store; move is a separate explicit operation. Existing definitions never move
automatically.** A program label and an output table schema are not selectors
for the definition store.

### Goals
- Preserve configured legacy storage and its persisted `program_slug`.
- Keep tenant tables free of persisted `program_slug`; derive it at runtime.
- Publish a credential-free catalog of registered stores for management UI.
- Support preview and transactional movement of one unscheduled definition,
  including legacy-to-tenant, without overwrite or executing the definition.
- Make conflict, dependency, concurrency and schedule limitations reviewable.

### Non-Goals
- Automatic migration, bulk migration, schema/table creation or DDL changes.
- Cross-database moves; renaming; automatic rewriting of other definitions or
  external consumers; redirects or fallback to legacy when tenant lookup fails.
- Automatic transfer of active schedules, cancellation of in-flight work,
  distributed scheduler coordination, or execution of SQL/pipeline outputs.
- Inferring a store from `program_slug`, `program_id`, hostname or SQL contents.

## 2. Architectural Design

### Overview
Use `QueryStore` and `QueryIdentity` as the canonical internal identity. A
selector is JSON null for the configured default or an exact registered schema
string. Literal `public` remains literal public and need not equal the default.
Resolve selectors once through the existing registry and never mutate ORM Meta.

Contract-specific writes select QueryModel validation for legacy and
TenantQueryDefinition for tenant. PATCH accepts stored `program_slug` on legacy,
including an unchanged value, without moving the row; tenant writes containing
that property return a structured 400 `invalid_tenant`. Omission on legacy
PATCH preserves its stored value. A legacy upsert that omits `program_slug`
preserves the existing value on conflict and applies the established default
only on insert. All PATCH fields must be checked against the selected model's
mutable-field allowlist; reject unknown fields before constructing SQL.
Legacy `get()` revision hashing must also include its raw stored program_slug:
accepting a program edit while retaining the same result-cache revision can
serve data produced under the previous program. Tenant runtime-only program
must not become a persisted field. This is a repository adapter correction,
not a change to the existing result-cache key format.

Legacy listing, schema metadata/export/returned persisted writes retain the stored
program; tenant metadata/export/write payloads exclude it. Runtime GET keeps
its existing detached QueryModel response. Do not serialize runtime fields
back into tenant persistence. Preserve explicit null PATCH semantics, slug
agreement, run-as rejection and existing audited schedule-change behavior.

### Component Diagram
```text
Management UI -> query-stores catalog -> TenantRegistry
              -> QueryManager -> contract-aware DefinitionRepository
              -> QueryMoveHandler -> QueryMoveService -> one DB transaction
                                      |-> preview/dependency blockers
                                      |-> source + target identity validation
                                      `-> post-commit owner-specific scheduler cleanup
```

### Integration Points
| Existing component | Integration | Contract |
|---|---|---|
| TenantRegistry | Catalog and selector resolution | Immutable snapshot; deduplicate physical stores |
| DefinitionRepository | CRUD compatibility and movement | Explicit qualified SQL; no ORM persistence on tenant |
| QueryManager | Existing management routes | Keep route methods, default owner and response conventions |
| QSScheduler | Post-commit cleanup | Sync exact source and target owner; never execute during move |
| Cache identity | Existing namespaces | New owner gets distinct keys; do not read old unqualified keys |

### Data Models and HTTP Contract (v1; shared verbatim in companion)
```text
StoreSelector = string | null
StoreDescriptor = { tenant: StoreSelector, schema: string, table: string,
                    contract: "legacy" | "tenant", is_default: boolean }
OwnedSlugRef = { tenant: StoreSelector, query_slug: string }
```
Only catalog-issued selectors are offered. Include the configured default even
if its table has a custom name; deduplicate aliases by physical identity and
prefer tenant=null for that entry. Expose schema/table/contract, never database
host, credentials, DSN, internal diagnostics or run-as identities. Listing a
store does not grant write permission. Use the same authenticated management
access boundary as existing CRUD; require a numeric authenticated actor for
move preview and commit, enforce database permissions and existing applicable
PBAC rules, and never treat client acknowledgements as authorization. Numeric
actor presence alone is not a management authorization check. The exact role
allowed to move is unresolved Q2 below; do not enable the new endpoints until
that boundary is decided, wired through the existing deployment authentication
boundary and covered by unauthenticated/unauthorized tests. Do not invent a
`slug:move` PBAC action or equate `slug:execute` with migration permission.

| Method and route | Request | Response |
|---|---|---|
| GET `/api/v1/management/query-stores` | No body | 200 `{version:1,data:StoreDescriptor[]}` |
| Existing management CRUD, `:meta`, `:insert` | GET/DELETE: `?tenant=schema`; writes: either query selector or top-level `tenant`, not both | Existing shapes/statuses; validation follows store contract |
| POST `/api/v1/management/queries/{slug}/move/preview` | `{tenant:null,destination_tenant:"wm_assembly"}` | 200 MovePlan |
| POST `/api/v1/management/queries/{slug}/move` | `{tenant:null,destination_tenant:"wm_assembly",plan_revision:"…",acknowledge_external_consumers:true,acknowledge_runtime_program_change:true}` | 200 MoveResult |

Routing `tenant` is stripped before model validation. Migration requires the
`tenant` key explicitly, including null. `destination_tenant` must be present;
null means configured default, not literal public. Reject duplicate/conflicting
selectors, empty strings, extra move options and identical canonical stores.
Migration does not support client-supplied definition payloads or overwrite.

```text
MovePlan = {
  version:1, source:StoreDescriptor, destination:StoreDescriptor, query_slug:string,
  plan_revision:string, can_move:boolean,
  blockers:[{code:string,message:string,path?:string}],
  warnings:[{code:string,message:string}],
  inherited_references:[{path:string,slug:string}],
  runtime_program:{before:string|null,after:string|null},
  consumer_review_required:true, schedule_policy:"unscheduled-only"
}
MoveResult = {
  version:1, moved:true, source:StoreDescriptor, destination:StoreDescriptor,
  query_slug:string, scheduler_sync:"ok"|"failed"|"not_configured"
}
Migration errors = {error_code:string,message:string,details?:object}
```
Preview is read-only. For a missing source return 404. Other actionable
conditions produce a plan with `can_move:false`; commit revalidates and returns
409 with the corresponding blocker code. A stale plan returns 409
`move_plan_stale`; an occupied destination returns 409 `move_target_exists`.
Other codes: `move_same_store` (400), `move_schedule_requires_quiescence` (409),
`move_inherited_references` (409), `move_dependency_analysis_unsupported` (409),
`move_acknowledgement_required` (400), `move_validation_failed` (400),
`move_stale_run_as` (409), `move_outcome_unknown` (503).
Keep existing tenant ownership status mappings for unknown/unavailable stores,
missing queries and database permission errors. No fallback writes on failure.

### Configured default and scheduler identity
Source review found a prerequisite gap: the current TenantRegistry scans only
tables named `queries`, classifies legacy by schema==public and assigns only
public as default. M2 must repair discovery/resolution to honor
`QueryModel.Meta.schema` and `.name` (QS_QUERIES_SCHEMA/QS_QUERIES_TABLE), not
merely describe this as existing behavior. Discover the exact configured table
in addition to tenant `queries` tables. Both the configured table and literal
public.queries retain the legacy contract; all other eligible `queries` tables
use tenant contract. Register/deduplicate the configured physical store even
with an empty tenant allowlist. A missing configured table must produce an
unavailable-store response; never select another tenant by discovery order or
fall back to public. A schema selector addresses that schema's `queries` table;
null addresses the configured table, including a custom table name. If the configured default is unavailable, catalog returns 503 rather than
claiming readiness or substituting public. A descriptor never proves write grants.

Map an identity back to the scheduler selector by physical equality with the
configured default: use null only for that exact store; otherwise use its exact
registered schema, including literal public. Do not use `contract == legacy`
to choose null. Correct QueryManager._sync_definition_jobs as well as the new
move path. Regression: a custom configured default and public.queries may hold
the same slug and must not remove or register each other's jobs.

### Migration semantics
1. Preview resolves both owners, reads raw stored source fields and checks
   target collision. Its digest covers source canonical identity, all raw
   persisted fields (including legacy program and repository-only run-as),
   destination identity and the resulting blocker/warning inventory. Do not
   rely solely on updated_at or on the existing runtime revision, which omits
   legacy program/run-as. `plan_revision` is a concurrency token, not a grant.
2. Block if any automatic execution is configured: `attributes.scheduler`,
   nonzero `cache_refresh`, scheduler declarations in `cache_options`, or DWH
   scheduling. Use a conservative union of existing scheduler eligibility
   rules. The plan names every trigger. Users must explicitly disable these
   through normal audited CRUD, sync removal in every scheduler process, wait
   for existing work to finish, and request a new preview. Pausing an in-memory
   job alone is insufficient. Deployment must use an operational maintenance
   window; no claim of global execution quiescence is made by this API.
3. Inspect saved multi `queries` entries and known Query source components.
   References without `tenant` would inherit the destination: block until the
   operator explicitly pins each reference to its intended owner in the saved
   definition. From default legacy use `tenant:null`; from nondefault source
   use its schema selector. Never use a regular-expression replacement of SQL.
   Unknown/dynamic dependency forms that cannot be analyzed safely block with
   an actionable path; plain SQL providers have no structured-child inventory.
4. Always warn that external consumers and inbound references are not fully
   discoverable; require acknowledgement and show the new execution route.
   Always report before/after runtime program. Program changes require the
   second acknowledgement only when before != after (otherwise it may be omitted
   or false): SQL can depend on program-specific variables or
   unqualified objects even when its text remains identical.
5. Commit uses ONE acquired connection and ONE transaction for source read
   `FOR UPDATE`, fingerprint/blocker revalidation, target validation and plain
   INSERT, persisted target verification and source DELETE. Use a stable
   ordered advisory lock per canonical owner/slug for competing move operations;
   target uniqueness remains mandatory to handle concurrent ordinary creates.
   No ON CONFLICT UPDATE. A unique violation becomes 409, with full rollback.
   Reverse moves follow the same lock order. Failures known to occur before
   commit roll back all row changes; no partial copy/delete is committed.
   Source DELETE must affect exactly one locked row; otherwise roll back.
   Avoid composing public repo.create/delete calls with separate connections.
   A lost connection or cancellation while awaiting COMMIT has unknown outcome:
   PostgreSQL may have committed before the response was lost. Return 503
   `move_outcome_unknown` when a response can be sent, with no moved=false or
   rollback claim; a dropped HTTP response is equally indeterminate. The DB
   transaction remains all-or-nothing, but the client must reconcile both
   owners and must not blindly repeat the move.
6. Keep slug name, query_raw, provider and all supported business fields; retain
   creation provenance and stamp migration update actor/time on the target.
   Preserve explicit nulls and server defaults deliberately; validate against
   destination columns and model before mutation. Legacy-to-tenant drops only
   stored program ownership; tenant-to-legacy initializes program_slug from
   source runtime program. No numeric program_id is inferred or rewritten.
   Never copy run-as identity. If an unscheduled source still has a nonnull
   stale run-as, preview emits `move_stale_run_as` (409 on commit) naming the
   required audited repair; it is not classified as a live schedule. Existing
   _apply_run_as returns early when both old/new schedules are absent, so a
   no-op PATCH cannot clear it. The operator runbook must specify an attributed
   repair transaction that updates the source to NULL and appends one `clear`
   event in its existing source audit table; no fake temporary schedule or
   unsafely unaudited SQL is permitted. This recovery is an operator action,
   not an automatic side effect of preview/move. A missing audit table must be
   repaired through the already documented run-as migration first. Preserve
   all existing append-only history, including after the source row is deleted.
7. After commit sync exact source and target aliases. Failure cannot claim
   rollback: return moved=true, scheduler_sync=failed and
   `X-QS-Scheduler-Sync: failed`. Retry must reload source/destination state;
   missing source on a repeated move is 404, never a second mutation. Commit
   validation precedence is: source existence, current destination conflict,
   concrete schedule/run-as/dependency blockers, then plan fingerprint mismatch;
   this makes actionable 409 codes deterministic. Preview orders blockers by
   code/path, and canonical digest inputs exclude volatile timestamps generated
   by model construction. Do not
   re-register or activate a schedule implicitly. Source cache may expire
   naturally; destination cannot use its owner-qualified keys.
8. Verification is persisted readback/model/reference validation, never a query
   run, including `/test` paths that might invoke provider-specific behavior.
   Existing v2/v3 legacy execution routes keep their meaning. Consumers of a
   moved definition must use the tenant route; no redirect is created.

## 3. Module Breakdown

### Delegation-eligible modules
| Module | Eligible? | Decided contract | Limitation |
|---|---|---|---|
| M1 Contract-aware persistence | yes | Legacy QueryModel vs TenantQueryDefinition; §2 null/upsert rules | Reverify anchors before implementation |
| M2 Management store catalog | yes | Configured-default registry repair and exact catalog contract | No DDL or permission inference |
| M3 Migration planning/transaction | no | §2 fixes transaction and error behavior | Q1 schedule policy and Q2 authorization require approval |
| M4 Regression and documentation | yes | §4 matrix and §5 criteria | Depends on M1–M3 |

### M1: Contract-aware persistence
**Paths:** `querysource/repositories/definitions.py`, `querysource/handlers/manager.py`.
**Depends on:** existing models, registry, run-as handling.
```python
# Existing signatures; verified anchors are in §6.
class DefinitionRepository:
    async def create(self, store: QueryStore, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate and serialize using the selected store contract."""
    async def upsert(self, identity: QueryIdentity, data: Mapping[str, Any], *, run_as_actor: int | None = None, request_info: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], bool]:
        """Retain legacy program on omission; reject tenant program payloads."""
    async def patch(self, identity: QueryIdentity, data: Mapping[str, Any], *, run_as_actor: int | None = None, request_info: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        """Apply only validated mutable fields without moving ownership."""
    def schema(self, store: QueryStore) -> Mapping[str, Any]:
        """Expose selected persistence contract, never a tenant runtime program."""
    async def export_insert(self, identity: QueryIdentity) -> str:
        """Export contract-correct fields to the exact qualified table."""
```

### M2: Store catalog
**Paths:** new `querysource/handlers/query_stores.py`; `querysource/services.py`,
`querysource/tenants.py`, `tests/tenants/test_tenant_registry.py`.
**Depends on:** existing QueryStore and model configuration; independent of M1.
Responsibility includes configured-default discovery/resolution repair described
in §2, with regression tests; no dynamic rediscovery or DDL on HTTP requests.
```python
# Proposed interface, not currently implemented.
# Existing registry methods to correct, not new public APIs.
class TenantRegistry:
    async def discover(self, conn: pg, *, allowlist: Sequence[str] | None = None) -> None:
        """Discover configured legacy table plus eligible per-schema queries."""
    def resolve(self, tenant: str | None = None) -> QueryStore:
        """Null selects configured physical default; public remains literal."""
class QueryStoreCatalog:
    async def list(self, request: web.Request) -> web.Response:
        """Return version=1 registered canonical store descriptors under management auth."""
```
Register static catalog/move routes before broad tenant execution routes.

### M3: Migration plan, transaction and handlers
**Paths:** new `querysource/query_moves.py`, `querysource/handlers/query_moves.py`;
`querysource/repositories/definitions.py`, `querysource/tenant_errors.py`,
`querysource/services.py`.
**Depends on:** M1, M2 descriptor contract and existing scheduler APIs.
```python
# Proposed interfaces. MovePlan/MoveResult are mappings matching §2 JSON.
class QueryMoveService:
    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Read-only dependency, schedule, collision and revision plan."""
    async def move(self, source: QueryIdentity, destination: QueryStore, *, plan_revision: str, actor: int, acknowledgements: Mapping[str, bool], request_info: Mapping[str, Any]) -> Mapping[str, Any]:
        """Revalidate and atomically move; never execute definition contents."""
class QueryMoveHandler:
    async def preview(self, request: web.Request) -> web.Response:
        """Resolve explicit selectors, authorize and return MovePlan."""
    async def move(self, request: web.Request) -> web.Response:
        """Authorize, commit and report any post-commit sync failure truthfully."""
```
The service owns the transaction through the existing connection factory;
private connection-bound helpers may be added here, not new public persistence
APIs invented during task execution. Source and target are same database only.

### M4: Tests and operator documentation
**Paths:** existing `tests/tenants/test_tenant_repository_writes.py`,
`tests/tenants/test_tenant_management_writes.py`; new
`tests/tenants/test_query_store_catalog.py`, `tests/tenants/test_query_moves.py`,
`tests/integration/test_query_moves_postgres.py`; `docs/PER_TENANT_QUERIES.md`.
**Depends on:** M1, M2, M3.
```python
# Test suite interfaces (new).
def test_legacy_write_contract():
    """Exercise omitted, unchanged and changed legacy program values."""
async def test_move_transaction_rollback():
    """Use real PostgreSQL to prove no partial copy/delete on injected failures."""
```

## 4. Test Specification

| Layer | Required scenarios |
|---|---|
| CRUD unit | Legacy create/upsert/PATCH/metadata/export keep program; tenant rejects program with stable 400; omitted legacy program survives upsert conflict; explicit PATCH null handling; unknown keys rejected |
| Ownership | Default custom schema/table, explicit public, allowlist, aliases, same physical store, unknown schema, no inferred selector |
| Catalog/HTTP | Auth boundary, numeric actor on migration, no secrets, no unsupported stores, static route ordering, selector duplicates and invalid body |
| Preview | Read-only; collision; every automatic-trigger form; stale run-as; implicit child refs and Query source; dynamic forms; runtime-program and consumer warnings |
| Real PostgreSQL | Successful move in both directions; exact fields/nulls; failure at target INSERT, verification, source DELETE and commit; source/destination DDL or permission failure |
| Concurrency | Source changes after preview including only program or run-as; ordinary concurrent target create; two moves to same target; opposite-direction moves; no overwrite/deadlock; lost COMMIT acknowledgement reports unknown rather than rollback |
| Runtime/scheduler | Legacy program-only edits change cache revision; no query execution or output calls; custom default versus public same-name sync isolation; source/target same-name cache isolation; schedule changes on ordinary CRUD still audited; sync failure after move reports committed state |
| Consumer example | Legacy pipeline child pinned tenant=null then moved to wm_assembly; old child stays legacy; destination uses tenant execution route |

Fixtures: two tenant tables without program_slug, default legacy table with it,
a configured custom legacy table, same-named slugs with different definitions,
a schedule/cache/DWH matrix, and write-capable sample pipeline from the incident.
Never run that pipeline against business tables. PostgreSQL rollback and race
checks are required evidence, not only mock assertions.

## 5. Acceptance Criteria

- [ ] An unchanged `program_slug` no longer breaks a legacy pipeline PATCH.
- [ ] Legacy program-only changes participate in definition cache revisions.
- [ ] Configured default schema/table, public and tenant stores resolve exactly;
      scheduler sync uses physical owner identity, not legacy/tenant contract.
- [ ] Unknown COMMIT/transport outcomes are reported and reconciled without
      claiming rollback or automatically repeating the move.
- [ ] Q1 and Q2 are answered before migration endpoints are enabled.
- [ ] Legacy program edits affect metadata only; no store change occurs.
- [ ] Legacy create/upsert, metadata and export preserve the legacy contract.
- [ ] Tenant writes reject program_slug with structured 400 before SQL mutation.
- [ ] Catalog exposes canonical registered stores and configured-default identity.
- [ ] No existing definition moves on deployment, load or ordinary save.
- [ ] Preview and commit implement exactly §2; collision never overwrites.
- [ ] Scheduled/refresh/DWH/stale-run-as and unsafe dependency cases block safely.
- [ ] A move is one DB transaction; rollback/race tests pass, and a simulated
      lost commit acknowledgement exercises the explicit unknown-outcome path.
- [ ] Permissions and allowlist checks apply to both ends; selectors are not grants.
- [ ] No SQL/pipeline execution is used for migration verification.
- [ ] Cache, schedules and consumer routing remain scoped to the correct owner.
- [ ] When COMMIT is acknowledged, later errors report that the move persisted;
      indeterminate COMMIT acknowledgement reports unknown rather than success/failure.
- [ ] Backend/frontend payload fixtures match the shared contract and docs include
      migration, maintenance, recovery and manual consumer-cutover instructions.
- [ ] Relevant tenant CRUD, scheduler run-as and legacy route regression suites pass.

## 6. Codebase Contract

Initial research used synchronized isolated dev. This review reverified edit
sites against the current local checkout; exact reviewed commit is below.
The earlier audit reproduced 400 for both legacy and tenant without opening a
DB connection; four existing repository-write tests passed in the original
checkout. Those tests do not prove migration behavior, which does not exist yet.

### Verified Imports and Existing Signatures
```python
from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition
from querysource.tenants import QueryStore, QueryIdentity, TenantRegistry
from querysource.repositories.definitions import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.cache_identity import definition_revision
```
Each symbol's declaring file is read and anchored below. Existing repository
constructor takes `registry` and async `connection_factory`; never use a shared
checked-out connection. Existing `TenantRegistry.resolve(tenant=None)` returns
an exact registered store or default; `stores()` returns eligible stores.
`QSScheduler.register_slug(slug, *, tenant=None)` scopes sync to the selected
owner. Existing raw-row revision helper is pure; build the move digest over
raw persisted values rather than the runtime adapter.

### Does NOT Exist (Anti-Hallucination)
There is currently no management store-catalog endpoint, move endpoint,
QueryMoveService, global reference inventory or distributed quiescence API.
`program_slug` does not route a management request. The tenant execution PATCH
inspects columns; it does not update or move a stored definition.

### Edit Sites (Blueprint Anchors)

Verified base commit: `7c5405045475109d9106f77d62d124b6fc2e9f40`. Occurrences are literal-line counts; reverify at task time.

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | `class DefinitionRepository:` | `querysource/repositories/definitions.py:94` | 1 |
| `querysource/handlers/manager.py` | MODIFY | `class QueryManager(QueryView):` | `querysource/handlers/manager.py:47` | 1 |
| `querysource/services.py` | MODIFY | `        ### Query Manager ###` | `querysource/services.py:239` | 1 |
| `querysource/tenant_errors.py` | MODIFY | `OWNERSHIP_STATUS = {` | `querysource/tenant_errors.py:5` | 1 |
| `tests/tenants/test_tenant_repository_writes.py` | MODIFY | `async def test_patch_null_and_immutable_keys() -> None:` | `tests/tenants/test_tenant_repository_writes.py:210` | 1 |
| `tests/tenants/test_tenant_management_writes.py` | MODIFY | `"""Route management mutations through selected-owner repository regression contracts."""` | `tests/tenants/test_tenant_management_writes.py:1` | 1 |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | `# Per-tenant queries` | `docs/PER_TENANT_QUERIES.md:1` | 1 |
| `querysource/tenants.py` | MODIFY | `class TenantRegistry:` | `querysource/tenants.py:80` | 1 |
| `tests/tenants/test_tenant_registry.py` | MODIFY | `async def test_discovery_preserves_legacy_program_slug_when_loading() -> None:` | `tests/tenants/test_tenant_registry.py:19` | 1 |
| `querysource/handlers/query_stores.py` | CREATE | — | — | — |
| `querysource/query_moves.py` | CREATE | — | — | — |
| `querysource/handlers/query_moves.py` | CREATE | — | — | — |
| `tests/tenants/test_query_store_catalog.py` | CREATE | — | — | — |
| `tests/tenants/test_query_moves.py` | CREATE | — | — | — |
| `tests/integration/test_query_moves_postgres.py` | CREATE | — | — | — |

### Additional verified contracts

| Symbol / behavior | Anchor |
|---|---|
| `class QueryModel(` | `querysource/models.py:49` |
| `class TenantQueryDefinition(` | `querysource/tenant_models.py:24` |
| `class QueryStore:` | `querysource/tenants.py:35` |
| `class QueryIdentity:` | `querysource/tenants.py:46` |
| `def resolve(self, tenant:` | `querysource/tenants.py:402` |
| `def stores(` | `querysource/tenants.py:427` |
| `def resolve_request_store(` | `querysource/handlers/tenant.py:24` |
| `def schema(` | `querysource/repositories/definitions.py:279` |
| `async def upsert(` | `querysource/repositories/definitions.py:389` |
| `async def _write_with_run_as(` | `querysource/repositories/definitions.py:582` |
| `async def register_slug(` | `querysource/scheduler/scheduler.py:608` |
| `def definition_revision(` | `querysource/cache_identity.py:50` |
| `def resolve_child_owner(` | `querysource/queries/multi/__init__.py:346` |

## 7. Implementation Notes & Constraints

Use qualified quoted identifiers from resolved registry objects and bound
values. Keep query definitions/data outputs separate. Do not change configured
legacy defaults or discover/create tables during a management request. Reuse
asyncdb transaction/commit/rollback conventions already exercised by run-as.
Missing destination model columns or incompatible defaults must fail before
source deletion. Do not pretend the catalog proves INSERT/DELETE privileges.

No new package dependency is required. Existing aiohttp/datamodel/asyncdb and
pytest are sufficient. No package version pin is changed by this spec.

Known risks: runtime program changes can change query meaning; external inbound
references cannot be proven complete; legacy slugs may embed schema/tenant in
SQL; active processes can outlive schedule removal; strict atomics cover DB
rows, not running jobs or external services. The maintenance procedure and
acknowledgements are mandatory and must not be described as a global lock.

## 8. Open Questions

### Resolved decisions
- [x] Legacy rows remain untouched unless explicitly migrated — user decision.
- [x] Per-schema creation, explicit moves and separate repo-local specs — user decision.
- [x] Save keeps owner; program metadata is distinct from store selection.
- [x] Respect configured default and literal public independently; registry repair
      and physical-identity scheduler selection are required implementation scope.
- [x] Lost COMMIT acknowledgement is unknown outcome; do not claim rollback.

### Product decisions still requiring approval
- [ ] **Q1 — Schedule policy:** accept the proposed first version that moves only
      definitions with all automatic execution disabled, followed by explicit
      maintenance and reactivation, or require live schedule transfer? Recommended:
      unscheduled-only v1, as specified above. Prior drafts marked this resolved
      even though it was only a proposal; it is not a user-approved decision yet.
- [ ] **Q2 — Migration authorization:** which authenticated users may preview and
      move: the deployment's management operators, or administrators only?
      Recommended: administrators for migration; ordinary CRUD keeps its current
      authorization. Numeric user ID and database write access alone are insufficient.
      Verify the deployment's actual group/role mapping before specifying a group
      name; no new PBAC action or role is assumed by this draft.

### Rollout prerequisites, not unresolved product design
Before a real move, assign maintenance ownership, identify every scheduler and
worker process, provide disposable integration fixtures, document inbound/external
consumers and the audited stale-run-as repair. Keep migration disabled until
these checks and Q1/Q2 are satisfied. These are release gates, not reasons to
block independent legacy-write fixes or ordinary tenant UI development.

## 9. Design Research Cross-Check

Independent model review: skipped (no qualifying accepted exploration/intake).
**Wiki/source review completed 2026-10-15.** The wikitoolkit CLI/MCP is absent;
queried each repository's existing SQLite FTS wiki and read the referenced pages
in read-only immutable mode. QuerySource snapshot: 2026-09-30T21:49:52+00:00;
frontend snapshot: 2026-09-30T15:46:01+00:00. The wiki is navigation/evidence,
not proof that a contract is implemented; source checks below override stale
page content. The referenced docs/sdd/CODEX.md remains absent.

| Wiki page ID | Current-source finding | Disposition |
|---|---|---|
| `file:querysource/tenants.py` | Discovery/default are hardcoded to queries/public, contrary to configurable-default promise | Add M2 registry repair and tests |
| `file:querysource/repositories/definitions.py` | Legacy program is removed before revision; stale run-as cannot be cleared by no-op PATCH | Add cache-revision criterion and explicit audited-repair blocker |
| `file:querysource/handlers/manager.py` | Sync selects null for all legacy-contract stores | Require physical-default comparison |
| `file:querysource/scheduler/scheduler.py` | Cache/query schedules are separate and in-memory jobs are per process | Keep explicit quiescence proposal as unresolved Q1 |
| Companion frontend API/browser/persistence pages | Owner selection and payload rules must stay consistent across both specs | Match error codes, projections, dirty-state and identity rules |

Transactional failure claim corrected: pre-commit rollback and indeterminate
commit acknowledgement are separate cases. Q1 and Q2 remain explicit; operational
checks were moved out of the product-question list.

## Worktree Strategy

One feature worktree for this spec, based on dev. M1 and M2 have no code
imports between them and can proceed independently. M3 depends on M1 and the
M2 descriptor contract; M4 consumes all three. Shared files: services.py
(M2/M3), definitions.py (M1/M3); tasks touching each must serialize. Real DB
fixtures/transaction fault tests are exclusive resources. No cross-repository
runtime implementation is included here. Backend contract must land before
frontend migration UI is enabled; companion implementation can develop against
shared fixtures while the API is under review.

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-15 | Codex | Initial coordinated draft from incident audit and user decisions |
| 0.2 | 2026-10-15 | Codex | Wiki/source review: registry/default, cache revision, commit uncertainty, scheduler identity and explicit Q1/Q2 |
