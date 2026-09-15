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

To be completed by the implementing agent: author/date, exact checks and results,
files changed, deployment gates still unverified, and any approved spec deviations.
