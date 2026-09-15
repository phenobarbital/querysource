---
id: F028
query_id: Q028
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: F006
depth: 1
---

# F028 — observability

## Summary

QS execution telemetry currently includes slug and timing only. LoggingService records request paths, which is insufficient to identify tenant for scheduled or programmatic execution.

## Citations

- path: `querysource/queries/qs.py`
  lines: 445-458
  symbol: `QS.query`

- path: `querysource/handlers/log.py`
  lines: 75-84
  symbol: `LoggingService.request_info`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
