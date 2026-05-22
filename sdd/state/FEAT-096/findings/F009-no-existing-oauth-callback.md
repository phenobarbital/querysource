---
id: F009
title: No existing OAuth callback handler exists in querysource/handlers/
queries: [Q007, Q008]
confidence: high
citations:
  - path: querysource/handlers/
    lines: directory listing
  - path: querysource/handlers/ (grep for "oauth|callback|access_token|refresh_token")
    lines: zero matches
---

# F009 — No existing OAuth callback handler

`querysource/handlers/` contains: `abstract.py, components.py, executor.py, log.py, manager.py, multi.py, outputs/, service.py, variables.py, variables.py`.

Repo-wide `grep "oauth\|callback\|access_token\|refresh_token" querysource/handlers/ querysource/auth/` returns **zero** matches. No existing OAuth callback handler, no token-exchange code, no refresh-token plumbing.

This means the Airtable callback handler at `/api/v1/qs/integrations/airtable/callback` is the **first** OAuth callback in QuerySource. Decisions to make in the proposal:

1. Where does the handler class live? Conventions point to either:
   - A new file `querysource/handlers/integrations.py` (or `querysource/handlers/integrations/airtable.py`) — mirrors aiohttp handler pattern.
   - Or co-located with the Source itself under a new `querysource/integrations/` package.
2. Where does the consent page live? Not in scope to host the HTML — typically a frontend route — but the proposal needs to specify which side owns it.
3. How is the OAuth client_id/client_secret pair carried? Server-side env vars (`AIRTABLE_CLIENT_ID`, `AIRTABLE_CLIENT_SECRET`) — never sent to the browser.

**Note on existing OAuth references found OUTSIDE handlers/auth:**
- `querysource/outputs/destinations/sharepoint.py` — uses client-credentials OAuth via Azure SDK (server-to-server, not user-delegated)
- `querysource/providers/sources/ga.py` — Google Analytics has OAuth references but uses a different (older) provider abstraction, not relevant to MultiQuery sources

So there is no template OAuth user-delegated flow to copy.
