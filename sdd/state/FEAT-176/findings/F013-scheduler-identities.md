---
id: F013
query_id: Q013
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F013 — scheduler-identities

## Summary

Scheduler startup hardcodes public.queries; live refresh uses QueryModel. Query, multi and refresh jobs use slug-only IDs and kwargs, making same-slug tenant jobs collide unless both are extended.

## Citations

- path: `querysource/scheduler/scheduler.py`
  lines: 523-546
  symbol: `QSScheduler.startup`

- path: `querysource/scheduler/scheduler.py`
  lines: 379-381
  symbol: `QSScheduler._slug_job_ids`

- path: `querysource/scheduler/scheduler.py`
  lines: 383-410
  symbol: `QSScheduler._fetch_slug_row`

- path: `querysource/scheduler/scheduler.py`
  lines: 442-481
  symbol: `QSScheduler.register_slug`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
