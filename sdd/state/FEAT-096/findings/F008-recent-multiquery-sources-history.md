---
id: F008
title: Recent commit history — multi-query sources are an active, freshly-landed workstream
queries: [Q013]
confidence: high
citations:
  - path: git log
    lines: --since=6 months ago -- querysource/queries/multi/sources/
---

# F008 — Recent multi-query sources history

`git log --since="6 months ago" -- querysource/queries/multi/sources/` (top 10):

```
1d655c9 fix(multiquery-new-sources): address code review findings
d5b1aa0 feat(multiquery-new-sources): TASK-651 — MultiQS Integration sources dispatch
68bdb2b feat(multiquery-new-sources): TASK-652 — Source Registry and __init__ Exports
8dd17c4 feat(multiquery-new-sources): TASK-650 — SourceTable Component
8631693 feat(multiquery-new-sources): TASK-649 — SourceS3 Component
68861a7 feat(multiquery-new-sources): TASK-648 — SourceSmartSheet Component
2710e3f feat(multiquery-new-sources): TASK-647 — SourceSharepoint Component
c25ac2d feat(multiquery-new-sources): TASK-646 — Refactor ThreadQuery to inherit ThreadSource
ae86973 feat(multiquery-new-sources): TASK-645 — Refactor ThreadFile to inherit ThreadSource
c811aff feat(multiquery-new-sources): TASK-644 — ThreadSource Base Class
```

This is the FEAT-093 workstream that **introduced ThreadSource** and refactored existing sources to inherit it. The Airtable source is a natural continuation — same pattern, just a new source.

**Implications:**
- All conventions (registry, optional imports, navconfig fallback) are recent and consistent — no legacy drift.
- The team explicitly added a "MultiQS Integration sources dispatch" task (TASK-651). Worth inspecting that to see whether dispatch already supports OAuth-style sources or needs extension.
