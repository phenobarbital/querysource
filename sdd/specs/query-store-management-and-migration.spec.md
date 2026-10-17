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
**Status**: approved
**Approved by**: User, 2026-10-15; approval includes live transfer and existing management permissions.
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
- Support preview and transactional movement of one definition with live schedule transfer,
  including legacy-to-tenant, without overwrite or extra verification runs.
- Make conflict, dependency, concurrency and schedule limitations reviewable.

### Non-Goals
- Automatic migration, bulk migration or creation of tenant schema/table DDL.
- Cross-database moves; renaming; automatic rewriting of other definitions or
  external consumers; redirects or fallback to legacy when tenant lookup fails.
- Cancellation of in-flight work, cross-database scheduler coordination or
  executing SQL/pipeline outputs as a migration verification step.
- Creating tenant schemas/tables automatically. Deployment DDL for the shared
  migration journal and schedule coordination state is in scope.
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
                                      `-> durable live scheduler handoff and recovery
```

### Integration Points
| Existing component | Integration | Contract |
|---|---|---|
| TenantRegistry | Catalog and selector resolution | Immutable snapshot; deduplicate physical stores |
| DefinitionRepository | CRUD compatibility and movement | Explicit qualified SQL; no ORM persistence on tenant |
| QueryManager | Existing management routes | Keep route methods, default owner and response conventions |
| QSScheduler | Live handoff | Drain existing runs, fence old dispatches, transfer trigger state and reconcile all mirrors |
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
access boundary as existing CRUD; resolve the numeric authenticated actor for
move audit attribution, enforce database permissions and existing applicable
PBAC rules, and never treat client acknowledgements as authorization. Numeric
actor presence alone is not a management authorization check. **User decision Q2:
retain current management permissions.** Preview, move, operation status and
reconciliation use the same deployed authentication/authorization boundary as
existing management reads/writes. Do not add an administrators-only rule, a new
role or a `slug:move` action. Preserve the existing datasource:use/pg_admin gate
when the resulting scheduled write-capable multi requires it. Numeric actor
resolution serves audit attribution and is not a replacement for those controls.


| Method and route | Request | Response |
|---|---|---|
| GET `/api/v1/management/query-stores` | No body | 200 `{version:1,data:StoreDescriptor[]}` |
| Existing management CRUD, `:meta`, `:insert` | GET/DELETE: `?tenant=schema`; writes: either query selector or top-level `tenant`, not both | Existing shapes/statuses; validation follows store contract |
| POST `/api/v1/management/queries/{slug}/move/preview` | `{tenant:null,destination_tenant:"wm_assembly"}` | 200 MovePlan |
| POST `/api/v1/management/queries/{slug}/move` | `{tenant:null,destination_tenant:"wm_assembly",plan_revision:"…",acknowledge_external_consumers:true,acknowledge_runtime_program_change:true,idempotency_key:"UUID"}` | 202 MoveOperation, or the same existing operation on an authorized retry |
| GET `/api/v1/management/query-moves/{operation_id}` | No mutation; existing management read boundary | 200 MoveOperation |
| POST `/api/v1/management/query-moves/{operation_id}/reconcile` | Existing management write boundary; idempotent | 200/202 MoveOperation; repairs the same operation, never repeats the row move |

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
  consumer_review_required:true, schedule_policy:"live-transfer",
  schedules:[{kind:"query"|"multi"|"cache",enabled:boolean,paused:boolean,
              timezone:string,next_fire_at:string|null,run_as:"preserve-existing"}],
  handoff_ready:boolean
}
MoveOperation = {
  version:1, operation_id:string, source:StoreDescriptor,
  destination:StoreDescriptor, query_slug:string,
  state:"preparing"|"draining"|"cutover"|"reconciling"|"completed"|"failed",
  moved:boolean|null, scheduler_sync:"pending"|"ok"|"failed"|"not_configured",
  error_code?:string, message?:string, updated_at:string
}
// moved=false: row move has not committed; true: durable cutover committed;
// null: acknowledgement is indeterminate until journal recovery resolves it.
// `failed` before cutover leaves source active; `reconciling` after cutover
// keeps moved=true and is never reported as a rolled-back move.
Migration errors = {error_code:string,message:string,details?:object}
```
Preview is read-only. For a missing source return 404. Other actionable
conditions produce a plan with `can_move:false`; submission revalidates and
returns 409 for immediate blockers. A blocker discovered by the asynchronous
worker is recorded as failed,moved=false,error_code on the same operation. A stale plan returns 409
`move_plan_stale`; an occupied destination returns 409 `move_target_exists`.
Other codes: `move_same_store` (400), `move_scheduler_not_ready` (409),
`move_scheduler_unsupported` (409), `move_schedule_invalid` (409),
`move_inherited_references` (409), `move_dependency_analysis_unsupported` (409),
`move_acknowledgement_required` (400), `move_validation_failed` (400),
`move_stale_run_as` (409), `move_outcome_unknown` (503),
`move_in_progress` (409), `move_idempotency_conflict` (409).
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
1. Preview is read-only: resolve owners, source revision, destination collision,
   inherited references and schedule readiness. Digest raw persisted fields
   (including legacy program/run-as), destination identity, schedule configuration
   revision and deterministic blocker/warning inventory. Exclude the naturally
   advancing schedule cursor and transient worker heartbeats: merely waiting for
   the next scheduled run must not stale an otherwise unchanged preview.
2. **User decision Q1: transfer schedules live.** A valid configured schedule is
   not a blocker and the operator is not asked to disable it. Inventory query,
   multi and cache jobs supported by QSScheduler. Preserve trigger definition,
   timezone, interval/biweekly anchor, logical job kind, desired paused state,
   next unclaimed fire time, coalescing/misfire policy and concurrency limits.
   Nonzero cache_refresh alone is not proof of an APScheduler job; use the actual
   scheduler registration predicates. External/DWH scheduling without an integrated
   coordinator returns move_scheduler_unsupported, never a silent manual fallback.
3. Implicit saved-child references still block until explicitly pinned to the
   intended owner. Do not change query_raw during move or rewrite SQL by regex.
   Unknown/dynamic reference forms block with an actionable path. Always report
   external-consumer review and before/after runtime program; require program
   acknowledgement only if the values differ. No redirect of old consumers.
4. POST move reserves a durable operation using a required UUID idempotency_key,
   bound to authenticated actor context, canonical endpoints, slug and request
   digest. The same authorized request returns the same operation ID; reuse for
   different parameters returns move_idempotency_conflict. Reauthorize every
   retry/status/reconcile call under current management permissions. Return 202
   after the operation is durable; a bounded worker advances it without keeping
   the HTTP request open for long-running source executions.
5. Freeze dispatch of NEW source schedule occurrences in shared state, reserve
   both owner/slug identities, and drain executions already admitted. They finish
   normally under their original owner/run-as, including awaited remote work.
   Do not cancel or replay them. Future destination occurrences cannot start
   before this drain and row cutover complete. Other slugs continue normally.
6. The row cutover uses one connection/transaction: ordered exclusive advisory
   locks for both identities, source SELECT FOR UPDATE, revision/permission/
   blocker revalidation, plain target INSERT, persisted readback, source DELETE
   affecting exactly one row, operation cutover record, new schedule owner and
   generation/cursor update. No ON CONFLICT UPDATE or separate create/delete
   transactions. Unique conflict is 409 and leaves source intact. Ordinary CRUD
   and schedule pause/resume/sync reject writes to reserved identities with
   move_in_progress until cutover recovery releases them; never resurrect source.
7. Preserve all business definition fields and creation provenance; stamp target
   migration actor/time. Tenant persistence omits program_slug; moving into legacy
   sets its program from the source runtime value. Keep program_id unchanged.
   For a scheduled definition preserve the ORIGINAL stored run-as user; moving
   must not rebind execution to the user pressing Move. Revalidate that delegated
   identity and existing scheduled-output grants before cutover. In the same
   transaction append source `clear` and destination `set` run-as audit events
   attributed to the mover when a nonnull run-as is transferred; do not emit
   null-to-null events. Preserve previous audit history. Missing required
   run-as/audit infrastructure blocks readiness before mutating either row.
   For an unscheduled source, a nonnull stale run-as remains move_stale_run_as;
   its attributed repair procedure is documented separately, not simulated by
   creating a temporary schedule. Never copy an arbitrary client run-as value.
8. Commit acknowledged: operation has moved=true. Reconcile scheduler mirrors
   automatically and continue future occurrences under the target. Failures
   after cutover leave state=reconciling and scheduler_sync=failed/pending, with
   source generation fenced; retry reconciliation of that operation, not move.
   Destination registration alone does not authorize dispatch: only a matching
   current durable generation/owner can claim it. A stale source mirror must
   skip even if it has not yet removed its in-memory job.
9. Before cutover, a proven failure restores source dispatch/cursor and records
   failed,moved=false; no target row remains. During lost COMMIT acknowledgement,
   inspect the operation's journal in the SAME database transaction before
   claiming success/failure. If unreachable, report move_outcome_unknown and
   retain the idempotency key; polling/retrying the same key resolves the same
   operation. Never infer rollback from a connection error. After restart, a
   lease-based recovery worker resumes draining or reconciliation from durable
   state; it cannot start another source-to-target row move.
10. Migration verification is metadata/readback/reference checks, never query
    execution. Scheduled work resumes only through normal scheduled dispatch
    after handoff. Cache keys stay owner-qualified; old source results expire
    without being used by destination. Existing legacy routes keep their meaning.

### Live schedule coordination protocol (required implementation scope)
The current MemoryJobStore and per-process register_slug are insufficient for
live transfers: they neither pause every process nor identify in-flight work.
This feature adds shared control state in the existing PostgreSQL database,
without changing the storage location of query result data or client tables.

- Deploy `docs/sql/query_move_control.sql` explicitly; no runtime DDL. Create
  `management.qs_query_moves` (UUID operation/idempotency identity, canonical
  source/target, actor, request digest, immutable plan snapshot, state, cutover
  outcome, recovery lease, error and timestamps),
  `management.qs_schedule_state` (stable logical schedule UUID, owner, kind,
  generation, configuration revision, trigger/cursor, paused state, dispatch
  gate and max_instances), `management.qs_schedule_runs` (UUID attempt,
  logical schedule, generation, scheduled fire time, outcome and worker), and
  `management.qs_scheduler_workers` (process ID, protocol version, heartbeat).
  Unique schedule occurrence key: (logical_schedule_id, scheduled_fire_at).
  Unique idempotency key scoped to deployment/actor; no credentials in records.
- Backfill live schedule state during upgraded scheduler registration/startup,
  with a serialized bootstrap per canonical owner/slug/kind. Persist the existing
  trigger anchor and cursor; do not recompute an interval from migration time.
  Startup and pause/resume/removal must use the same durable desired state.
  Job owner revalidation and QS/MultiQS construction must compare canonical
  physical default identity before selecting null; owner.schema alone does not
  identify a configured custom table. Preserve external owner envelope v1 shape.
  If existing per-process mirrors disagree on paused state/anchor, preview
  reports move_scheduler_not_ready rather than guessing which one to preserve.
- All query/multi/cache dispatchers admit work through the shared generation
  gate BEFORE constructing/loading a QS/MultiQS definition. Acquire the shared
  owner advisory lock FIRST, then revalidate gate/generation and claim the due
  scheduled occurrence in a short transaction; atomically advance the durable
  next-fire cursor and enforce the existing max_instances; persist the attempt.
  APScheduler is a wakeup/mirror mechanism, not a second independent authority
  for due times. Multiple processes waking the same occurrence can admit it
  once only. Reuse actual trigger get_next_fire_time behavior, timezone,
  coalescing and misfire settings; a handoff never resets cadence to now.
- The run retains that SAME dedicated PostgreSQL shared advisory lock for its
  canonical owner/slug until its query/pipeline and awaited remote work finish;
  release it and the connection in finally. Transfer freezes the generation
  gate first and then waits for exclusive locks in deterministic order, so new
  admissions stop and already admitted work drains. Durable run records and
  recovery additionally prevent trusting an expired worker heartbeat as proof
  that business work completed. External remote work whose outcome cannot be
  established leaves draining pending with an actionable error, not forced
  continuation or automatic replay.
- Keep the logical schedule UUID and last-claimed/next-unclaimed cursor across
  the owner change. Occurrences due during drain remain in the cursor and are
  handled once on the destination using the existing misfire/coalescing policy.
  A paused schedule remains paused. The transfer itself never runs an extra
  occurrence. Multiple source/destination mirrors cannot admit the same slot.
- Lease-based reconciliation/polling is durable; NOTIFY may accelerate it but is
  not the sole delivery path. Startup recovers unfinished operations BEFORE
  enabling affected schedule dispatch. A lease takeover revalidates journal,
  generation and run records; leases do not permit replaying a claimed attempt.
- This protocol provides one admission per scheduled occurrence. It does not
  promise exactly-once external effects after a process dies mid-query. An
  admitted attempt with an unknown external outcome is retained for operator
  reconciliation; never retry it automatically at the destination. Preserve
  existing notifications with actual owner/job IDs and surface pending status.
- Before enabling live transfer, roll out protocol support to ALL scheduler and
  scheduled-job worker processes in the deployment and establish shared state.
  Feature readiness requires the deployment rollout gate plus compatible worker
  heartbeats; heartbeats alone cannot prove that an unreported old process is
  absent. Missing DDL, unsupported worker or uncoordinated external scheduler
  yields move_scheduler_not_ready/unsupported BEFORE source dispatch is frozen.
  No manual per-slug deactivation is required once protocol readiness is met.
- The same PostgreSQL metadata service is required for live coordinated dispatch;
  loss of it fails admission closed for affected coordinated schedules, with
  notifications. Existing already-running work is not canceled. This is a
  documented deployment requirement, not a request for broader user permissions.

### Terminal and retry contract
Only state=completed with moved=true is final success. `cutover`/`reconciling`
may already have moved=true and must be shown as a committed move still
transferring schedules. `failed,moved=false` is safe pre-cutover failure.
Unknown HTTP outcomes recover by operation ID/idempotency key. Concrete
source/collision/readiness/dependency errors precede generic move_plan_stale.

## 3. Module Breakdown

### Delegation-eligible modules
| Module | Eligible? | Decided contract | Limitation |
|---|---|---|---|
| M1 Contract-aware persistence | yes | Legacy QueryModel vs TenantQueryDefinition; §2 null/upsert rules | Reverify anchors before implementation |
| M2 Management store catalog | yes | Configured-default registry repair and exact catalog contract | No DDL or permission inference |
| M3 Migration planning/transaction | no | Durable operation, atomic row cutover and shared live scheduler protocol | Distributed failure cases require senior review; user decisions resolved |
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
`querysource/services.py`; new `querysource/scheduler/handoff.py`,
`docs/sql/query_move_control.sql`; modify `querysource/scheduler/scheduler.py`,
`querysource/scheduler/jobs.py`, `querysource/handlers/scheduler.py`.
**Depends on:** M1, M2 descriptor contract and existing scheduler APIs. Control
DDL/backfill and dispatch protocol must land before live-move enablement.
```python
# Proposed interfaces. MovePlan/MoveOperation are mappings matching §2 JSON.
class QueryMoveService:
    async def preview(self, source: QueryIdentity, destination: QueryStore, *, actor: int) -> Mapping[str, Any]:
        """Read-only dependency, schedule, collision and revision plan."""
    async def move(self, source: QueryIdentity, destination: QueryStore, *, plan_revision: str, idempotency_key: str, actor: int, acknowledgements: Mapping[str, bool], request_info: Mapping[str, Any]) -> Mapping[str, Any]:
        """Reserve/reuse durable live-transfer operation and return its status."""
    async def status(self, operation_id: str) -> Mapping[str, Any]:
        """Return persisted progress; reauthorize at the management boundary."""
    async def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        """Idempotently resume the same operation; never copy/delete twice."""
class QueryMoveHandler:
    async def preview(self, request: web.Request) -> web.Response:
        """Resolve explicit selectors, authorize and return MovePlan."""
    async def move(self, request: web.Request) -> web.Response:
        """Authorize and return 202 for a durable handoff operation."""
    async def status(self, request: web.Request) -> web.Response:
        """Expose recoverable progress under current management permissions."""
    async def reconcile(self, request: web.Request) -> web.Response:
        """Resume existing operation under the same current write permissions."""
# New querysource/scheduler/handoff.py; no implementation bodies.
class ScheduleHandoffCoordinator:
    async def prepare(self, operation_id: str) -> Mapping[str, Any]:
        """Check readiness and freeze new admissions for this identity."""
    async def drain(self, operation_id: str) -> Mapping[str, Any]:
        """Wait for admitted runs to finish without canceling them."""
    async def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        """Apply durable owner/generation/cursor to every scheduler mirror."""
    async def recover(self) -> None:
        """Recover leased unfinished operations before affected dispatch starts."""
    async def admit(self, logical_schedule_id: str, generation: int) -> Mapping[str, Any] | None:
        """Claim a due slot once or reject stale/frozen/paused work."""
```
An unscheduled move still uses the durable journal/recovery worker but does
not require an enabled QSScheduler engine. Start that operation worker through
QuerySource lifecycle independently of ENABLE_QS_SCHEDULER. Scheduled moves
require the live protocol readiness described above.

The service owns the transaction through the existing connection factory;
private connection-bound helpers may be added here, not new public persistence
APIs invented during task execution. Source and target are same database only. Reserve control-pool capacity so
long-lived run locks cannot starve the operation worker; cover pool exhaustion
and lost lock connections without allowing unfenced destination admission.

### M4: Tests and operator documentation
**Paths:** existing `tests/tenants/test_tenant_repository_writes.py`,
`tests/tenants/test_tenant_management_writes.py`; new
`tests/tenants/test_query_store_catalog.py`, `tests/tenants/test_query_moves.py`,
`tests/integration/test_query_moves_postgres.py`, new
`tests/tenants/test_live_schedule_handoff.py`,
`tests/integration/test_live_schedule_handoff_postgres.py`;
`docs/PER_TENANT_QUERIES.md`.
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
| Preview | Read-only; collision; valid schedules are transferable; protocol/DDL readiness; unsupported external jobs; stale unscheduled run-as; implicit child refs and Query source; dynamic forms; runtime-program and consumer warnings |
| Live handoff | Two scheduler processes, running source job, queued fire during drain, paused job, timezone/interval anchor, cached query and multi jobs, original run-as/audit, old mirror after cutover |
| Recovery | Crash before freeze, during drain, lost COMMIT acknowledgement, after cutover/before mirror update; same idempotency key; worker lease takeover; unknown remote execution not replayed |
| Permissions | Existing authorized management operator can move; no new admin-only check; existing scheduled pg_admin/datasource gate and unauthenticated/denied cases remain enforced |
| Real PostgreSQL | Successful move in both directions; exact fields/nulls; failure at target INSERT, verification, source DELETE and commit; source/destination DDL or permission failure |
| Concurrency | Source changes after preview including only program or run-as; ordinary concurrent target create; two moves to same target; opposite-direction moves; no overwrite/deadlock; lost COMMIT acknowledgement reports unknown rather than rollback |
| Runtime/scheduler | Legacy program-only edits change cache revision; no migration-triggered query/output calls; normal scheduled work resumes only through due admission; custom default versus public same-name sync isolation; source/target same-name cache isolation; schedule changes on ordinary CRUD still audited; sync failure after move reports committed state |
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
- [ ] Q1 live transfer and Q2 existing management permissions are implemented
      without an administrators-only rule; protocol rollout readiness is checked.
- [ ] Legacy program edits affect metadata only; no store change occurs.
- [ ] Legacy create/upsert, metadata and export preserve the legacy contract.
- [ ] Tenant writes reject program_slug with structured 400 before SQL mutation.
- [ ] Catalog exposes canonical registered stores and configured-default identity.
- [ ] No existing definition moves on deployment, load or ordinary save.
- [ ] Preview, operation submission/status/reconciliation implement §2; collision
      never overwrites and no intermediate state is labeled completed.
- [ ] Valid QSScheduler query/multi/cache jobs transfer live without manual
      deactivation, restart or re-binding their run-as to the mover.
- [ ] Trigger/timezone/anchor, next due cursor, pause state and runtime settings
      survive cutover; due slots during drain follow existing misfire policy.
- [ ] Multi-process dispatch admits each scheduled occurrence once; old source
      generations remain fenced even when local mirror cleanup fails.
- [ ] Already admitted source runs finish before destination admission; a crash
      with unknown remote effects remains pending and is never replayed.
- [ ] Durable operations recover after restart and idempotent retries return the
      same operation; post-cutover reconciliation never repeats the row move.
- [ ] Missing coordination DDL/protocol, unsupported external jobs, stale
      unscheduled run-as and unsafe dependencies fail explicitly before cutover.
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
QueryMoveService, shared schedule journal/admission protocol, global reference
inventory or distributed quiescence API. All live-transfer control tables,
operation endpoints and the coordinator above are new planned interfaces.
`program_slug` does not route a management request. The tenant execution PATCH
inspects columns; it does not update or move a stored definition.

### Edit Sites (Blueprint Anchors)

Verified base commit: `a4334bd1bd803221c3484a435fc8f600f554694a`. Occurrences are literal-line counts; reverify at task time.

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
| `querysource/scheduler/scheduler.py` | MODIFY | `class QSScheduler:` | `querysource/scheduler/scheduler.py:121` | 1 |
| `querysource/scheduler/jobs.py` | MODIFY | `async def scheduled_query_job(` | `querysource/scheduler/jobs.py:61` | 1 |
| `querysource/handlers/scheduler.py` | MODIFY | `class SchedulerJobsView(BaseView):` | `querysource/handlers/scheduler.py:74` | 1 |
| `querysource/scheduler/handoff.py` | CREATE | — | — | — |
| `docs/sql/query_move_control.sql` | CREATE | — | — | — |
| `tests/tenants/test_live_schedule_handoff.py` | CREATE | — | — | — |
| `tests/integration/test_live_schedule_handoff_postgres.py` | CREATE | — | — | — |
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

No new package dependency is required. Existing aiohttp/datamodel/asyncdb,
APScheduler and pytest are sufficient. Shared coordination tables and their
explicit deployment/backfill are required; no tenant queries table is created. No package version pin is changed by this spec.

Known risks: live transfer requires shared PostgreSQL control DDL, upgraded
workers and durable admission state; runtime program changes can change query meaning; external inbound
references cannot be proven complete; legacy slugs may embed schema/tenant in
SQL; active processes can outlive schedule removal; strict atomics cover DB
rows, not running jobs or external services. Automatic drain/recovery coordinates scheduled work only, not arbitrary
manual consumers; acknowledgements do not claim a global application lock.

## 8. Open Questions

### Resolved decisions
- [x] Existing legacy rows remain until an explicit move — user decision.
- [x] Per-schema creation, explicit moves and separate repo-local IDs — user decision.
- [x] **Q1 — Live schedule transfer.** User: "1 trasnferencias en vivo".
      Transfer automatically through durable freeze, drain, cutover and recovery;
      no manual per-slug schedule deactivation or restart is required.
- [x] **Q2 — Existing management permissions.** User: "2 los premisos actuales
      esta bien". Use current management authentication/authorization and current
      scheduled-output grants. Do not add an administrators-only restriction.
- [x] Keep original run-as across a live move; mover is the audit actor only.
- [x] Preserve configured default/public distinction, owner-qualified caching
      and explicit handling of unknown commit/execution outcomes.

### Remaining product questions
None. Do not reopen Q1/Q2 during task decomposition.

### Implementation and rollout prerequisites
Apply shared control DDL and backfill, upgrade every scheduler/worker to the
handoff protocol, establish the deployment readiness gate, and provide real
multi-process PostgreSQL recovery/race fixtures. Inventory external consumers
and any external/DWH scheduler integrations; unsupported engines must report an
explicit readiness blocker. These are implementation/release requirements, not
new user decisions or a demand to disable each schedule before moving.

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
| `file:querysource/scheduler/scheduler.py` | Cache/query schedules are separate and MemoryJobStore is per-process | User chose live transfer: add durable shared admission, drain and recovery |
| Companion frontend API/browser/persistence pages | Owner selection and payload rules must stay consistent across both specs | Match error codes, projections, dirty-state and identity rules |

Transactional failure claim corrected: pre-commit rollback and indeterminate
commit acknowledgement are separate cases. The subsequent user answers resolve Q1/Q2 as live transfer and existing
management permissions; the live coordinator scope replaces manual quiescence.

## Worktree Strategy

One feature worktree for this spec, based on dev. M1 and M2 have no code
imports between them and can proceed independently. M3 depends on M1 and the
M2 descriptor contract; M4 consumes all three. Shared files: services.py
(M2/M3), definitions.py (M1/M3); tasks touching each must serialize. Shared control DDL/backfill, multi-process scheduler fixtures and transaction
fault tests are exclusive resources. M3 includes a dispatch-protocol prerequisite
followed by operation orchestration; do not enable moves before protocol readiness. No cross-repository
runtime implementation is included here. Backend contract must land before
frontend migration UI is enabled; companion implementation can develop against
shared fixtures while the API is under review.

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-15 | Codex | Initial coordinated draft from incident audit and user decisions |
| 0.2 | 2026-10-15 | Codex | Wiki/source review: registry/default, cache revision, commit uncertainty, scheduler identity and explicit Q1/Q2 |
| 0.3 | 2026-10-15 | Codex | Resolve Q1/Q2: live schedule handoff with durable recovery; retain existing management permissions |
