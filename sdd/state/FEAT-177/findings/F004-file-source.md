---
id: F004
query_id: Q004
type: read
intent: local file source
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F004 — local file source

## Summary

FileSource (dispatched from the `files:` block, not SOURCE_REGISTRY) reads a local path with masks, supports only text/csv and Excel MIME types (zip/gz aware); any other mime raises ValueError. No parquet support. Extra options are forwarded to pandas readers as **params.

## Citations

- path: `querysource/queries/multi/sources/file.py`
  lines: 21-51
  symbol: `FileSource.__init__`
  excerpt: pops path/mime; resolves masks

- path: `querysource/queries/multi/sources/file.py`
  lines: 69-105
  symbol: `FileSource.fetch`
  excerpt: csv/excel only; ValueError otherwise

- path: `querysource/queries/multi/sources/file.py`
  lines: 12-18
  symbol: `excel_based`
  excerpt: mime tuple reused by S3Source

