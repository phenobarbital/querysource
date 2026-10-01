---
id: F011
query_id: Q011
type: git_log
intent: origin feature and recent activity
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F011 — SharepointSource history (180 days)
## Summary
12 commits. Created 2026-05-19 (TASK-647). June 2026 hardening wave by Juan2coder: platform.version patch, site URL/tenant host, directory parsing + subsites + large-file timeout, sheet_name/pd_args, full `url` via Graph /shares, date masks. Last touch 2026-07-01 (masks optional in schema). Every one of these fixes is a behaviour a OneDrive clone should inherit.
## Citations
- path: `querysource/queries/multi/sources/sharepoint.py`
  excerpt: |
    c112ad97 2026-07-01 fix(SharepointSource): make source.masks optional in schema
    6d8681b6 2026-06-29 refactor(sources): use flowtask-style masks dict
    b640ea23 2026-06-25 refactor(SharepointSource): resolve full url via Graph /shares
    3337c7c9 2026-06-16 feat(SharepointSource): add sheet_name and pd_args
    70d030a5 2026-06-16 fix(SharepointSource): directory parsing, subsite support, large file timeout
    07e2cd37 2026-06-16 fix(SharepointSource): patch platform.version() trailing space on Linux
    2710e3fe 2026-05-19 feat(multiquery-new-sources): TASK-647 — SourceSharepoint Component
