---
id: F003
query_id: Q003
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F003 — startup

## Summary

QuerySource and QueryConnection are singletons. Connection setup registers its startup before qs_start and scheduler startup. Master mode currently preloads datasources, not all query definitions; lazy mode opens a direct connection.

## Citations

- path: `querysource/services.py`
  lines: 50-105
  symbol: `QuerySource`

- path: `querysource/connections.py`
  lines: 156-209
  symbol: `QueryConnection.start`

- path: `querysource/services.py`
  lines: 312-332
  symbol: `QuerySource.setup`

## Wiki orientation

`file:querysource/services.py` (query score 0.18) supplied orientation; source line reads verified the relevant startup and routing blocks. Scheduler startup stub scored 1.00. The second semantic query mostly returned unrelated SharePoint authentication matches; these were not treated as application tenancy evidence.


## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
