---
id: F026
query_id: Q026
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: F013
depth: 1
---

# F026 — scheduler-api

## Summary

The scheduler management API extracts only slug from job kwargs, and POST forwards only slug to register_slug. List/get/delete/pause/resume need tenant-aware addressing and access checks.

## Citations

- path: `querysource/handlers/scheduler.py`
  lines: 77-105
  symbol: `SchedulerJobsView._serialize_job`

- path: `querysource/handlers/scheduler.py`
  lines: 189-224
  symbol: `SchedulerJobsView.post`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
