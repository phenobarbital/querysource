---
id: F007
query_id: Q007
type: read
intent: catalog/docs for sources
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F007 — Component docs generation
## Summary
Only `query.catalog.yaml` exists as a source companion; SharepointSource has no catalog YAML — its `generated/SharepointSource.json` is produced by introspection (`extract_source_schema` parses `creds.get(...)`/`source.get(...)` patterns into dotted attributes) via `generate-multiquery-docs` (pre-commit hook). A new source must keep the same `options.get('credentials')` / `source.get(...)` idiom so introspection works, and regenerate `generated/`.
## Citations
- path: `querysource/queries/multi/sources/query.catalog.yaml`
  lines: 1-17
- path: `querysource/queries/multi/_introspect.py`
  lines: 950-958
  symbol: `extract_source_schema`
- path: `generated/SharepointSource.json`
  lines: 1-40
- path: `.pre-commit-config.yaml`
  lines: 15-17
  symbol: generate-multiquery-docs hook
- path: `querysource/cli/generate_docs.py`
  symbol: `main`
