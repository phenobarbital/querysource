---
id: FEAT-176
title: Per-tenant query ownership with backward-compatible QuerySource APIs
slug: per-tenant-queries
type: feature
mode: enrichment
status: review
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-09-15
  summary_oneline: Schema-per-tenant query ownership, discovery and APIs with legacy compatibility
overall_confidence: medium
base_branch: dev
research_state: sdd/state/FEAT-176/
created: 2026-09-15
updated: 2026-09-15
---

# FEAT-176 — Per-tenant queries

> **Mode:** enrichment · **Confidence:** medium · **Status:** review
>
> Synthesis approved; five product questions answered. Implementation is the next phase.
> [Research audit](../state/FEAT-176/) · [User decisions](../state/FEAT-176/decisions.md)

## 0. Origin

The user requests schema-per-client storage of query definitions, startup discovery of schemas containing `queries.query_slug`, an optional initialization allowlist, tenant execution routes and tenant-aware CRUD, while preserving legacy defaults and APIs. The original request is preserved verbatim below and in the [source record](../state/FEAT-176/source.md).

> $sdd-proposal per-tenant-queries -- Currently Querysource only reads queries from "public.queries" table at main postgres, but we are looking for a new version of querysource where queries will live per-tenant, where tenant means an isolated postgres schema per-client, a new querysource routine will search (using information schema) for all schemas in postgres with "queries" table and if that "queries" table have a column "query_slug", with an optional "allowlist" filter for loading only "allowed" tenants provided during `QuerySource` initialization, {tenant}.queries will be a copy of public.queries but without "program_slug" columns because we are already are in an particular schema. when QS loads in server-mode, all tenants will be scanned for "queries" table, a register an internal record with all supported tenants, I know this new feature will be affect across all internal functionalities in Querysource, but this will be a major release but preserving backward compatibility, that means: current APIs /api/v2/services/queries and /api/v3/queries will be for "public" queries, demanding a new URL for per-tenant queries (/api/v1/{tenant}/queries/), the same will happen to query-slug CRUD operations, where if user pass a "tenant" attribute, queries will be listed, edited or inserted into the "queries" tenant table, if tenant is absent or null, will use "public.queries". we need to do a complete research around what parts of code are touching for this new "per-tenant" queries funcionality.

## 1. Synthesis Summary

Tenant is the structural owner of a saved query definition: a client accesses a query prepared for its consumption, regardless of the schemas that query reads. The release must carry owner identity through definition lookup, CRUD, result caches, nested execution, scheduling and existing authorization checks. Existing schema/table overrides and public APIs remain backward compatible. Tenant rows omit persisted `program_slug`; runtime program context is derived from the tenant, with `program_id` provisionally retained. The deployment allowlist and current access controls are sufficient for this release; no mandatory new membership layer is added. The code impact is grounded, while deployed DDL, ORM mechanics and external-worker behavior require verification in the specification. [F001–F004, F006–F019; U1–U5]

### Product decisions

| Decision | Final direction | Provenance |
|---|---|---|
| Tenant API | One handler supports single and multi-query execution; GET collection lists definitions; existing management endpoints remain the CRUD interface with a tenant selector. | U1 |
| Legacy configuration | Preserve `QS_QUERIES_SCHEMA` and `QS_QUERIES_TABLE`; defaults remain `public.queries`. | U2 |
| Program fields | Derive runtime `program_slug` from the tenant name; do not persist it on tenant rows. Retain `program_id` as the working assumption, reflecting the user's “probably.” | U3 |
| Access control | Tenant loading allowlist plus current access controls is sufficient for now. PBAC remains optional. | U4 |
| Ownership | Query ownership does not restrict accessed schemas or data sources. | U5 |

## 2. Codebase Findings

### 2.1 Localization

Paths and symbols below are backed by the cited findings. “Inspect” means verify the boundary during implementation rather than automatically editing every function. Full line-level localization is in [synthesis.json](../state/FEAT-176/synthesis.json).

| File | Relevant symbols / ranges | Impact | Evidence |
|---|---|---|---|
| `querysource/models.py` | `QueryModel` (48-107) | Persistence fields and immutable schema selection | F001 |
| `querysource/interfaces/connections.py` | `Connection.get_query_slug` (444-488); `SLUG_CACHE` (45-48); `Connection.get_slug` (494-506) | All definition lookups; legacy default and tenant repository routing | F001, F012 |
| `querysource/services.py` | `QuerySource.setup` (134-216); `QuerySource.qs_start` (369-375); `QuerySource` (50-105) | Initialization allowlist, registry startup and API routes | F002, F003 |
| `querysource/connections.py` | `QueryConnection.start` (156-209); `QueryConnection.in_cache` (98-103); `QueryConnection.from_cache` (105-118) | Pool lifecycle, startup ordering and result-cache boundary | F003, F012 |
| `querysource/queries/models.py` | `Query` (11-25); `QueryResult` (28-43) | Inspect raw execution contract; tenant is routing metadata | F005 |
| `querysource/queries/qs.py` | `QS.build_provider` (164-225); `QS.query` (380-430) | Single-query lookup, cache identity and execution events | F006 |
| `querysource/queries/obj.py` | `QueryObject.__init__` (28-62); `QueryObject.build_provider` (88-101) | Local child-query definition lookup | F007 |
| `querysource/queries/base.py` | `BaseQuery` (19-62) | Inspect shared context and lifecycle forwarding | F008 |
| `querysource/interfaces/queries.py` | `AbstractQuery.__init__` (50-102); `AbstractQuery.caching_data` (296-355); `AbstractQuery.save_in_cache` (267-283) | Owner context and asynchronous cache writes | F008, F010 |
| `querysource/queries/executor.py` | `Executor.introspect` (17-55); `Executor.start` (57-64) | Inspect separation of datasource introspection from ownership discovery | F009 |
| `querysource/datasources/introspection.py` | `AnsiSQLIntrospector._tables_sql` (121-133); `AnsiSQLIntrospector._columns_sql` (135-142) | Existing information-schema pattern | F011 |
| `querysource/scheduler/scheduler.py` | `QSScheduler.startup` (523-546); `QSScheduler._slug_job_ids` (379-381); `QSScheduler._fetch_slug_row` (383-410); `QSScheduler.register_slug` (442-481) | Startup loading, live synchronization and job identity | F013 |
| `querysource/queries/multi/__init__.py` | `MultiQS.query` (197-241) | Parent and child definition ownership | F014 |
| `querysource/queries/multi/sources/executors.py` | `LocalExecutor.execute` (85-112); `RemoteExecutor.execute` (180-208) | Local and remote owner propagation | F014 |
| `querysource/queries/multi/sources/query.py` | `ThreadQuery.fetch` (64-85) | Threaded child execution boundary | F014 |
| `querysource/handlers/manager.py` | `QueryManager.get` (71-131); `QueryManager._paginate_list` (169-205); `QueryManager.patch` (277-289); `QueryManager.delete` (341-349); `QueryManager.put` (414-453); `QueryManager.post` (461-495); `QueryManager.get_query_insert` (47-69) | All CRUD methods, metadata, pagination and SQL export | F015 |
| `querysource/auth/credentials.py` | `ResolvedCredentials` (17-37) | Inspect preservation of current credential resolution | F016 |
| `querysource/handlers/abstract.py` | `AbstractHandler._enforce_pbac` (324-355); `AbstractHandler.get_source` (264-279) | Context forwarding and current policy enforcement | F016, F027 |
| `querysource/handlers/service.py` | `QueryService.query` (186-192) | Legacy execution, conditions and inspection | F016 |
| `querysource/handlers/multi.py` | `QueryHandler._preflight_multiquery` (50-71); `QueryHandler.query` (214-230) | Unified tenant execution pattern and actual-child policy checks | F016, F027 |
| `querysource/conf.py` | `QS_QUERIES_SCHEMA` (352-354); `ENABLE_QS_SCHEDULER` (356-358); `QS_PBAC_ENABLED` (428-431); `QUERYSET_REDIS` (93-96) | Preserve configured defaults and optional PBAC | F017 |
| `pyproject.toml` | `dependencies` (43-80) | Existing dependencies; no new package required by this proposal | F018 |
| `querysource/providers/abstract.py` | `BaseProvider` (73-79); `BaseProvider.checksum` (229-230) | Derived program context and checksum contract | F019 |
| `querysource/parsers/abstract.pyx` | `AbstractParser._program_slug_sync` (162-166) | Compiled parser program context | F019 |
| `querysource/parsers/pgsql.pyx` | `pgSQLParser.build_query` (295-304) | Filter callback program value | F019 |
| `querysource/providers/external.py` | `externalProvider.checksum` (68-70) | Inspect provider-specific cache hashes | F019 |
| `querysource/providers/sources/abstract.py` | `baseSource.checksum` (150-161) | Inspect source-specific cache hashes | F019 |
| `docs/sample_sqlserver.sql` | `public.queries sample INSERT` (15-24) | Sample inserts, not authoritative query DDL | F020 |
| `docs/sql/datasources_logic.sql` | `public.datasources DDL` (5-5) | Datasource DDL, not tenant-query DDL | F020 |
| `tests/test_queryslug_concurrency.py` | `test_new_pattern_is_race_free` (78-89) | Extend regression coverage or documentation | F021 |
| `tests/handlers/conftest.py` | `seeded_query_slugs` (152-173) | Extend regression coverage or documentation | F021 |
| `tests/handlers/test_querymanager_pagination.py` | `TestQueryManagerListPagination.test_default_pagination_returns_envelope` (275-301) | Extend regression coverage or documentation | F021 |
| `docs/QSSCHEDULER.md` | `Job kinds` (1-17) | Extend regression coverage or documentation | F022 |
| `querysource/queries/multi/sources/query.catalog.yaml` | `Query component contract` (15-40) | Extend regression coverage or documentation | F022 |
| `querysource/handlers/_pagination.py` | `FILTERABLE_COLUMNS` (45-48); `SORTABLE_COLUMNS` (52-62); `SEARCHABLE_COLUMNS` (64-70); `build_where_clause` (280-300); `_validate_bare_identifier` (404-413) | Model-specific fields, filtering, sorting and search | F024 |
| `querysource/scheduler/jobs.py` | `scheduled_query_job` (35-46); `scheduled_multiqs_job` (50-90); `cache_refresh_job` (94-121) | Owner in all scheduled execution payloads | F025 |
| `querysource/scheduler/notifications.py` | `NotificationManager.notify` (37-52) | Compatible notification context | F025 |
| `querysource/handlers/scheduler.py` | `SchedulerJobsView._serialize_job` (77-105); `SchedulerJobsView.post` (189-224) | Owner-aware serialization and management | F026 |
| `querysource/handlers/log.py` | `LoggingService.request_info` (75-84) | Owner in audit information | F028 |
| `querysource/providers/rethink.py` | `rethinkProvider.checksum` (83-89) | Inspect provider fallbacks and hashes | F029 |
| `querysource/providers/influx.py` | `influxProvider.checksum` (51-64) | Inspect provider fallbacks and hashes | F029 |
| `querysource/providers/arangodb.py` | `arangodbProvider.checksum` (72-87) | Inspect provider fallbacks and hashes | F029 |
| `querysource/providers/deltatbl.py` | `deltatblProvider.checksum` (95-98) | Inspect provider fallbacks and hashes | F029 |
| `querysource/providers/iceberg.py` | `icebergProvider.checksum` (89-92) | Inspect provider fallbacks and hashes | F029 |

Additional search-located parser contracts: `querysource/parsers/abstract.pxd:22`, `sql.pyx:372`, `cql.pyx:178` and `sosql.pyx:217` expose or pass `program_slug`. Inspect all callback implementations and rebuild affected Cython interfaces if changed. [F019]

### 2.2 Constraints discovered

- **Configurable legacy storage:** the persistence model uses configured schema/table defaults, while scheduler startup explicitly reads `public.queries`. The new shared storage resolver must remove this inconsistency without discarding existing overrides. [F013, F017; U2]
- **On-demand definitions:** master startup loads datasource definitions; normal saved-query lookup reads PostgreSQL on demand. `SLUG_CACHE` is declared but unused in the inspected lookup path. Eager query-definition preloading would be new behavior. [F003, F012]
- **Concurrency:** current reads pass `_connection=conn` rather than mutating shared model state. Changing `QueryModel.Meta.schema` during a request risks the same class of race already covered by a regression test. [F001, F021, F023]
- **Tenant row shape:** model fields, default projections, searchable/sortable columns, metadata and INSERT export all currently assume `program_slug` exists. [F004, F015, F024]
- **Result keys:** cache hashes are provider-specific and omit explicit tenant identity. A change only to database lookup cannot isolate owner-specific results. [F006, F010, F019, F029]
- **Current access controls:** PBAC is optional, credentials use existing user/profile/default resolution, and resource names currently use slugs. Preserve current enforcement while carrying owner context; the feature does not require membership policies. [F016–F017; U4]

### 2.3 Relevant history

| Commit | Date | Observed change | Evidence |
|---|---|---|---|
| `aefa01e37e39c836ed18e272ad2c98972b242c06` | 2026-09-15 | Research baseline; release 4.5.16 | F023 |
| `3e30c17` | 2026-08-19 | Stored multi-query source normalization | F023 |
| `fa97f4f53ee5f39894ba1015156f0ad451423c93` | 2026-05-06 | QueryModel lookup concurrency fix | F023 |

## 3. Probable Scope

### 3.1 Structural ownership

A query owned by `client_a` is loaded from `client_a.queries` and is presented for that client's consumption. Its SQL may join `shared.customers`, `warehouse.orders` and `client_b.reference_data` when existing database permissions allow that access. Its saved definition, cache identity, scheduled job and audit owner remain `client_a`.

Do not rewrite SQL, set a tenant-wide execution `search_path`, inspect SQL to prohibit other schemas, or infer the query owner from tables used in the SQL. Registry membership selects stored definitions; it does not constrain all referenced data. This distinction also applies to raw queries, pipelines, remote jobs and outputs. [U5; F005, F009, F014, F030]

Nested **definition lookup** still needs explicit ownership context. Proposed default: a child with no owner selector inherits the parent's owner; a child may explicitly select another registered owner's saved definition through the same allowlist and current access controls. Do not introduce a blanket cross-owner restriction, and do not search other owners when a slug is missing. These addressing details are proposed specification rules; the user's answer concerns structural ownership, not a new child-reference syntax.

### 3.2 Discovery and registry

Add an asynchronous routine using the main PostgreSQL metadata connection. Find `information_schema.tables` entries named `queries` joined to `information_schema.columns` entries named `query_slug`, matching catalog, schema and table. Exclude system/temporary schemas and distinguish base tables from views. The marker column discovers candidates; validate the full persistence column contract before recording a candidate as usable. [F004, F011]

PostgreSQL column metadata is limited to the connected role's visibility and includes view columns. A successful scan is an inventory visible to that service role, not proof that no other schema exists. Record incompatible or inaccessible configured tenants with diagnostic reasons. [PostgreSQL columns documentation](https://www.postgresql.org/docs/18/infoschema-columns.html) [F030]

Proposed initialization contract: `QuerySource(tenant_allowlist=None)`. The exact parameter name is a specification choice; the user requested an initialization allowlist.

| Allowlist | Proposed behavior |
|---|---|
| Missing / `None` | Register all compatible discovered tenant stores. |
| Empty collection | Register no additional tenant stores. |
| Explicit names | Register only exact allowed schema names. |
| Unknown/incompatible allowed tenant | Report a configuration diagnostic; never redirect its queries to the default store. |
| Legacy default store | Remains available independently, preserving configured schema/table defaults. |

Registry entries should retain canonical schema/table, column capabilities, compatibility state, diagnostic reason and discovery generation. Avoid duplicate registration and scheduling when discovery sees the configured legacy table. Keep legacy row shape distinct from a tenant row shape even if the configured default schema is not `public`.

**Proposed loading baseline:** discover the registry at server startup, before tenant scheduled jobs and request readiness; read query definitions on demand. The user clarified ownership but did not explicitly select eager versus lazy loading. This baseline follows existing lookup behavior. Runtime registry refresh and eager definition caching are follow-up capabilities rather than prerequisites. Lazy/programmatic usage must use the same owner resolver without requiring an HTTP request. [F002–F003, F012–F013]

### 3.3 Persistence boundary

Introduce one definition repository for get/list/count/create/update/delete/schema/export. Route the existing lookup and management paths through it, retaining per-call connection handling. It must choose the storage contract before model construction, filtering or SQL generation. [F001, F004, F015]

Candidate implementation: separate public and tenant persistence models with independently constructed immutable metadata, or explicitly schema-qualified repository SQL mapped into typed rows. Verify asyncdb's installed behavior for metadata inheritance, field exclusion and schema overrides before selecting the mechanism. Do not introduce new dependencies without need.

Use parameter binding for data and tested identifier quoting for schema/table names accepted by the registry. Do not mutate shared model schema or pooled `search_path` per request. The existing pagination identifier validator supports fewer names than PostgreSQL allows; either support the broader names consistently or declare and validate naming constraints at discovery. [F021, F024]

PostgreSQL schemas support qualified access but are not rigid access boundaries; database privileges continue to govern accessed data. [PostgreSQL schema documentation](https://www.postgresql.org/docs/18/ddl-schemas.html) [F030]

For tenant rows, remove `program_slug` from persistence, default list fields, filters, sorting, search, schema metadata and exported INSERT statements. Supply a **runtime-only** program value derived from the owner to providers and parsers. Retain `program_id` as the working model contract and verify its deployed default/constraints. Legacy rows retain their stored program fields and semantics. [F004, F015, F019, F024, F029; U3]

### 3.4 API contract

| Route / input | Behavior |
|---|---|
| Existing `/api/v2/services/queries` family | Existing single-query behavior and configured default store. |
| Existing `/api/v3/queries` family | Existing multi-query behavior and configured default store. |
| `GET /api/v1/{tenant}/queries/` | List definitions owned by that tenant, using existing pagination conventions. |
| `GET` / `POST /api/v1/{tenant}/queries/{slug}` | One handler executes a stored single or multi query owned by that tenant. |
| `POST /api/v1/{tenant}/queries/` | Proposed inline multi-query execution entry with that owner context. |
| Existing `/api/v1/management/queries` family + tenant selector | List/read/create/update/delete the selected owner's definitions; preserve existing CRUD methods and statuses. |
| CRUD selector missing or JSON null | Use configured legacy schema/table; this is `public.queries` by default. |
| Raw executor `/api/v1/queries/test`, `/run`, `/schema` | Preserve datasource/driver semantics; datasource schema is not ownership selection. |
| Scheduler management family | Carry owner through job identity, listing and controls. |

Existing route evidence: F002, F015, F026. Product direction: U1–U2. Proposed details such as inline POST and child-reference syntax must be specified with compatibility tests.

For CRUD, propose `?tenant=client_a` on GET/DELETE and a reserved top-level JSON `tenant` attribute for writes. When multiple selectors are present, require agreement. Strip ownership metadata before model validation, WHERE filters or execution conditions. Reject empty or malformed selectors instead of converting them to the default store. Unknown/disallowed tenant or missing slug must not fall back to another owner. [F015, F024, F027]

Legacy execution routes stay bound to their configured default store; a `tenant` input must not silently redirect those routes. Explicit tenant routes select the discovered `{tenant}.queries` contract. Proposed explicit `tenant=public` behavior is literal `public.queries`, independently from omitted legacy selection; verify this distinction when non-default legacy overrides are in use.

Preserve listing envelopes and count headers, empty-result statuses, `:meta`, `:insert`, output suffixes and inspection behavior. Tenant metadata and INSERT exports must describe the tenant shape. Test trailing-slash handling, legacy test/column routes and new inspection equivalents. [F002, F015, F024]

**Route collision:** `/api/v1/management/queries` already exists. A tenant named `management` collides if the new route is normalized to that path. Proposed first-release policy: preserve the legacy route, reject/reserve conflicting tenant route names with a diagnostic, and document the restriction. The spec must test the exact slash/method route table before implementation. [F002]

### 3.5 Execution and caching

Carry owner context through the handler, QS, MultiQS, QueryObject, local child executors and remote transport. Keep output aliases distinct from definition slugs. Saved pipelines must retain owner context while expanding children; the single-query fallback needs the same treatment. [F006–F008, F014, F027]

Prefer a central, versioned cache-key envelope containing owner/store identity, slug and provider checksum. Apply it to lookup, writes, refresh and mutation invalidation. Include query revision or invalidate results on definition changes; exact mechanics belong in the spec. Identical SQL for two owners may intentionally access the same data but must not accidentally share owner-specific cached results. Preserve legacy cache compatibility where safe, and never read unqualified legacy keys for a tenant-owned execution. [F006, F010, F012, F019, F029]

Keep current credential and permission enforcement. If existing policy evaluation caches decisions by resource identity, include sufficient owner context to distinguish same-named definitions; do not infer a new mandatory membership requirement. Tenant cache separation does not by itself establish user-specific result-cache isolation, so verify existing credential-dependent behavior separately. [F016–F017; U4]

### 3.6 Scheduling, workers and observability

Load scheduled definitions from the registered stores through the shared repository. Owner-qualified job IDs and kwargs must survive startup, live sync, pause/resume, delete and cache refresh. Preserve legacy public/default job identities where needed. Updating one owner's slug must not remove another owner's jobs. Keep notification callback signatures compatible while adding owner information through a compatible context or identifier. [F013, F025–F026]

The remote executor dispatches to `querysource.remote.query_handler`, but no matching remote module was found in this checkout. Verify the external worker implementation and its protocol before enabling tenant remote execution. Old workers that cannot preserve owner context must fail explicitly; passing tenant as an ordinary SQL condition is insufficient. [F014]

Add owner information to query timing events, logs, scheduler notifications and API job records. Inspect exported filenames and shared output paths for collisions, without changing destination schemas merely because ownership is tenant-scoped. Verify registry lifecycle and scheduler ownership across multiple server processes. [F025–F028]

### 3.7 Migration, sequencing and non-goals

1. Verify deployed DDL, defaults, indexes, unique keys, triggers, grants, sequences and foreign keys. The repo has sample query inserts but no canonical query-table CREATE statement from the scoped search. [F020]
2. Specify tenant row shape and registry/repository interfaces, preserving legacy overrides.
3. Propagate owner through all execution, result-cache and background boundaries before enabling tenant traffic.
4. Add tenant CRUD, collection listing, execution routes and diagnostics.
5. Verify with public plus two tenant schemas sharing slug names; retain public regression coverage.
6. Roll out with an explicit allowlist, observe discovery and execution, then expand.

No automatic movement/deletion of public queries, schema-wide rewriting of SQL, migration of all datasource/credential/process-variable tables, mandatory PBAC membership or new cross-schema data restriction is included. Data migration, if requested, needs explicit row mappings, verification and rollback records. A tenant table being a “copy” does not establish that copied defaults or foreign keys point to tenant-local objects.

### 3.8 Required validation

| Scenario | Required assertion |
|---|---|
| Legacy defaults and overrides | Existing execution/CRUD responses and configured storage remain unchanged. |
| Same slug in public/default and two tenants | Each read/write/cache/job resolves the intended owner. |
| Concurrent shared-pool requests | No shared schema/connection mutation or owner leakage. |
| Discovery and allowlist | Cover None, empty, explicit, unknown, incompatible, view, missing marker, denied grants and quoted names. |
| Tenant model and pagination | No persisted/projected/searched `program_slug`; tenant-aware count, fields, sort, metadata and INSERT export. |
| Runtime program | Tenant-derived callback context and provider fallbacks; legacy program behavior retained; `program_id` contract verified. |
| Ownership versus data access | A tenant-owned query may join other schemas under existing DB permissions; cache/job owner stays fixed. |
| Nested definitions | Parent context, aliases, explicit owner addressing and missing-slug behavior remain unambiguous; no implicit cross-owner search. |
| Routes and selectors | Detect `management` collision, slash/suffix issues and conflicting selectors; routing metadata never leaks into filters. |
| Cache lifecycle | Same SQL across owners, revision/invalidation, refresh, threaded writes and legacy keys behave as specified. |
| Scheduler | Startup, live sync, deletion/pause/resume, refresh and notifications preserve owner. |
| Remote execution | Transport and worker preserve owner; unsupported workers do not execute the default store by mistake. |
| Existing authorization | Allowlist and current controls work with PBAC disabled or enabled; no new membership prerequisite. |
| Observability | HTTP, programmatic, scheduled and remote events identify the owner. |

Use existing concurrency and pagination fixtures as starting points, plus the located PBAC, scheduler and multi-query suites. Include real PostgreSQL/Redis integration coverage; fake-model tests cannot establish schema isolation or deployed grants. No runtime tests were executed during this documentation-only proposal. [F021]

## 4. Confidence Map

| ID | Claim | Evidence | Confidence |
|---|---|---|---|
| C1 | Current slug lookup uses one configured QueryModel schema and does not use the program argument to choose storage. | F001, F012, F017 | high |
| C2 | Server startup currently preloads datasources; tenant discovery and a registry would be new startup behavior. | F002, F003 | high |
| C3 | Tenant tables without program_slug require changes to persistence fields, pagination, metadata and INSERT exports. | F004, F015, F024 | high |
| C4 | Schema selection must avoid shared mutable model metadata to preserve concurrent query isolation. | F001, F021, F023 | high |
| C5 | The current cache path accepts provider-specific hashes with no explicit tenant namespace. | F006, F010, F019, F029 | high |
| C6 | Multi-query parent, child and remote dispatch paths must carry tenant identity explicitly. | F007, F014, F027 | high |
| C7 | Scheduler startup, live sync, job IDs, execution kwargs and serialization must become tenant-aware. | F013, F025, F026 | high |
| C8 | Removing program_slug changes custom filter context and some non-SQL target fallbacks unless a compatibility adapter is designed. | F019, F029 | high |
| C9 | Existing policy checks use slug resource names and PBAC is optional; tenant loading can preserve current access controls, as the user selected. | F016, F017, F027 | high |
| C10 | A shared repository plus immutable tenant context is the preferred way to cover the identified lookup paths without per-request schema mutation. | F001, F003, F015, F021 | medium |
| C11 | A central tenant-aware result-cache key wrapper is preferable to modifying every provider checksum independently. | F006, F010, F019, F029 | medium |
| C12 | Production schema migration details and the remote worker contract are not sufficiently established by this repository trace. | F014, F020, F022 | medium |

Nine high-confidence source claims and three medium-confidence design/evidence-gap claims. User decisions settle product direction; they do not raise confidence in untested ORM, worker or production behavior. Overall confidence remains **medium** because deeper verification reached the research budget boundary.

## 5. Resolved Questions and Specification Follow-ups

### Resolved user questions

- [x] **U1** — Use one tenant handler for single/multi execution, GET collection listing, and existing management endpoints for CRUD with a tenant selector.
  User answer: “Yes”

- [x] **U2** — Preserve existing QS_QUERIES_SCHEMA and QS_QUERIES_TABLE overrides for legacy/default calls. Without overrides they continue to use public.queries.
  User answer: “preserve”

- [x] **U3** — Derive runtime program_slug from the tenant name without persisting that column. Retain program_id as the working DDL assumption; the user qualified retention as probable.
  User answer: “yes, derived from tenant name, and yes, probably tenant tables will retain program_id”

- [x] **U4** — The initialization allowlist plus current access controls is sufficient for this release. Do not introduce mandatory tenant-membership authorization or require PBAC to enable tenant queries.
  User answer: “sufficient for now”

- [x] **U5** — Tenant denotes structural ownership of the saved definition, not a restriction on schemas queried for the client. Cross-schema SQL and data-source access remain subject to existing access controls. Do not infer a ban on cross-tenant data access or inspect SQL to enforce ownership.
  User answer: “tenant's ownership is structural, not related if query cross tenants, is about a client accessing a data for their consumpcion, unrelated if internal query lands across different schemas.”

### Proposed baselines and technical follow-ups

- Startup registry discovery with on-demand definitions is a proposed baseline, not an explicit user selection. Runtime refresh can be added separately.
- Retain `program_id` provisionally; verify the actual tenant DDL before fixing its required/default behavior.
- Specify precise selector precedence, child-definition syntax, explicit-public behavior and reserved route names through tests.
- Verify installed asyncdb schema mechanics, production schema/grants and the external worker contract.
- Complete deeper output/custom-filter/external-caller review during specification; these areas were not fully inspected within the 40-file research limit.

These are specification tasks and declared assumptions, not unresolved requests to approve the research again.

## 6. Recommended Next Step

**`/sdd-spec FEAT-176`** — the impact synthesis is approved and the main product contracts are settled. Turn this proposal into a specification with explicit checks for the technical gaps above, then split implementation into registry/persistence, API/CRUD, execution/cache, scheduler/worker and verification workstreams.

Use `/sdd-brainstorm FEAT-176` only if specification work exposes a material design fork. This change is not suitable for a single localized task.

## 7. Research Audit

| Artifact | Location |
|---|---|
| Source | [source.md](../state/FEAT-176/source.md) |
| Approved plan and follow-ups | [research_plan.json](../state/FEAT-176/research_plan.json) |
| Findings | [30 finding digests](../state/FEAT-176/findings/) |
| Historical synthesis review | [research-review.md](../state/FEAT-176/research-review.md) |
| User decisions | [decisions.md](../state/FEAT-176/decisions.md) |
| Structured synthesis | [synthesis.json](../state/FEAT-176/synthesis.json) |
| State | [state.json](../state/FEAT-176/state.json) |
| Artifact checks | [validation.json](../state/FEAT-176/validation.json) |

Research counters: 40 targeted file reads / 40, 23 searches / 25, 4 Git calls / 10, 271.065 seconds / 300. Wiki orientation and upstream documentation are supplemental. Thirty grouped research topics produced 80 initial localization entries; subsequent artifact validation is separate from research counters.

Research was bounded at the file limit: primary paths were localized, while deployed DDL/grants, ORM schema mechanics, worker implementation, output paths and application-specific callers need deeper verification. No live DB, Redis or external worker was exercised. Artifact validation checks citation references, symbol names, line ranges, JSON schemas and links; it is not runtime certification.

## 8. Provenance

Generated using the repository's [`sdd-proposal` workflow](../../.claude/commands/sdd-proposal.md), proposal template and schema version 1.0. Synthesis approval and numbered user answers are recorded in the audit. The proposal remains `review` because final proposal acceptance is distinct from synthesis approval; this does not prevent delivery or the workflow-required commit.
