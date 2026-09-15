---
id: F015
query_id: Q015
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F015 — crud

## Summary

Every CRUD branch selects QueryModel; listing, count, schema metadata and INSERT export also use it. tenant must be extracted before model validation and equality filters. Default listing explicitly projects program_slug.

## Citations

- path: `querysource/handlers/manager.py`
  lines: 71-131
  symbol: `QueryManager.get`

- path: `querysource/handlers/manager.py`
  lines: 169-205
  symbol: `QueryManager._paginate_list`

- path: `querysource/handlers/manager.py`
  lines: 277-289
  symbol: `QueryManager.patch`

- path: `querysource/handlers/manager.py`
  lines: 341-349
  symbol: `QueryManager.delete`

- path: `querysource/handlers/manager.py`
  lines: 414-453
  symbol: `QueryManager.put`

- path: `querysource/handlers/manager.py`
  lines: 461-495
  symbol: `QueryManager.post`

- path: `querysource/handlers/manager.py`
  lines: 47-69
  symbol: `QueryManager.get_query_insert`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
