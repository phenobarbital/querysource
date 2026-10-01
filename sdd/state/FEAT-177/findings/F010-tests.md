---
id: F010
query_id: Q012
type: glob+read
intent: test patterns
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F010 — test patterns

## Summary

Per-source unit tests live flat in tests/ (test_source_s3.py, test_source_table.py, ...), constructing the source with request=None and asyncio.Queue(), mocking aioboto3 via patch; registry tests assert presence in SOURCE_REGISTRY and __all__.

## Citations

- path: `tests/test_source_s3.py`
  lines: 1-130
  symbol: `TestS3Source`
  excerpt: config parsing, key building, csv/gz/excel parsing, ImportError, ValueError

- path: `tests/test_source_registry.py`
  lines: 10-74
  symbol: `-`
  excerpt: registry + __all__ assertions

