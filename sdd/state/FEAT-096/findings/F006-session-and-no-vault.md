---
id: F006
title: Session model uses navigator_session; NO "vault" concept exists in querysource
queries: [Q008, Q009, Q012, Q014]
confidence: high
citations:
  - path: querysource/handlers/abstract.py
    lines: 9, 220-251, 313-323
    symbols: [_get_user_session, SessionData, get_session]
  - path: querysource/auth/
    lines: directory contents
  - path: querysource/auth/credentials.py
    lines: 17-225
    symbols: [CredentialResolver, ResolvedCredentials]
---

# F006 — Session model and the (non-existent) "vault"

## Session retrieval pattern

`querysource/handlers/abstract.py:225-251` shows the canonical session lookup:

```python
async def _get_user_session(self, request: web.Request) -> Optional[SessionData]:
    cached = request.get('user_session', _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    try:
        session = await get_session(request, new=False)
    except RuntimeError:
        self.logger.error('QS: User Session system is not installed.')
        session = None
    request['user_session'] = session
    return session
```

- Sessions come from `navigator_session.get_session(request, new=False)` (line 9, 246).
- `SessionData` behaves dict-like (used as `session.get(AUTH_SESSION_OBJECT, {})` at line 318).
- The memoization key `request['user_session']` is the project-wide convention.

## "Vault" — does NOT exist

Repository-wide `grep "vault\|Vault" querysource/` returns **zero** results. The user's prompt term "session vault" has no implementation today. It must be designed as part of this feature, and the simplest mapping is: **write the Airtable credentials directly into the session under a known key, e.g. `session['airtable'] = {"access_token": ..., "refresh_token": ..., "expires_at": ...}`**.

## auth/credentials.py is NOT a vault

`querysource/auth/credentials.py:17-225` defines `CredentialResolver` — but it is a database-connection resolver (HOST/PORT/USER/PASSWORD/DATABASE three-tier env-var lookup, from FEAT-091). It is not applicable to OAuth tokens or external-API credentials.

## Implications for AirtableSource

1. The Source class will need a `_get_user_session(self._request)` equivalent — but it lives in a `ThreadSource`, not a handler. Options:
   - Lift the helper into a small mixin or utility callable from anywhere with a `web.Request`.
   - Re-implement the (~6-line) lookup inline in `AirtableInterface`.
2. There is no pre-built session-write mechanism. The OAuth callback handler must do `session['airtable'] = {...}` itself.
3. **navigator_session may not even be installed/configured** in every QuerySource deployment (handler code defensively logs "User Session system is not installed" at line 248). The Airtable callback and Source must degrade gracefully when sessions are unavailable.
