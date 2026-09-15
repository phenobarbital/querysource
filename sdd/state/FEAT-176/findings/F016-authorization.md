---
id: F016
query_id: Q016
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F016 — authorization

## Summary

Credentials are resolved per user/profile/default, not per query-storage schema. Runtime policy enforcement uses resource names from handlers. Deployment tenant allowlisting alone cannot define end-user authorization.

## Citations

- path: `querysource/auth/credentials.py`
  lines: 17-37
  symbol: `ResolvedCredentials`

- path: `querysource/handlers/abstract.py`
  lines: 324-355
  symbol: `AbstractHandler._enforce_pbac`

- path: `querysource/handlers/abstract.py`
  lines: 425-455
  symbol: `AbstractHandler._enforce_pbac`

- path: `querysource/handlers/service.py`
  lines: 186-192
  symbol: `QueryService.query`

- path: `querysource/handlers/multi.py`
  lines: 50-71
  symbol: `QueryHandler._preflight_multiquery`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
