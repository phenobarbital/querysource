---
id: F004
title: SOURCE_REGISTRY in sources/__init__.py — how new sources are dispatched
queries: [Q002, Q018]
confidence: high
citations:
  - path: querysource/queries/multi/sources/__init__.py
    lines: 1-28
    symbols: [SOURCE_REGISTRY]
---

# F004 — Source registry

`querysource/queries/multi/sources/__init__.py:1-28` exposes:

```python
SOURCE_REGISTRY: dict = {
    "SharepointSource": SharepointSource,
    "SmartSheetSource": SmartSheetSource,
    "S3Source": S3Source,
    "TableSource": TableSource,
}
```

Plus public exports of every source class via `__all__`. The docstring on the dict (line 20-21) states it is used by `MultiQS` for dynamic dispatch from YAML config — the "type" string in YAML maps to a class via this dict.

**Implication for AirtableSource:** registration is mechanical — add `from .airtable import AirtableSource`, add to `__all__`, and add `"AirtableSource": AirtableSource` to the dict. Recent commit `68bdb2b feat(multiquery-new-sources): TASK-652 — Source Registry and __init__ Exports` confirms this is the live convention.
