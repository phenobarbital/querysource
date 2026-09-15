---
id: F025
query_id: Q025
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: F013
depth: 1
---

# F025 — background-context

## Summary

All three job functions instantiate QS/MultiQS using only slug; their extra kwargs are ignored. Notifications use an established three-argument callback signature that must remain compatible.

## Citations

- path: `querysource/scheduler/jobs.py`
  lines: 35-46
  symbol: `scheduled_query_job`

- path: `querysource/scheduler/jobs.py`
  lines: 50-90
  symbol: `scheduled_multiqs_job`

- path: `querysource/scheduler/jobs.py`
  lines: 94-121
  symbol: `cache_refresh_job`

- path: `querysource/scheduler/notifications.py`
  lines: 37-52
  symbol: `NotificationManager.notify`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
