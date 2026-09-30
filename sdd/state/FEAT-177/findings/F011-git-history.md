---
id: F011
query_id: Q013
type: git_log
intent: recent activity
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F011 — recent activity

## Summary

sources/ is actively maintained: masks support added 2026-06-29 (6d8681b, 30cccae) and SharepointSource refactors (June/July); latest touches 2026-09-30 (76b8851 slug-definition handoff, a4536ff empty-result fix). Nothing in flight on S3Source or parquet.

## Citations

- path: `querysource/queries/multi/sources/`
  lines: -
  symbol: `6d8681b`
  excerpt: refactor(sources): flowtask-style masks dict

- path: `querysource/queries/multi/sources/`
  lines: -
  symbol: `76b8851`
  excerpt: perf(multi): load a slug definition once per request

- path: `querysource/queries/multi/sources/`
  lines: -
  symbol: `a4536ff`
  excerpt: fix on filter parser and empty results

