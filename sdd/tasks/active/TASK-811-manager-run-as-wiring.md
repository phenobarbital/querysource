# TASK-811: QueryManager passes the session user as `run_as_actor`

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-805, TASK-809
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7, manager half (S6, the resolved U7). The run-as user is
whoever **creates or changes the schedule** through the management API.
`QueryManager.patch/put/post` read the numeric session user id and pass it,
together with a small `request_info`, to `repo.patch()` / `repo.upsert()`.
TASK-809 decides whether anything is written.

---

## Scope

- Add a helper `QueryManager._run_as_context() -> tuple[int | None, dict]`
  that returns the session user id (via `user_id_from_session`) and
  `{"method", "path", "remote"}`.
- Pass `run_as_actor=` and `request_info=` at the three repository call sites.
- Write unit tests.

**NOT in scope**: `SchedulerJobsView` (no change, see TASK-812's regression test).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | pass actor + request_info |
| `tests/handlers/test_manager_run_as.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navigator_session import get_session                          # verified: handlers/abstract.py:8
from ..auth.identity_tokens import user_id_from_session            # TASK-805
```

### Existing Signatures to Use
```python
# querysource/handlers/manager.py
from ..utils.handlers import QueryView                             # :25 (QueryView(BaseView) — navigator BaseView; no session helper)
class QueryManager(QueryView):                                     # :36
    async def patch(self):   # :415 … `result = await repo.patch(identity, data)` :463
    async def put(self):     # :671 … `identity = QueryIdentity(store=store, slug=data['query_slug'])` :717 then upsert :718
    async def post(self):    # :780 … `identity = QueryIdentity(store=store, slug=slug['query_slug'])` :833 then upsert :834
# repo.patch/upsert(..., *, run_as_actor: int | None = None, request_info: Mapping | None = None)   # TASK-809
# get_session(request, new=False) may raise RuntimeError when the session system is not installed (see abstract.py:315-321)
```

### Does NOT Exist
- ~~`QueryManager._get_user_session`~~: that helper lives on `AbstractHandler`, not `QueryView`. Use `get_session` directly.
- ~~Passing a username as actor~~: numeric only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/manager.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_manager_run_as.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/manager.py#QueryManager.patch",
    "sym:querysource/handlers/manager.py#QueryManager.put",
    "sym:querysource/handlers/manager.py#QueryManager.post"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- If session lookup fails (`RuntimeError` or no session), return
  `(None, request_info)`. The write proceeds, and TASK-809 leaves run-as untouched.
- `remote` is `self.request.remote`. Never include headers, cookies or tokens.

---

## Implementation Blueprint

### Steps (in order)
1. Add the imports and the `_run_as_context` helper — *why*: one place for session logic.
2. Update the three call sites — *why*: S6 capture point.
3. Write the tests.

### `querysource/handlers/manager.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ..utils.handlers import QueryView' querysource/handlers/manager.py) — :25
# AFTER:
from navigator_session import get_session
from ..auth.identity_tokens import user_id_from_session

# inside class QueryManager (FILL IN: place right after the class docstring / first helper):
    async def _run_as_context(self) -> tuple[int | None, dict]:
        """(numeric session user id or None, minimal request_info) for run-as auditing."""
        info = {"method": self.request.method, "path": self.request.path, "remote": self.request.remote}
        # FILL IN: get_session(self.request, new=False) guarded; return (user_id_from_session(session), info)

# occurrences: 1 (verified: grep -c '                result = await repo.patch(identity, data)' …) — :463
# REPLACE with:
                actor, info = await self._run_as_context()
                result = await repo.patch(identity, data, run_as_actor=actor, request_info=info)
# occurrences: 1 each (verified: grep -c "slug=data['query_slug'])" → put :717 ; grep -c "slug=slug['query_slug'])" → post :833)
# in both: the line after the QueryIdentity(...) line becomes:
                actor, info = await self._run_as_context()
                result, is_created = await repo.upsert(identity, data, run_as_actor=actor, request_info=info)
```
**Why**: the capture happens only on real definition writes, never on scheduler re-sync (S6).

### FILL IN checklist
- [ ] `_run_as_context` session guard
- [ ] placement of the helper in the class

---

## Acceptance Criteria

- [ ] patch, put and post pass `run_as_actor` equal to the session's numeric user id, plus `request_info`.
- [ ] No session → `run_as_actor=None` and the write succeeds.
- [ ] The existing manager tests pass.

## Validation Commands

- `pytest tests/handlers/test_manager_run_as.py -q`
- `pytest tests/handlers/test_querymanager_pagination.py -q`

---

## Test Specification

```python
# tests/handlers/test_manager_run_as.py
async def test_manager_passes_session_actor():
    ...  # FILL IN: patch get_session → {"session": {"user_id": 42}}; fake repo records kwargs for patch/upsert


async def test_manager_without_session_passes_none():
    ...  # FILL IN: get_session raises RuntimeError → run_as_actor None
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-811 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
