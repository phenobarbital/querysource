# TASK-648: Document multi-query routing in QSScheduler README/runbook

**Feature**: FEAT-092 — QSScheduler Multi-Query Support
**Spec**: `sdd/specs/qsscheduler-multi-support.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-644, TASK-645, TASK-646
**Assigned-to**: unassigned

---

## Context

The spec's Open Question Q2 was resolved as "three documentation
surfaces: README + new-job docstring + module docstring". The docstring
on `scheduled_multiqs_job` is handled by TASK-644; the module docstring
at the top of `querysource/scheduler/scheduler.py` is handled by
TASK-645. This task adds the third surface: an operator-facing
README/runbook page for QSScheduler.

The project's `docs/` directory currently has no QSScheduler page — the
scheduler was introduced by FEAT-008 without one. This task creates
that page at `docs/QSSCHEDULER.md` (matching the uppercase convention
of `docs/COLUMN_FILTER_EXAMPLE.md`,
`docs/IMPLEMENTATION_SUMMARY.md`, etc.).

Implements the README portion of **Open Question Q2 (resolved)** and
**Module 4** from the spec
(`sdd/specs/qsscheduler-multi-support.spec.md` §3).

---

## Scope

- Create `docs/QSSCHEDULER.md` with the following sections:
  - **Overview** — one paragraph: QSScheduler is gated behind
    `ENABLE_QS_SCHEDULER`; reads schedule definitions from
    `public.queries` on startup; uses `MemoryJobStore` (jobs rebuild on
    every restart).
  - **Job kinds** — table of the three job kinds and their id prefixes
    (`query_<slug>`, `multi_<slug>`, `cache_<slug>`).
  - **Scheduling a single query** — short SQL example showing how to
    set `attributes.scheduler` on a row with `provider='db'`.
  - **Scheduling a multi-query** — short SQL example with
    `provider='multi'` and a multi-query JSON in `query_raw`. Note that
    multi rows are excluded from cache-refresh jobs.
  - **Reserved JSON sub-key** — short note that
    `attributes.scheduler.output` is reserved for a future
    result-handling patch and is currently parsed but ignored. Reader
    is told the JSON key is forward-compatible — present-day
    schedulers can include it harmlessly.
  - **Misconfiguration WARN** — note that `provider='multi'` with
    non-JSON `query_raw` produces a startup WARNING and the job runs
    anyway in single-query fallback mode.
- Add a single line in the project root `README.md` linking to the new
  document, IF the root README already contains a links/index section.
  Otherwise skip — don't introduce a new section just for one link.

**NOT in scope**:
- Source-code docstrings → TASK-644 (job callable) and TASK-645 (module
  docstring).
- Smoke test → TASK-647.
- Live runtime examples or screenshots.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/QSSCHEDULER.md` | CREATE | Operator-facing runbook for QSScheduler v1 + multi-query support. |
| `README.md` | MODIFY (conditional) | Add a one-line link to `docs/QSSCHEDULER.md` only if a documentation/links section already exists. |

---

## Codebase Contract (Anti-Hallucination)

### Verified References

The doc describes external (DB row / config / log) behaviors only. The
implementation files this doc describes are:

```python
# querysource/scheduler/scheduler.py
class QSScheduler: ...                                 # line 30
    def _load_scheduled_queries(self, rows): ...       # line 89
    def _load_cache_refresh_jobs(self, rows): ...      # line 131
    async def startup(self, app): ...                  # line 179

# querysource/scheduler/jobs.py
async def scheduled_query_job(...): ...                # line 12
async def cache_refresh_job(...): ...                  # line 40
async def scheduled_multiqs_job(...): ...              # added by TASK-644

# querysource/conf.py — ENABLE_QS_SCHEDULER flag
# (verify in conf.py — was introduced by FEAT-008)
```

### Existing `docs/` Naming Convention

```
docs/
├── COLUMN_FILTER_EXAMPLE.md
├── IMPLEMENTATION_SUMMARY.md
├── INTEGRATION_VISUAL_GUIDE.md
├── JOIN_AND_COLUMN_FILTER_INTEGRATION.md
└── ... (uppercase markdown files)
```

→ Use `docs/QSSCHEDULER.md` (uppercase) to match.

### Does NOT Exist

- ~~`docs/scheduler.md` / `docs/qsscheduler.md` (lowercase)~~ — the
  convention is uppercase per the existing files.
- ~~`docs/SDD_*.md`~~ — the SDD specs live under `sdd/specs/`, not
  `docs/`.
- ~~A pre-existing `docs/SCHEDULER.md`~~ — does not exist;
  TASK-648 creates it.

---

## Implementation Notes

### Pattern to Follow

```markdown
# QSScheduler — Embedded Query Scheduler

QSScheduler is an embedded APScheduler that runs scheduled queries
from `public.queries` definitions. It is gated behind
`ENABLE_QS_SCHEDULER` and uses an in-memory job store (jobs rebuild on
every restart).

## Job kinds

| Job kind        | Job ID prefix | Source column(s)                    | Runtime entry point |
|-----------------|---------------|-------------------------------------|---------------------|
| Single-query    | `query_<slug>`| `attributes.scheduler`              | `QS(slug=...).query()` |
| Multi-query     | `multi_<slug>`| `attributes.scheduler` + `provider='multi'` | `MultiQS(slug=...).query()` |
| Cache refresh   | `cache_<slug>`| `cache_options` + `is_cached=True`  | `QS(slug=...).query()` |

Multi-query slugs do **not** receive a cache-refresh job; their
sub-slug caches are refreshed inside the MultiQS pipeline itself.

## Scheduling a single query

```sql
UPDATE public.queries
SET attributes = jsonb_set(
    COALESCE(attributes, '{}'::jsonb),
    '{scheduler}',
    '{"schedule_type": "interval", "schedule": {"minutes": 30}}'::jsonb
)
WHERE query_slug = 'my_query';
```

## Scheduling a multi-query

```sql
UPDATE public.queries
SET attributes  = jsonb_set(
        COALESCE(attributes, '{}'::jsonb),
        '{scheduler}',
        '{"schedule_type": "interval", "schedule": {"hours": 1}}'::jsonb
    ),
    provider    = 'multi',
    query_raw   = '{"queries": {"sub_a": {"slug": "a"}, "sub_b": {"slug": "b"}}}'
WHERE query_slug = 'my_multi';
```

The job will register at startup as `multi_my_multi` and will run the
MultiQS pipeline (sub-query fan-out) end-to-end on each tick. Results
are discarded.

## Reserved JSON sub-key

The JSON key `attributes.scheduler.output` is reserved for a future
result-handling patch. v1 parses it but ignores it. You may include
it today; the scheduler will log a single `DEBUG` line per startup
acknowledging it.

## Misconfiguration WARN

If `provider='multi'` but `query_raw` is empty, plain SQL, or
otherwise not a valid multi-query JSON payload, the scheduler logs a
`WARNING` at startup and registers the job anyway. At runtime, MultiQS
silently falls back to single-query mode.
```

### Key Constraints

- No code blocks more complex than what's shown above.
- Keep the document under 200 lines.
- Use absolute pronouns of QuerySource conventions (e.g. "QSScheduler",
  "MultiQS", `public.queries`) consistently — do not invent
  alternative names.

### References in Codebase

- `querysource/scheduler/scheduler.py` — feature being documented.
- `querysource/scheduler/jobs.py` — the three job callables.
- `sdd/specs/qsscheduler-multi-support.spec.md` §1, §2 — original
  motivation and behavior description.
- `sdd/specs/querysource-scheduler.spec.md` (FEAT-008) — predecessor
  feature, source of the v1 scheduler description.

---

## Acceptance Criteria

- [ ] `docs/QSSCHEDULER.md` exists with all six sections listed in
      Scope.
- [ ] Job-kinds table contains exactly three rows (single-query,
      multi-query, cache-refresh) with the correct id-prefix column.
- [ ] Multi-query SQL example uses `provider='multi'` and a
      JSON-formatted `query_raw`.
- [ ] The doc states that multi rows are excluded from cache-refresh.
- [ ] The doc explains the `attributes.scheduler.output` reserved
      key.
- [ ] The doc describes the misconfiguration WARN behavior.
- [ ] If a README.md links section exists, a one-line link to the new
      doc is added. Otherwise README.md is untouched.
- [ ] No linting errors on markdown if the project lints markdown
      (project does not run a markdown linter by default; skip unless
      `ruff` or `markdownlint` config is found).

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/qsscheduler-multi-support.spec.md`,
   especially §1 and §2.
2. **Verify TASK-644 / 645 / 646 are completed** — the behaviors this
   doc describes must already be in the codebase.
3. **Spot-check `querysource/scheduler/scheduler.py` and
   `querysource/scheduler/jobs.py`** to confirm the documented log
   messages and behaviors match what was implemented (the doc is the
   user contract — do not document a behavior that didn't ship).
4. **Update task status** in
   `sdd/tasks/index/qsscheduler-multi-support.json` → `"in_progress"`.
5. **Write** `docs/QSSCHEDULER.md`.
6. **Check README.md** — if a docs/links section exists, add a single
   link line; otherwise leave README.md untouched.
7. **Move this file** to
   `sdd/tasks/completed/TASK-648-qsscheduler-readme-multi-support.md`.
8. **Update index** → `"completed"`.
9. **Fill in Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
