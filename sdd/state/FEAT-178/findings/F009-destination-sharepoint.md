---
id: F009
query_id: Q009
type: read
intent: second SharePoint client for credential patterns
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F009 — ToSharepoint destination duplicates the Graph client setup
## Summary
`ToSharepoint(AbstractDestination)` independently re-implements Graph auth (`_build_graph_client`, sync `azure.identity.ClientSecretCredential`), the platform.version patch, site/drive resolution (`_resolve_site_id`, `_parse_directory_path`, `_resolve_drive`) and upload. No shared Graph helper exists between source and destination — a OneDrive source adds a third copy unless a helper is extracted.
## Citations
- path: `querysource/queries/multi/destinations/sharepoint.py`
  lines: 1-60
  symbol: `ToSharepoint`
- path: `querysource/queries/multi/destinations/sharepoint.py`
  lines: 108-148
  symbol: `ToSharepoint._build_graph_client`
- path: `querysource/queries/multi/destinations/sharepoint.py`
  lines: 239-279
  symbol: `ToSharepoint._resolve_drive`
