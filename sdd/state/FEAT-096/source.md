---
kind: inline
jira_key: null
fetched_at: 2026-05-22T15:27:20Z
summary_oneline: New Airtable Source for MultiQuery extending ThreadSource, backed by AirtableInterface with OAuth2 + PAT auth
---

# New Airtable Source

Documentation: https://airtable.com/developers/web/api/introduction

A new Source object for Multi Query extending from ThreadSource and using a new
interface "AirtableInterface" with all required features for Airtable.

**Scope clarification (Phase 0 Q&A):** The `AirtableSource` (the MultiQuery
Source class) is **read-only** for this feature — it extracts records from a
provided table and returns a pandas DataFrame. The `AirtableInterface` is
designed to encapsulate **all** Airtable-related code (read + write/create-table)
so that future work can add write capability without re-touching the Source layer.

## Default credentials

If no per-user credential is provided, fall back to environment variables:

- `AIRTABLE_CLIENT_ID`
- `AIRTABLE_CLIENT_SECRET`
- `AIRTABLE_BASE_ID`
- `AIRTABLE_ACCESS_TOKEN` *(Personal Access Token — value REDACTED, never commit a real token)*

> ⚠️ A real token value was pasted into the prompt session and has been
> redacted everywhere in the proposal + state files. The submitter should
> rotate the token in Airtable.

## How it works

On `configure()` of QuerySource, register a CLIENT CALLBACK as an aiohttp route:

```
{local_server}/api/v1/qs/integrations/airtable/callback
```

The callback handler stores the OAuth2 access token (and refresh token, when
issued by Airtable) into the user's session vault under the key `airtable`.

Per-user consent is a separate page where users can "connect" to Airtable; on
successful auth the credentials land in the session vault.

When the component is invoked:

1. Inspect the incoming `web.Request` for an authenticated user session.
2. If a session exists and has an `airtable` vault entry → use OAuth credentials.
3. If no session credential → fall back to the Personal Access Token in
   `AIRTABLE_ACCESS_TOKEN`.
4. If neither is available → raise an authentication error.

## Goal

- A new `Source` in MultiQuery for retrieving tables from Airtable, supporting
  **OAuth2** auth or **Personal Access Token** auth, extracting records from a
  table identified by **table id** or a **full Airtable URL** like
  `https://airtable.com/appSbbMXLZUy1cF17/tblJ6L35I1Mm4LxGZ/viwgoy7x66reyDHcD`.
- Use `aiohttp` in async mode for all interactions with the Airtable API.
- Return a pandas DataFrame, like every other MultiQuery source.
