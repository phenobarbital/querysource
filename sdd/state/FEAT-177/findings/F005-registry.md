---
id: F005
query_id: Q005,Q008
type: read+grep
intent: registration and dispatch
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F005 — registration and dispatch

## Summary

New sources are exposed via SOURCE_REGISTRY (type name as used in YAML `sources:` list) and __all__. MultiQS iterates self._sources entries {type: config}, looks the class up in SOURCE_REGISTRY, auto-names duplicates Type_1, Type_2, and instantiates cls(name, config, request, queue). ComponentRegistry merges SOURCE_REGISTRY into the component catalog.

## Citations

- path: `querysource/queries/multi/sources/__init__.py`
  lines: 1-35
  symbol: `SOURCE_REGISTRY`
  excerpt: AirtableSource, SharepointSource, SmartSheetSource, S3Source, TableSource

- path: `querysource/queries/multi/__init__.py`
  lines: 535-557
  symbol: `MultiQS (thread dispatch)`
  excerpt: files: -> FileSource; sources: -> SOURCE_REGISTRY lookup, DriverError on unknown type

- path: `querysource/queries/multi/registry.py`
  lines: 148-153
  symbol: `ComponentRegistry`
  excerpt: components.update(SOURCE_REGISTRY)

