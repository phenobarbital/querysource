---
id: F018
query_id: Q018
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F018 — dependencies

## Summary

The project declares APScheduler, psycopg2, navigator-api, jsonschema and navigator-auth. This proposal introduces no dependency. The persistence model imports asyncdb; schema override behavior needs a focused dependency contract check during specification.

## Citations

- path: `pyproject.toml`
  lines: 43-80
  symbol: `dependencies`

- path: `pyproject.toml`
  lines: 112-119
  symbol: `dependencies`

- path: `querysource/models.py`
  lines: 5-13
  symbol: `QueryModel`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
