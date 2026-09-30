---
id: F006
query_id: Q006
type: grep
intent: how source dispatch references SharePoint
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 1
---
# F006 — MultiQS dispatch of `sources:` entries
## Summary
MultiQS iterates `self._sources` entries, looks up the type key in `SOURCE_REGISTRY`, raises DriverError for unknown types, names repeated types `<Type>_<n>`, and instantiates `cls(name, config, request, queue)`. Threads are run with bounded concurrency and joined off the loop. No per-source special-casing — registry entry is sufficient. `registry.py` also merges SOURCE_REGISTRY into the component catalogue.
## Citations
- path: `querysource/queries/multi/__init__.py`
  lines: 541-556
  excerpt: |
    cls = SOURCE_REGISTRY.get(source_type)
    ...
    t = cls(name, config, self._request, self._queue)
- path: `querysource/queries/multi/registry.py`
  lines: 148-155
  symbol: component registry (SOURCE_REGISTRY merge)
