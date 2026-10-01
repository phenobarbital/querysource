---
id: F001
query_id: Q001
type: wiki_query
intent: orient on SharePoint source and its origin
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F001 — SharePoint source lineage (wiki)
## Summary
Wiki ranks TASK-647 (SourceSharepoint Component, score 1.00) and spec multiquery-new-sources (FEAT that added SharePoint/SmartSheet/S3/Table sources, score 0.76) top; ToSharepoint destination (TASK-654) is a sibling. The spec states SharePoint was adapted from flowtask's Sharepoint interface.
## Citations
- path: `sdd/tasks/completed/TASK-647-source-sharepoint.md`
  symbol: TASK-647
- path: `sdd/specs/multiquery-new-sources.spec.md`
  lines: 205-208
  excerpt: |
    ### Module 4: SourceSharepoint
    - Responsibility: Download a single file (CSV/Excel) from a SharePoint document library via Microsoft Graph API ... Adapted from flowtask/flowtask/interfaces/Sharepoint.py.
