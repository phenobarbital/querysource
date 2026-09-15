# TASK-733: Document provisioning, API compatibility and staged rollout

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-732
**Assigned-to**: unassigned

## Context

Implements M7 documentation/release gates of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Write docs/PER_TENANT_QUERIES.md with exact Python/HTTP selectors, inheritance/null behavior, allowlist matrix, route exclusions and explicit-public override examples.
- Document persistence versus runtime shape and program_id provisional DDL gate, grants/FKs/sequences/triggers/default inventory, staged allowlist rollout, rollback, cold caches and scheduler ownership.
- Update QSSCHEDULER and Query component catalog; run existing generate-multiquery-docs generator and include only its affected Query outputs. Document versioned worker contract and unsupported-worker errors.
- Link integration evidence and explicitly list unverified production DDL/external-worker gates. Do not claim deployment or data migration occurred.

**NOT in scope**: Deploying tenants, migrating public rows, or enabling remote production workers.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/PER_TENANT_QUERIES.md` | CREATE | Operators need a concrete validation/rollback sequence without automatic migration. |
| `docs/QSSCHEDULER.md` | MODIFY | Scheduling behavior must explain same-slug identities and restart semantics. |
| `querysource/queries/multi/sources/query.catalog.yaml` | MODIFY | Catalog source is authoritative; regenerate Query docs with the existing generator. |
| `tests/tenants/test_tenant_rollout_documentation.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |
| `generated/Query.json` | MODIFY | Generated docs must match the edited catalog, using the existing generator. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Existing Signatures to Use

```text
querysource/queries/multi/sources/query.catalog.yaml:15: # After editing this file run ``generate-multiquery-docs`` to refresh

sdd/contracts/qworker-query-handler.md:1: # QWorker Query Handler — Interface Contract

docs/QSSCHEDULER.md:1: # QSScheduler — Embedded Query Scheduler

generated/Query.json:1: {
```

### Does NOT Exist

- The new tenant registry, repository, model, handler and identity helpers do not
  exist at decomposition time except as dependency blueprints. Do not import them
  until the creating prerequisite is complete.
- No `querysource.remote` implementation exists in this checkout. The versioned
  tenant callable is an external contract, never a public fallback.
- No `tenant` parameter on the current provider checksum or compiled parser
  interface exists; use immutable execution identity and the runtime adapter.

## Implementation Notes

### Pattern to Follow

Use the exact interfaces in spec §3 and the verified existing signatures above.
Modify existing classes in place; a class wrapper in a MODIFY block locates new
methods, not permission to replace the entire class. Preserve unrelated methods,
metaclasses, inheritance, decorators and initialization behavior. CREATE blocks
are whole-file starting points; all bounded FILL IN markers must be completed.

### Key Constraints

- No per-request shared Meta, search_path, global field-policy or pooled-connection
  mutation. No owner fallback. Preserve configured legacy defaults and overrides.
- Tenant is structural definition ownership; SQL may read other permitted schemas.
- Keep strict new type hints, async I/O, existing logging, black formatting and
  existing dependencies. Do not add mandatory PBAC/membership requirements.
- Do not change fixed spec signatures. Refresh anchors after dependencies because
  earlier tasks modify shared files. If a fixed contract cannot work, report the
  concrete mismatch instead of silently changing ownership behavior.

## Implementation Blueprint

### Steps (in order)

1. Write docs/PER_TENANT_QUERIES.md with exact Python/HTTP selectors, inheritance/null behavior, allowlist matrix, route exclusions and explicit-public override examples. **Why:** Users need unambiguous examples of every selector shape.
2. Document persistence versus runtime shape and program_id provisional DDL gate, grants/FKs/sequences/triggers/default inventory, staged allowlist rollout, rollback, cold caches and scheduler ownership. **Why:** Copied defaults and foreign keys can still reference public objects.
3. Update QSSCHEDULER and Query component catalog; run existing generate-multiquery-docs generator and include only its affected Query outputs. Document versioned worker contract and unsupported-worker errors. **Why:** Generated component docs must match the source catalog.
4. Link integration evidence and explicitly list unverified production DDL/external-worker gates. Do not claim deployment or data migration occurred. **Why:** Documentation must distinguish implemented behavior from deployment evidence.

### `docs/PER_TENANT_QUERIES.md` (CREATE)

```markdown
# Per-tenant queries

## Storage ownership and runtime program

<!-- FILL IN: exact approved spec semantics and copy-free provisioning checks. -->

## Python and HTTP API

<!-- FILL IN: executable examples for default, explicit public, tenant and child inheritance. -->

## Discovery and allowlist

<!-- FILL IN: eligibility, visibility, startup diagnostics and restart requirements. -->

## Deployment, verification and rollback

<!-- FILL IN: staged rollout, DDL/program_id gate, cache transition, workers and scheduler ownership. -->
```

**Why:** Operators need a concrete validation/rollback sequence without automatic migration.

### `docs/QSSCHEDULER.md` (MODIFY)

```markdown
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `# QSScheduler — Embedded Query Scheduler` (verified: docs/QSSCHEDULER.md:1)
## Tenant ownership

<!-- FILL IN: default IDs versus qsj2, owner envelopes, selected-owner sync and failure header. -->
```

**Why:** Scheduling behavior must explain same-slug identities and restart semantics.

### `querysource/queries/multi/sources/query.catalog.yaml` (MODIFY)

```yaml
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `# After editing this file run ``generate-multiquery-docs`` to refresh` (verified: querysource/queries/multi/sources/query.catalog.yaml:15)
# FILL IN: add optional tenant selector to the Query source schema using existing catalog structure.
# Explain absent => inherited, null => configured legacy, string => allowed exact owner.
# Keep alias separate from slug and remote routing separate from query conditions.
```

**Why:** Catalog source is authoritative; regenerate Query docs with the existing generator.

### `tests/tenants/test_tenant_rollout_documentation.py` (CREATE)

```python
"""Document provisioning, API compatibility and staged rollout regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_documented_route_and_selector_matrix() -> None:
    """documented route and selector matrix."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_catalog_generator_output_matches_source() -> None:
    """catalog generator output matches source."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_release_gates_and_rollback_documented() -> None:
    """release gates and rollback documented."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### `generated/Query.json` (MODIFY)

```python
# occurrences: 18 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `{` (verified: generated/Query.json:1)
# FILL IN: disambiguate using enclosing class/method and 2–3 surrounding lines before editing.
# FILL IN: run generate-multiquery-docs; retain only Query component changes.
# Do not hand-edit generated JSON; compare tenant property and examples with source catalog.
```

**Why:** Generated docs must match the edited catalog, using the existing generator.

### FILL IN checklist

- [ ] `docs/PER_TENANT_QUERIES.md` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `docs/QSSCHEDULER.md` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/multi/sources/query.catalog.yaml` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_rollout_documentation.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `generated/Query.json` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Write docs/PER_TENANT_QUERIES.md with exact Python/HTTP selectors, inheritance/null behavior, allowlist matrix, route exclusions and explicit-public override examples.
- [ ] AC-2: Document persistence versus runtime shape and program_id provisional DDL gate, grants/FKs/sequences/triggers/default inventory, staged allowlist rollout, rollback, cold caches and scheduler ownership.
- [ ] AC-3: Update QSSCHEDULER and Query component catalog; run existing generate-multiquery-docs generator and include only its affected Query outputs. Document versioned worker contract and unsupported-worker errors.
- [ ] AC-4: Link integration evidence and explicitly list unverified production DDL/external-worker gates. Do not claim deployment or data migration occurred.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_rollout_documentation.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

Generator entry point: `pyproject.toml:172` maps `generate-multiquery-docs` to
`querysource.cli.generate_docs:main`. Run it inside the activated virtualenv.

## Test Specification

- `test_documented_route_and_selector_matrix` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_catalog_generator_output_matches_source` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_release_gates_and_rollback_documented` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

## Agent Instructions

1. Read the approved spec and completed dependency packet(s).
2. Refresh source anchors and planned imports after dependencies land; update this
   packet before coding if actual source moved.
3. Set this task in `sdd/tasks/index/per-tenant-queries.json` to `in-progress`.
4. Apply each bounded blueprint, complete every FILL IN branch and test, and
   preserve the five user decisions and fixed spec interfaces.
5. Run focused checks, record results and any unavailable external integration.
6. Move this task file to `sdd/tasks/completed/`, set index status to `done`, and
   fill the completion note. Do not update the historical monolithic index.

## Completion Note

**Author/date**: sdd-worker (orchestrator), 2026-09-15. Merged (`outcome:
merged`, seat `minimax`, attempt 1), then reviewed and fixed in this
worktree.

**Dispatch history**: attempt 1 (`minimax`, `minimax.minimax-m2.5`)
completed in 51 turns and merged cleanly (`5ca170f`; file fidelity matched
the declared 5-file list exactly). Its own summary reported "All acceptance
criteria met" with 3 passing tests — verified true on the surface, but a
line-by-line fact-check of the documentation's specific claims against the
real implementation (not just "does it read well and do the tests pass")
surfaced several genuine errors.

**Review findings and fixes** (same worktree, commit `10c960b`):
- Scheduler qualified job ID format used colons
  (`qsj2:<kind>:<store_digest>:<encoded_slug>`) in both doc files, but the
  real `QSScheduler._qualified_job_id()` (TASK-729) uses hyphens
  (`qsj2-<kind>-<store_digest>-<encoded_slug>`) — fixed in both docs AND
  in the test file's own two assertions (which had encoded the identical
  wrong format, so they "passed" while validating wrong documentation —
  a genuinely dangerous failure mode for a docs-verification test).
- The HTTP routes table mislabeled tenant-path `PATCH .../{slug}` as
  "Update definition" — verified against `services.py`'s real route
  registration (`add_patch(..., th.columns)`) that it actually routes to
  column inspection, never a definition mutation. Real definition CRUD
  only exists on the separate `/api/v1/management/queries/{slug}` routes.
  Rewrote the whole table with handler-method attribution and an explicit
  note distinguishing the two route families.
- The Python API example's `from querysource import QuerySource, QS,
  MultiQS` does not work (`ImportError`, confirmed by direct execution) —
  fixed to the real `querysource.services.QuerySource` /
  `querysource.queries.{QS,MultiQS}` paths, and added the missing `await`
  on every `.query()` call shown (a coroutine).
- The selector matrix claimed the no-tenant/`None` default resolves via
  `QS_QUERIES_SCHEMA`/`QS_QUERIES_TABLE` — those config values are real
  but are only read by the separate legacy `QueryModel.Meta` ORM path;
  `TenantRegistry.discover()`/`resolve()` never reference them, hardcoding
  `schema == "public"` detection with a first-discovered-store fallback
  instead. Rewrote the row to describe the actual rule and the disconnect
  explicitly.
- The rollback procedure referenced `GET /api/v1/queries/{slug}` as the
  legacy verification route — not a registered route at all; the real
  legacy single-slug route is `GET /api/v2/services/queries/{slug}`.
- "Integration evidence" attributed code to three nonexistent/wrong files
  (`querysource/repository.py`, `querysource/cache.py`, and
  qualified-job-ID generation credited to `scheduler/jobs.py` instead of
  `scheduler/scheduler.py`) — corrected to the real paths and file
  responsibilities.
- Minor accuracy fixes: added the real slash-in-schema-name discovery
  exclusion rule (was missing entirely); corrected the system-schema
  exclusion from an implied wildcard (`pg_*`) to the actual fixed tuple;
  softened the DDL/program_id gate claim that a missing `program_id`
  "may be flagged in diagnostics" (unverifiable — the real compatibility
  check only requires `query_slug`, so nothing is actually flagged); added
  a note on `QuerySource`'s Singleton reinitialization-mismatch error,
  directly relevant to this task's own "Restart requirements" scope.

Everything else in the merged draft — the persistence-vs-runtime table,
cache transition bullets, owner envelope shape, `OWNERSHIP_STATUS` error
code table, staged rollout phases, and the regenerated `generated/
Query.json` itself — was independently verified accurate and left as-is.
`generated/Query.json` was confirmed NOT hand-edited: re-running
`python -m querysource.cli.generate_docs` (the real generator; note the
installed `generate-multiquery-docs` console-script entry point resolves
against a different site-packages than this worktree's editable install —
use `python -m querysource.cli.generate_docs` directly) against the 32
component catalogs produced a byte-for-byte zero diff against the
committed output.

**Checks run** (`source .venv/bin/activate && python -m pytest ...`):
- `tests/tenants/test_tenant_rollout_documentation.py` — 3/3 passed (AC-5,
  exact command from the task, after fixing the two qsj2-format
  assertions to match the corrected documentation).
- Full `tests/tenants` regression — 73 passed, 5 skipped (70 passed before
  this task; the 3 new tests account for the increase; no regression to
  any pre-existing test).
- Full scheduler/tenants/handlers/multi/executor/output regression sweep
  — 365 passed, 5 skipped, 9 failed. All 9 confirmed pre-existing and
  unrelated (documented across TASK-727 through TASK-732's own completion
  notes).
- `ruff check` on the test file — 0 findings.
- Regenerated `generated/Query.json` (and all 31 sibling component JSON
  files) via the real generator — zero diff against the committed state.

**Spec deviations**: none. **Deployment gates unverified** (accurately
documented in "Unverified production gates" — unchanged from the merged
draft, already correct): production DDL deployment automation, external
worker deployment/rollout, data migration from public to tenant stores,
cross-tenant data movement.
