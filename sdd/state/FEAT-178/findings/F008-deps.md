---
id: F008
query_id: Q008
type: grep
intent: available MS auth/Graph deps
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F008 — Microsoft Graph dependencies
## Summary
`[project.optional-dependencies] sharepoint = [msgraph-sdk>=1.0, azure-identity>=1.0, httpx>=0.24]`; httpx[http2] is also a core dep. No msal/office365 deps. No MS/Graph keys in `querysource/conf.py` — credentials resolved per-source via navconfig names (Q012: no matches).
## Citations
- path: `pyproject.toml`
  lines: 151-155
  symbol: `sharepoint` extra
- path: `pyproject.toml`
  lines: 47
  excerpt: '"httpx[http2]>=0.26.0",'
- path: `querysource/conf.py`
  excerpt: (no sharepoint/onedrive/azure/msgraph matches)
