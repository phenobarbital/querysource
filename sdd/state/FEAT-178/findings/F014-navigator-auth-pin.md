---
id: F014
query_id: Q015
type: grep
intent: does querysource use / pin navigator-auth identity features
executed_at: 2026-09-30T14:37:23Z
parent_id: F013
depth: 2
---
# F014 — Dependency pin vs. installed navigator-auth
## Summary
pyproject pins `navigator-auth>=0.15.8`, but the venv has 0.28.2, which is where the identity/vault features were found. querysource code has no references to `navigator_auth.identity` or `user_identities`; the identity routes are registered by navigator-auth itself (`handlers/__init__.py`), not by querysource. Whether a querysource deployment enables the Azure backend or the identity routes is deployment configuration and cannot be seen from this repo.
## Citations
- path: `pyproject.toml`
  lines: 118
  excerpt: '"navigator-auth>=0.15.8",'
- path: `querysource/`
  excerpt: (no matches for navigator_auth identity / user_identities)
