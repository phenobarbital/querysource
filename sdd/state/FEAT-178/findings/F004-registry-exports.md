---
id: F004
query_id: Q004
type: read
intent: registration / exports
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F004 — Source registration
## Summary
Sources are exported in `__all__` and registered in `SOURCE_REGISTRY` (type name as used in YAML → class). A new source needs an import, an `__all__` entry and a registry entry.
## Citations
- path: `querysource/queries/multi/sources/__init__.py`
  lines: 1-35
  symbol: `SOURCE_REGISTRY`
  excerpt: |
    SOURCE_REGISTRY: dict = {
        "AirtableSource": AirtableSource,
        "SharepointSource": SharepointSource,
        ...
