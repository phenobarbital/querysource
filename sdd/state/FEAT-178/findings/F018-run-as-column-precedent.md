---
id: F018
query_id: Q019
type: read
intent: U8 — precedent for adding a column (+ audit) to the queries tables
executed_at: 2026-09-30T14:46:00Z
parent_id: F017
depth: 2
---
# F018 — Column-addition precedent (FEAT-151) and audit patterns
## Summary
- The repo has no canonical DDL for `public.queries` (FEAT-176 F020). Schema changes are shipped as documented SQL in `docs/PER_TENANT_QUERIES.md`: a "Provisional DDL gate" CREATE TABLE for tenant stores (`"{schema}".queries`) and a "Legacy store migration" `ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition ...`.
- That precedent is tolerant: reads on a store without the column return a default, and writes omit an empty value, so un-migrated stores keep working.
- The scheduler definition is read from `attributes.scheduler` (`QSScheduler`, scheduler.py:295-301). `QueryModel` also has a `dwh_scheduler` jsonb field. Neither holds a run-as user today.
- Audit pattern available to mirror: navigator-auth's `auth.user_vault_audit (user_id, key, operation, key_version, session_id)`, written on every vault operation. querysource's `LoggingService.request_info` (handlers/log.py:76-109) builds per-request audit info that includes verified ownership fields.
## Citations
- path: `docs/PER_TENANT_QUERIES.md`
  lines: 35-75
  excerpt: |
    CREATE TABLE "{schema}".queries ( query_slug VARCHAR PRIMARY KEY, attributes JSONB, ... );
    ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition TEXT[] DEFAULT '{}'::text[];
- path: `sdd/state/FEAT-176/findings/F020-ddl-gap.md`
  excerpt: no canonical CREATE TABLE for query definitions in repo
- path: `querysource/scheduler/scheduler.py`
  lines: 295-301
  excerpt: 'scheduler_def = attributes.get("scheduler")'
- path: `querysource/models.py`
  lines: 93
  symbol: `QueryModel.dwh_scheduler`
- path: `querysource/handlers/log.py`
  lines: 76-109
  symbol: `LoggingService.request_info`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/identity/store.py`
  excerpt: audit pattern (auth.user_vault_audit via navigator_session vault)
