# TASK-808: Run-as column — tolerant reads + payload guards in DefinitionRepository

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6, part one (G5, G6). The run-as user is a **repository-only**
column, `scheduler_run_as_user_id`, and never part of `QueryModel` or
`TenantQueryDefinition`. This task must land **before any DDL is applied**:
`TenantQueryDefinition(**row)` raises `TypeError` on an unknown column
(verified 2026-09-30), so a migrated store would otherwise break every read.

---

## Scope

- Add `RUN_AS_COLUMN = "scheduler_run_as_user_id"` at module level.
- `_row_to_persisted`: pop `RUN_AS_COLUMN` before validation, so it never
  reaches models, `definition_revision`, list, get, export or `:insert`.
- `create`/`upsert`/`patch`: reject a payload containing `RUN_AS_COLUMN` with
  `TenantError("… cannot be set via the API", error_code="invalid_tenant")`.
- `schedulable()`: select the column. On a missing-column error, retry with
  `NULL AS scheduler_run_as_user_id`.
- `get_run_as(identity) -> int | None`: `None` when unset, when the row is
  missing, or when the column is absent.
- Write unit tests with a mock connection.

**NOT in scope**: writing the column and the audit (TASK-809), and the DDL docs (TASK-810).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | constant, pop, guards, tolerant reads |
| `tests/tenants/test_run_as_repository_reads.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from asyncdb.drivers.pg import UndefinedTableError, pg        # verified: definitions.py:4
from asyncdb.drivers.pg import UndefinedColumnError           # verified import (add to the same line)
from querysource.tenant_errors import TenantError             # verified: definitions.py imports it
from querysource.repositories.definitions import DefinitionRepository  # tests
```

### Existing Signatures to Use
```python
# querysource/repositories/definitions.py
_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"   # :49
def _qualified_table(self, store) -> str                        # :70
def _row_to_persisted(self, row, store) -> tuple[dict, str|None]  # :88 — `data = dict(row)` then
    legacy_program_slug = data.pop("program_slug", None) if store.contract == "legacy" else None   # :104
    validated = TenantQueryDefinition(**data)                   # :105
async def _fetch_row(self, store, slug)                         # :121 (SELECT *)
def _translate_write_error(self, err, store) -> None            # :128 (pattern: typed error first, then message match)
async def schedulable(self, store) -> tuple[Mapping, ...]:      # :282 (SELECT {_SCHEDULABLE_COLUMNS} … WHERE {_SCHEDULABLE_PREDICATE})
async def create(self, store, data)                             # :292 (TenantQueryDefinition(**data) :299)
async def upsert(self, identity, data)                          # :331 (TenantQueryDefinition(**data) :346)
async def patch(self, identity, data)                           # :399 — `if "program_slug" in data:` guard :421
# tests/tenants/test_tenant_repository_writes.py:69-115 — _MockConn / _factory pattern to copy
```

### Does NOT Exist
- ~~`TenantQueryDefinition.scheduler_run_as_user_id`~~ / ~~`QueryModel.scheduler_run_as_user_id`~~: never add them.
- ~~A field allowlist in `QueryManager.patch`~~: the repository guard is the only protection.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/repositories/definitions.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_run_as_repository_reads.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository._row_to_persisted",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.schedulable",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.create",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.upsert",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.patch"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Missing-column detection must mirror `_translate_write_error`: check
  `isinstance(exc, UndefinedColumnError)` first, then the message text
  (`RUN_AS_COLUMN in msg and "does not exist" in msg`). Anything else re-raises.
- Log a WARNING **once per store** (keep a module-level set of
  `(schema, table)`) when the fallback is used. Do not spam on every scheduler sync.
- `get_run_as`: run `SELECT {col} FROM {table} WHERE query_slug = $1`. A
  missing-column error returns `None`, and a missing row returns `None`.
  Return an `int` when the value is not null.

---

## Implementation Blueprint

### Steps (in order)
1. Add the constant, import and helper — *why*: one name used everywhere.
2. Pop the column in `_row_to_persisted` — *why*: the pop must precede every DDL rollout.
3. Add the guards in `create`, `upsert` and `patch` — *why*: G6, PATCH is otherwise an open column writer.
4. Add the tolerant `schedulable` and new `get_run_as`, then write the tests.

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"' …) — :49
# BEFORE — insert above it:
RUN_AS_COLUMN: str = "scheduler_run_as_user_id"
_RUN_AS_FALLBACK_WARNED: set[tuple[str, str]] = set()


def _is_missing_run_as_column(exc: Exception) -> bool:
    """True when exc means the store has not been migrated (no run-as column)."""
    # FILL IN: isinstance(exc, UndefinedColumnError) or (RUN_AS_COLUMN in msg and "does not exist" in msg)

# occurrences: 1 (verified: grep -c '        legacy_program_slug = data.pop("program_slug", None) if store.contract == "legacy" else None' …) — :104
# BEFORE — insert above it:
        # FEAT-159: repository-only column; never enters models, revisions or API output.
        data.pop(RUN_AS_COLUMN, None)

# occurrences: 1 (verified: grep -c '        if "program_slug" in data:' …) — :421 (patch)
# BEFORE — insert above it:
        self._reject_run_as(data)
# In create (:299) and upsert (:346): call `self._reject_run_as(data)` as the first statement
#   before `validated = TenantQueryDefinition(**data)` — FILL IN: anchor on the line above each (2 occurrences
#   of the validate line → use the method name context `async def create(` / `async def upsert(`).

# occurrences: 1 (verified: grep -c '    async def delete(self, identity: QueryIdentity) -> bool:' …) — :476
# BEFORE — insert above it:
    @staticmethod
    def _reject_run_as(data: Mapping[str, Any]) -> None:
        """Refuse API payloads that try to set the run-as user (G6)."""
        if RUN_AS_COLUMN in data:
            raise TenantError(
                f"{RUN_AS_COLUMN} cannot be set via the API",
                error_code="invalid_tenant",
            )

    async def get_run_as(self, identity: QueryIdentity) -> int | None:
        """Stored run-as user id; None when unset, row missing, or column absent."""
        # FILL IN: per Key Constraints
```
Also change `schedulable()` (`:282`): select
`f"{_SCHEDULABLE_COLUMNS}, {RUN_AS_COLUMN}"`, and on
`_is_missing_run_as_column(exc)` retry with
`f"{_SCHEDULABLE_COLUMNS}, NULL AS {RUN_AS_COLUMN}"` and warn once per store.
**Why**: popping the column keeps `definition_revision(persisted)` (the cache
identity) unchanged by run-as edits. The guards turn today's accidental
`TypeError` into a deliberate API error.

### FILL IN checklist
- [ ] `_is_missing_run_as_column`
- [ ] `_reject_run_as` calls in create/upsert (anchor by method context)
- [ ] tolerant `schedulable` with warn-once
- [ ] `get_run_as`

---

## Acceptance Criteria

- [ ] A row containing `scheduler_run_as_user_id` validates, and the value is absent from the persisted dict and the revision input.
- [ ] `patch`, `upsert` and `create` with the key raise `TenantError(invalid_tenant)`.
- [ ] `schedulable()` returns rows with the key both on migrated stores (real value) and un-migrated ones (`None`).
- [ ] `get_run_as` returns int or `None` as specified.
- [ ] The existing repository tests pass.

## Validation Commands

- `pytest tests/tenants/test_run_as_repository_reads.py -q`
- `pytest tests/tenants/test_tenant_repository_reads.py -q`
- `pytest tests/tenants/test_tenant_repository_writes.py -q`

---

## Test Specification

```python
# tests/tenants/test_run_as_repository_reads.py
import pytest

from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository
from querysource.tenant_errors import TenantError


def test_row_to_persisted_pops_run_as():
    ...  # FILL IN: minimal row + {RUN_AS_COLUMN: 42}; persisted lacks the key


@pytest.mark.parametrize("method", ["patch", "upsert"])
async def test_payload_rejects_run_as_key(method):
    ...  # FILL IN: TenantError with error_code == "invalid_tenant"; no SQL issued


async def test_schedulable_tolerates_missing_column():
    ...  # FILL IN: first fetch_all raises UndefinedColumnError-like; second returns rows; key present as None


async def test_get_run_as_values():
    ...  # FILL IN: 42 → 42; NULL → None; missing row → None; missing column → None
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-808 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

Seat: sonnet (native) · Attempts: 1. Implemented run-as repository reads in definitions.py. Merge-tier validation 17 passed. No fix commits (review: coder-review:f499ed3983a8659cad06fff3).
