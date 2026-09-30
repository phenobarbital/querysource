---
type: feature
base_branch: dev
projects: [scheduler, handlers, auth, multiquery]
tags: [scheduler, pbac, pg-admin, write-steps, multiquery]
---

# Feature Specification: Scheduler Admin Gate for Write-Capable Multi-Queries

**Feature ID**: FEAT-160
**Date**: 2026-09-30
**Author**: Juan2coder (decision by Jesus Lara)
**Status**: approved
**Target version**: 5.2.0

---

## 1. Motivation & Business Requirements

### Problem Statement
FEAT-155/156/157 let a MultiQuery pipeline write with the full-access `DB*` credentials, through
`TableDelete` and `ExecuteSQL` Output steps and through source `pre-hook`/`post-hook`. The
`pg_admin` write gate (`datasource:use` on `pg_admin`) is enforced only for **inline** pipelines
sent over HTTP (`QueryHandler._preflight_multiquery`, `write_access`) and for principal-based
callers.

A multi **stored as a slug** and **scheduled** (`attributes.scheduler`) runs unattended from
`scheduled_multiqs_job`. That job builds `MultiQS(slug=slug, tenant=tenant)` with no principal
(`querysource/scheduler/jobs.py:144`), so nothing checks who authorised the write. Jesus Lara:
*"send it to the scheduler … in the absence of peer review it is dangerous"*. His two options
were (1) draft plus admin review, as for ETLs, or (2) only admins may send to the scheduler.
**Option 2 is chosen for v1.**

### Goals
- Scheduling a **write-capable multi-query** requires the caller to hold `datasource:use` on
  `pg_admin`. A multi is write-capable when its stored pipeline (`query_raw` of a
  `provider == "multi"` definition):
  - has an `Output` step in `WRITE_DESTINATIONS`; or
  - has any `queries` entry that declares `pre-hook` or `post-hook`.
- The check runs at **both write points of a schedule**:
  1. the slug-management API (`QueryManager.put` / `post` / `patch`, `querysource/handlers/manager.py`),
     which stores `attributes.scheduler` and then calls `scheduler.register_slug`;
  2. the scheduler sync endpoint (`SchedulerJobsView.post`, `querysource/handlers/scheduler.py:208`),
     which registers a stored slug into the live scheduler.
- **Scope is only multis that write (decided by Juan2coder).** Read-only multis, plain query
  schedules and cache schedules are unaffected.
- The check evaluates the **resulting** definition, i.e. the stored row merged with the incoming
  change. So adding `ExecuteSQL` to an already-scheduled multi, or adding a schedule to an existing
  write multi, are both gated.
- PBAC disabled means no change: the existing fast-path no-op applies.
- The request-scoped PBAC check is reusable outside `AbstractHandler`, because both views derive
  from navigator's `BaseView` and have no `_enforce_pbac`.

### Non-Goals (explicitly out of scope)
- Option 1 (draft + admin review / approval workflow).
- Gating **manual HTTP execution** of a stored write multi by a non-admin, or gating **saving** a
  write multi *without* a schedule. Both remain possible in v1 and are recorded in §7 and §8.
- Re-validating already-stored rows at scheduler startup (`QSScheduler` bulk load). Rows are trusted
  as saved; write steps only exist from FEAT-155 onwards, so this gate should merge together with,
  or before, FEAT-156.
- Changing the deny status convention. PBAC denials answer **404**, as `_enforce_pbac` does today.

---

## 2. Architectural Design

### Overview

**M1: request-scoped PBAC helper (behaviour-preserving extraction).**
- Move the body of `AbstractHandler._enforce_pbac` (`handlers/abstract.py:326`) into a module-level
  coroutine `enforce_request_pbac(request, resource_type, resource_name, action, *, logger)` in
  `querysource/auth/request_gate.py`. It covers the fast-path no-op, fail-closed on a missing
  resource name or session, sessionless authz, `resolve_evaluator`, `build_eval_context`, `evaluate`,
  and 404 on deny.
- Move the memoised session lookup of `_get_user_session` (`:298`) to `get_request_session(request, *, logger)`.
- The two `AbstractHandler` methods become thin delegates, so all existing behaviour and tests are
  unchanged.

**M2: write-capability predicate.** In `querysource/queries/multi/__init__.py`:
- `pipeline_requires_write_grant(pipeline: object) -> bool` returns True when
  `_output_step_names(pipeline.get("Output")) & WRITE_DESTINATIONS`, or when any
  `pipeline["queries"]` value is a dict containing a key in `SOURCE_HOOK_KEYS = ("pre-hook", "post-hook")`.
- `definition_requires_scheduler_grant(definition: Mapping) -> bool`:
  - True only when `provider == "multi"`, `attributes.scheduler` is truthy, and the pipeline parsed
    from `query_raw` is write-capable;
  - `query_raw` that is not valid JSON counts as not write-capable, because MultiQS then falls back to
    single-query mode (`scheduler.py:328-341`).
- The helpers read `WRITE_DESTINATIONS` at call time, so FEAT-156's `ExecuteSQL` is picked up
  automatically. `SOURCE_HOOK_KEYS` uses the same literal keys as FEAT-157's `HOOK_KEYS`.
  Whichever of FEAT-157 and FEAT-160 lands second makes one import the other; see §7.

**M3: slug-management gate (`QueryManager`).**
- In `put` and `post`, before `repo.upsert`: build the resulting definition from the incoming
  `data`, merged over the existing row when one exists (`repo.get(identity)`, a missing row means
  a create). If `definition_requires_scheduler_grant(merged)`, then
  `await enforce_request_pbac(self.request, ResourceType.DATASOURCE, "pg_admin", "datasource:use", logger=self.logger)`.
- In `patch`, do the same before `repo.patch`, always merging over the stored row.
- A deny raises `web.HTTPNotFound`. Nothing is written and `register_slug` is not called.

**M4: scheduler sync gate (`SchedulerJobsView.post`).**
- After body validation and before `scheduler.register_slug` (`handlers/scheduler.py:259`), load the
  stored definition through `app['qs_tenant_registry']` / `app['qs_definition_repository']` with the
  same tenant selector.
- If `definition_requires_scheduler_grant(row)`, enforce the same grant.
- A missing row, or a registry/repository that is not configured, keeps today's behaviour:
  `register_slug` reports it.

### Component Diagram
```
PUT/POST/PATCH /queries (QueryManager) ─┐
                                        ├─ merge(stored row, incoming data)
POST scheduler sync (SchedulerJobsView) ┘        │
                                   definition_requires_scheduler_grant()   (multi/__init__.py)
                                        │ True
                         enforce_request_pbac(DATASOURCE,"pg_admin","datasource:use")   (auth/request_gate.py)
                                        │ allowed
                         repo.upsert/patch  →  scheduler.register_slug
AbstractHandler._enforce_pbac / _get_user_session ──delegate──→ auth/request_gate.py
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractHandler._enforce_pbac` / `_get_user_session` | refactor (delegate) | behaviour unchanged |
| `QueryManager.put/post/patch` | modifies | gate before repository write |
| `SchedulerJobsView.post` | modifies | gate before `register_slug` |
| `WRITE_DESTINATIONS`, `_output_step_names` (FEAT-155) | uses | read at call time |
| `DefinitionRepository.get` | uses | stored row for merge / sync check |

### Data Models
None new.

### New Public Interfaces
```python
async def enforce_request_pbac(request, resource_type, resource_name: str, action: str, *, logger) -> None
async def get_request_session(request, *, logger) -> "SessionData | None"
def pipeline_requires_write_grant(pipeline: object) -> bool
def definition_requires_scheduler_grant(definition: Mapping[str, Any]) -> bool
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: `auth/request_gate.py` extraction | yes | move bodies verbatim; handler methods delegate; no behaviour change | — |
| M2: write-capability predicates | yes | signatures + rules in §2 | — |
| M3: `QueryManager` gate | yes | merge rule + gate placement in §2 | — |
| M4: scheduler sync gate | yes | load + gate placement in §2 | — |

### Module 1: Request-scoped PBAC helper
- **Path**: `querysource/auth/request_gate.py` (new); `querysource/handlers/abstract.py` (modify)
- **Responsibility**: A PBAC enforcement usable from any aiohttp view, not only `AbstractHandler`.
- **Depends on**: existing `querysource/auth/enforcement.py`
- **Interface Skeleton**:
  ```python
  # querysource/auth/request_gate.py  (new)
  from aiohttp import web
  from navigator_session import SessionData, get_session                 # verified: handlers/abstract.py:8
  from .enforcement import build_eval_context, evaluate, resolve_evaluator  # verified: handlers/abstract.py:11
  from ..conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ                       # verified: handlers/abstract.py:12

  async def get_request_session(request: web.Request, *, logger: logging.Logger) -> SessionData | None:
      """Memoised ``get_session(request, new=False)`` on ``request['user_session']`` (moved from AbstractHandler._get_user_session)."""

  async def enforce_request_pbac(request: web.Request, resource_type, resource_name: str, action: str,
                                 *, logger: logging.Logger) -> None:
      """Moved verbatim from AbstractHandler._enforce_pbac (handlers/abstract.py:326-440).

      Raises:
          web.HTTPNotFound: deny, missing resource_name, or PBAC on without session/authz.
      """
  # handlers/abstract.py — `_get_user_session` / `_enforce_pbac` keep their signatures and delegate.
  ```

### Module 2: Write-capability predicates
- **Path**: `querysource/queries/multi/__init__.py` (modify)
- **Depends on**: FEAT-155 (`WRITE_DESTINATIONS`, `_output_step_names`, already on dev)
- **Interface Skeleton**:
  ```python
  # after `def _output_step_names(output: object) -> set[str]:` (verified: __init__.py:75)
  SOURCE_HOOK_KEYS: tuple[str, str] = ("pre-hook", "post-hook")

  def pipeline_requires_write_grant(pipeline: object) -> bool:
      """True when ``Output`` uses a WRITE_DESTINATIONS step or any ``queries`` entry declares a hook."""

  def definition_requires_scheduler_grant(definition: Mapping[str, Any]) -> bool:
      """True for a scheduled (``attributes.scheduler``) ``provider == "multi"`` definition whose
      ``query_raw`` JSON pipeline is write-capable; malformed ``query_raw`` → False."""
  ```

### Module 3: Slug-management gate
- **Path**: `querysource/handlers/manager.py` (modify)
- **Depends on**: M1, M2
- **Interface Skeleton**:
  ```python
  class QueryManager(QueryView):                                        # verified: manager.py:36
      async def _enforce_scheduler_grant(self, repo, identity: QueryIdentity, data: dict) -> None:
          """Merge ``data`` over the stored row (if any) and enforce pg_admin when the result
          is a scheduled write-capable multi. Raises web.HTTPNotFound on deny."""
  # call sites, before the repository write:
  #   patch: before `result = await repo.patch(identity, data)`             (verified: manager.py:463)
  #   put:   before `result, is_created = await repo.upsert(identity, data)` (verified: manager.py:718, occurrence 1 of 2)
  #   post:  before `result, is_created = await repo.upsert(identity, data)` (verified: manager.py:834, occurrence 2 of 2)
  ```

### Module 4: Scheduler sync gate
- **Path**: `querysource/handlers/scheduler.py` (modify)
- **Depends on**: M1, M2
- **Interface Skeleton**:
  ```python
  class SchedulerJobsView(BaseView):                                     # verified: scheduler.py:70
      async def _enforce_scheduler_grant(self, slug: str, tenant: str | None) -> None:
          """Load the stored definition (registry + definition repository) and enforce pg_admin when
          ``definition_requires_scheduler_grant``; missing row / repo → no-op (register_slug reports)."""
  # call site: before `result = await scheduler.register_slug(slug, tenant=tenant)` (verified: scheduler.py:259)
  ```

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_request_gate_parity` (`tests/test_request_gate.py`) | M1 | disabled PBAC no-op; no session → 404; allow; deny → 404; sessionless authz path (mirrors existing `_enforce_pbac` tests) |
| existing `tests/handlers/test_multiquery_pbac_smoke.py` | M1 | still green (delegation) |
| `test_pipeline_requires_write_grant` (`tests/test_scheduler_write_predicates.py`) | M2 | TableDelete / ExecuteSQL Output → True; Table only → False; hook on a queries entry → True; malformed shapes → False |
| `test_definition_requires_scheduler_grant` | M2 | not multi / no scheduler / read-only pipeline / bad JSON → False; scheduled write multi → True |
| `test_manager_gate_*` (`tests/test_manager_scheduler_gate.py`) | M3 | put/post/patch of a scheduled write multi without grant → 404 and repo not called; with grant → written; read-only scheduled multi → no PBAC call; patch adding `ExecuteSQL` to a scheduled multi → gated; patch adding `attributes.scheduler` to a stored write multi → gated |
| `test_scheduler_sync_gate_*` (`tests/test_scheduler_sync_gate.py`) | M4 | write multi without grant → 404, `register_slug` not awaited; read-only → registered; missing row → unchanged behaviour |

### Integration Tests
| Test | Description |
|---|---|
| — | none (PBAC + repository are mocked in unit tests) |

---

## 5. Acceptance Criteria

- [ ] With PBAC enabled, saving (PUT/POST/PATCH) or syncing a **scheduled write-capable multi** without `datasource:use` on `pg_admin` returns 404, writes nothing, and registers no job.
- [ ] With the grant, the same operations behave exactly as today.
- [ ] Read-only multis, plain query schedules and cache schedules trigger **no** additional PBAC call.
- [ ] The decision uses the stored row merged with the incoming change (both "add schedule to write multi" and "add write step to scheduled multi" are gated).
- [ ] `AbstractHandler._enforce_pbac` / `_get_user_session` behaviour is unchanged (existing PBAC tests pass).
- [ ] `pytest tests/test_request_gate.py tests/test_scheduler_write_predicates.py tests/test_manager_scheduler_gate.py tests/test_scheduler_sync_gate.py tests/handlers/test_multiquery_pbac_smoke.py -q` passes; `ruff check` clean on touched files.

---

## 6. Codebase Contract

### Verified Imports
```python
from navigator_session import SessionData, get_session                       # verified: querysource/handlers/abstract.py:8
from querysource.auth.enforcement import build_eval_context, evaluate, resolve_evaluator  # verified: abstract.py:11
from querysource.conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ                 # verified: abstract.py:12
from querysource.auth import ResourceType                                    # verified: querysource/handlers/multi.py:9
from querysource.queries.multi import WRITE_DESTINATIONS, _output_step_names # verified: querysource/queries/multi/__init__.py:72,75
from querysource.tenants import QueryIdentity                                # verified: querysource/handlers/manager.py:21
```

### Existing Class Signatures
```python
# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):                                  # line 32
    async def _get_user_session(self, request) -> Optional[SessionData]:  # line 298 (memoises request['user_session'])
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None:  # line 326, body to :440
# querysource/handlers/manager.py
class QueryManager(QueryView):                                       # line 36; app['qs_tenant_registry'], app['qs_definition_repository']
    async def patch(self):  # line 415 — repo.patch(identity, data) at :463
    async def put(self):    # line 671 — repo.upsert(identity, data) at :718
    async def post(self):   # line 780 — repo.upsert(identity, data) at :834
    async def _sync_definition_jobs(self, identity) -> bool:  # line 121 → scheduler.register_slug (:139)
# querysource/handlers/scheduler.py
class SchedulerJobsView(BaseView):                                   # line 70
    async def post(self) -> web.Response:                            # line 208; register_slug at :259
# querysource/repositories/definitions.py
async def get(self, identity: QueryIdentity) -> LoadedDefinition:    # line 161 (runtime: QueryModel)
# querysource/models.py — QueryModel.attributes: Optional[dict] (:55), query_raw (:78), provider default 'db' (:81)
# querysource/scheduler/jobs.py:144 — MultiQS(slug=slug, tenant=tenant) (no principal)
```

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.auth.request_gate`~~ — created here.
- ~~`_enforce_pbac` on `QueryManager` / `SchedulerJobsView`~~ — both derive from navigator's `BaseView`/`QueryView`, not `AbstractHandler`.
- ~~An approval/draft workflow for definitions~~ — out of scope (option 1).
- ~~`HOOK_KEYS` on dev~~ — FEAT-157 (`querysource/interfaces/source_hooks.py`) is not merged; use `SOURCE_HOOK_KEYS` here.

### Edit Sites (Blueprint Anchors)
Verified against: `c26af0c`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/auth/request_gate.py` | CREATE | — | — | — |
| `querysource/handlers/abstract.py` | MODIFY | `    async def _enforce_pbac(` | `abstract.py:326` | 1 |
| `querysource/handlers/abstract.py` | MODIFY | `    async def _get_user_session(` | `abstract.py:298` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `def _output_step_names(output: object) -> set[str]:` | `__init__.py:75` | 1 |
| `querysource/handlers/manager.py` | MODIFY | `                result = await repo.patch(identity, data)` | `manager.py:463` | 1 |
| `querysource/handlers/manager.py` | MODIFY | `                result, is_created = await repo.upsert(identity, data)` (put at :718, post at :834 — quote the enclosing method) | `manager.py:718,834` | 2 |
| `querysource/handlers/scheduler.py` | MODIFY | `            result = await scheduler.register_slug(slug, tenant=tenant)` | `scheduler.py:259` | 1 |
| `tests/test_request_gate.py` | CREATE | — | — | — |
| `tests/test_scheduler_write_predicates.py` | CREATE | — | — | — |
| `tests/test_manager_scheduler_gate.py` | CREATE | — | — | — |
| `tests/test_scheduler_sync_gate.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Keep 404-on-deny and fail-closed semantics identical to `_enforce_pbac`.
- Parse `query_raw` with `json.loads` exactly as `QSScheduler` does (`scheduler.py:328-331`).

### Known Risks / Gotchas
- **Stored write multis can still be executed manually over HTTP by any caller with `slug:execute`.** v1 accepts this. Option 1 (draft + admin review) or a save-time gate is the follow-up.
- **Merge order.** Merge FEAT-160 no later than FEAT-156, so no scheduled `ExecuteSQL` multi can be saved ungated.
- **`SOURCE_HOOK_KEYS` duplicates FEAT-157's `HOOK_KEYS`.** Whichever merges second must make one module import the other (FEAT-157 TASK-825 or a FEAT-160 task, depending on order).
- **Merge semantics for `patch`.** It must use the stored row plus the patch, not the patch alone. Otherwise a patch that only changes `attributes` slips through.

### External Dependencies
None new.

---

## 8. Open Questions

- [x] Which option for scheduled writes? — *Resolved by Jesus Lara / Juan2coder (2026-09-30)*: option 2, only admins (`pg_admin`) may schedule.
- [x] Scope of the gate? — *Resolved by Juan2coder*: only multis that write (`WRITE_DESTINATIONS` Output steps or source hooks).
- [ ] Follow-up: should saving (not scheduling) a write multi, or executing a stored one over HTTP, also require `pg_admin` (or go through option 1 review)? — *Owner: Jesus Lara*

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document; decision taken directly with Jesus Lara)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy
- Isolation: one feature worktree `.claude/worktrees/feat-FEAT-160-multi-scheduler-admin-gate`.
- Module graph: M3 → M1 and M2 (it calls `enforce_request_pbac` and `definition_requires_scheduler_grant`). M4 → M1 and M2. M1 and M2 have no edges between them, so they run concurrently. M3 and M4 run concurrently after them.
- Shared files: none between modules.
- Exclusive resources: none. The worktree needs `python setup.py build_ext --inplace` before tests, because Cython `.so` files are not versioned.
- Cross-feature: needs FEAT-155 (merged). It is independent of FEAT-156/157 at the code level; see §7 for merge order.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-30 | Juan2coder | Initial draft (Jesus Lara option 2, scope: write multis only) |
