# TASK-645: Route `_load_scheduled_queries` by `provider` and warn on misconfig

**Feature**: FEAT-092 — QSScheduler Multi-Query Support
**Spec**: `sdd/specs/qsscheduler-multi-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Today `_load_scheduled_queries` (`querysource/scheduler/scheduler.py:89`)
registers every schedulable row through `scheduled_query_job` —
including `provider='multi'` rows that should run through `MultiQS`.
This task adds the inline branch and routes `provider='multi'` rows to
the new `scheduled_multiqs_job` created in TASK-644.

It also implements the Q1 resolution from the spec's Open Questions
section: a load-time `WARN` log when a `provider='multi'` row's
`query_raw` does not parse as multi-query JSON (i.e., `MultiQS` will
silently fall back to single-query mode at runtime). The warning is
informational — the job is still registered.

Implements **Module 2** from the spec
(`sdd/specs/qsscheduler-multi-support.spec.md` §3) and addresses
**Open Question Q1 (resolved: yes)** and the part of **Q2** about the
module-level docstring at the top of `scheduler.py`.

---

## Scope

- In `querysource/scheduler/scheduler.py`:
  - Update the import line at `:24` to also import
    `scheduled_multiqs_job` from `querysource.scheduler.jobs`.
  - Extend the SELECT at `:206-210` to also fetch `query_raw` (needed for
    the misconfig warning).
  - Inside `_load_scheduled_queries`, AFTER `trigger = self._parse_trigger(...)`
    and AFTER the `if trigger is None: continue` guard, branch on
    `row.get('provider')`:
    - `'multi'` → register `scheduled_multiqs_job` with `id=f"multi_{slug}"`
      and `name=f"Scheduled multi-query: {slug}"`. Parse
      `attributes.scheduler.output` if present (a `DEBUG`-level log
      acknowledging it; do NOT pass it into kwargs — it's reserved).
      Also attempt to JSON-decode `row.get('query_raw')`; if the decoded
      object is NOT a dict with a `'queries'` or `'files'` key, emit a
      single `WARN`-level log naming the slug (job is STILL registered).
    - any other value → keep existing `scheduled_query_job` registration
      bit-for-bit (id `query_{slug}`, name `Scheduled query: {slug}`).
  - Update the module docstring at the TOP of `scheduler.py` (lines 1-5)
    to mention the new provider routing AND the reserved
    `attributes.scheduler.output` sub-key.

- In `tests/test_scheduler_core.py`:
  - Add unit tests covering: multi-provider rows route to
    `scheduled_multiqs_job`; non-multi rows route to `scheduled_query_job`
    (regression); WARN log fires when `query_raw` is plain SQL;
    DEBUG log fires when `attributes.scheduler.output` is present;
    rows missing `attributes.scheduler` are skipped regardless of
    provider.

**NOT in scope** (covered by other tasks):
- Implementation of `scheduled_multiqs_job` itself → TASK-644.
- Multi-row skip in `_load_cache_refresh_jobs` → TASK-646.
- Smoke/integration test fixture → TASK-647.
- README documentation → TASK-648 (this task only touches the
  `scheduler.py` module docstring).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | Import line; SELECT extension; provider branch in `_load_scheduled_queries`; WARN-on-misconfig; DEBUG-log of reserved output sub-key; top-of-file module docstring. |
| `tests/test_scheduler_core.py` | MODIFY | Add ~5 unit tests for the routing decisions and log lines. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# Existing import line at querysource/scheduler/scheduler.py:24 — MODIFY:
from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,   # NEW — introduced by TASK-644
)
# verified destination: querysource/scheduler/jobs.py (post-TASK-644)

# Already imported, do NOT re-add:
from querysource.scheduler.notifications import NotificationManager
# querysource/scheduler/scheduler.py:25
```

### Existing Signatures to Use

```python
# querysource/scheduler/scheduler.py:30
class QSScheduler:
    def _parse_trigger(
        self, schedule_type: str, schedule: dict,
    ): ...                                          # line 57

    def _load_scheduled_queries(self, rows: list) -> int: ...  # line 89 — MODIFY

    async def startup(self, app: web.Application) -> None: ... # line 179
    # ^ contains the SELECT statement at lines 204-210 — MODIFY

# Existing add_job invocation pattern at scheduler.py:115-126 (DO NOT remove —
# branch into a new arm for the multi case; keep this arm intact):
self._scheduler.add_job(
    scheduled_query_job,
    trigger=trigger,
    id=job_id,                          # f"query_{slug}"
    name=f"Scheduled query: {slug}",
    replace_existing=True,
    kwargs={
        "slug": slug,
        "notification_manager": self._notification_manager,
    },
)
```

### Existing SELECT to Modify

```python
# querysource/scheduler/scheduler.py:204-210 (inside startup) — current:
sql = (
    "SELECT query_slug, attributes, cache_options, provider, is_cached "
    "FROM public.queries "
    "WHERE (attributes IS NOT NULL AND attributes != '{}') "
    "   OR (cache_options IS NOT NULL AND cache_options != '{}')"
)

# After this task — add query_raw to the column list:
sql = (
    "SELECT query_slug, attributes, cache_options, provider, is_cached, "
    "       query_raw "
    "FROM public.queries "
    "WHERE (attributes IS NOT NULL AND attributes != '{}') "
    "   OR (cache_options IS NOT NULL AND cache_options != '{}')"
)
```

### JSON Decode Pattern to Mirror

```python
# Reference: querysource/queries/multi/__init__.py:113-141
# MultiQS already decodes query_raw via self._encoder.load(...).
# For the LOAD-TIME warn check in this task, use json.loads from stdlib
# (the scheduler module is not exposing an _encoder). The fallback
# detection condition is:
#
#   payload = json.loads(query_raw)
#   if not (isinstance(payload, dict)
#           and ('queries' in payload or 'files' in payload)):
#       <emit WARN>
#
# Any JSON decode error is also a WARN trigger.
```

### Does NOT Exist

- ~~`PROVIDER_JOB_MAP`~~ — rejected in spec §1 Non-Goals; do NOT introduce
  a strategy table.
- ~~`_load_scheduled_multi_queries` as a separate method~~ — rejected in
  brainstorm Round 1; the routing stays inline in
  `_load_scheduled_queries`.
- ~~`public.queries.is_multiquery` / `is_multi` column~~ — does not
  exist. Routing uses the existing `provider` column.
- ~~A `request` or `user_session` argument on any path used by the
  scheduled job~~ — scheduled jobs have no HTTP request.
- ~~`row['provider']` (subscript access)~~ — use `row.get('provider')`
  to treat legacy NULL rows as "not multi" (defensive — `provider`
  defaults to `'db'` per `querysource/models.py:74`, but legacy rows
  may carry NULL).
- ~~Validating `attributes.scheduler.output` schema in v1~~ — out of
  scope per spec §1 Non-Goals. Only a DEBUG-level log acknowledging it.

---

## Implementation Notes

### Pattern to Follow

```python
# Inside _load_scheduled_queries, after trigger parsing:
#   trigger = self._parse_trigger(schedule_type, schedule)
#   if trigger is None:
#       continue
#
#   provider = row.get("provider")
#   if provider == "multi":
#       # Reserved output sub-key — parse, log, do NOT pass into kwargs.
#       reserved_output = scheduler_def.get("output")
#       if reserved_output:
#           self.logger.debug(
#               "Query '%s' declares reserved attributes.scheduler.output — "
#               "ignored in v1 (forward-compatible).", slug,
#           )
#
#       # Misconfig WARN (Q1 resolution from spec §8).
#       raw = row.get("query_raw") or ""
#       try:
#           payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
#       except json.JSONDecodeError:
#           payload = None
#       if not (isinstance(payload, dict)
#               and ("queries" in payload or "files" in payload)):
#           self.logger.warning(
#               "Multi-query slug '%s' has query_raw that is not a multi-query "
#               "JSON payload — MultiQS will fall back to single-query mode "
#               "at runtime.", slug,
#           )
#
#       job_id = f"multi_{slug}"
#       self._scheduler.add_job(
#           scheduled_multiqs_job,
#           trigger=trigger,
#           id=job_id,
#           name=f"Scheduled multi-query: {slug}",
#           replace_existing=True,
#           kwargs={
#               "slug": slug,
#               "notification_manager": self._notification_manager,
#           },
#       )
#       self.logger.info("Registered scheduled multi-query job: %s", job_id)
#       count += 1
#       continue
#
#   # else: existing single-query path — unchanged.
#   ... (the current scheduler.py:115-128 block, intact)
```

### Module Docstring Update

The current docstring at the top of `scheduler.py` reads:

```python
"""QSScheduler Core — Embedded APScheduler for QuerySource.

Creates scheduled jobs from public.queries definitions.
Gated behind ENABLE_QS_SCHEDULER config flag.
"""
```

Extend it to (per Q2 resolution — module docstring is one of the three
documentation surfaces):

```python
"""QSScheduler Core — Embedded APScheduler for QuerySource.

Creates scheduled jobs from public.queries definitions.
Gated behind ENABLE_QS_SCHEDULER config flag.

Job routing:
    - provider='multi'  → scheduled_multiqs_job (id: multi_<slug>)
    - otherwise         → scheduled_query_job   (id: query_<slug>)

Cache-refresh jobs (id: cache_<slug>) are registered ONLY for
non-multi rows where is_cached=True.

Reserved JSON sub-key:
    attributes.scheduler.output — parsed but ignored in v1; reserved
    for a future result-handling patch (see FEAT-092).
"""
```

### Key Constraints

- `import json` at the top of `scheduler.py` (it's currently NOT
  imported — verify before adding).
- Do NOT centralize the `f"multi_{slug}"` literal — it appears in two
  places (here and inside `scheduled_multiqs_job`). Symmetry is
  intentional.
- Do NOT change the behavior of any non-multi row. Run all existing
  scheduler unit tests to confirm.
- Logger usage stays consistent with the rest of the file
  (`self.logger.info`, `self.logger.warning`, `self.logger.debug` —
  the binding is at `scheduler.py:38`).

### References in Codebase

- `querysource/scheduler/scheduler.py:89-129` — existing
  `_load_scheduled_queries` body; insert new branch here.
- `querysource/scheduler/scheduler.py:204-210` — SELECT to extend.
- `querysource/queries/multi/__init__.py:113-141` — reference for the
  same JSON-parse-and-fallback heuristic (we re-use the condition
  shape, not the code).

---

## Acceptance Criteria

- [ ] `querysource/scheduler/scheduler.py` imports
      `scheduled_multiqs_job` alongside the existing two job callables.
- [ ] The `SELECT` includes `query_raw` so the load-time check can run.
- [ ] `_load_scheduled_queries` branches inline on `row.get('provider')
      == 'multi'` and registers `scheduled_multiqs_job` with
      `id=f"multi_{slug}"`, `name=f"Scheduled multi-query: {slug}"`.
- [ ] Non-multi rows continue to register `scheduled_query_job` with id
      `f"query_{slug}"` and name `f"Scheduled query: {slug}"` — no
      regression.
- [ ] A `provider='multi'` row whose `query_raw` is empty / plain SQL /
      malformed JSON triggers exactly ONE `WARNING` log line (per row,
      per startup). The job is still registered.
- [ ] A row with `attributes.scheduler.output = {...}` triggers exactly
      ONE `DEBUG` log line acknowledging it. The kwargs passed to the
      `add_job` call do NOT include `output`.
- [ ] Rows without `attributes.scheduler` are skipped regardless of
      provider (existing behavior preserved).
- [ ] The module docstring at the top of `scheduler.py` is updated per
      the snippet above.
- [ ] Unit tests in `tests/test_scheduler_core.py` pass:
      `pytest tests/test_scheduler_core.py -v` (5 new test cases).
- [ ] No linting errors: `ruff check querysource/scheduler/scheduler.py`.
- [ ] Existing scheduler tests still pass:
      `pytest tests/test_scheduler_jobs.py
      tests/test_scheduler_notifications.py -v`.

---

## Test Specification

```python
# tests/test_scheduler_core.py — additions
import pytest
from unittest.mock import MagicMock, patch

from querysource.scheduler.scheduler import QSScheduler


def _row(provider="db", attrs=None, query_raw=None):
    return {
        "query_slug": "test_slug",
        "attributes": attrs if attrs is not None else {
            "scheduler": {"schedule_type": "interval",
                          "schedule": {"minutes": 30}}
        },
        "cache_options": {},
        "provider": provider,
        "is_cached": False,
        "query_raw": query_raw,
    }


class TestLoadScheduledQueriesProviderRouting:

    def test_routes_multi_provider_to_new_job(self, sched):
        from querysource.scheduler.jobs import scheduled_multiqs_job
        sched._scheduler = MagicMock()
        sched._load_scheduled_queries([_row(provider="multi",
                                            query_raw='{"queries":{}}')])
        args, kwargs = sched._scheduler.add_job.call_args
        assert args[0] is scheduled_multiqs_job
        assert kwargs["id"] == "multi_test_slug"
        assert kwargs["name"] == "Scheduled multi-query: test_slug"

    def test_keeps_single_query_path_for_non_multi(self, sched):
        from querysource.scheduler.jobs import scheduled_query_job
        sched._scheduler = MagicMock()
        sched._load_scheduled_queries([_row(provider="db")])
        args, kwargs = sched._scheduler.add_job.call_args
        assert args[0] is scheduled_query_job
        assert kwargs["id"] == "query_test_slug"

    def test_skips_row_without_scheduler_attribute(self, sched):
        sched._scheduler = MagicMock()
        sched._load_scheduled_queries([_row(provider="multi", attrs={})])
        sched._scheduler.add_job.assert_not_called()

    def test_warns_on_misconfigured_multi(self, sched, caplog):
        sched._scheduler = MagicMock()
        with caplog.at_level("WARNING"):
            sched._load_scheduled_queries([
                _row(provider="multi", query_raw="SELECT 1"),
            ])
        assert any(
            "MultiQS will fall back to single-query mode" in r.message
            for r in caplog.records
        )

    def test_debug_log_for_reserved_output_subkey(self, sched, caplog):
        sched._scheduler = MagicMock()
        attrs = {"scheduler": {
            "schedule_type": "interval",
            "schedule": {"minutes": 30},
            "output": {"type": "tableOutput"},
        }}
        with caplog.at_level("DEBUG"):
            sched._load_scheduled_queries([
                _row(provider="multi",
                     attrs=attrs,
                     query_raw='{"queries":{}}'),
            ])
        assert any(
            "attributes.scheduler.output" in r.message
            for r in caplog.records
        )
        _, kwargs = sched._scheduler.add_job.call_args
        assert "output" not in kwargs["kwargs"]


@pytest.fixture
def sched():
    """Fresh QSScheduler instance, no event loop wiring required."""
    return QSScheduler()
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/qsscheduler-multi-support.spec.md`,
   especially §2 (Architectural Design) and §6 (Codebase Contract).
2. **Verify TASK-644 is completed** — check
   `sdd/tasks/completed/TASK-644-*.md` exists and
   `scheduled_multiqs_job` is importable.
3. **Verify the Codebase Contract** — re-read
   `querysource/scheduler/scheduler.py:89-129` and lines 204-210 to
   confirm the structure is what this task expects.
4. **Update task status** in
   `sdd/tasks/index/qsscheduler-multi-support.json` → `"in_progress"`.
5. **Implement** the changes (import, SELECT, branch, WARN, DEBUG,
   module docstring).
6. **Run tests**:
   `pytest tests/test_scheduler_core.py tests/test_scheduler_jobs.py -v`.
7. **Move this file** to
   `sdd/tasks/completed/TASK-645-load-scheduled-queries-provider-routing.md`.
8. **Update index** → `"completed"`.
9. **Fill in Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
