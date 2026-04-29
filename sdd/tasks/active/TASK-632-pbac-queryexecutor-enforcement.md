# TASK-632: QueryExecutor PBAC enforcement (incl. dry_run)

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-630, TASK-631
**Assigned-to**: unassigned

---

## Context

Implements **Module 6** of the spec. Wires PBAC enforcement into
`QueryExecutor.query()` (`querysource/handlers/executor.py:51`) and
`QueryExecutor.dry_run()` (line 104). Both endpoints accept either a slug or
a raw inline query in the payload — the enforcement branches accordingly:

- Payload contains `slug` → run `slug:execute` check.
- Payload contains a raw inline query (no slug) → run `raw_query:execute`
  check (explicit, separate permission).

Then both branches run the datasource and driver checks.

`dry_run` is gated identically to `query()`. This was Open Question #5 in the
spec, resolved as "same checks" — without it an attacker probes slug
existence via the dry-run endpoint (200 vs 404 leaks the catalog).

Sequential after TASK-631 to keep the enforcement contract uniform across
the three execution handlers.

---

## Scope

- Inside `QueryExecutor.query()` (line 51): after `get_payload()` parses the
  body and before `get_executor()` builds the `Executor`, branch on the
  payload:
  - If a slug field is present in `data`: call
    `_enforce_pbac(..., ResourceType.SLUG, slug, "slug:execute")`.
  - Else (raw inline query): call
    `_enforce_pbac(..., ResourceType.RAW_QUERY, "raw_query", "raw_query:execute")`.
- After `Executor.query()` resolves the underlying datasource/driver, add
  the same `datasource:use` and `driver:use` checks as TASK-631.
- Apply **identical** logic to `QueryExecutor.dry_run()` (line 104). The
  cleanest path is to extract a private helper on `QueryExecutor`:
  ```python
  async def _enforce_payload(self, request, data, query):
      # branches on slug vs raw_query, then datasource + driver
  ```
  …called from both `query()` and `dry_run()`.
- Continue using the `ResourceType` shim from TASK-631
  (`from querysource.auth import ResourceType`).

**NOT in scope**: MultiQuery enforcement (TASK-633); QueryService enforcement
(TASK-631, already done); driver-layer credential resolution (TASK-636).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/executor.py` | MODIFY | Add `_enforce_payload` helper; call from `query()` and `dry_run()`. |
| `tests/handlers/test_queryexecutor_pbac_smoke.py` | CREATE | Smoke tests for both branches. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.auth import ResourceType   # shim from TASK-631
# AbstractHandler._enforce_pbac inherited.
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/executor.py:19
class QueryExecutor(AbstractHandler):  # 149 lines total
    async def get_payload(self, request: web.Request) -> dict:        # line 29
        data = None
        if request.content_type == 'application/json':
            data = await self.get_json(request)
        else:
            data = await self.body(request)
        return data
    def get_executor(self, data, request: web.Request) -> Executor:   # line 42
        query = Executor(request=request)
        query.start(data)
        return query
    async def query(self, request):                                    # line 51
        payload = await self.get_payload(request)
        # ...
        query = self.get_executor(payload, request)
        obj = await query.query()                                      # line 79
        return self.json_response(...)
    async def dry_run(self, request: web.Request = None):              # line 104
        data = await self.get_payload(request)
        # ...
        obj = await query.dry_run()
        return self.json_response(...)
```

### Does NOT Exist

- ~~A `slug` field with a fixed canonical name~~ — verify the actual key the
  payload uses. The agent must `grep` for `'slug'` / `"slug"` / `args.get('slug')`
  in `executor.py` and the `Executor` start path. Use the actual key.
- ~~`Executor.is_raw_query` attribute~~ — does not exist. Detect via "no slug
  in payload" rather than via an attribute.
- ~~`ResourceType.RAW_QUERY` accessible from navigator-auth~~ — use the
  shim from TASK-631. Action verb is the literal string `"raw_query:execute"`.

---

## Implementation Notes

### Branching logic

```python
async def _enforce_payload(self, request, data, query):
    """Run pre-execution PBAC checks. Branches on slug vs raw_query."""
    slug = (data or {}).get('slug') if isinstance(data, dict) else None
    if slug:
        await self._enforce_pbac(
            request,
            resource_type=ResourceType.SLUG,
            resource_name=slug,
            action="slug:execute",
        )
    else:
        await self._enforce_pbac(
            request,
            resource_type=ResourceType.RAW_QUERY,
            resource_name="raw_query",
            action="raw_query:execute",
        )
    # Datasource + driver checks (after the executor has resolved them):
    ds = getattr(query, 'datasource', None) or getattr(query, '_datasource', None)
    drv = getattr(query, 'driver', None) or getattr(query, '_driver', None)
    if ds:
        await self._enforce_pbac(request, ResourceType.DATASOURCE, ds, "datasource:use")
    if drv:
        await self._enforce_pbac(request, ResourceType.DRIVER, drv, "driver:use")
```

### Insert into `query()` and `dry_run()`

In `query()` at line 51:

```python
async def query(self, request):
    payload = await self.get_payload(request)
    query = self.get_executor(payload, request)
    await self._enforce_payload(request, payload, query)   # ← NEW
    obj = await query.query()
    return self.json_response(...)
```

In `dry_run()` at line 104, same insertion after `get_executor` and
before `await query.dry_run()`.

### When the datasource/driver isn't yet resolved

`Executor.start(data)` (called by `get_executor`) populates the executor.
If `datasource`/`driver` aren't available at that point, the
`_enforce_payload` helper should still run the slug-or-rawquery check —
the datasource/driver checks just no-op when the attrs are absent
(`getattr(..., None)`). The actual driver instantiation happens later inside
`Executor.query()`.

If the driver name is only known **after** `Executor.query()` starts
running, lift the datasource/driver checks into `Executor` itself in a
follow-up — but for v1, do best-effort enforcement at the handler boundary.

### Key Constraints

- **`dry_run` MUST be gated identically.** This is non-negotiable per
  spec §8 (resolved Q5). Do not exempt it.
- **First failure short-circuits.** `_enforce_pbac` raises; nothing after
  it runs.
- **Hide existence.** All denials are 404 — the existing `_enforce_pbac`
  helper handles that.

### References in Codebase

- `querysource/handlers/executor.py:19-149` — read end-to-end.
- `querysource/queries/executor.py` (or wherever `Executor` lives) — `grep`
  for the class to verify `datasource`/`driver` attribute names. Document
  the chosen path.

---

## Acceptance Criteria

- [ ] `_enforce_payload` is called from both `query()` and `dry_run()`.
- [ ] PBAC disabled: existing tests pass unchanged.
- [ ] PBAC enabled, slug payload, allowed: 200.
- [ ] PBAC enabled, slug payload, denied: 404.
- [ ] PBAC enabled, raw payload, no `raw_query:execute` permission: 404.
- [ ] PBAC enabled, raw payload, with `raw_query:execute` permission: executes.
- [ ] `dry_run` returns 404 for the same payload that `query()` returns 404 for.
- [ ] `Executor.query()` (or `.dry_run()`) is NOT awaited when the slug
      check denies — verifiable via mock spy.
- [ ] No regressions: full test suite green.

---

## Test Specification

```python
# tests/handlers/test_queryexecutor_pbac_smoke.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from querysource.handlers.executor import QueryExecutor


class TestQueryExecutorPbacSmoke:
    async def test_query_calls_enforce_payload_before_executor_query(self):
        h = QueryExecutor.__new__(QueryExecutor)
        h.logger = MagicMock()
        h._enforce_payload = AsyncMock(side_effect=web.HTTPNotFound())
        h.get_payload = AsyncMock(return_value={"slug": "x"})
        h.get_executor = MagicMock(return_value=MagicMock(query=AsyncMock()))
        with pytest.raises(web.HTTPNotFound):
            await h.query(MagicMock(spec=web.Request))
        h.get_executor.return_value.query.assert_not_awaited()

    async def test_dry_run_uses_same_helper(self):
        h = QueryExecutor.__new__(QueryExecutor)
        h.logger = MagicMock()
        h._enforce_payload = AsyncMock(side_effect=web.HTTPNotFound())
        h.get_payload = AsyncMock(return_value={"raw_sql": "SELECT 1"})
        h.get_executor = MagicMock(return_value=MagicMock(dry_run=AsyncMock()))
        with pytest.raises(web.HTTPNotFound):
            await h.dry_run(MagicMock(spec=web.Request))
        h.get_executor.return_value.dry_run.assert_not_awaited()

    async def test_raw_payload_uses_raw_query_action(self):
        h = QueryExecutor.__new__(QueryExecutor)
        h.logger = MagicMock()
        h._enforce_pbac = AsyncMock()
        # Call _enforce_payload directly with no slug
        executor = MagicMock(datasource=None, driver=None)
        await h._enforce_payload(MagicMock(), {"raw_sql": "..."}, executor)
        # Find the call with action="raw_query:execute"
        call = h._enforce_pbac.await_args_list[0]
        assert call.kwargs["action"] == "raw_query:execute"
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 6) + 6.
2. Read `executor.py` end-to-end (149 lines).
3. Grep for the actual slug-payload key name and the executor's
   datasource/driver attrs. Document chosen paths.
4. Implement `_enforce_payload` helper and call from both methods.
5. Add the smoke tests.
6. Run `pytest tests/ -x -q`.
7. Move task to `done/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Slug payload key**:
**Datasource/driver attribute paths**:

**Deviations from spec**: none | describe if any
