# TASK-812: Scheduler carries run-as into scheduled MultiQS jobs

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-805, TASK-806, TASK-808
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7, scheduler half (S4, the resolved U7). Scheduled MultiQS runs
have no request or session. `QSScheduler` keeps `app["auth"]` at startup,
reads the stored run-as user for each multi row, and passes `run_as_user_id`
plus `identity_auth` in the job kwargs. `scheduled_multiqs_job` builds a
scheduler `SourceIdentityContext`. No token material ever enters job kwargs.

---

## Scope

- `QSScheduler.startup`: `self._auth = app.get("auth")`.
- `_fetch_slug_row`: add `RUN_AS_COLUMN` using `repository.get_run_as(identity)`,
  guarded with `getattr`.
- The multi `add_job` kwargs in `_register_query_row` add
  `"run_as_user_id": row.get(RUN_AS_COLUMN)` and
  `"identity_auth": getattr(self, "_auth", None)`.
- `scheduled_multiqs_job`: accept `run_as_user_id` and `identity_auth`. When
  `run_as_user_id is not None`, pass
  `identity_context=SourceIdentityContext.for_scheduler(...)` to MultiQS.
  **Otherwise keep the call exactly `MultiQS(slug=slug, tenant=tenant)`**.
- Regression test: `SchedulerJobsView.post` never writes run-as.

**NOT in scope**: the repository (TASK-808/809) and the manager (TASK-811).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | keep auth; carry run-as |
| `querysource/scheduler/jobs.py` | MODIFY | build the context |
| `tests/scheduler/test_run_as_scheduler.py` | CREATE | unit + regression tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.repositories.definitions import RUN_AS_COLUMN             # TASK-808
from querysource.auth.identity_tokens import SourceIdentityContext          # TASK-805 (lazy import inside jobs.py function body)
```

### Existing Signatures to Use
```python
# querysource/scheduler/scheduler.py
class QSScheduler:                                                  # :120
    def _register_query_row(self, row: dict, store=None) -> Optional[str]:   # :280; multi add_job kwargs dict :350-359 ("job_id": job_id, :358)
    async def _fetch_slug_row(self, slug, *, tenant=None) -> dict | None:    # :492; returns dict :549-556 ("query_raw": … :555)
    async def startup(self, app) -> None:                           # :659; `self._repository = app.get("qs_definition_repository")` :684
# querysource/scheduler/jobs.py
async def scheduled_multiqs_job(slug, notification_manager=None, *, owner=None, job_id=None, **kwargs) -> None:  # :104-112 (`job_id: str | None = None,` :109)
    qs = MultiQS(slug=slug, tenant=tenant)                          # :144
# tests that constrain this task:
#   tests/test_scheduler_jobs.py:174   mock_cls.assert_called_once_with(slug="test_slug", tenant=None)   ← must stay green
#   tests/test_scheduler_multi_routing.py:66-74 builds QSScheduler via __new__ (no __init__) → use getattr(self, "_auth", None)
#   tests/tenants/test_tenant_management_reads.py:178 _FakeRepo lacks get_run_as → getattr guard in _fetch_slug_row
```

### Does NOT Exist
- ~~`QSScheduler._auth`~~ before this task, and it is absent in `__new__`-built test instances.
- ~~Token or credential values in job kwargs~~: forbidden.
- ~~A run-as write in `SchedulerJobsView.post`~~: must stay absent.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/scheduler.py", "action": "MODIFY"},
    {"path": "querysource/scheduler/jobs.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_run_as_scheduler.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/scheduler/scheduler.py#QSScheduler._register_query_row",
    "sym:querysource/scheduler/scheduler.py#QSScheduler._fetch_slug_row",
    "sym:querysource/scheduler/scheduler.py#QSScheduler.startup",
    "sym:querysource/scheduler/jobs.py#scheduled_multiqs_job"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Keep `app["auth"]` at startup — *why*: the scheduler loop's path to `IdentityProvider`.
2. Carry run-as in `_fetch_slug_row` and the multi kwargs — *why*: startup rows already include it from `schedulable()` (TASK-808).
3. Build the context in the job, only when run-as is present — *why*: keeps `tests/test_scheduler_jobs.py:174` exact.
4. Write the tests, including the `SchedulerJobsView.post` regression.

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        self._repository = app.get("qs_definition_repository")' …) — :684
# AFTER:
        # FEAT-159: auth handler for sessionless delegated identities in scheduled multi jobs.
        self._auth = app.get("auth")
# occurrences: 1 (verified: grep -c '                    "job_id": job_id,' …) — :358
# AFTER:
                    "run_as_user_id": row.get(RUN_AS_COLUMN),
                    "identity_auth": getattr(self, "_auth", None),
# occurrences: 1 (verified: grep -c '            "query_raw": getattr(runtime, "query_raw", None),' …) — :555
# AFTER:
            RUN_AS_COLUMN: await self._run_as_for(identity),
# plus a helper method on QSScheduler:
    async def _run_as_for(self, identity: Any) -> int | None:
        """Stored run-as user (None if the repository or the column does not support it)."""
        getter = getattr(self._repository, "get_run_as", None)
        # FILL IN: return await getter(identity) if getter else None; log+None on exception
```

### `querysource/scheduler/jobs.py` (MODIFY)
```python
# occurrences: 3 (verified: grep -c '    job_id: str | None = None,' querysource/scheduler/jobs.py) — use the one at :109
# FILL IN: disambiguate — the anchor is inside `async def scheduled_multiqs_job(` (lines 104-112); insert AFTER it:
    run_as_user_id: int | None = None,
    identity_auth: Any = None,
# occurrences: 1 (verified: grep -c '        qs = MultiQS(slug=slug, tenant=tenant)' …) — :144
# REPLACE with:
        if run_as_user_id is not None:
            from querysource.auth.identity_tokens import SourceIdentityContext
            qs = MultiQS(slug=slug, tenant=tenant,
                         identity_context=SourceIdentityContext.for_scheduler(run_as_user_id, identity_auth))
        else:
            qs = MultiQS(slug=slug, tenant=tenant)
# FILL IN: add both params to the docstring Args
```

### FILL IN checklist
- [ ] `_run_as_for` guard
- [ ] jobs.py disambiguated insertion + docstring
- [ ] tests incl. the `SchedulerJobsView.post` no-write regression

---

## Acceptance Criteria

- [ ] Multi `add_job` kwargs include `run_as_user_id` and `identity_auth`, and nothing token-like.
- [ ] `scheduled_multiqs_job(run_as_user_id=42, identity_auth=a)` builds MultiQS with `identity_context.origin == "scheduler"`.
- [ ] `tests/test_scheduler_jobs.py`, `tests/test_scheduler_multi_routing.py` and `tests/scheduler/test_jobs_no_principal.py` pass unchanged.
- [ ] `SchedulerJobsView.post` makes no run-as repository write.

## Validation Commands

- `pytest tests/scheduler/test_run_as_scheduler.py -q`
- `pytest tests/test_scheduler_jobs.py -q`
- `pytest tests/test_scheduler_multi_routing.py -q`
- `pytest tests/scheduler/test_jobs_no_principal.py -q`

---

## Test Specification

```python
# tests/scheduler/test_run_as_scheduler.py
async def test_multi_job_kwargs_carry_run_as():
    ...  # FILL IN: _make_sched_mocked pattern (tests/test_scheduler_multi_routing.py:66); row with RUN_AS_COLUMN=42


async def test_scheduled_multiqs_job_builds_context():
    ...  # FILL IN: patch("querysource.queries.MultiQS"); kwargs identity_context.user_id == 42, origin "scheduler"


async def test_scheduler_sync_never_writes_run_as():
    ...  # FILL IN: SchedulerJobsView.post with a fake repo; assert no patch/upsert/_apply_run_as calls
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-812 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
