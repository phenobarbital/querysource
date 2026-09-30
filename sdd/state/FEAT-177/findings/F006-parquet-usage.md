---
id: F006
query_id: Q006
type: grep
intent: existing parquet handling
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F006 — existing parquet handling

## Summary

Parquet is already used in the codebase only for WRITING: ToS3 destination writes .parquet via df.to_parquet(engine='pyarrow'), and the Redis cache serializes rows to parquet with pyarrow.parquet (lazy import). No MultiQS source reads parquet today.

## Citations

- path: `querysource/queries/multi/destinations/s3.py`
  lines: 28-70
  symbol: `ToS3 format detection`
  excerpt: csv/csv.gz/parquet/xlsx by extension

- path: `querysource/queries/multi/destinations/s3.py`
  lines: 141-143
  symbol: `ToS3`
  excerpt: df.to_parquet(buf, index=False, engine='pyarrow')

- path: `querysource/utils/cache_serialization.py`
  lines: 6-38
  symbol: `PARQUET_MAGIC / is_parquet_payload`
  excerpt: pyarrow.parquet lazy import

