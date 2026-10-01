---
id: F012
query_id: Q013
type: read
intent: locate the "user's session vault" (follow-up to user answer on U5)
executed_at: 2026-09-30T14:37:23Z
parent_id: F008
depth: 1
---
# F012 — Session vault lives in navigator_session, not querysource
## Summary
querysource has no vault code of its own (grep for "vault" in querysource/*.py finds nothing; this matches FEAT-096 F006). The vault is `navigator_session.vault.SessionVault`: secrets are double-sealed (a Redis/memory layer keyed per session, plus the Postgres `auth.user_vault_secrets` layer keyed per user). It exposes async `set/get/exists/delete/keys`, and `load_for_session` loads every user secret at session start. It is bound to `session_uuid`, `user_id`, an asyncpg pool and a Redis client.
## Citations
- path: `.venv/lib/python3.11/site-packages/navigator_session/vault/session_vault.py`
  lines: 132-178
  symbol: `SessionVault.__init__`
- path: `.venv/lib/python3.11/site-packages/navigator_session/vault/session_vault.py`
  lines: 274-409
  symbol: `SessionVault.set`, `SessionVault.get`, `SessionVault.load_for_session`
- path: `.venv/lib/python3.11/site-packages/navigator_session/vault/targets/user_vault.py`
  lines: 33-47
  symbol: `UserVaultTarget` (auth.user_vault_secrets)
- path: `.venv/lib/python3.11/site-packages/navigator_auth/vault/integration.py`
  lines: 24-151
  symbol: `VAULT_SESSION_KEY = "_vault"`, `get_session_vault`, `_attach_vault_to_request`
