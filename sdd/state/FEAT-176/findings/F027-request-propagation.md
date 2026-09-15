---
id: F027
query_id: Q027
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: F015
depth: 1
---

# F027 — request-propagation

## Summary

Single-query handler combines URL query parameters and body into conditions. Multi-query handler constructs MultiQS without passing request explicitly in the inspected call; tenant identity and authorization context must survive this transition.

## Citations

- path: `querysource/handlers/service.py`
  lines: 259-269
  symbol: `QueryService.query`

- path: `querysource/handlers/abstract.py`
  lines: 264-279
  symbol: `AbstractHandler.get_source`

- path: `querysource/handlers/multi.py`
  lines: 214-230
  symbol: `QueryHandler.query`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
