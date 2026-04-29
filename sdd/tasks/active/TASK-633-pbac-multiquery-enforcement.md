# TASK-633: MultiQuery PBAC enforcement (all-or-nothing pre-flight)

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-630, TASK-632
**Assigned-to**: unassigned

---

## Context

Implements **Module 7** of the spec. Adds an **all-or-nothing pre-flight**
PBAC check to the multi-query path. `QueryHandler.query()` builds a
`MultiQS` from a body that can reference N slugs (in `_queries`), N file
paths (in `_files`), and inline raw queries. If the user is denied any one
of those components, the entire MultiQuery is rejected with 404 — no thread
fan-out runs.

Uses `Guardian.filter_resources()` (one batch call per resource type) for
efficiency rather than calling `_enforce_pbac` per component.

Sequential after TASK-632 to maintain the same enforcement contract across
all three execution handlers.

---

## Scope

- Modify `QueryHandler.query()` at `querysource/handlers/multi.py:32`. After
  the body is parsed and `_queries` / `_files` are extracted but **before**
  `MultiQS(...)` is constructed (or `MultiQS.query()` is awaited), run the
  pre-flight:
  1. Extract the user session via `await self._get_user_session(request)`.
  2. Build resource-name lists:
     - slugs ← list(`_queries.keys()`) (or whatever the dict layout uses)
     - files ← list(`_files.keys()`)
     - raw_queries ← any inline-query components in the body that don't have
       a slug; if your traversal can't reliably detect these, treat their
       presence as a single `raw_query:execute` requirement.
  3. Call `Guardian.filter_resources()` once per non-empty resource-type
     bucket.
  4. If any one component is denied (`result.denied` is non-empty), raise
     `web.HTTPNotFound`.
- Modify `MultiQS.__init__` at `querysource/queries/multi/__init__.py:53`
  to accept an optional `user_session: SessionData | None = None` keyword.
  Store on `self._user_session`. The constructor signature change is
  additive — pass through from the handler. **Do not** enforce inside
  `MultiQS` itself; the handler pre-flight is sufficient.
- After the pre-flight, run the same datasource/driver checks the other
  handlers do — but at the **post-build** stage where `MultiQS` has resolved
  underlying drivers. If that level of detail is impractical for v1, scope
  to the slug/file/raw-query pre-flight and document the gap in the
  Completion Note.

**NOT in scope**: enforcement on QueryService (TASK-631) or QueryExecutor
(TASK-632); changes to `MultiQS.query()`'s parallel execution loop.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/multi.py` | MODIFY | Pre-flight `Guardian.filter_resources()` calls in `query()`. |
| `querysource/queries/multi/__init__.py` | MODIFY | `MultiQS.__init__` accepts `user_session`. |
| `tests/handlers/test_multiquery_pbac_smoke.py` | CREATE | Smoke tests for the all-or-nothing rule. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.auth import ResourceType   # shim from TASK-631
# Guardian is at request.app['security'] — accessed dynamically, no import.
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/handlers/multi.py:22
class QueryHandler(AbstractHandler):
    async def query(self, request: web.Request) -> web.StreamResponse:  # line 32
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        slug = args.get('slug', None)                                   # line 38
        options = await self.json_data(request)                         # line 47
        # ... extracts _queries, _files from body ...
        qs = MultiQS(
            slug=slug, queries=_queries, files=_files,
            query=options, conditions=data,
        )                                                                # ~line 129
        result, options = await qs.query()                              # line 137

# /home/jesuslara/proyectos/parallel/querysource/querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(
        self,
        slug: str = None,
        queries: Optional[list] = None,
        files: Optional[list] = None,
        query: Optional[dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        **kwargs,
    ): ...
    async def query(self):                                              # line 102
        # iterates self._queries (line 140) and self._files (line 156)

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/guardian.py:16
class Guardian:
    async def filter_resources(
        self,
        resources: List[str],
        request: web.Request,
        resource_type: ResourceType = ResourceType.TOOL,
        action: str = "tool:execute",
    ) -> "FilteredResources":
        # Returns FilteredResources(allowed: List[str], denied: List[str], policies_applied: List[str])
```

### Does NOT Exist

- ~~Per-component `_enforce_pbac` calls in a loop~~ — use the **batch**
  `Guardian.filter_resources()`. Looping single-resource checks is
  inefficient and defeats the LRU cache key sharing.
- ~~`MultiQS.enforce_pbac`~~ — do not add enforcement to `MultiQS` itself.
  The constructor stores the session; enforcement stays in the handler.
- ~~`QueryHandler.columns`~~ — that's a separate route (line ~155) and is
  out of scope for this task. Stick to `query()`.

---

## Implementation Notes

### Pre-flight in `QueryHandler.query()`

```python
async def query(self, request: web.Request) -> web.StreamResponse:
    params = self.query_parameters(request)
    args = self.match_parameters(request)
    slug = args.get('slug', None)
    options = await self.json_data(request)
    data = await self.get_payload(request)
    # ... existing parsing of _queries, _files ...

    # ── PBAC pre-flight ─────────────────────────────────────────────
    guardian = request.app.get('security')
    if guardian is not None:
        # Will run only when PBAC is enabled.
        session = await self._get_user_session(request)
        if session is None:
            raise web.HTTPNotFound()

        await self._preflight_multiquery(
            request,
            slugs=list((_queries or {}).keys()),
            files=list((_files or {}).keys()),
            has_raw_query=any(<detect-raw-component>),
        )
    # ─────────────────────────────────────────────────────────────────

    qs = MultiQS(
        slug=slug, queries=_queries, files=_files,
        query=options, conditions=data,
        user_session=session if guardian is not None else None,
    )
    result, options = await qs.query()
    ...
```

`_preflight_multiquery` is a private helper on `QueryHandler`:

```python
async def _preflight_multiquery(self, request, slugs, files, has_raw_query):
    guardian = request.app['security']
    if slugs:
        r = await guardian.filter_resources(
            resources=slugs, request=request,
            resource_type=ResourceType.SLUG, action="slug:execute",
        )
        if r.denied:
            self.logger.info("MultiQuery denied: slugs=%s", r.denied)
            raise web.HTTPNotFound()
    if files:
        r = await guardian.filter_resources(
            resources=files, request=request,
            resource_type=ResourceType.SLUG, action="slug:execute",  # files reuse SLUG action;
                                                                      # see note below
        )
        if r.denied:
            self.logger.info("MultiQuery denied: files=%s", r.denied)
            raise web.HTTPNotFound()
    if has_raw_query:
        # Raw queries are a single binary check, not a list:
        await self._enforce_pbac(
            request,
            resource_type=ResourceType.RAW_QUERY,
            resource_name="raw_query",
            action="raw_query:execute",
        )
```

> **Note on file resources**: the spec uses `ResourceType.SLUG` for files
> in MultiQuery because they're effectively named queries served from disk.
> If implementation discovers files need their own taxonomy, document the
> deviation and use `ResourceType.RAW_QUERY` or a new value.

### `MultiQS.__init__` extension

```python
def __init__(
    self,
    slug: str = None,
    queries: Optional[list] = None,
    files: Optional[list] = None,
    query: Optional[dict] = None,
    conditions: dict = None,
    request: web.Request = None,
    loop: asyncio.AbstractEventLoop = None,
    user_session: Optional["SessionData"] = None,   # ← NEW
    **kwargs,
):
    # ... existing body ...
    self._user_session = user_session
```

The session is stored for use by downstream driver instantiation (TASK-637).
`MultiQS.query()` itself does not change.

### Key Constraints

- **All-or-nothing.** First denied resource → 404 — the entire MultiQuery
  rejects. Do NOT execute partial. The spec is explicit on this (§5).
- **Use `Guardian.filter_resources()`, not loop of `_enforce_pbac`.** This
  is one batch call per resource type — the cache key reuse and the Rust
  filter path matter for the perf budget.
- **Pre-flight runs before `MultiQS(...)` is constructed.** Don't pay the
  thread fan-out cost only to throw it away.
- **`MultiQS.__init__` change is additive.** Existing callers that don't
  pass `user_session` continue to work.

### References in Codebase

- `querysource/handlers/multi.py:32-160` — full `query()` method.
- `querysource/queries/multi/__init__.py:53-200` — `MultiQS` class.
- `navigator_auth/abac/guardian.py:16` — `filter_resources` signature.

---

## Acceptance Criteria

- [ ] `MultiQS(...)` accepts `user_session=...` without breaking existing
      callers.
- [ ] PBAC disabled: all existing MultiQuery tests pass unchanged.
- [ ] PBAC enabled, all components allowed: MultiQuery executes as today,
      identical payload.
- [ ] PBAC enabled, one slug denied (out of N): 404; **no** thread starts
      for any component (verified via mock spy on the thread starters at
      `multi/__init__.py:140,156`).
- [ ] PBAC enabled, one file denied: 404.
- [ ] PBAC enabled, raw query without `raw_query:execute`: 404.
- [ ] No regressions: `pytest tests/ -x -q` green.

---

## Test Specification

```python
# tests/handlers/test_multiquery_pbac_smoke.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from querysource.handlers.multi import QueryHandler


class TestMultiQueryPreflight:
    async def test_one_denied_rejects_all(self):
        guardian = MagicMock()
        # filter_resources returns denied non-empty for the slug list:
        guardian.filter_resources = AsyncMock(return_value=MagicMock(
            allowed=["a"], denied=["b"], policies_applied=[],
        ))

        h = QueryHandler.__new__(QueryHandler)
        h.logger = MagicMock()
        h._get_user_session = AsyncMock(return_value={"username": "alice"})
        h._enforce_pbac = AsyncMock()

        # We're testing _preflight_multiquery directly:
        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        with pytest.raises(web.HTTPNotFound):
            await h._preflight_multiquery(
                request, slugs=["a", "b"], files=[], has_raw_query=False,
            )

    async def test_all_allowed_passes(self):
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(return_value=MagicMock(
            allowed=["a", "b"], denied=[], policies_applied=[],
        ))
        h = QueryHandler.__new__(QueryHandler)
        h.logger = MagicMock()
        h._enforce_pbac = AsyncMock()
        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}
        # Should NOT raise
        await h._preflight_multiquery(
            request, slugs=["a", "b"], files=[], has_raw_query=False,
        )


class TestMultiQSConstructor:
    def test_accepts_user_session_kwarg(self):
        from querysource.queries.multi import MultiQS
        # Should not raise:
        instance = MultiQS(slug="s", user_session={"username": "alice"})
        assert instance._user_session == {"username": "alice"}

    def test_user_session_default_none(self):
        from querysource.queries.multi import MultiQS
        instance = MultiQS(slug="s")
        assert getattr(instance, '_user_session', None) is None
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 7) + 6.
2. Read `querysource/handlers/multi.py:32-160` and
   `querysource/queries/multi/__init__.py:53-200` end-to-end.
3. Determine how raw inline queries surface in the multi-query body and
   document the detection logic chosen.
4. Implement the pre-flight helper and the `MultiQS.__init__` extension.
5. Add smoke tests.
6. Run full suite `pytest tests/ -x -q`.
7. Move task to `done/` and update the index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Raw-query detection method**:

**Deviations from spec**: none | describe if any
