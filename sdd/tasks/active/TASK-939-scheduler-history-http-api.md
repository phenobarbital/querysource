# TASK-939: Scheduler history HTTP API (runs, run-now, durable PATCH, overlay)

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-938
**Assigned-to**: unassigned

---

## Context

Spec §2 "New Public Interfaces" and §3 Module 9. This is the contract the Concierge
frontend spec consumes. History reads are bounded, and they degrade to `503
history_unavailable` (endpoints) or `history_available: false` (jobs list).

---

## Scope

- `SchedulerJobsView.get`:
  - add `paused` per job (`next_run_time is None`; in-memory, kept in sync with the
    definition by TASK-937);
  - add `last_run` from one bounded `scheduler._history.last_runs(ids)` call;
  - add `history_available` to the envelope.
- `SchedulerJobsView.patch`: call `await scheduler.set_job_paused_durable(job_id, …, user=…, reason=body.get("reason"))`.
  Unknown job or missing slug → 404.
- New `SchedulerRunsView(BaseView)`:
  - `GET /api/v1/qs/scheduler/runs` (query filters);
  - `GET /api/v1/qs/scheduler/runs/{run_id}`;
  - `GET /api/v1/qs/scheduler/jobs/{job_id}/runs`.
  - `limit` defaults to 50 and is capped at 200; `since`/`until` are ISO-8601.
- New `SchedulerRunNowView(BaseView)`: `POST /api/v1/qs/scheduler/jobs/{job_id}/run`.
  - Applies `_enforce_scheduler_grant(slug, tenant)` for multi jobs.
  - Maps `KeyError`→404, `RunInProgress`→409 `{error_code: "run_in_progress", run_id}`,
    `RuntimeError`→503 `{error_code: "history_unavailable"}`; success → 202
    `{run_id, status: "running"}`.
- User id from the session: `user_id_from_session(await get_session(self.request, new=False))`,
  `None` on failure.
- Register the three new routes in `services.py`, inside the `ENABLE_QS_SCHEDULER`
  block. The literal `/runs` paths must be registered so they are not shadowed by
  `/jobs/{job_id}`.

**NOT in scope**: frontend.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/scheduler.py` | MODIFY | Overlay, durable PATCH, new views |
| `querysource/services.py` | MODIFY | Register routes |
| `tests/handlers/test_scheduler_runs_api.py` | CREATE | API tests with a stub scheduler |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navigator.views import BaseView                              # verified: querysource/handlers/scheduler.py:26
from navigator_session import get_session                         # verified: querysource/handlers/manager.py:19
from querysource.auth.identity_tokens import user_id_from_session # verified: querysource/auth/identity_tokens.py:41 (manager.py:23)
from querysource.scheduler.history import HistoryUnavailable      # TASK-930
from querysource.scheduler.scheduler import RunInProgress         # TASK-938
```

### Existing Signatures to Use
```python
# querysource/handlers/scheduler.py
def _kind_from_id(job_id: str) -> str                                         # line 42
class SchedulerJobsView(BaseView):                                            # line 74
    def _get_scheduler(self) -> QSScheduler | None                            # line 88
    async def _enforce_scheduler_grant(self, slug: str, tenant: str | None)   # line 98
    def _serialize_job(self, job: Job) -> dict                                # line 157
    async def get(self) -> web.Response                                       # line 202
    def _unavailable_response(self) -> web.Response                           # line 254
    async def patch(self) -> web.Response                                     # line 374; set_job_paused at 409
    # helpers used: self.match_parameters(self.request), self.json_response(response=..., status=...)
# querysource/services.py lines 453-466: ENABLE_QS_SCHEDULER block registering SchedulerJobsView via self.app.router.add_view(...), routes.append(r)
```

### Does NOT Exist
- ~~`self.request.user`~~: use the session helper above.
- ~~An existing `/runs` route~~: new here.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/scheduler.py", "action": "MODIFY"},
    {"path": "querysource/services.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_scheduler_runs_api.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/handlers/scheduler.py#SchedulerJobsView"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add a module helper `async def _session_user(request) -> str | None`.
2. Overlay in `_serialize_job` (`paused`) and in `get()` (`last_run`, `history_available`);
   the `last_runs` call goes in `get()`, once per request.
3. Replace the PATCH call.
4. Add the two views. *Why separate classes:* `SchedulerJobsView.get` already
   dispatches on `{job_id}`; the new paths have different shapes.
5. Register the routes.

### `querysource/handlers/scheduler.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        if not scheduler.set_job_paused(job_id, action == "pause"):' querysource/handlers/scheduler.py)
# REPLACE (verified: querysource/handlers/scheduler.py:409) with:
        user = await _session_user(self.request)
        if not await scheduler.set_job_paused_durable(
            job_id, action == "pause", user=user, reason=body.get("reason") if isinstance(body, dict) else None
        ):
```
```python
# occurrences: 1 (verified: grep -c '    def _serialize_job(self, job: Job) -> dict:' querysource/handlers/scheduler.py)
# INSIDE _serialize_job, add to `result` (after "pending"):
            "paused": nrt is None,
```
```python
# APPEND at end of module
class SchedulerRunsView(BaseView):
    """GET run history: /scheduler/runs, /scheduler/runs/{run_id}, /scheduler/jobs/{job_id}/runs."""

    async def get(self) -> web.Response:
        # FILL IN: scheduler = request.app.get("qs_scheduler"); history = scheduler._history → None → 503 history_unavailable;
        #   params = match_parameters; run_id → get_run (404 when None); else list_runs(job_id from path or query, slug,
        #   status, since, until, limit≤200, offset) → {runs, total, limit, offset}; HistoryUnavailable → 503
        ...


class SchedulerRunNowView(BaseView):
    """POST /api/v1/qs/scheduler/jobs/{job_id}/run — start a manual run."""

    async def post(self) -> web.Response:
        # FILL IN: scheduler lookup (503); job lookup for slug/owner; if kind == "multi": reuse
        #   SchedulerJobsView._enforce_scheduler_grant logic (call it via a shared module function or instantiate
        #   the check — do NOT duplicate the policy); user = await _session_user(...);
        #   run_now → 202; KeyError → 404; RunInProgress → 409; RuntimeError → 503
        ...
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "'/api/v1/qs/scheduler/jobs/{job_id}', SchedulerJobsView" querysource/services.py)
# AFTER the `routes.append(r)` that follows line 464:
            from .handlers.scheduler import SchedulerRunNowView, SchedulerRunsView
            for path, view in (
                ('/api/v1/qs/scheduler/runs', SchedulerRunsView),
                ('/api/v1/qs/scheduler/runs/{run_id}', SchedulerRunsView),
                ('/api/v1/qs/scheduler/jobs/{job_id}/runs', SchedulerRunsView),
                ('/api/v1/qs/scheduler/jobs/{job_id}/run', SchedulerRunNowView),
            ):
                r = self.app.router.add_view(path, view)
                routes.append(r)
```

### FILL IN checklist
- [ ] `_session_user` (exceptions → None).
- [ ] `get()` overlay: `last_run` + `history_available`, bounded and degrading.
- [ ] Both views; grant reuse without duplicating policy.
- [ ] Tests: overlay + degraded; filters/cap/404/503; run-now 202/404/409/503 and the grant 403.

---

## Acceptance Criteria

- [ ] Endpoints and status codes match spec §2 exactly.
- [ ] History down → `GET /jobs` 200 with `last_run: null`, `history_available: false`; history endpoints 503.
- [ ] PATCH persists via `set_job_paused_durable`.
- [ ] Existing `tests/test_scheduler_handler_integration.py` passes.

---

## Validation Commands

- `pytest tests/handlers/test_scheduler_runs_api.py -q`
- `pytest tests/test_scheduler_handler_integration.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-938 is done.
2. Re-verify anchors; set `in-progress`; implement; run the Validation Commands.
3. Close with `scripts/sdd/close_task.sh TASK-939 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
