# TASK-631: QueryService PBAC enforcement

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-630
**Assigned-to**: unassigned

---

## Context

Implements **Module 5** of the spec. Wires PBAC enforcement into
`QueryService.query()` (`querysource/handlers/service.py:134`). The slug-based
named-query path is the most-trafficked endpoint; this is the canonical
example of how `_enforce_pbac` is called from a handler.

Three PBAC checks per request, in order:

1. `slug:execute` against the slug name (line 173 in current code).
2. `datasource:use` against the resolved datasource name (after `build_provider`).
3. `driver:use` against the resolved driver name.

Each check raises `web.HTTPNotFound` on deny — the first failure short-circuits.

---

## Scope

- Inside `QueryService.query()`, **after** the slug is read at line 173 and
  **before** `get_source()` is called, call:
  ```python
  await self._enforce_pbac(
      request,
      resource_type=ResourceType.SLUG,
      resource_name=slug,
      action="slug:execute",
  )
  ```
- After `await query.build_provider()` completes (and the datasource and
  driver names are known), add two more checks:
  ```python
  await self._enforce_pbac(request, ResourceType.DATASOURCE, ds_name, "datasource:use")
  await self._enforce_pbac(request, ResourceType.DRIVER, drv_name, "driver:use")
  ```
- Use **string literals** for the `ResourceType` enum values until TASK-640
  (navigator-auth upstream PR) ships and is pinned. Specifically, until
  TASK-640 is merged and `navigator-auth>=0.20.0` is pinned, pass the
  enum-or-string form by importing a small QS-local shim:
  ```python
  # querysource/auth/_resource_types.py — created if not present
  try:
      from navigator_auth.abac.policies.resources import ResourceType
      _SLUG = ResourceType.SLUG  # AttributeError until upstream merge
  except (ImportError, AttributeError):
      class _StringResourceType(str): __slots__ = ()
      ResourceType = type("ResourceType", (), {
          "SLUG":       _StringResourceType("slug"),
          "DATASOURCE": _StringResourceType("datasource"),
          "DRIVER":     _StringResourceType("driver"),
          "RAW_QUERY":  _StringResourceType("raw_query"),
      })
  ```
  Use this shim in the handler. Once TASK-640 lands, the shim becomes a
  pass-through and no handler edits are required.
- Locate the datasource name and driver name. Inspect `Executor` /
  `QS.build_provider()` to find where these are stored on the query object;
  pass the actual attribute names (e.g., `query.datasource`, `query.driver`)
  in the enforcement calls.

**NOT in scope**: enforcement on QueryExecutor or MultiQuery (those are
TASK-632 and TASK-633); changes to `get_source()` or `build_provider()`
themselves.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/service.py` | MODIFY | Three `_enforce_pbac` calls inside `query()`. |
| `querysource/auth/_resource_types.py` | CREATE | String-shim for `ResourceType` until TASK-640 lands. |
| `querysource/auth/__init__.py` | MODIFY | Re-export `ResourceType` shim. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.auth import ResourceType   # via the shim — safe before TASK-640
# AbstractHandler._enforce_pbac is inherited; no new import needed.
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/service.py:31
class QueryService(AbstractHandler):
    async def query(self, request):                                   # line 134
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        slug: str = args['slug']                                      # line 173 — slug read here
        # ...
        if query := await self.get_source(request, slug, conditions, driver=args):
            await query.build_provider()
            # ↑ datasource/driver are resolved after this call.
            output = DataOutput(request, query=query, ...)
            return await output.response()                            # line 295

# Handler base — querysource/handlers/abstract.py (TASK-630 added these):
class AbstractHandler(BaseHandler):
    async def _get_user_session(self, request) -> Optional[SessionData]: ...
    async def _enforce_pbac(self, request, resource_type, resource_name, action) -> None:
        """Raises web.HTTPNotFound on deny. No-op when PBAC disabled."""
```

Identifying the **datasource name** and **driver name** to enforce against —
verify the actual attribute names on the query object before wiring:

```bash
# Run before implementing:
grep -n "self.datasource\|self.driver\|self._driver\|self._datasource" \
     querysource/queries/qs.py querysource/queries/abstract.py
```

If the name lives elsewhere (e.g. on `args['driver']`), use whatever path the
handler already uses to resolve it. Document the chosen path in the
Completion Note.

### Does NOT Exist

- ~~`ResourceType.SLUG` accessible directly from navigator-auth~~ — until
  TASK-640 lands, use the shim at `querysource.auth._resource_types`.
- ~~A handler-level `is_authenticated` decorator~~ — `_enforce_pbac` covers
  authentication-by-side-effect (no session ⇒ 404 when PBAC enabled). Do not
  add an explicit `@is_authenticated` decorator here.
- ~~`response.status = 404`-style mutation~~ — enforcement raises
  `web.HTTPNotFound` (handled centrally in `_enforce_pbac`).

---

## Implementation Notes

### Where to insert

`querysource/handlers/service.py:134` is the entry point. The slug read sits
at line 173. Insert the slug check **immediately after the slug is read and
before any DB/source work begins**:

```python
slug: str = args['slug']
await self._enforce_pbac(
    request,
    resource_type=ResourceType.SLUG,
    resource_name=slug,
    action="slug:execute",
)
```

For the datasource and driver checks, find the spot **after**
`query.build_provider()` resolves them (typically right after that line).
Inspect the existing flow before deciding the exact insertion point.

### Edge case: slug → datasource/driver mismatch

If `build_provider()` itself fails (slug points at a non-existent driver),
the existing error path returns its own 4xx. PBAC checks should run
**after** that path to avoid masking real errors as 404s — except for the
slug check, which must run first to enforce hide-existence.

### Key Constraints

- **Order matters**: slug check first, then datasource, then driver. The
  first failure short-circuits — `web.HTTPNotFound` is raised by
  `_enforce_pbac`.
- **No try/except around `_enforce_pbac`**: it raises web exceptions that
  aiohttp handles as the response. Catching it would defeat enforcement.
- **Resource-name correctness**: `slug` is the URL match-info; the
  datasource and driver names must come from `query.<attr>` after
  `build_provider()`. Do not re-derive from URL params.

### References in Codebase

- `querysource/handlers/service.py:134-300` — full method body for context.
- `querysource/handlers/abstract.py` — base helpers added by TASK-630.

---

## Acceptance Criteria

- [ ] Existing `pytest tests/ -x -q` passes with `QS_PBAC_ENABLED=False`
      (default) — zero behavioural delta.
- [ ] When PBAC is enabled and policies grant `slug:execute` for the test
      user: a `GET /api/v2/services/queries/{slug}` returns 200.
- [ ] When PBAC is enabled and policies do NOT grant `slug:execute`: same
      request returns 404 (not 403, not 500).
- [ ] When `slug:execute` is granted but `datasource:use` is denied: 404.
- [ ] When `slug:execute` and `datasource:use` granted but `driver:use`
      denied: 404.
- [ ] No partial work happens on deny — verify by spy that
      `query.build_provider()` is NOT called when the slug check denies.

---

## Test Specification

The full integration tests for `QueryService` enforcement live in TASK-642.
This task adds a smoke test only:

```python
# tests/handlers/test_queryservice_pbac_smoke.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from querysource.handlers.service import QueryService


class TestQueryServicePbacSmoke:
    async def test_slug_check_runs_before_get_source(self):
        """Verify _enforce_pbac is called before get_source on the slug check."""
        # Setup: mock _enforce_pbac to raise 404; verify get_source not called.
        h = QueryService.__new__(QueryService)
        h.logger = MagicMock()
        h._enforce_pbac = AsyncMock(side_effect=web.HTTPNotFound())
        h.get_source = AsyncMock()
        h.query_parameters = MagicMock(return_value={})
        h.match_parameters = MagicMock(return_value={'slug': 'forbidden_slug'})
        # ... build a minimal request fixture that returns these
        # Assertion: web.HTTPNotFound raised; h.get_source not awaited.
```

(The agent may need to adapt this skeleton to the actual `query()` flow.
The mandatory check is that `_enforce_pbac` runs before `get_source`.)

---

## Agent Instructions

1. Read spec sections 2, 3 (Module 5), 6, 7.
2. Read `querysource/handlers/service.py:134-300` end-to-end.
3. Run the grep in the Codebase Contract to identify the datasource/driver
   attribute names.
4. Create the `querysource/auth/_resource_types.py` shim and re-export
   `ResourceType` from `querysource/auth/__init__.py`.
5. Wire the three `_enforce_pbac` calls into `QueryService.query()`.
6. Add the smoke test.
7. Run `pytest tests/ -x -q` to verify no regressions and confirm the
   smoke test passes.
8. Move task to `done/` and update the index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-04-30
**Notes**: Three _enforce_pbac calls wired into QueryService.query(). slug:execute
check inserted immediately after slug parsing, before get_source(). datasource:use
and driver:use checks inserted after build_provider() succeeds. ResourceType shim
created at querysource/auth/_resource_types.py. 2/2 smoke tests pass.
**Datasource attribute path used**: query._qs._definition.provider (via getattr chain)
**Driver attribute path used**: query._provider.driver (via getattr chain)

**Deviations from spec**: none
