# TASK-829: Slug-management gate in `QueryManager` (put / post / patch)

**Feature**: FEAT-160 — Scheduler Admin Gate for Write-Capable Multi-Queries
**Spec**: `sdd/specs/multi-scheduler-admin-gate.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-827, TASK-828
**Assigned-to**: unassigned

---

## Context

Spec §2 M3 and §3 Module 3. `QueryManager.put` / `post` / `patch` store `attributes.scheduler` through
the definition repository and then call `scheduler.register_slug` (`_sync_definition_jobs`). This task
adds `QueryManager._enforce_scheduler_grant`. It runs **before** the repository write, builds the
*resulting* definition, and, when `definition_requires_scheduler_grant` (TASK-828) says so, requires
`datasource:use` on `pg_admin` through `enforce_request_pbac` (TASK-827). On deny,
`web.HTTPNotFound` propagates, nothing is written, and `register_slug` is never reached.

### How the "resulting definition" is built (decided)
1. **PBAC disabled** (`self.request.app.get('security') is None`): return immediately. The stored row
   is not read. This is required as well as being a fast path: the existing
   `tests/tenants/test_tenant_management_writes.py::_FakeRepo` has **no `get` method**, and those
   tests must stay green.
2. **Stored base**: `loaded = await repo.get(identity)` (`DefinitionRepository.get`,
   `repositories/definitions.py:161`). It raises `TenantError(error_code="query_not_found")`
   (`definitions.py:165-168`, `tenant_errors.py:15-20`) when there is no row. That means a **create**,
   so the base is `{}`. Any other `TenantError` / exception propagates, and the handler's existing
   `except TenantError` (→ `err.code`) or `except Exception` (→ 422 / 500) answers. Nothing is written
   (fail-closed). Otherwise the base is `{k: getattr(loaded.runtime, k, None) for k in ("provider", "query_raw", "attributes")}`,
   the same `getattr` projection `QSScheduler._fetch_slug_row` uses (`scheduler/scheduler.py:548-556`).
3. **Merge**: for `provider` and `query_raw`, the incoming `data[key]` wins when present. For
   `attributes`, a JSON string is first parsed with `json.loads` (left as-is when invalid). When both
   sides are dicts, they are merged **shallowly** (`{**stored, **incoming}`), so a patch of only
   `attributes.scheduler` keeps the stored pipeline in view. Otherwise the incoming value wins. This is
   deliberately conservative: if the repository *replaces* `attributes`, the shallow merge can only
   over-gate, never under-gate.
4. **Placement**: inside each method's existing `try:`, right after `identity = QueryIdentity(...)` and
   before `repo.patch` / `repo.upsert`. The same `try` gets a new first clause,
   `except web.HTTPNotFound: raise`. Without it, the existing `except Exception` would turn the deny
   into 422 (`patch`) or 500 (`put`/`post`). `aiohttp` HTTP exceptions subclass `Exception`.

`self.request` is `aiohttp.web.View.request`. navigator's `BaseView.__init__` stores it as `_request`
(`.venv/.../navigator/views/base.py:608-610`). `self.logger` is set by `BaseHandler.post_init`
(`base.py:56-57`) with `_logger_name = 'QS.Manager'` (`manager.py:48-50`).

---

## Scope

- Add module imports to `querysource/handlers/manager.py`: `json`, `typing.Any`, `aiohttp.web`,
  `ResourceType`, `enforce_request_pbac`, `definition_requires_scheduler_grant`.
- Add the module constant `_SCHEDULER_GATE_KEYS` and the method `QueryManager._enforce_scheduler_grant`.
- Call it before `repo.patch` (in `patch`) and before `repo.upsert` (in `put` and in `post`).
- Add `except web.HTTPNotFound: raise` as the first `except` of those three `try` blocks.
- Create `tests/test_manager_scheduler_gate.py`.

**NOT in scope**: the legacy (no registry / no repository) ORM branches of `put` / `post` / `patch`
are not gated. They never call `register_slug`, and `_sync_definition_jobs` only runs in the
repository branch. Also out of scope: `delete`, the scheduler sync endpoint (TASK-830), and gating
unscheduled saves or manual execution (spec §1 Non-Goals).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | imports, `_SCHEDULER_GATE_KEYS`, `QueryManager._enforce_scheduler_grant`, 3 call sites + 3 `except web.HTTPNotFound: raise` |
| `tests/test_manager_scheduler_gate.py` | CREATE | put/post/patch gate tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `71ebae0`. Line numbers are unchanged from the spec (`c26af0c`).

### Verified Imports
```python
from querysource.auth import ResourceType                                        # verified: querysource/auth/__init__.py:17 (ResourceType.DATASOURCE: _resource_types.py:43)
from querysource.auth.request_gate import enforce_request_pbac                   # created by TASK-827
from querysource.queries.multi import definition_requires_scheduler_grant        # created by TASK-828
from querysource.tenant_errors import TenantError                                # verified: manager.py:20 (already imported)
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry        # verified: manager.py:21 (already imported)
from querysource.repositories import DefinitionRepository                        # verified: manager.py:19 (already imported)
```
Importing `querysource.queries.multi` from `handlers/manager.py` creates no cycle. This was checked on
a scratch copy: `import querysource.handlers.manager` succeeds.

### Existing Signatures to Use
```python
# querysource/handlers/manager.py
from math import ceil                                             # line 11
from asyncdb.exceptions import NoDataFound                        # line 13
from ..models import QueryModel                                   # line 18
class QueryManager(QueryView):                                    # line 36
    def post_init(self, *args, **kwargs): self._logger_name = 'QS.Manager'  # line 48-50
    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:  # line 121 → scheduler.register_slug (:139)
    async def patch(self):   # line 415 — try: identity=QueryIdentity(store=store, slug=query_slug) (:462); repo.patch (:463); return self.json_response(result) (:472); except TenantError (:473); except Exception → 422
    async def put(self):     # line 671 — identity=QueryIdentity(store=store, slug=data['query_slug']) (:717); repo.upsert (:718); except TenantError (:729); except Exception → critical (500)
    async def post(self):    # line 780 — identity=QueryIdentity(store=store, slug=slug['query_slug']) (:833); repo.upsert (:834); except TenantError (:845)

# querysource/repositories/definitions.py
async def get(self, identity: QueryIdentity) -> LoadedDefinition:  # line 161; TenantError(error_code="query_not_found") at :165-168
# querysource/tenants.py
class LoadedDefinition: identity; runtime: QueryModel; revision: str   # line 54
class QueryIdentity: store: QueryStore; slug: str (frozen dataclass)   # line 46
# querysource/tenant_errors.py
class TenantError(QueryException): __init__(self, message, *, error_code); self.error_code  # line 15-20; "query_not_found": 404 (:8)

# TASK-827 — querysource/auth/request_gate.py
async def enforce_request_pbac(request, resource_type, resource_name: str, action: str, *, logger, session_resolver=None, allow_sessionless=None) -> None  # raises web.HTTPNotFound
# TASK-828 — querysource/queries/multi/__init__.py
def definition_requires_scheduler_grant(definition: Mapping[str, Any]) -> bool
```

### Does NOT Exist
- ~~`QueryManager._enforce_pbac`~~: `QueryManager` is a `QueryView` / `BaseView`, not an `AbstractHandler`. Use `enforce_request_pbac`.
- ~~`from aiohttp import web` in `manager.py`~~: not imported today (the `web.HTTPBadRequest` at :94 is only a docstring). Add it.
- ~~`import json` / `from typing import Any` in `manager.py`~~: add them.
- ~~`repo.get` on `tests/tenants/test_tenant_management_writes.py::_FakeRepo`~~: absent. That is why the PBAC-disabled early return must precede `repo.get`.
- ~~`LoadedDefinition.runtime.to_dict()` as the projection~~: use `getattr(runtime, key, None)`, as the scheduler does.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/manager.py", "action": "MODIFY"},
    {"path": "tests/test_manager_scheduler_gate.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/manager.py#QueryManager",
    "sym:querysource/handlers/manager.py#QueryManager.patch",
    "sym:querysource/handlers/manager.py#QueryManager.put",
    "sym:querysource/handlers/manager.py#QueryManager.post",
    "sym:querysource/handlers/manager.py#QueryManager._sync_definition_jobs",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.get",
    "sym:querysource/tenant_errors.py#TenantError",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/tenants.py#LoadedDefinition",
    "sym:querysource/auth/_resource_types.py#ResourceType"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- With PBAC disabled, behaviour is byte-for-byte unchanged, with no extra repository read
  (`tests/tenants/test_tenant_management_writes.py` must pass unedited).
- Read-only multis, plain query schedules and cache schedules make **no** `enforce_request_pbac` call (spec AC 3).
- The deny is 404 (`web.HTTPNotFound`) and it is raised before `repo.patch` / `repo.upsert`, so `_sync_definition_jobs` never runs.
- `_strip_selectors` has already removed `tenant` from `data` at each call site. Pass that `data` object.

---

## Implementation Blueprint

### Steps (in order)
1. Add the imports and `_SCHEDULER_GATE_KEYS`. *Why*: the gate needs `web`, `json`, `Any`, the PBAC helper and the predicate.
2. Add `_enforce_scheduler_grant` above `_sync_definition_jobs`. *Why*: the merge rule plus the gate live in one place (spec §3 M3).
3. Insert the three call sites and the three `except web.HTTPNotFound: raise` clauses. *Why*: gate before the write, and keep the deny a 404.
4. Create the tests and run the Validation Commands plus `ruff check querysource/handlers/manager.py tests/test_manager_scheduler_gate.py`.

### `querysource/handlers/manager.py` (MODIFY — imports + constant)
```python
# occurrences: 1 (verified: grep -c '^from math import ceil$' querysource/handlers/manager.py)
# REPLACE `from math import ceil` (verified: manager.py:11) with:
import json
from math import ceil
from typing import Any

# occurrences: 1 (verified: grep -c '^from asyncdb.exceptions import NoDataFound$' querysource/handlers/manager.py)
# BEFORE — insert above `from asyncdb.exceptions import NoDataFound` (verified: manager.py:13)
from aiohttp import web

# occurrences: 1 (verified: grep -c '^from ..models import QueryModel$' querysource/handlers/manager.py)
# REPLACE `from ..models import QueryModel` (verified: manager.py:18) with:
from ..auth import ResourceType
from ..auth.request_gate import enforce_request_pbac
from ..models import QueryModel
from ..queries.multi import definition_requires_scheduler_grant

# occurrences: 1 (verified: grep -c '^class QueryManager(QueryView):$' querysource/handlers/manager.py)
# BEFORE — insert above `class QueryManager(QueryView):` (verified: manager.py:36), two blank lines before the class
# FEAT-160: stored-row fields the scheduler admin gate merges with the incoming change.
_SCHEDULER_GATE_KEYS: tuple[str, str, str] = ("provider", "query_raw", "attributes")
```
**Why**: this import order passes ruff `I`. It was checked on a scratch copy.

### `querysource/handlers/manager.py` (MODIFY — `_enforce_scheduler_grant`)
```python
# occurrences: 1 (verified: grep -c '    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:' querysource/handlers/manager.py)
# BEFORE — insert above `    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:` (verified: manager.py:121)
    async def _enforce_scheduler_grant(
        self,
        repo: DefinitionRepository,
        identity: QueryIdentity,
        data: dict,
    ) -> None:
        """Gate a scheduled write-capable multi behind ``datasource:use`` on ``pg_admin`` (FEAT-160).

        The decision uses the *resulting* definition: the stored row
        (``repo.get(identity)``; ``query_not_found`` means a create, so the
        base is empty) with ``data`` merged over it for ``provider`` and
        ``query_raw`` (incoming value wins) and ``attributes`` (shallow merge,
        incoming keys win, so a patch of only ``attributes.scheduler`` keeps the
        stored pipeline in view). PBAC disabled (``app['security']`` absent)
        returns before the stored row is read.

        Args:
            repo: The definition repository the write goes through.
            identity: The definition identity being written.
            data: The incoming (selector-stripped) payload.

        Raises:
            web.HTTPNotFound: When the merged definition needs the grant and
                the caller does not hold it.
            TenantError: When reading the stored row fails with any code other
                than ``query_not_found`` (the caller's handler maps it).
        """
        if self.request.app.get('security') is None:
            return  # PBAC disabled: no stored-row read, no check
        stored: dict[str, Any] = {}
        try:
            loaded = await repo.get(identity)
        except TenantError as err:
            if err.error_code != "query_not_found":
                raise
        else:
            stored = {
                key: getattr(loaded.runtime, key, None)
                for key in _SCHEDULER_GATE_KEYS
            }
        merged: dict[str, Any] = dict(stored)
        for key in ("provider", "query_raw"):
            if key in data:
                merged[key] = data[key]
        if "attributes" in data:
            incoming = data["attributes"]
            if isinstance(incoming, str):
                try:
                    incoming = json.loads(incoming)
                except ValueError:
                    pass
            base = stored.get("attributes")
            if isinstance(incoming, dict) and isinstance(base, dict):
                merged["attributes"] = {**base, **incoming}
            else:
                merged["attributes"] = incoming
        if definition_requires_scheduler_grant(merged):
            await enforce_request_pbac(
                self.request,
                ResourceType.DATASOURCE,
                "pg_admin",
                "datasource:use",
                logger=self.logger,
            )
```

### `querysource/handlers/manager.py` (MODIFY — call sites)
```python
# The one-line spec anchors are ambiguous, so use the unique two-line contexts below:
#   grep -c '                result, is_created = await repo.upsert(identity, data)'  -> 2 (put :718, post :834)
#   grep -c '                identity = QueryIdentity(store=store, slug=query_slug)'  -> 3 (:202 get, :462 patch, :569 delete)
# Each identity line below is unique:
#   grep -c "                identity = QueryIdentity(store=store, slug=data\['query_slug'\])"  -> 1 (:717, put)
#   grep -c "                identity = QueryIdentity(store=store, slug=slug\['query_slug'\])"  -> 1 (:833, post)
#   grep -c '                result = await repo.patch(identity, data)'                          -> 1 (:463, patch)

# (1) patch — REPLACE (manager.py:462-463)
                identity = QueryIdentity(store=store, slug=query_slug)
                result = await repo.patch(identity, data)
# WITH
                identity = QueryIdentity(store=store, slug=query_slug)
                await self._enforce_scheduler_grant(repo, identity, data)
                result = await repo.patch(identity, data)

# (2) put — REPLACE (manager.py:717-718)
                identity = QueryIdentity(store=store, slug=data['query_slug'])
                result, is_created = await repo.upsert(identity, data)
# WITH
                identity = QueryIdentity(store=store, slug=data['query_slug'])
                await self._enforce_scheduler_grant(repo, identity, data)
                result, is_created = await repo.upsert(identity, data)

# (3) post — REPLACE (manager.py:833-834)
                identity = QueryIdentity(store=store, slug=slug['query_slug'])
                result, is_created = await repo.upsert(identity, data)
# WITH
                identity = QueryIdentity(store=store, slug=slug['query_slug'])
                await self._enforce_scheduler_grant(repo, identity, data)
                result, is_created = await repo.upsert(identity, data)
```

### `querysource/handlers/manager.py` (MODIFY — keep the deny a 404)
```python
# grep -c '            except TenantError as err:' -> 4 (:473 patch, :591 delete, :729 put, :845 post) — AMBIGUOUS; delete (:591) is NOT touched.
# (a) patch — unique context (grep -c '                return self.json_response(result)$' -> 1, :472). REPLACE
                return self.json_response(result)
            except TenantError as err:
# WITH
                return self.json_response(result)
            except web.HTTPNotFound:
                raise
            except TenantError as err:

# (b) put AND (c) post — the context below occurs exactly 2 times (:728-729 in put, :844-845 in post;
#     grep -c '                return self.json_response(result, status=status)' -> 2). Apply to BOTH:
                return self.json_response(result, status=status)
            except TenantError as err:
# WITH
                return self.json_response(result, status=status)
            except web.HTTPNotFound:
                raise
            except TenantError as err:
```
**Why**: without the re-raise, `except Exception` answers 422 (`patch`) or 500 (`put`/`post`) instead of the spec's 404.

### `tests/test_manager_scheduler_gate.py` (CREATE)
```python
"""FEAT-160 / TASK-829: QueryManager gate for scheduled write-capable multis."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

_PBAC = "querysource.handlers.manager.enforce_request_pbac"

WRITE_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]})
READ_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"Table": {"table": "t"}}]})
SCHEDULE = {"schedule_type": "cron", "schedule": {"hour": 1}}


def _registry() -> TenantRegistry:
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource", schema="tenant1", table="queries",
        contract="tenant", columns=frozenset({"query_slug", "description"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


class _Repo:
    """Fake DefinitionRepository: ``stored`` None -> query_not_found on get."""

    def __init__(self, stored: dict | None = None):
        self.stored = stored
        self.calls: list[tuple[str, QueryIdentity, dict]] = []

    async def get(self, identity: QueryIdentity):
        if self.stored is None:
            raise TenantError("Query not found", error_code="query_not_found")
        return SimpleNamespace(runtime=SimpleNamespace(**self.stored))

    async def upsert(self, identity: QueryIdentity, data: dict):
        self.calls.append(("upsert", identity, data))
        return {"query_slug": identity.slug}, self.stored is None

    async def patch(self, identity: QueryIdentity, data: dict):
        self.calls.append(("patch", identity, data))
        return {"query_slug": identity.slug}


def _manager(repo: _Repo, *, json_data: dict, match_info: dict | None = None, pbac: bool = True):
    from querysource.handlers.manager import QueryManager

    request = MagicMock(spec=web.Request)
    request.method = "POST"
    request.query = {}
    request.match_info = match_info or {}
    request.app = {"qs_tenant_registry": _registry(), "qs_definition_repository": repo}
    if pbac:
        request.app["security"] = MagicMock()
    manager = QueryManager(request)

    async def fake_json_data(req=None):
        return dict(json_data)

    manager.json_data = fake_json_data
    manager.get_arguments = lambda: request.match_info
    return manager


def _write_multi(**extra) -> dict:
    return {"query_slug": "s1", "provider": "multi", "query_raw": WRITE_RAW,
            "attributes": {"scheduler": SCHEDULE}, **extra}


def _assert_pg_admin_call(mock: AsyncMock) -> None:
    args = mock.await_args.args
    assert args[1:] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")


@pytest.mark.parametrize("verb", ["put", "post"])
async def test_manager_gate_denies_scheduled_write_multi(verb, monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(), match_info={"slug": "s1"} if verb == "post" else {})
    with pytest.raises(web.HTTPNotFound):
        await getattr(manager, verb)()
    assert repo.calls == []
    _assert_pg_admin_call(deny)


@pytest.mark.parametrize("verb", ["put", "post"])
async def test_manager_gate_allows_with_grant(verb, monkeypatch):
    allow = AsyncMock(return_value=None)
    monkeypatch.setattr(_PBAC, allow)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(), match_info={"slug": "s1"} if verb == "post" else {})
    response = await getattr(manager, verb)()
    assert response.status == 201
    assert repo.calls[0][0] == "upsert"
    _assert_pg_admin_call(allow)


async def test_manager_gate_read_only_multi_no_pbac_call(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored=None)
    manager = _manager(repo, json_data=_write_multi(query_raw=READ_RAW))
    response = await manager.put()
    assert response.status == 201
    pbac.assert_not_awaited()


async def test_manager_gate_pbac_disabled_skips_stored_read(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored=None)
    repo.get = AsyncMock(side_effect=AssertionError("stored row must not be read"))
    manager = _manager(repo, json_data=_write_multi(), pbac=False)
    response = await manager.put()
    assert response.status == 201
    pbac.assert_not_awaited()


async def test_manager_gate_patch_adding_write_step_is_gated(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored={"provider": "multi", "query_raw": READ_RAW, "attributes": {"scheduler": SCHEDULE}})
    manager = _manager(repo, json_data={"query_raw": WRITE_RAW}, match_info={"slug": "s1"})
    with pytest.raises(web.HTTPNotFound):
        await manager.patch()
    assert repo.calls == []


async def test_manager_gate_patch_adding_schedule_is_gated(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    repo = _Repo(stored={"provider": "multi", "query_raw": WRITE_RAW, "attributes": {"owner": "x"}})
    manager = _manager(repo, json_data={"attributes": {"scheduler": SCHEDULE}}, match_info={"slug": "s1"})
    with pytest.raises(web.HTTPNotFound):
        await manager.patch()
    assert repo.calls == []


async def test_manager_gate_patch_unscheduled_write_multi_not_gated(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = _Repo(stored={"provider": "multi", "query_raw": READ_RAW, "attributes": None})
    manager = _manager(repo, json_data={"query_raw": WRITE_RAW}, match_info={"slug": "s1"})
    response = await manager.patch()
    assert response.status == 200
    assert repo.calls[0][0] == "patch"
    pbac.assert_not_awaited()
```
**Why**: this follows `tests/tenants/test_tenant_management_writes.py::_manager_with_app` (a real
`QueryManager(request)` with a MagicMock request and a fake repository). It patches
`querysource.handlers.manager.enforce_request_pbac`, the name the module imports.

### FILL IN checklist
- [ ] None. Every block is complete and was run green on a scratch copy of `71ebae0` with TASK-827/828 applied.

---

## Acceptance Criteria

- [ ] With PBAC enabled, PUT / POST / PATCH of a scheduled write-capable multi without the grant raise 404, and `repo.upsert` / `repo.patch` are not called.
- [ ] With the grant, the same requests return today's statuses (201/202/200).
- [ ] A read-only scheduled multi, and PBAC disabled, make no `enforce_request_pbac` call. With PBAC disabled the stored row is not read.
- [ ] A patch adding `ExecuteSQL`/`TableDelete` to a scheduled multi is gated. A patch adding `attributes.scheduler` to a stored write multi is gated.
- [ ] Existing manager tests pass unedited (`tests/tenants/test_tenant_management_writes.py`, `tests/tenants/test_tenant_management_reads.py`, `tests/handlers/test_querymanager_pagination.py`).
- [ ] `ruff check querysource/handlers/manager.py tests/test_manager_scheduler_gate.py` is clean.

## Validation Commands

- `python -m pytest tests/test_manager_scheduler_gate.py -q -p no:cacheprovider`
- `python -m pytest tests/tenants/test_tenant_management_writes.py -q -p no:cacheprovider`
- `python -m pytest tests/tenants/test_tenant_management_reads.py -q -p no:cacheprovider`
- `python -m pytest tests/handlers/test_querymanager_pagination.py -q -p no:cacheprovider`

---

## Test Specification

See the `tests/test_manager_scheduler_gate.py` block above. It covers spec §4 `test_manager_gate_*`.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-scheduler-admin-gate --feature-id FEAT-160`).
   **Environment:** in a fresh worktree, first run `python setup.py build_ext --inplace`, because the Cython
   `.so` files are not versioned. Then run every test from the worktree root with the shared venv, as
   `python -m pytest <file> -q -p no:cacheprovider`.
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: TASK-827 and TASK-828 must be `"done"` in `sdd/tasks/index/multi-scheduler-admin-gate.json`.
4. **Verify the Codebase Contract**: re-run every `grep -c` above. Confirm the counts (1 / 2 / 3 / 4) before editing.
5. **Update status** in `sdd/tasks/index/multi-scheduler-admin-gate.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint. Never change a signature or path the blueprint fixes.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-829 multi-scheduler-admin-gate verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Added QueryManager._enforce_scheduler_grant and gated put/post/patch before repo write, with except web.HTTPNotFound: raise. 9 new tests + existing manager/tenant tests pass; ruff clean (one isort fix applied to imports).

**Deviations from spec**: none
