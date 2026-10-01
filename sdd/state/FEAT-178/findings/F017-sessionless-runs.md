---
id: F017
query_id: Q018
type: read
intent: U7 — scheduled MultiQS runs and where a user_id can live
executed_at: 2026-09-30T14:42:55Z
parent_id: null
depth: 1
---
# F017 — Scheduled MultiQS runs have no user; jobs are rebuilt from the DB
## Summary
- `scheduled_multiqs_job` constructs `MultiQS(slug=slug, tenant=tenant)` with no request, user_session or user. Its kwargs are `slug`, `notification_manager`, `owner` (a tenant owner envelope) and `job_id`.
- Jobs are added in `QSScheduler` (`add_job(... kwargs={...})`) both at startup and through `SchedulerJobsView.post`. That handler takes only `slug` and `tenant` and treats the `public.queries` row as the source of truth.
- `QueryModel` has `created_by: int` / `updated_by: int` (the author, with a TODO noting it isn't validated).

Consequence: a `run_as_user_id` held only in APScheduler kwargs would be lost on restart. It must be persisted with the slug's scheduler definition, set from the authenticated registrant's session, then threaded job → MultiQS → OneDriveSource.

`IdentityStore(db_pool, cipher)` needs only an asyncpg pool to the auth DB (`app["authdb"]`) and `IdentityCipher` (vault keys from env). Both are reachable from the scheduler, which starts inside the app.
## Citations
- path: `querysource/scheduler/jobs.py`
  lines: 104-158
  symbol: `scheduled_multiqs_job`
- path: `querysource/scheduler/scheduler.py`
  lines: 330-360, 586
  symbol: `QSScheduler` add_job (multi path), `QSScheduler.register_slug`
- path: `querysource/handlers/scheduler.py`
  lines: 95-135, 208-260
  symbol: `SchedulerJobsView._serialize_job`, `SchedulerJobsView.post`
- path: `querysource/models.py`
  lines: 100, 106
  symbol: `QueryModel.created_by`, `QueryModel.updated_by`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/identity/store.py`
  lines: 49-53, 222
  symbol: `IdentityStore.__init__`, `IdentityStore.get_by_provider`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/handlers/user_identities.py`
  lines: 83-90
  symbol: `BaseIdentityView._store` (app["authdb"])
