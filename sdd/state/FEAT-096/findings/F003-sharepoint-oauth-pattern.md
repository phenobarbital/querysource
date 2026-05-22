---
id: F003
title: SharepointSource — closest OAuth-credentialed analogue
queries: [Q004]
confidence: high
citations:
  - path: querysource/queries/multi/sources/sharepoint.py
    lines: 20-225
    symbols: [SharepointSource, fetch, _parse_file_content]
---

# F003 — SharepointSource (OAuth client-credentials analogue)

Lines 20-225. Sharepoint uses **Azure client-credentials (client_id / client_secret / tenant_id)** — a form of OAuth — and is the closest OAuth-style precedent in the multi-query sources tree. Key points:

- Credentials read from `options['credentials']` with all-uppercase navconfig fallbacks (lines 56-69): `SHAREPOINT_APP_ID`, `SHAREPOINT_APP_SECRET`, `SHAREPOINT_TENANT_ID`.
- **Heavy SDK imports are lazy / optional** (lines 113-128): `from azure.identity.aio import ClientSecretCredential` and `from msgraph import GraphServiceClient` inside `fetch()`. Missing deps raise `ImportError` with install hint (`pip install querysource[sharepoint]`).
- The `web.Request` from `ThreadSource.__init__` is **not** consulted — Sharepoint runs purely on app-level creds. There is no per-user OAuth-token retrieval pattern in any existing source.

**Implication for AirtableSource:**
- Use the same optional-import pattern if we need a real Airtable SDK (pyairtable). Plain aiohttp keeps the dep surface smaller.
- The "use the user's session token if present, else fall back to PAT" behavior is **net-new** — no precedent in any existing source to copy. Air­tableSource will be the first source to read per-user tokens from `web.Request`.
