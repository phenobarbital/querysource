# TASK-810: Run-as DDL — documentation (qs_owner/qs_app) + integration fixture

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-809
**Assigned-to**: unassigned

---

## Context

Spec §2 Data Models, §3 Module 6, and §8 Q3 (resolved: `qs_owner` + `qs_app`
roles). Schema changes ship as documented SQL, following the FEAT-151 pattern
(`docs/PER_TENANT_QUERIES.md:35-75`). The opt-in tenant integration fixture
must create the column and the audit table so the PG-backed tests (TASK-814)
exercise real SQL.

---

## Scope

- `docs/PER_TENANT_QUERIES.md`:
  - add `scheduler_run_as_user_id INTEGER` to the Provisional DDL gate;
  - add a "Run-as user and audit (FEAT-159)" migration section with the
    ALTER, the audit table, the immutability trigger, the
    `qs_owner`/`qs_app` roles and grants, and the owner rollout check;
  - state the deploy order: code first (TASK-808), then the DDL.
- `tests/tenants/conftest.py`: add the column to `_TENANT_TABLE_DDL`, plus a
  companion DDL string for `"{schema}".queries_run_as_audit` (with trigger)
  that the provisioning helper executes.

**NOT in scope**: the OneDrive usage docs (TASK-813).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/PER_TENANT_QUERIES.md` | MODIFY | DDL gate + migration section |
| `tests/tenants/conftest.py` | MODIFY | integration DDL gains column + audit table |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
None (docs and SQL strings only).

### Existing Signatures to Use
```text
docs/PER_TENANT_QUERIES.md
  :35  ### Provisional DDL gate
  :40  CREATE TABLE "{schema}".queries (
  :50      updated_at TIMESTAMPTZ DEFAULT now()          ← add the column line above it
  :54  ### Legacy store migration (FEAT-151)            ← add the new section after this section
tests/tenants/conftest.py
  :20-33  _TENANT_TABLE_DDL = ( 'CREATE TABLE "{schema}".queries (' … ')' )
  :30     "columns_definition text[] DEFAULT '{}'::text[], "   ← add the run-as column line below
  :97-101 provisioning executes CREATE SCHEMA then _TENANT_TABLE_DDL.format(schema=schema)
```
Canonical SQL: copy it verbatim from the spec §2 Data Models block, which is
the source of truth for the column, audit table, function, trigger, roles and
rollout check.

### Does NOT Exist
- ~~A migrations framework in the repo~~: SQL is documented, not auto-applied.
- ~~`<app_role>`~~: replaced by `qs_app` (§8 Q3).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/PER_TENANT_QUERIES.md", "action": "MODIFY"},
    {"path": "tests/tenants/conftest.py", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- **Exclusive task (`parallel: false`)**: it edits `tests/tenants/conftest.py`,
  which every tenant test loads.
- In the fixture, the audit table name must match TASK-809's
  `_run_as_audit_table` (`{table}_run_as_audit`, so `queries_run_as_audit`).
  The fixture should create the trigger too, so `test_run_as_audit_append_only`
  (TASK-814) is meaningful. Roles are **not** created in the fixture, because
  the test DB user owns everything there.
- The docs must say explicitly: **deploy the code first, then run the DDL.**
  An un-migrated store keeps working, while a migrated store with old code
  breaks every read.

---

## Implementation Blueprint

### Steps (in order)
1. Edit the DDL gate and add the migration section — *why*: operators apply SQL from docs (FEAT-151 precedent).
2. Extend the fixture DDL and execute the audit DDL after the table DDL — *why*: TASK-814 PG tests.

### `docs/PER_TENANT_QUERIES.md` (MODIFY)
```markdown
<!-- occurrences: 1 (verified: grep -c '    updated_at TIMESTAMPTZ DEFAULT now()' docs/PER_TENANT_QUERIES.md) — :50 -->
<!-- BEFORE — insert above it: -->
    scheduler_run_as_user_id INTEGER,
<!-- occurrences: 1 (verified: grep -c '### Legacy store migration (FEAT-151)' docs/PER_TENANT_QUERIES.md) — :54 -->
<!-- AFTER that section's end — new section: -->
### Scheduler run-as user and audit trail (FEAT-159)
FILL IN: deploy-order paragraph; spec §2 SQL verbatim (ALTER, audit table, function, trigger,
roles qs_owner/qs_app + grants, rollout check); one paragraph on semantics (set only when a
schedule is created/changed via the management API; never via PATCH payload or scheduler re-sync).
```

### `tests/tenants/conftest.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "    \"columns_definition text[] DEFAULT '{}'::text[], \"" tests/tenants/conftest.py) — :30
# AFTER — insert below it:
    "scheduler_run_as_user_id integer, "
# plus a new module constant after _TENANT_TABLE_DDL:
_RUN_AS_AUDIT_DDL = (
    # FILL IN: CREATE TABLE "{schema}".queries_run_as_audit (...) ; CREATE FUNCTION … ; CREATE TRIGGER …
)
# and in provisioning (:101 area): execute _RUN_AS_AUDIT_DDL.format(schema=schema) after the table DDL,
#   checking the returned error like the existing calls do.
```

### FILL IN checklist
- [ ] Docs section text and SQL verbatim from the spec
- [ ] `_RUN_AS_AUDIT_DDL` and its execution in provisioning

---

## Acceptance Criteria

- [ ] The docs contain the column in the DDL gate and the full FEAT-159 migration section with `qs_owner`/`qs_app` and the rollout check.
- [ ] The fixture DDL includes the column and creates the audit table plus trigger when PG tests are enabled.
- [ ] Without `QS_TEST_POSTGRES_DSN`, the tenant suite still skips cleanly.

## Validation Commands

- `pytest tests/tenants/test_tenant_rollout_documentation.py -q`
- `pytest tests/tenants/test_tenant_repository_reads.py -q`

---

## Test Specification

```python
# No new test file: the rollout-documentation test and the opt-in PG tests (TASK-814) cover this task.
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-810 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
