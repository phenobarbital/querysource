---
id: F029
query_id: Q029
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: F019
depth: 1
---

# F029 — provider-fallbacks

## Summary

Scoped source search shows program-derived target fallbacks in RethinkDB, Influx and ArangoDB. Other provider hashes use slug/conditions/table path, so a central tenant cache envelope avoids missing provider-specific overrides.

## Citations

- path: `querysource/providers/rethink.py`
  lines: 83-89
  symbol: `rethinkProvider.checksum`

- path: `querysource/providers/influx.py`
  lines: 51-64
  symbol: `influxProvider.checksum`

- path: `querysource/providers/arangodb.py`
  lines: 72-87
  symbol: `arangodbProvider.checksum`

- path: `querysource/providers/deltatbl.py`
  lines: 95-98
  symbol: `deltatblProvider.checksum`

- path: `querysource/providers/iceberg.py`
  lines: 89-92
  symbol: `icebergProvider.checksum`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
