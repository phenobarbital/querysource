---
id: F015
query_id: Q016
type: read
intent: U6 — is the Azure identity-link flow enabled for querysource?
executed_at: 2026-09-30T14:42:55Z
parent_id: F013
depth: 2
---
# F015 — querysource enables only BasicAuth; the Azure backend is the corporate SSO
## Summary
- `settings/settings.py` sets `AUTHENTICATION_BACKENDS = ('navigator_auth.backends.BasicAuth',)`, and `app.py` wires `AuthHandler()`. Only BasicAuth is enabled.
- `AuthHandler.get_external_backend(service)` returns only enabled `_external_auth` backends, so `/api/v1/user/identities/link/azure` would return 404 ("Unknown or disabled provider").
- `AzureAuth` (`_service_name="azure"`) is the corporate login. It is bound to `AZURE_ADFS_TENANT_ID` and `AZURE_ADFS_CLIENT_ID` (the tenant-specific authorize/token URIs are built from them), and its identity scopes come from the single global `AZURE_IDENTITY_SCOPES`.
- `env/prod/.env` defines `AZURE_ADFS_*` keys (values not read) but no `AZURE_IDENTITY_SCOPES` and no `AUTHENTICATION_BACKENDS`.

Reusing `AzureAuth` for OneDrive would therefore mean enabling corporate Azure login in this app, widening the corporate app's consented scopes, and making the corporate app registration accept personal Microsoft accounts. **U6 = no**, which by the user's rule means a new provider in navigator-auth.
## Citations
- path: `settings/settings.py`
  lines: 146-148
  excerpt: "AUTHENTICATION_BACKENDS = ('navigator_auth.backends.BasicAuth',)"
- path: `app.py`
  lines: 29-31
  symbol: `Main.configure` (AuthHandler().setup)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/auth.py`
  lines: 353-362
  symbol: `AuthHandler.get_external_backend`
- path: `.venv/lib/python3.11/site-packages/navigator_auth/handlers/user_identities.py`
  lines: 67-71
  symbol: `BaseIdentityView._backend` (404 when disabled)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/backends/azure.py`
  lines: 86, 102-108
  symbol: `AzureAuth` (_service_name, tenant-bound URIs)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/conf.py`
  lines: 192-196, 632-634
  symbol: `AUTHENTICATION_BACKENDS`, `AZURE_IDENTITY_SCOPES`
- path: `env/prod/.env`
  lines: 354
  excerpt: AZURE_ADFS_SCOPES (key name only)
