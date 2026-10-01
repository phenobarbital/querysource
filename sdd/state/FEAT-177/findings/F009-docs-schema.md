---
id: F009
query_id: Q009
type: read+glob
intent: component docs/schema
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F009 — component docs/schema

## Summary

Each component has a generated JSON schema under generated/<Component>.json (S3Source.json, FileSource.json, ...) produced by the `generate-multiquery-docs` CLI (querysource.cli.generate_docs:main); companion *.catalog.yaml files supply prose/constraints on top of code introspection (only query.catalog.yaml exists in sources/). Schema fixes are committed alongside source changes (c112ad9).

## Citations

- path: `generated/S3Source.json`
  lines: -
  symbol: `-`
  excerpt: generated schema

- path: `pyproject.toml`
  lines: 174
  symbol: `generate-multiquery-docs`
  excerpt: querysource.cli.generate_docs:main

- path: `querysource/queries/multi/sources/query.catalog.yaml`
  lines: 1-20
  symbol: `-`
  excerpt: companion doc model

