# TASK-830: Scheduler sync gate in `SchedulerJobsView.post`

**Feature**: FEAT-160 — Scheduler Admin Gate for Write-Capable Multi-Queries
**Spec**: `sdd/specs/multi-scheduler-admin-gate.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-827, TASK-828
**Assigned-to**: unassigned

---

## Context

Spec §2 M4 and §3 Module 4. `POST /api/v1/qs/scheduler/jobs` (`SchedulerJobsView.post`) registers a
stored slug into the live scheduler (`scheduler.register_slug`). It is the second write point of a
schedule. This task adds `SchedulerJobsView._enforce_scheduler_grant(slug, tenant)` and calls it after
body/tenant validation and before `register_slug`. When the stored definition is a scheduled
write-capable multi (`definition_requires_scheduler_grant`, TASK-828), it requires `datasource:use` on
`pg_admin` through `enforce_request_pbac` (TASK-827).

### How the stored row is resolved (decided, mirrors `QSScheduler`)
- `QSScheduler.startup` takes `self._registry = app.get("qs_tenant_registry") or TenantRegistry()` and
  `self._repository = app.get("qs_definition_repository")` (`scheduler/scheduler.py:683-684`).
  `register_slug` resolves `store = self._registry.resolve(tenant)` (`:615`), and
  `_fetch_slug_row` does `self._repository.get(QueryIdentity(store=store, slug=slug))` (`:516-526`)
  with the `getattr(runtime, …)` projection (`:548-556`).
- The view uses **the same resolution**, read from `self.request.app`:
  `registry = app.get("qs_tenant_registry") or TenantRegistry()`. The `or TenantRegistry()` fallback
  is deliberate. Without it, an app with a repository but no published registry would sync an ungated
  job through the scheduler's own fallback.
- **Unchanged behaviour (return without a check), so `register_slug` reports as today:**
  - PBAC disabled (`app.get("security") is None`, checked first, with no DB read);
  - no repository (`register_slug` then fetches no row and registers nothing);
  - `registry.resolve(tenant)` raises `TenantError` (`register_slug` raises the same one, and the
    existing handler maps it to `exc.code`);
  - `repo.get` raises `TenantError(error_code="query_not_found")` (`register_slug` only removes jobs).
- **Fail-closed**: any other `repo.get` failure (store unavailable, driver error) → `logger.warning`
  plus `web.HTTPNotFound`. Otherwise the gate would skip a transiently unreadable row, and
  `register_slug` might then read and register it ungated.
- **Placement**: the call goes **above** the `try:` that wraps `register_slug` (`handlers/scheduler.py:258`).
  That `try` ends in `except Exception → 500`, which would swallow the 404.

The view uses the module logger `logger = logging.getLogger("QS.SchedulerJobsView")`
(`handlers/scheduler.py:35`), not `self.logger`, because unit tests build the view with
`SchedulerJobsView.__new__` (`tests/test_scheduler_handler_unit.py:170`), so `post_init` never runs.
The view reaches the app through `self.request.app` (`web.View.request`, stored as `_request` by
navigator `BaseView.__init__`).

---

## Scope

- Add module imports to `querysource/handlers/scheduler.py`: `ResourceType`, `enforce_request_pbac`,
  `definition_requires_scheduler_grant`, `QueryIdentity`, `TenantRegistry`.
- Add `SchedulerJobsView._enforce_scheduler_grant` and call it in `post` before the `register_slug` `try:`.
- Create `tests/test_scheduler_sync_gate.py`.

**NOT in scope**: `delete` / `patch` (pause / resume) on the jobs view, which only remove or pause jobs.
Also out of scope: re-validating rows at `QSScheduler.startup` (spec Non-Goals) and `QueryManager`
(TASK-829).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/scheduler.py` | MODIFY | imports, `SchedulerJobsView._enforce_scheduler_grant`, call before `register_slug` |
| `tests/test_scheduler_sync_gate.py` | CREATE | sync gate tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `71ebae0`. Line numbers are unchanged from the spec (`c26af0c`).

### Verified Imports
```python
from querysource.auth import ResourceType                                  # verified: querysource/auth/__init__.py:17
from querysource.auth.request_gate import enforce_request_pbac             # created by TASK-827
from querysource.queries.multi import definition_requires_scheduler_grant  # created by TASK-828
from querysource.tenant_errors import TenantError                          # verified: handlers/scheduler.py:28 (already imported)
from querysource.tenants import QueryIdentity, TenantRegistry              # verified: querysource/tenants.py:46,80 (used the same way by scheduler/scheduler.py)
```
Importing `querysource.queries.multi` from `handlers/scheduler.py` creates no cycle. This was checked
on a scratch copy: `import querysource.handlers.scheduler` succeeds.

### Existing Signatures to Use
```python
# querysource/handlers/scheduler.py
from __future__ import annotations                               # line 20
logger = logging.getLogger("QS.SchedulerJobsView")               # line 35
class SchedulerJobsView(BaseView):                               # line 70
    def _get_scheduler(self) -> QSScheduler | None:              # line 84 — self.request.app.get("qs_scheduler")
    def _serialize_job(self, job: Job) -> dict:                  # line 94 — insertion anchor (insert ABOVE it)
    async def post(self) -> web.Response:                        # line 208
        tenant = body.get("tenant") ...  # validated non-empty str or None (:251-256)
        try:                                                     # line 258
            result = await scheduler.register_slug(slug, tenant=tenant)  # line 259
        except TenantError as exc: ... status=exc.code           # :260-268
        except Exception as exc: ... status=500                  # :269-274

# querysource/tenants.py
class TenantRegistry: def __init__(self) -> None; def resolve(self, tenant: str | None = None) -> QueryStore  # lines 80, 83, 402 (raises TenantError tenant_not_available)
# querysource/repositories/definitions.py:161
async def get(self, identity: QueryIdentity) -> LoadedDefinition  # TenantError(error_code="query_not_found") on missing row
# querysource/scheduler/scheduler.py
self._registry = app.get("qs_tenant_registry") or TenantRegistry()   # line 683
async def register_slug(self, slug: str, *, tenant: str | None = None) -> dict:  # line 586
```

### Does NOT Exist
- ~~`SchedulerJobsView._enforce_pbac`~~: `BaseView` has none. Use `enforce_request_pbac`.
- ~~`self.logger` on a `__new__`-built view~~: use the module `logger`.
- ~~A public `QSScheduler.fetch_row` / `registry` accessor~~: `_fetch_slug_row` / `_registry` are private. Do not call them; resolve from `app` as described above.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/scheduler.py", "action": "MODIFY"},
    {"path": "tests/test_scheduler_sync_gate.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/scheduler.py#SchedulerJobsView",
    "sym:querysource/handlers/scheduler.py#SchedulerJobsView.post",
    "sym:querysource/handlers/scheduler.py#SchedulerJobsView._serialize_job",
    "sym:querysource/tenants.py#TenantRegistry",
    "sym:querysource/tenants.py#TenantRegistry.resolve",
    "sym:querysource/tenants.py#QueryIdentity",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.get",
    "sym:querysource/scheduler/scheduler.py#QSScheduler.register_slug",
    "sym:querysource/tenant_errors.py#TenantError",
    "sym:querysource/auth/_resource_types.py#ResourceType"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Existing scheduler handler tests build `request.app = {"qs_scheduler": scheduler}` with no
  `security`, so the gate is a no-op for them and they must pass unedited.
- `tests/test_scheduler_handler_integration.py::TestSchedulerJobsAPI::test_post_returns_405` **already
  fails on `dev` at `71ebae0`** (400 != 405), independent of this task. It is therefore not a gate.
  Check that it is still the only failure in that file.
- A read-only multi, a plain query schedule and a cache schedule make no `enforce_request_pbac` call.

---

## Implementation Blueprint

### Steps (in order)
1. Add the imports. *Why*: the gate needs the PBAC helper, the predicate and the tenant types.
2. Add `_enforce_scheduler_grant` above `_serialize_job`. *Why*: it mirrors the scheduler's store/row resolution (spec §3 M4).
3. Insert the call above the `register_slug` `try:`. *Why*: the 404 must not be swallowed by `except Exception`.
4. Create the tests and run the Validation Commands plus `ruff check querysource/handlers/scheduler.py tests/test_scheduler_sync_gate.py`.

### `querysource/handlers/scheduler.py` (MODIFY — imports)
```python
# occurrences: 1 (verified: grep -c '^from querysource.tenant_errors import TenantError$' querysource/handlers/scheduler.py)
# REPLACE `from querysource.tenant_errors import TenantError` (verified: handlers/scheduler.py:28) with:
from querysource.auth import ResourceType
from querysource.auth.request_gate import enforce_request_pbac
from querysource.queries.multi import definition_requires_scheduler_grant
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, TenantRegistry
```

### `querysource/handlers/scheduler.py` (MODIFY — `_enforce_scheduler_grant`)
```python
# occurrences: 1 (verified: grep -c '    def _serialize_job(self, job: Job) -> dict:' querysource/handlers/scheduler.py)
# BEFORE — insert above `    def _serialize_job(self, job: Job) -> dict:` (verified: handlers/scheduler.py:94)
    async def _enforce_scheduler_grant(self, slug: str, tenant: str | None) -> None:
        """Gate syncing a scheduled write-capable multi behind ``pg_admin`` (FEAT-160).

        Loads the stored definition the same way ``QSScheduler`` does: the
        store comes from ``app['qs_tenant_registry']`` (or a default
        ``TenantRegistry()``, as ``QSScheduler.startup`` falls back to) resolved
        with ``tenant``, and the row from ``app['qs_definition_repository']``.
        PBAC disabled, no repository, an unresolvable tenant, or a missing row
        return without a check, so ``register_slug`` reports exactly as
        before. Any other read failure is fail-closed (404).

        Args:
            slug: The query slug being synced.
            tenant: The validated tenant selector from the body (None = default).

        Raises:
            web.HTTPNotFound: When the stored definition needs the grant and
                the caller does not hold it, or the stored row cannot be read.
        """
        app = self.request.app
        if app.get("security") is None:
            return  # PBAC disabled: no stored-row read, no check
        repo = app.get("qs_definition_repository")
        if repo is None:
            return  # register_slug cannot read a row either: nothing is registered
        registry = app.get("qs_tenant_registry") or TenantRegistry()
        try:
            store = registry.resolve(tenant)
        except TenantError:
            return  # register_slug raises and reports the same TenantError
        try:
            loaded = await repo.get(QueryIdentity(store=store, slug=slug))
        except TenantError as exc:
            if exc.error_code == "query_not_found":
                return  # missing row: register_slug only removes jobs
            logger.warning(
                "Scheduler admin gate: cannot read slug '%s' (%s); denying", slug, exc
            )
            raise web.HTTPNotFound() from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Scheduler admin gate: cannot read slug '%s' (%s); denying", slug, exc
            )
            raise web.HTTPNotFound() from exc
        runtime = loaded.runtime
        row = {
            "provider": getattr(runtime, "provider", None),
            "query_raw": getattr(runtime, "query_raw", None),
            "attributes": getattr(runtime, "attributes", None),
        }
        if definition_requires_scheduler_grant(row):
            await enforce_request_pbac(
                self.request,
                ResourceType.DATASOURCE,
                "pg_admin",
                "datasource:use",
                logger=logger,
            )
```

### `querysource/handlers/scheduler.py` (MODIFY — call site in `post`)
```python
# occurrences: 1 (verified: grep -c '            result = await scheduler.register_slug(slug, tenant=tenant)' querysource/handlers/scheduler.py)
# `        try:` alone occurs 3× (:228, :258, :330), so target the two-line context (:258-259). REPLACE
        try:
            result = await scheduler.register_slug(slug, tenant=tenant)
# WITH
        # FEAT-160: a scheduled write-capable multi needs pg_admin to be synced.
        await self._enforce_scheduler_grant(slug, tenant)

        try:
            result = await scheduler.register_slug(slug, tenant=tenant)
```

### `tests/test_scheduler_sync_gate.py` (CREATE)
```python
"""FEAT-160 / TASK-830: scheduler sync endpoint gate for scheduled write-capable multis."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.handlers.scheduler import SchedulerJobsView
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore, TenantRegistry

_PBAC = "querysource.handlers.scheduler.enforce_request_pbac"

WRITE_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]})
READ_RAW = json.dumps({"queries": {"a": {"slug": "report_a"}}})
SCHEDULE = {"scheduler": {"schedule_type": "cron", "schedule": {"hour": 1}}}


def _registry() -> TenantRegistry:
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource", schema="tenant1", table="queries",
        contract="tenant", columns=frozenset({"query_slug"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


def _repo(stored: dict | None) -> MagicMock:
    repo = MagicMock()
    if stored is None:
        repo.get = AsyncMock(side_effect=TenantError("Query not found", error_code="query_not_found"))
    else:
        repo.get = AsyncMock(return_value=SimpleNamespace(runtime=SimpleNamespace(**stored)))
    return repo


def _view(*, repo, pbac: bool = True, body: dict | None = None):
    scheduler = MagicMock()
    scheduler.register_slug = AsyncMock(return_value={"slug": "s1", "registered": [], "removed": []})
    request = MagicMock()
    request.json = AsyncMock(return_value=body or {"slug": "s1"})
    request.app = {"qs_scheduler": scheduler, "qs_tenant_registry": _registry()}
    if repo is not None:
        request.app["qs_definition_repository"] = repo
    if pbac:
        request.app["security"] = MagicMock()
    view = SchedulerJobsView.__new__(SchedulerJobsView)
    view._request = request
    return view, scheduler


async def test_scheduler_sync_gate_denies_write_multi(monkeypatch):
    deny = AsyncMock(side_effect=web.HTTPNotFound())
    monkeypatch.setattr(_PBAC, deny)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE}))
    with pytest.raises(web.HTTPNotFound):
        await view.post()
    scheduler.register_slug.assert_not_awaited()
    assert deny.await_args.args[1:] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")


async def test_scheduler_sync_gate_allows_with_grant(monkeypatch):
    allow = AsyncMock(return_value=None)
    monkeypatch.setattr(_PBAC, allow)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE}))
    response = await view.post()
    assert response.status == 200
    scheduler.register_slug.assert_awaited_once_with("s1", tenant=None)


async def test_scheduler_sync_gate_read_only_registered(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    view, scheduler = _view(repo=_repo({"provider": "multi", "query_raw": READ_RAW, "attributes": SCHEDULE}))
    response = await view.post()
    assert response.status == 200
    pbac.assert_not_awaited()
    scheduler.register_slug.assert_awaited_once()


@pytest.mark.parametrize("case", ["missing_row", "no_repo", "pbac_off", "unknown_tenant"])
async def test_scheduler_sync_gate_unchanged_behaviour(case, monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = None if case == "no_repo" else _repo(None if case == "missing_row" else {
        "provider": "multi", "query_raw": WRITE_RAW, "attributes": SCHEDULE,
    })
    body = {"slug": "s1", "tenant": "nope"} if case == "unknown_tenant" else None
    view, scheduler = _view(repo=repo, pbac=case != "pbac_off", body=body)
    await view.post()
    pbac.assert_not_awaited()
    scheduler.register_slug.assert_awaited_once()


async def test_scheduler_sync_gate_store_failure_fails_closed(monkeypatch):
    pbac = AsyncMock()
    monkeypatch.setattr(_PBAC, pbac)
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=TenantError("down", error_code="tenant_store_unavailable"))
    view, scheduler = _view(repo=repo)
    with pytest.raises(web.HTTPNotFound):
        await view.post()
    scheduler.register_slug.assert_not_awaited()
```
**Why**: this follows `tests/test_scheduler_handler_unit.py:166-175` (a `__new__` view plus a MagicMock
request with a dict `app`). It patches `querysource.handlers.scheduler.enforce_request_pbac`.

### FILL IN checklist
- [ ] None. Every block is complete and was run green on a scratch copy of `71ebae0` with TASK-827/828 applied.

---

## Acceptance Criteria

- [ ] With PBAC enabled, syncing a stored scheduled write multi without the grant raises 404, and `register_slug` is not awaited.
- [ ] With the grant, the response is 200 as today. A read-only multi is registered with no PBAC call.
- [ ] A missing row, no repository, PBAC disabled, or an unknown tenant give unchanged behaviour: `register_slug` is awaited and there is no PBAC call.
- [ ] A store read failure is fail-closed (404, `register_slug` not awaited).
- [ ] Existing scheduler handler tests pass unedited (`tests/test_scheduler_handler_unit.py`, `tests/tenants/test_tenant_scheduler_api_jobs.py`).
- [ ] `ruff check querysource/handlers/scheduler.py tests/test_scheduler_sync_gate.py` is clean.

## Validation Commands

- `python -m pytest tests/test_scheduler_sync_gate.py -q -p no:cacheprovider`
- `python -m pytest tests/test_scheduler_handler_unit.py -q -p no:cacheprovider`
- `python -m pytest tests/tenants/test_tenant_scheduler_api_jobs.py -q -p no:cacheprovider`

---

## Test Specification

See the `tests/test_scheduler_sync_gate.py` block above. It covers spec §4 `test_scheduler_sync_gate_*`.

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
4. **Verify the Codebase Contract**: re-run every `grep -c` above. If a count differs, re-locate the anchor before editing.
5. **Update status** in `sdd/tasks/index/multi-scheduler-admin-gate.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint. Never change a signature or path the blueprint fixes.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-830 multi-scheduler-admin-gate verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Added SchedulerJobsView._enforce_scheduler_grant and call before register_slug. 8 new tests + existing scheduler tests pass; ruff clean.

**Deviations from spec**: none
