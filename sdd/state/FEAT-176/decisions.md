# FEAT-176 — User decisions

Synthesis approved before Q&A. The following answers resolve the product direction; assumptions remain explicitly labeled.

## U1

**Question:** Should tenant execution use one v3-style handler for single and multi queries, with CRUD kept on the management API using a tenant selector; what should GET on the tenant collection return?

**User answer (verbatim):** Yes

**Resolution:** Use one tenant handler for single/multi execution, GET collection listing, and existing management endpoints for CRUD with a tenant selector.

## U2

**Question:** For deployments overriding QS_QUERIES_SCHEMA or QS_QUERIES_TABLE, should legacy calls retain that configured default or become strictly public.queries?

**User answer (verbatim):** preserve

**Resolution:** Preserve existing QS_QUERIES_SCHEMA and QS_QUERIES_TABLE overrides for legacy/default calls. Without overrides they continue to use public.queries.

## U3

**Question:** Should the tenant name replace program_slug for filter callbacks and provider fallbacks, and should tenant rows retain the existing program_id column?

**User answer (verbatim):** yes, derived from tenant name, and yes, probably tenant tables will retain program_id

**Resolution:** Derive runtime program_slug from the tenant name without persisting that column. Retain program_id as the working DDL assumption; the user qualified retention as probable.

## U4

**Question:** What authorizes a caller to use a tenant: the deployment allowlist alone, or tenant membership enforced by PBAC or another existing identity source?

**User answer (verbatim):** sufficient for now

**Resolution:** The initialization allowlist plus current access controls is sufficient for this release. Do not introduce mandatory tenant-membership authorization or require PBAC to enable tenant queries.

## U5

**Question:** Should discovery load only a tenant registry and scheduled definitions at startup, or eagerly load all tenant query definitions; are runtime tenant refresh and cross-tenant child queries required in this release?

**User answer (verbatim):** tenant's ownership is structural, not related if query cross tenants, is about a client accessing a data for their consumpcion, unrelated if internal query lands across different schemas.

**Resolution:** Tenant denotes structural ownership of the saved definition, not a restriction on schemas queried for the client. Cross-schema SQL and data-source access remain subject to existing access controls. Do not infer a ban on cross-tenant data access or inspect SQL to enforce ownership.

**Proposed baseline:** Startup discovery with on-demand definition reads is the proposed baseline, not an explicitly confirmed loading preference. Child definition lookup inherits its owner context when not specified; reading data across schemas does not change the owner.
