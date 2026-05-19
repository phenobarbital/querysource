# TASK-644: Implement `scheduled_multiqs_job` callable

**Feature**: FEAT-092 — QSScheduler Multi-Query Support
**Spec**: `sdd/specs/qsscheduler-multi-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The QSScheduler currently has exactly two job callables in
`querysource/scheduler/jobs.py`:
`scheduled_query_job` and `cache_refresh_job`. Both call
`QS(slug=...).query()` internally. This task introduces a third sibling,
`scheduled_multiqs_job`, that calls `MultiQS(slug=...).query()` instead.
The downstream loader change (TASK-645) wires this callable into
`_load_scheduled_queries` once it exists.

Implements **Module 1** from the spec
(`sdd/specs/qsscheduler-multi-support.spec.md` §3).

---

## Scope

- Add a new `async def scheduled_multiqs_job(slug, notification_manager=None,
  **kwargs) -> None` to `querysource/scheduler/jobs.py`, structurally
  mirroring `scheduled_query_job` (lines 12-37).
- Lazy-import `MultiQS` inside the function body (matching the existing
  lazy-import of `QS` at `jobs.py:25` and `:56`).
- Instantiate `MultiQS(slug=slug)` with no other constructor kwargs (no
  `request`, no `user_session`, no `conditions`, no `queries`, no `files`,
  no `query`).
- `await qs.query()` and discard the returned `(result, options)` tuple.
- On any `Exception`: `logger.warning(...)`, then
  `notification_manager.notify(job_id=f"multi_{slug}", slug=slug,
  error=exc)` exactly once. Swallow the exception (do NOT re-raise) so
  APScheduler does not also emit a `JobErrorEvent` that would notify
  again. This matches `scheduled_query_job` behavior at `jobs.py:28-37`.
- Docstring on the new callable MUST include:
  1. A one-line summary identical in spirit to `scheduled_query_job`'s
     ("Execute a scheduled multi-query by slug. Result is discarded.").
  2. A note that the reserved JSON sub-key
     `attributes.scheduler.output` is forward-compatible and **not**
     interpreted in v1.
  3. A `TODO` line: `v2 may accept an optional conditions kwarg (parity
     with QS(slug, conditions=...))`.
- Update the module docstring at the top of `querysource/scheduler/jobs.py`
  to describe **three** job kinds instead of two.
- Add unit tests in `tests/test_scheduler_jobs.py` covering the three
  acceptance criteria below.

**NOT in scope** (covered by other tasks):
- Routing logic in `_load_scheduled_queries` → TASK-645.
- Cache-refresh skip guard → TASK-646.
- Smoke-test routing fixture → TASK-647.
- README documentation → TASK-648.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/jobs.py` | MODIFY | Add `scheduled_multiqs_job` callable; update module docstring to describe three job kinds. |
| `tests/test_scheduler_jobs.py` | MODIFY | Add unit tests for the new callable. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# Lazy import inside the new callable (same pattern as the existing two):
from querysource.queries import MultiQS
# verified: re-exported at querysource/queries/__init__.py:7
# verified: class defined at querysource/queries/multi/__init__.py:53

# Module logger already exists; reuse it (do NOT add a second logger):
# querysource/scheduler/jobs.py:9
#   logger = logging.getLogger("QSScheduler.Jobs")
```

### Existing Signatures to Use

```python
# querysource/scheduler/jobs.py — sibling reference pattern to MIRROR
async def scheduled_query_job(            # line 12
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...

async def cache_refresh_job(              # line 40
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...

# querysource/queries/multi/__init__.py — the class the new job will call
class MultiQS(BaseQuery):                 # line 53
    def __init__(
        self,
        slug: str = None,
        queries: Optional[list] = None,
        files: Optional[list] = None,
        query: Optional[dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        user_session: Optional[object] = None,
        **kwargs,
    ): ...                                # line 59
    async def query(self): ...            # line 105 — returns (result_dict, options) tuple
    # handler unpack proves the tuple shape:
    # querysource/handlers/multi.py:231 → result, options = await qs.query()

# querysource/scheduler/notifications.py — callback contract (unchanged)
class NotificationManager:                # line 21
    def notify(
        self,
        job_id: str,
        slug: str,
        error: Exception,
    ) -> None: ...                        # line 37
```

### Does NOT Exist

- ~~`MultiQS.from_slug` / `MultiQS.dispatch_slug` / `MultiQS.slug_only`~~ —
  not real methods. Slug-only invocation is `MultiQS(slug=...).query()`.
- ~~`MultiQS(...).run()` / `.execute()` / `.fetch()`~~ — only
  `await qs.query()` exists at line 105.
- ~~`NotificationManager.notify_multi(...)`~~ — there is no
  multi-specific notify method. Use the existing `notify(job_id, slug,
  error)` signature.
- ~~A `request` kwarg on the job callable~~ — scheduled jobs have no
  aiohttp request. Do NOT add one.
- ~~A separate logger for multi jobs~~ — reuse the module logger
  `logging.getLogger("QSScheduler.Jobs")` already defined at
  `jobs.py:9`.

---

## Implementation Notes

### Pattern to Follow

```python
# Reference: querysource/scheduler/jobs.py:12-37 (scheduled_query_job).
# The new callable should be a near-line-for-line twin, swapping QS for
# MultiQS and the job_id prefix.
#
# Key points:
#   - try / except Exception is the outer shape.
#   - Lazy-import inside the try block.
#   - logger.warning(...) on failure (not logger.error — matches sibling).
#   - notification_manager check is `if notification_manager:`
#     (the sibling uses the same guard at jobs.py:32).
#   - Discard the tuple: `_result, _options = await qs.query()` or just
#     `await qs.query()` — the return is not used.
```

### Key Constraints

- async throughout — `async def`, `await qs.query()`.
- NO request, NO user session, NO conditions in the `MultiQS(...)` call.
- Job-id format for the notification call is the f-string literal
  `f"multi_{slug}"`. Do NOT centralize it in a constant — TASK-645 uses
  the same literal at the loader registration site, and a constant would
  add indirection for two call sites.
- Swallow exceptions after notifying (do NOT re-raise).
- Docstring TODO must be the literal text `TODO: v2 may accept an
  optional conditions kwarg (parity with QS(slug, conditions=...))`.

### References in Codebase

- `querysource/scheduler/jobs.py:12` — copy this function's structure.
- `querysource/queries/multi/__init__.py:53-141` — confirm slug-only
  invocation path before implementing.
- `querysource/scheduler/notifications.py:37` — confirm `notify(...)`
  signature.

---

## Acceptance Criteria

- [ ] `querysource/scheduler/jobs.py` defines
      `async def scheduled_multiqs_job(slug: str, notification_manager=None,
      **kwargs) -> None`.
- [ ] The callable lazy-imports `MultiQS` from `querysource.queries` and
      instantiates `MultiQS(slug=slug)` (no other args).
- [ ] On exception, calls `notification_manager.notify(job_id=f"multi_{slug}",
      slug=slug, error=<exc>)` exactly once, then returns without
      re-raising.
- [ ] Module docstring at the top of `jobs.py` is updated to describe
      three job kinds.
- [ ] Docstring on the new function contains: one-line summary, reserved
      `attributes.scheduler.output` note, and the literal `TODO: v2 may
      accept an optional conditions kwarg ...` line.
- [ ] New unit tests in `tests/test_scheduler_jobs.py` pass:
      `pytest tests/test_scheduler_jobs.py -v` (3 new test cases).
- [ ] No linting errors: `ruff check querysource/scheduler/jobs.py
      tests/test_scheduler_jobs.py`.
- [ ] Import works: `from querysource.scheduler.jobs import
      scheduled_multiqs_job`.

---

## Test Specification

```python
# tests/test_scheduler_jobs.py — additions
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from querysource.scheduler.jobs import scheduled_multiqs_job


class TestScheduledMultiQSJob:

    @pytest.mark.asyncio
    async def test_calls_multiqs_with_slug_only(self):
        """scheduled_multiqs_job constructs MultiQS(slug=slug) and awaits query()."""
        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(return_value=({}, {}))
            mock_cls.return_value = mock_instance

            await scheduled_multiqs_job(slug="test_slug")

            mock_cls.assert_called_once_with(slug="test_slug")
            mock_instance.query.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_notifies_on_exception(self):
        """scheduled_multiqs_job notifies the manager when MultiQS.query() raises."""
        notification_manager = MagicMock()
        boom = RuntimeError("boom")

        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(side_effect=boom)
            mock_cls.return_value = mock_instance

            await scheduled_multiqs_job(
                slug="bad_slug",
                notification_manager=notification_manager,
            )

            notification_manager.notify.assert_called_once_with(
                job_id="multi_bad_slug",
                slug="bad_slug",
                error=boom,
            )

    @pytest.mark.asyncio
    async def test_swallows_exception(self):
        """scheduled_multiqs_job does NOT re-raise after notifying."""
        with patch("querysource.queries.MultiQS") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(side_effect=ValueError("nope"))
            mock_cls.return_value = mock_instance

            # Must not raise.
            await scheduled_multiqs_job(
                slug="x",
                notification_manager=MagicMock(),
            )
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qsscheduler-multi-support.spec.md`.
2. **Check dependencies** — none.
3. **Verify the Codebase Contract** — `grep` or `read` each referenced
   file to confirm the listed signatures still match. Re-confirm
   `MultiQS` is importable from `querysource.queries` (its `__init__.py`
   line 7 export).
4. **Update task status** in `sdd/tasks/index/qsscheduler-multi-support.json`
   → `"in_progress"` with your session ID.
5. **Implement** the new callable + module docstring update.
6. **Run unit tests** and confirm they pass.
7. **Move this file** to `sdd/tasks/completed/TASK-644-scheduled-multiqs-job-callable.md`.
8. **Update index** → `"completed"` with `completed_at` timestamp.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
