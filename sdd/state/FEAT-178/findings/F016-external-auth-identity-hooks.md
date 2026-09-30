---
id: F016
query_id: Q017
type: read
intent: how a new navigator-auth provider plugs into the identity-link flow
executed_at: 2026-09-30T14:42:55Z
parent_id: F015
depth: 2
---
# F016 — ExternalAuth provides a generic identity-link flow
## Summary
`ExternalAuth` already implements the identity flow generically.
- `authorize_identity` stores the flow state in `IdentityFlowStore` and redirects (302) to `authorize_uri` with `identity_scopes()` plus `identity_authorize_params()`.
- `exchange_code_for_tokens` and `refresh_identity_tokens` do the authorization_code and refresh_token grants against `_token_uri`.
- The shared callback detects `flow == "identity_link"` and calls `finish_identity_link`.

A new provider only needs `_service_name`, the authorize/token URIs, `get_identity_client()` and `identity_scopes()`. Caveat: `ExternalAuth.configure` also registers login routes (`/api/v1/auth/{service}/`, `/auth/{service}/login`). A link-only OneDrive provider must avoid exposing itself as a login method, for example by overriding `configure` so it registers only the callback, or by adding a navigator-auth flag.
## Citations
- path: `.venv/lib/python3.11/site-packages/navigator_auth/backends/external.py`
  lines: 80-157
  symbol: `ExternalAuth`, `ExternalAuth.configure` (login + callback routes)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/backends/external.py`
  lines: 380-386
  excerpt: "flow = await self._flow_store.consume_link(state) ... return await self.finish_identity_link(request, flow)"
- path: `.venv/lib/python3.11/site-packages/navigator_auth/backends/external.py`
  lines: 495-611
  symbol: `identity_scopes`, `identity_authorize_params`, `get_identity_client`, `authorize_identity`, `exchange_code_for_tokens`, `refresh_identity_tokens`, `finish_identity_link`
