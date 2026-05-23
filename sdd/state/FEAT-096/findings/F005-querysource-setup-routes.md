---
id: F005
title: QuerySource.setup() is the route-registration entry point (not "configure()")
queries: [Q006, Q019]
confidence: high
citations:
  - path: querysource/services.py
    lines: 49-310
    symbols: [QuerySource, QuerySource.setup]
  - path: querysource/__init__.py
    lines: 1-19
---

# F005 — Where routes get registered

`querysource/__init__.py` is essentially empty (just version exports, lines 1-19). The actual app-level class lives in `querysource/services.py:49`:

- `class QuerySource(metaclass=Singleton)` (line 49)
- `def setup(self, app: web.Application) -> web.Application` (line 97) — the canonical entry point that wires every route. **There is no method called `configure()`** anywhere in the package.

Inside `setup()` (lines 97-310):
- Routes are registered with `self.app.router.add_get/add_post/add_view(...)` calls (lines 137-273)
- New v3 namespaced routes follow the `/api/v3/qs/<thing>` convention (e.g. `/api/v3/qs/components` lines 218-227 from FEAT-095)
- Startup/shutdown hooks are appended via `self.app.on_startup.append(...)` (lines 286-291)
- Optional subsystems (e.g. `QSScheduler`) are conditionally set up via env flags (lines 292-296)

**Implication for AirtableSource:** The user prompt says "on `configure()` of Querysource we need to configure a CLIENT CALLBACK as an aiohttp route `/api/v1/qs/integrations/airtable/callback`". That method is actually `QuerySource.setup()`. The callback registration belongs there — likely guarded by a new env flag (e.g. `QS_AIRTABLE_OAUTH_ENABLED`) to keep the existing tenants unaffected. The path `/api/v1/qs/integrations/airtable/callback` is consistent with the `/api/v1/qs/...` namespacing convention (cf. `/api/v1/qs/audit_log` at line 167).
