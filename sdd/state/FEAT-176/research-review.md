# FEAT-176 — Per-tenant queries: research and synthesis review

Status: historical research review; synthesis approved. Subsequent user decisions supersede the provisional alternatives below. See [decisions](decisions.md) and the [final proposal](../../proposals/per-tenant-queries.proposal.md), especially the structural-ownership and existing-access-control decisions.

- Source: [original request](source.md)
- Baseline: `dev`, commit `aefa01e37e39c836ed18e272ad2c98972b242c06`.
- Overall confidence: **medium**. Current code paths are well localized; several behavioral choices and deployment contracts remain open.
- Method: wiki orientation, scoped source searches, targeted file reads, history and PostgreSQL documentation. No application or database changes.
- Evidence: [structured synthesis](synthesis.json), [research state](state.json), and [finding digests](findings/).

## 1. Main conclusion

This feature changes query identity from a bare slug to **tenant plus slug** throughout the system. Selecting another table in the HTTP handler is insufficient: definition lookup also happens inside single-query execution, local multi-query children, multi-query parent loading and scheduler refresh. Results, policy resources and scheduled jobs need the same identity. [F001, F006, F007, F013, F014, F016]

The recommended direction is a shared definition repository receiving an immutable tenant context, backed by a registry of discovered schemas. Every entry point resolves that context before database access or cache lookup. The persistence contract for tenant rows excludes `program_slug`; public rows keep their existing fields. This is a design recommendation, not an existing repository abstraction. [F004, F015, F021]

### Corrections to the initial assumptions

1. **Public is the default, not the only configurable storage location.** `QS_QUERIES_SCHEMA` and `QS_QUERIES_TABLE` already configure the model. Fixing all legacy APIs to literal `public.queries` changes behavior for deployments using those overrides. [F017]
2. **Normal server startup does not load all saved queries.** It loads `public.datasources`; slug lookup subsequently reads the model on demand. The optional scheduler separately scans `public.queries`. Registry discovery and eager definition loading are different behaviors. [F003, F012, F013]
3. **Program context reaches execution.** Providers and compiled parsers consume `program_slug`, and some providers use it for a target database or bucket fallback. Removing the database column needs an explicit runtime compatibility decision. [F019, F029]
4. **The definition cache and result cache are different.** `SLUG_CACHE` is declared, but the inspected lookup path does not use it. The active result cache uses provider checksums in Redis. Do not design a migration around an assumed active definition cache. [F010, F012]
5. **A shared metadata race was already fixed.** Lookups pass `_connection=conn` instead of mutating model class metadata. Mutating the shared schema during a request would recreate the same class of concurrency risk. [F021, F023]

## 2. Impact map

“Change” means the tenant release needs a concrete change at that boundary. “Review” means verify the contract and change only where the selected design requires it.

| Area | Files and symbols | Required work | Evidence |
|---|---|---|---|
| Initialization and routes | `querysource/services.py`: `QuerySource.__init__`, `setup`, `qs_start` | **Change:** initialization allowlist, registry installation, tenant routes, startup ordering. Consider singleton reinitialization and multiple applications in one process. | F002–F003 |
| Main database lifecycle | `querysource/connections.py`: `QueryConnection.start`, cache accessors | **Change/review:** discovery after main DB is available; registry access in server and programmatic execution. Master and lazy modes differ. | F003, F012 |
| Definition lookup | `querysource/interfaces/connections.py`: `Connection.get_query_slug`, `get_slug` | **Change:** tenant argument, validated storage selection, consistent missing-tenant/slug errors. The current `program` parameter does not select a schema. | F001, F012 |
| Persistence fields | `querysource/models.py`: `QueryModel` | **Change:** tenant model/row mapping without persisted `program_slug`; preserve the public model. Decide `program_id`. | F004 |
| CRUD and export | `querysource/handlers/manager.py`: GET, PATCH, DELETE, PUT, POST, `get_query_insert` | **Change:** resolve tenant for every branch, metadata and SQL export; strip routing attributes before model operations. | F015 |
| Pagination | `querysource/handlers/_pagination.py`: field sets and SQL builders | **Change:** tenant-specific projection, filter, sort, search and count SQL. Static sets currently include `program_slug`. | F024 |
| Single-query execution | `querysource/handlers/service.py`, `handlers/abstract.py`, `queries/qs.py` | **Change:** carry context through execution, test, column inspection, errors, policies and cache access. | F006, F016, F027 |
| Query base classes | `querysource/interfaces/queries.py`, `queries/base.py` | **Change/review:** explicit immutable context rather than incidental conditions; preserve output and lifecycle behavior. | F008, F010 |
| Multi-query parent | `querysource/handlers/multi.py`, `queries/multi/__init__.py` | **Change:** parent definition lookup, child inheritance, saved/inline pipelines, single-query fallback and resolved child authorization. | F014, F027 |
| Multi-query child | `querysource/queries/obj.py`, `queries/multi/sources/query.py`, `sources/executors.py` | **Change:** context through local and remote dispatch, removed from query conditions. Preserve aliases separately from actual slugs. | F007, F014 |
| Result caching | `queries/qs.py`, `interfaces/queries.py`, `connections.py`; provider checksum implementations | **Change:** a tenant-aware key boundary used for reads, writes, refresh and invalidation. Review all checksum overrides; a central wrapper reduces duplication. | F006, F010, F019, F029 |
| Provider program behavior | `providers/abstract.py`; `providers/rethink.py`, `influx.py`, `arangodb.py` | **Change/review:** explicit program fallback semantics independent from storage schema. | F019, F029 |
| Compiled parser callbacks | `parsers/abstract.pyx`, `abstract.pxd`, `pgsql.pyx`, `sql.pyx`, `cql.pyx`, `sosql.pyx` | **Change/review:** runtime program/tenant context consumed by filters; rebuild Cython modules if their interface changes. Callback sites beyond PostgreSQL were search-located, not deeply inspected. | F019 |
| Scheduled definitions | `scheduler/scheduler.py`: startup, row registration, `_fetch_slug_row`, `register_slug`, `_slug_job_ids` | **Change:** scan registered tenant stores, preserve public jobs, tenant-qualified IDs and live sync. | F013 |
| Background execution | `scheduler/jobs.py`, `scheduler/notifications.py` | **Change:** tenant in job payloads and execution; callback compatibility for existing integrations. | F025 |
| Scheduler API | `handlers/scheduler.py`: serialization and management methods | **Change:** tenant in list/detail, sync, delete and pause/resume authorization. | F026 |
| Policy and credentials | `handlers/abstract.py`, `handlers/multi.py`, `auth/credentials.py`, `conf.py` | **Change/review:** tenant-aware resource identities and policy decisions; define the source of membership. Keep target datasource credentials separate unless expressly expanded. | F016–F017 |
| Metrics and audit | `queries/qs.py`, `handlers/log.py`, scheduler notifications | **Change:** explicit tenant identity on events and failures, including requestless execution. Review exported filenames/output paths for collision risk during implementation; this trace did not establish an existing file collision. | F028 |
| Raw execution and introspection | `queries/models.py`, `queries/executor.py`, `datasources/introspection.py` | **Review:** keep raw datasource operations distinct from metadata storage discovery. The existing introspector supplies a SQL pattern, not a ready-made tenant registry. | F005, F009, F011 |
| Documentation and tests | pagination/concurrency tests, scheduler docs, Query component catalog | **Change:** tenant contract, fixtures, same-slug isolation and rollout guidance; regenerate component docs from the catalog. | F021–F022 |

The detailed [localization list](synthesis.json) supplies symbol-level ranges. These are inspected locations, not a promise that every listed function must be edited.

## 3. Proposed architecture and invariants

### 3.1 Discovery and registry

Recommended discovery sequence:

1. Use the main PostgreSQL metadata connection after startup has established it.
2. Join information-schema table and column metadata on catalog, schema and table. Select tables named `queries` with a column named `query_slug`.
3. Exclude system/temporary schemas. Distinguish base tables from views; the requested contract says table.
4. Apply the initialization allowlist before accepting tenant stores.
5. Validate the accepted table against the tenant persistence contract and record compatibility/status.
6. Publish one stable registry snapshot before starting tenant scheduled jobs or serving tenant requests.

The `query_slug` column is the **discovery criterion**, not proof that a table has every column required by query execution or CRUD. A minimal two-column table may match discovery and still be incompatible. Keep discovered candidates distinguishable from operationally supported stores. [F004, F011]

PostgreSQL exposes only columns visible to the connected role, and column metadata also includes views. Discovery can inventory visible candidates, not prove that no other tenant exists. Use documented schema/table grants and surface discovery failures explicitly. [PostgreSQL columns documentation](https://www.postgresql.org/docs/18/infoschema-columns.html) [F030]

Proposed registry record: canonical schema, table, validated columns, status and diagnostic reason, discovery timestamp/generation. The schema name should remain exact; do not lowercase a quoted PostgreSQL identifier or concatenate unchecked URL text into SQL.

Recommended allowlist semantics, **pending review**:

| Input | Meaning |
|---|---|
| Omitted / `None` | Discover all qualifying non-public tenant stores visible to the service. |
| Empty collection | Enable no non-public tenant stores. |
| Explicit collection | Enable only exact matching tenant names. |
| Unknown allowed name | Record a clear configuration problem; never resolve it to public. |
| Public compatibility store | Keep available independently of the non-public tenant allowlist. |

Start with registry discovery and on-demand query definitions, matching current query loading. Eager preloading would introduce freshness, memory, reload and cross-process invalidation responsibilities that do not currently exist in the inspected definition path. Startup-only discovery is a proposed first-release limit; runtime add/remove/refresh remains a product decision. [F003, F012]

### 3.2 Definition repository and safe schema selection

Use a single storage-selection boundary for list/get/create/update/delete/export, called by HTTP handlers, QS, QueryObject, MultiQS and scheduler. Pass tenant explicitly, alongside the existing per-call connection.

Do not mutate `QueryModel.Meta.schema`, `Meta.name` or `Meta.connection` for each request. Candidate implementations are independently constructed tenant model classes with immutable metadata, or explicitly qualified repository SQL mapped into typed rows. A shared base persistence model with separate public and tenant fields is another option. The installed asyncdb behavior for subclass metadata, field exclusion and schema overrides must be verified before choosing one; this research did not establish those capabilities. [F004, F021]

Use schema-qualified identifiers and parameter binding for data values. Validate schema names against the registry, then quote them through a tested identifier path. The current pagination helper accepts a narrower identifier set than PostgreSQL can represent; do not silently register a name that execution cannot address. Avoid changing pooled connection `search_path` to select a query store. PostgreSQL documents that schemas are not rigid access boundaries and that search-path resolution has trust implications. [PostgreSQL schema documentation](https://www.postgresql.org/docs/18/ddl-schemas.html) [F024, F030]

Unknown, denied or incompatible tenants must never fall back to public. Missing/null selection in a top-level CRUD call selects the public/default contract once the override decision is settled. An empty string and the literal string `"null"` need explicit input validation; neither should accidentally become permission to read public data.

### 3.3 API compatibility

Verified current surfaces:

| Surface | Existing behavior | Tenant-release requirement |
|---|---|---|
| `/api/v2/services/queries` and `/{slug}` | Single-query service; slug GET/POST, PATCH/HEAD columns | Preserve legacy request and response semantics; bind legacy storage according to the public/override decision. |
| `/api/v2/test/queries` and `/{slug}` | Raw and saved query testing | Preserve public behavior; decide tenant equivalents for saved-query inspection. |
| `/api/v3/queries` and `/{slug}{meta}` | Multi-query execution, also stored single-query fallback | Preserve existing pipeline/error/output behavior. |
| `/api/v1/management/queries` and `/{slug}` | CRUD, pagination, metadata and insert export | Add tenant selection for every operation, with absent/null default behavior. |
| `/api/v1/queries/test`, `/run`, `/schema` | Raw driver/datasource operations | Avoid reinterpreting datasource schema as query-storage tenant. |
| `/api/v1/{tenant}/queries/` | New requested family | Finalize collection methods, slug suffix, output suffixes and multi-query behavior. |
| `/api/v1/qs/scheduler/jobs` and `/{job_id}` | Scheduler lifecycle management | Extend tenant-aware identity, visibility and control while retaining public job compatibility. |

Evidence: F002, F005, F009, F013, F015, F026.

A route collision already follows from the requested pattern: tenant `management` would produce `/api/v1/management/queries`, an existing CRUD URL. Decide whether such names are reserved or need a different addressing rule. Registering a generic dynamic route without testing this case can make one tenant unreachable or route it to a legacy handler.

For CRUD, the recommended selector is a query parameter for GET/DELETE and a reserved top-level JSON attribute for writes. If both are supplied, require agreement. A tenant path should be authoritative and conflicting body/query selectors should be rejected. These are proposed rules; the user requested a `tenant` attribute but did not specify its HTTP representation.

Remove tenant routing metadata before equality filtering, persistence validation and execution conditions. Otherwise GET may reject `tenant` as an unknown column, or execution may treat it as query input. Preserve pagination envelopes, headers, empty-list statuses, single-row responses, `:meta` and `:insert`. Test trailing-slash behavior explicitly. [F015, F024, F027]

### 3.4 Program compatibility

The requested schema omits only `program_slug`; the model also has a required `program_id`. Do not remove both by assumption.

There are two distinct identities:

- **Storage tenant:** where the saved definition lives.
- **Execution program:** currently passed into parser filters and used by some provider fallbacks.

Deriving execution program from tenant may be appropriate, but it can change which RethinkDB database, Influx bucket or ArangoDB database gets queried. An in-memory compatibility adapter could supply a program value to providers without persisting `program_slug`; keeping it separately in attributes is another option. Resolve this before migration. Merely allowing the missing property to become `default` or `None` is not a compatibility plan. [F019, F029]

This feature should not automatically rewrite arbitrary SQL, move datasource definitions, change credentials, or move result tables. Storing SQL in a tenant schema does not constrain which schemas that SQL can access.

### 3.5 Execution, caches and policy

Propagate one resolved identity through QS, MultiQS, QueryObject and child executors. A multi-query alias is a result name, not necessarily the stored slug to authorize. Validate the resolved parent and each actual child reference after loading a saved pipeline. The inspected multi handler passes a cached user session but does not explicitly pass the request at its MultiQS construction site; preserving context needs focused coverage. [F014, F016, F027]

Recommended child semantics: omitted child tenant inherits the parent before calling the low-level resolver. Explicit cross-tenant references require a separately authorized design; never implicitly search other tenants or public for a missing slug. Confirm this with the release scope. Programmatic and scheduled calls must supply the same context without depending on an HTTP request.

Use a versioned result-key envelope over tenant identity, slug and the existing provider checksum. Both retrieval and cache writers must use it; also include definition revision/generation or targeted invalidation when definition changes could otherwise leave stale results. Preserve existing public cache keys if required for a gradual rollout, but never let a tenant read an unqualified legacy key. A query revision/target identity and existing user credential scoping may impose further cache dimensions; tenant separation alone does not prove per-user result isolation. [F006, F010, F019, F029]

PBAC currently uses slug resource names and is optional. Add tenant to the resource/context and decision-cache identity without accidentally granting tenant access through old wildcard policies. Resolve whether the service allowlist is sufficient in a trusted deployment or whether user membership is mandatory. Apply that decision consistently to execution, management, listing, inspection, exports, nested queries and scheduler controls. [F016–F017]

### 3.6 Scheduling and remote workers

The scheduler must consume the same registry and repository as request execution. Use an unambiguous tenant-qualified job identifier and pass tenant explicitly to every job callable. Synchronizing/removing one tenant's slug must not remove another tenant's jobs. Update serialization and notification information while preserving existing callback callers. [F013, F025–F026]

Discovery-before-scheduler ordering is required. For multiple web processes, registry snapshots and scheduled work ownership need deployment validation; existing in-memory jobs do not establish distributed single execution.

RemoteExecutor calls the string `querysource.remote.query_handler`. The querysource file inventory in this checkout contains no matching remote module. Treat the actual worker implementation as an external contract gap. Extend the transport payload only together with a compatible worker, and fail explicitly when an old worker cannot honor tenant context. Passing tenant as an ordinary condition is insufficient. [F014]

## 4. Migration and rollout proposal

1. Establish canonical public and tenant DDL from the deployment: columns, defaults, keys, indexes, triggers, ownership, grants, referenced sequences and foreign keys. Repository sample inserts are not sufficient. [F020]
2. Define tenant DDL as public-compatible fields minus the agreed program column(s), with per-table slug uniqueness.
3. Introduce registry and repository behavior while retaining public tests and public data.
4. Add tenant context end-to-end before enabling tenant traffic, especially cache and background boundaries.
5. Provision one test tenant and a separate second tenant with identical slugs; validate against real PostgreSQL and Redis.
6. Roll out using an explicit allowlist, confirm discovery diagnostics and legacy public traffic, then expand.
7. Rollback disables tenant traffic/jobs and reverts application behavior; do not automatically move or drop public or tenant tables.

Automatic copying of all existing public rows, dual writes, and moving existing client queries are not assumed. If data migration is needed, use explicit per-tenant row mapping, counts/checksums and a rollback record. A copied default or foreign key can still reference a public object; inspect it rather than assuming schema cloning yields isolation.

## 5. Validation matrix for implementation

| Scenario | Required assertion |
|---|---|
| Public only, no tenant argument | Existing single/multi execution, CRUD, pagination, metadata and export contracts remain valid. |
| Same slug in public, tenant A and tenant B | Each operation returns/modifies only the selected definition. |
| Concurrent requests using a shared pool | Schema and connection identity never leak between requests; include mixed reads and writes with forced coroutine interleaving. |
| Discovery | Include valid tenant, missing queries table, missing query_slug, view, incompatible fields, quoted name, denied grants and system schema. |
| Allowlist | Distinguish omitted, None, empty and explicit values; unknown/disallowed tenant has no public fallback. |
| CRUD and metadata | All methods plus count/search/sort/fields/:meta/:insert target the selected schema and omit program_slug on tenant persistence. |
| Request selectors | Reject mismatches and invalid types; remove routing metadata from conditions and WHERE filters. |
| Route table | Preserve existing management route, handle reserved names and test trailing slash/output suffix behavior. |
| Cache | Same SQL/conditions in two tenants never shares results; test refresh, mutation invalidation, public legacy key compatibility and threaded writes. |
| Programs and parsers | Assert callback program values and non-SQL datasource fallbacks with no persisted program_slug; rebuild/test affected extensions. |
| Nested execution | Saved/inline pipelines, single fallback, aliases, child inheritance, explicit cross-tenant rejection and authorization at actual child slugs. |
| Scheduler | Same slug creates distinct jobs; startup, sync, delete/pause/resume, refresh and notification retain tenant. |
| Worker transport | Tenant survives serialization; incompatible workers fail rather than executing public. |
| Access control | Listing/count/metadata/CRUD/execute/jobs enforce the chosen policy before data disclosure or cache hits; disabled-PBAC mode behaves intentionally. |
| Observability | Events identify tenant for HTTP, local, scheduled and remote execution. |

Existing starting points include the concurrency regression and management pagination fixtures. Searches also located scheduler, PBAC and multi-query suites. No tests were run because this phase changes research artifacts only; the matrix describes required future verification. [F021]

## 6. Coverage and limitations

The principal definition, CRUD, execution, cache, scheduler and parser boundaries have source evidence. This is not an exhaustive runtime certification.

- Forty distinct files were inspected in targeted ranges; additional files were located by exact search matches. Large outputs were sometimes truncated, and only visible evidence is relied upon.
- Production DDL, current grants, live schema names, actual number of tenants and deployment startup costs were not inspected.
- The installed ORM's schema-selection mechanics and actual worker implementation remain technical follow-ups.
- Output destinations, all application-specific filters, all generated interfaces and every external caller were not inspected in depth.
- No destructive action, runtime implementation, data migration, build or live integration test was performed.
- The research file budget was reached. Broad discovery is complete; unresolved deeper verification is recorded rather than claiming complete production coverage.

## 7. Decisions for synthesis review

### U1

Should tenant execution use one v3-style handler for single and multi queries, with CRUD kept on the management API using a tenant selector; what should GET on the tenant collection return?

- Unified tenant execution; GET collection lists definitions; management remains the CRUD API
- Mirror legacy v2 execution for tenant routes and keep multi execution separate
- Use the tenant collection for CRUD and a distinct execution suffix

### U2

For deployments overriding QS_QUERIES_SCHEMA or QS_QUERIES_TABLE, should legacy calls retain that configured default or become strictly public.queries?

- Preserve configured legacy default; explicit public still means public.queries
- Legacy APIs must always use public.queries; deprecate overrides with migration guidance

### U3

Should the tenant name replace program_slug for filter callbacks and provider fallbacks, and should tenant rows retain the existing program_id column?

- Derive program context from tenant; retain program_id
- Keep program context separate in attributes; retain program_id
- Remove both program columns and define a new callback contract

### U4

What authorizes a caller to use a tenant: the deployment allowlist alone, or tenant membership enforced by PBAC or another existing identity source?

- Shared trusted service: deployment allowlist plus current access controls
- Require tenant membership through PBAC on execution, CRUD, listing and jobs
- Require tenant membership from the host application's authorization layer

### U5

Should discovery load only a tenant registry and scheduled definitions at startup, or eagerly load all tenant query definitions; are runtime tenant refresh and cross-tenant child queries required in this release?

- Startup registry, on-demand definitions, child queries inherit tenant, no cross-tenant children
- Eagerly preload all definitions with startup-only discovery
- Support runtime registry refresh and explicitly authorized cross-tenant children
## 8. Recommendation and provenance

Validate this synthesis, then resolve the five decisions above. The current recommendation is **manual review** of this synthesis and its coverage limits. Use `/sdd-brainstorm FEAT-176` for unresolved choices, followed by `/sdd-spec FEAT-176` once contracts are settled. This is a major-release feature with multiple implementation workstreams, not a single-file task.

Evidence confidence: nine directly supported claims and three design/evidence-gap inferences. Overall confidence remains medium. The authoritative claim map is in [synthesis.json](synthesis.json).

The invoked [.claude/commands/sdd-proposal.md](../../../.claude/commands/sdd-proposal.md) requires a synthesis review gate before Q&A and final proposal rendering. This report makes that review concrete. Final proposal commit follows that workflow after review; this artifact is not an acceptance record.
