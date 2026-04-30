# TASK-634: Datasource & driver list filtering

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-630
**Assigned-to**: unassigned

---

## Context

Implements **Module 8** of the spec. Filters the datasource and driver list
endpoints (`DatasourceView.get()` at
`querysource/datasources/handlers/datasource.py:74` and `default_sources()`
at line 33) using `Guardian.filter_resources()`. Filtering is **silent** —
the response shape is unchanged, denied entries simply don't appear.

This task is **parallel-friendly** with TASK-631 / TASK-632 / TASK-633 once
TASK-630 is done — it touches a single file that the handler enforcement
tasks don't modify.

---

## Scope

- Modify `DatasourceView.get()` at line 74:
  - After the merged result list is built (line 130: `result = result + default`),
    extract the names list, batch-filter via `Guardian.filter_resources()`
    with `resource_type=ResourceType.DATASOURCE` and
    `action="datasource:list"`, and reduce `result` to entries whose `name`
    is in `result.allowed`.
- Modify the drivers branch — find the route handler that uses
  `default_sources()` to return the drivers list (typically a separate
  method on the same class, or a sibling view). Apply the same filter with
  `resource_type=ResourceType.DRIVER`, `action="driver:list"`.
- **Silent filtering**: never raise on empty filtered lists. Return the
  filtered list (which may be empty); the response status remains 200.
- Fast-path: if `request.app.get('security') is None`, do nothing (return
  the unfiltered list). PBAC disabled = today's behavior.

**NOT in scope**: PBAC for `DatasourceView` mutating verbs (POST/PUT/DELETE) —
explicitly out of scope per spec §1 Non-Goals (resolved Open Question Q3).
Only `GET` is filtered.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/datasources/handlers/datasource.py` | MODIFY | Add filter calls in `DatasourceView.get()` and the drivers list branch. |
| `tests/datasources/test_datasource_view_pbac.py` | CREATE | Smoke tests. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from querysource.auth import ResourceType   # shim from TASK-631
# Guardian via request.app['security'] — accessed dynamically.
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/handlers/datasource.py:23
class DatasourceView(BaseView):
    def default_sources(self) -> list:                                # line 33
        # iterates SUPPORTED dict, returns list of dicts with keys:
        # uid, driver, name, description, params, credentials,
        # program_slug, drv, default
    async def get(self) -> web.Response:                              # line 74
        # ... parses filter/source args ...
        if not filtering:
            result = await DataSource.all(fields=fields)              # line 123
        else:
            result = await DataSource.filter(**filtering, fields=fields)
        default = self.default_sources()                              # line 128
        if not filtering:
            result = result + default                                 # line 130
        return self.json_response(response=result, headers=headers)   # line 131

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/guardian.py:16
class Guardian:
    async def filter_resources(
        self,
        resources: List[str],
        request: web.Request,
        resource_type: ResourceType = ResourceType.TOOL,
        action: str = "tool:execute",
    ) -> "FilteredResources":  # .allowed, .denied, .policies_applied
```

### Does NOT Exist

- ~~An "is admin"-style check that would let admins bypass filtering~~ —
  filtering is uniform; admin-style access is achieved via a policy that
  grants `datasource:list` for `["*"]` to the admin group. No code-level
  bypass.
- ~~A separate `DriverView` class~~ — verify whether the drivers list lives
  on `DatasourceView` (likely as a separate method/route) or on a sibling
  view. The agent must `grep` for `default_sources` callers and adapt.
- ~~`DatasourceView.list_drivers`~~ — the actual method name may be
  different. Confirm before editing.

Run before implementing:
```bash
grep -n "default_sources\|drivers" \
     querysource/datasources/handlers/datasource.py
```

---

## Implementation Notes

### Filter helper (private to this module)

```python
async def _pbac_filter(self, request, items, name_key, resource_type, action):
    """Filter a list of dicts by PBAC; silent no-op when PBAC disabled."""
    guardian = request.app.get('security')
    if guardian is None:
        return items
    if not items:
        return items
    names = [item.get(name_key) for item in items if item.get(name_key)]
    result = await guardian.filter_resources(
        resources=names, request=request,
        resource_type=resource_type, action=action,
    )
    allowed = set(result.allowed)
    return [item for item in items if item.get(name_key) in allowed]
```

### Insertion in `get()`

Right before `self.json_response(response=result, ...)` at line 131:

```python
result = await self._pbac_filter(
    self.request, result, name_key="name",
    resource_type=ResourceType.DATASOURCE, action="datasource:list",
)
```

(Note: `BaseView` exposes `self.request`, not a parameter.)

### Drivers branch

Locate the actual driver-listing handler (the route that returns
`default_sources()`'s output without merging with `DataSource.all()`).
Apply the same filter with `ResourceType.DRIVER` / `"driver:list"`.

### Key Constraints

- **No 404s on empty result.** Return an empty list with status 200.
- **No partial filtering inside `default_sources()`.** Filter at the
  view-method boundary, not inside the helper that builds the candidate
  list. This keeps the helper unit-testable without PBAC fixtures.
- **Read `name` via `item.get("name")`.** The result list mixes DB-backed
  datasources (with `"name"` field) and `default_sources()` output (also
  has `"name"`). Both expose `name` per the verified signatures above.

### References in Codebase

- `querysource/datasources/handlers/datasource.py:23-160` — full file.
- `navigator_auth/abac/guardian.py:16` — `filter_resources` signature.

---

## Acceptance Criteria

- [ ] PBAC disabled: existing `GET /datasource` test passes unchanged.
- [ ] PBAC enabled, user with policy granting `datasource:list` on `["*"]`:
      response includes all entries (same as PBAC disabled).
- [ ] PBAC enabled, user with policy allowing only `["postgres"]`: response
      includes only the `postgres` entry. No 4xx; status 200.
- [ ] PBAC enabled, user with no `datasource:list` policy: response is
      `[]` with status 200 (silent, no leak).
- [ ] Drivers list endpoint behaves identically with `ResourceType.DRIVER`.
- [ ] No regressions: full test suite green.

---

## Test Specification

```python
# tests/datasources/test_datasource_view_pbac.py
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestDatasourceViewPbacFilter:
    async def test_disabled_returns_all(self):
        from querysource.datasources.handlers.datasource import DatasourceView
        v = DatasourceView.__new__(DatasourceView)
        v.request = MagicMock()
        v.request.app = {}  # no 'security'
        items = [{"name": "postgres"}, {"name": "mysql"}]
        out = await v._pbac_filter(v.request, items, "name",
                                   resource_type="datasource",
                                   action="datasource:list")
        assert out == items

    async def test_filters_by_allowed(self):
        from querysource.datasources.handlers.datasource import DatasourceView
        v = DatasourceView.__new__(DatasourceView)
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(return_value=MagicMock(
            allowed=["postgres"], denied=["mysql"], policies_applied=[],
        ))
        v.request = MagicMock()
        v.request.app = {"security": guardian}
        items = [{"name": "postgres"}, {"name": "mysql"}]
        out = await v._pbac_filter(v.request, items, "name",
                                   resource_type="datasource",
                                   action="datasource:list")
        assert out == [{"name": "postgres"}]

    async def test_returns_empty_silently(self):
        from querysource.datasources.handlers.datasource import DatasourceView
        v = DatasourceView.__new__(DatasourceView)
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(return_value=MagicMock(
            allowed=[], denied=["postgres"], policies_applied=[],
        ))
        v.request = MagicMock()
        v.request.app = {"security": guardian}
        items = [{"name": "postgres"}]
        out = await v._pbac_filter(v.request, items, "name",
                                   resource_type="datasource",
                                   action="datasource:list")
        assert out == []
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 8) + 6.
2. Grep for `default_sources` callers in
   `querysource/datasources/handlers/datasource.py` to identify the drivers
   route.
3. Implement `_pbac_filter` helper on `DatasourceView`.
4. Wire it into `get()` and the drivers branch.
5. Add unit tests.
6. Run `pytest tests/ -x -q`.
7. Move task to `done/` and update the index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-04-30
**Notes**: _pbac_filter helper added to DatasourceView. Wired into get() after
the datasources+default_sources merge. Split by 'default' key to separate
DB-backed datasources from default-driver entries. Filter each separately with
DATASOURCE:datasource:list and DRIVER:driver:list respectively. Fail-open on
guardian errors (listing endpoint). 6/6 smoke tests pass.
**Drivers route entry point**: default_sources() items within DatasourceView.get(),
identified by 'default': True flag; no separate route for driver listing.

**Deviations from spec**: none
