---
id: F013
query_id: Q014
type: read
intent: existing login UI + refresh-token capture flow
executed_at: 2026-09-30T14:37:23Z
parent_id: F012
depth: 2
---
# F013 — navigator-auth already ships an "Identity Vault" link flow (UI + token capture)
## Summary
navigator_auth.identity links external OAuth2 identities to the authenticated user. The pieces:
- `GET /api/v1/user/identities/link/{provider}` (`IdentityLinkHandler`) sends the user to the provider (302).
- The backend callback exchanges the code for tokens (`exchange_code_for_tokens`) and stores them encrypted in `auth.user_identities` (`IdentityStore.save_linked_identity`).
- `GET /api/v1/user/identities/manage` is an HTML management page (`IdentitiesManageView`).
- `GET /api/v1/user/identities/{provider}/credential` (`IdentityCredentialHandler`) returns the decrypted credential. It checks the session-vault cache first (key `identity:{provider}`), then the DB, auto-refreshes the token when it is about to expire, and persists the rotated token (`store.update_tokens`) before re-caching it in the vault.

The Azure backend implements the flow with MSAL (`authorize_identity`, `exchange_code_for_tokens`, `refresh_identity_tokens`). Scopes come from `AZURE_IDENTITY_SCOPES` (default `User.Read`), and the authority tenant is `AZURE_ADFS_TENANT_ID` (default `common`).
## Citations
- path: `.venv/lib/python3.11/site-packages/navigator_auth/handlers/user_identities.py`
  lines: 171-217
  symbol: `IdentityCredentialHandler.get`
  excerpt: |
    cached = await cached_credential(session, provider)
    ...
    token = await backend.refresh_identity_tokens(token.refresh_token)
    identity = await store.update_tokens(identity, token)
    await cache_credential(session, provider, credential)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/handlers/user_identities.py`
  lines: 219-247
  symbol: `IdentityLinkHandler`, `IdentitiesManageView`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/handlers/__init__.py`
  lines: 73-74
  excerpt: 'r"/api/v1/user/identities/link/{provider:[a-z0-9_]+}", IdentityLinkHandler'
- path: `.venv/lib/python3.11/site-packages/navigator_auth/identity/store.py`
  lines: 19, 112-280, 358-400
  symbol: `IDENTITY_VAULT_KEY = "identity:{provider}"`, `IdentityStore`, `cache_credential`, `cached_credential`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/backends/azure.py`
  lines: 292-380
  symbol: `AzureAuth.identity_scopes`, `authorize_identity`, `exchange_code_for_tokens`, `refresh_identity_tokens`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/conf.py`
  lines: 407, 632-634
  symbol: `AZURE_ADFS_TENANT_ID` (fallback "common"), `AZURE_IDENTITY_SCOPES` (fallback "User.Read")
