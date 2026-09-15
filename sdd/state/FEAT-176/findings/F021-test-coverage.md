---
id: F021
query_id: Q021
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F021 — test-coverage

## Summary

Existing tests cover shared-model concurrency and public-route pagination with fake DB pools. Search results locate PBAC, scheduler and multi-query tests; they do not establish schema-per-tenant isolation coverage.

## Citations

- path: `tests/test_queryslug_concurrency.py`
  lines: 78-89
  symbol: `test_new_pattern_is_race_free`

- path: `tests/handlers/conftest.py`
  lines: 152-173
  symbol: `seeded_query_slugs`

- path: `tests/handlers/test_querymanager_pagination.py`
  lines: 275-301
  symbol: `TestQueryManagerListPagination.test_default_pagination_returns_envelope`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
