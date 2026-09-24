# TASK-759: `columns_definition` field and write policy

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2, design-research S7, spec §8 Q4 (answered: "Yes, become writable").
Multi definitions declare their output columns in a new `columns_definition` array,
returned by HEAD/PATCH on the tenant route (TASK-760). The field must exist on **both**
`TenantQueryDefinition` and `QueryModel` because `QueryModel.Meta.strict = True`
rejects unknown keys when the repository builds the runtime model. The name must never
be `columns`: `Model.columns()` is the `datamodel` method the repository calls.
Writes must keep working on stores that have not been migrated yet.

---

## Scope

- Declare `columns_definition: List[str]` (array, default `[]`) on `TenantQueryDefinition` and `QueryModel`, right after `grouping`.
- In `DefinitionRepository.create()` and `upsert()`, drop `columns_definition` from the INSERT column list when it is empty.
- Confirm it is writable through the existing repository API (create, upsert, patch) — no new endpoint (Q4).
- Write unit tests.

**NOT in scope**: DDL docs and the test DDL fixture (TASK-763); handlers (TASK-760).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/tenant_models.py` | MODIFY | new field on `TenantQueryDefinition` |
| `querysource/models.py` | MODIFY | new field on `QueryModel` |
| `querysource/repositories/definitions.py` | MODIFY | omit empty `columns_definition` on create/upsert |
| `tests/tenants/test_columns_definition.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.repositories.definitions import DefinitionRepository, _TENANT_COLUMNS  # verified: querysource/repositories/definitions.py:41,56
from querysource.tenant_models import TenantQueryDefinition   # verified: querysource/repositories/definitions.py:21
from querysource.models import QueryModel                     # verified: querysource/repositories/definitions.py:19
from querysource.tenants import QueryIdentity, QueryStore     # verified: tests/tenants/test_tenant_repository_writes.py:7
```

### Existing Signatures to Use
```python
# querysource/tenant_models.py:24  (module forbids `X | None` and `list[X]` syntax — Cython datamodel)
class TenantQueryDefinition(BaseModel):
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)  # line 50

# querysource/models.py:48
class QueryModel(Model):
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)  # line 67
    class Meta: strict = True   # lines 101-105

# querysource/repositories/definitions.py
_TENANT_COLUMNS = frozenset(TenantQueryDefinition(query_slug="__probe__").columns().keys())  # 41-43
async def create(self, store: QueryStore, data: Mapping[str, Any]) -> Mapping[str, Any]:   # 292
    persisted = {k: v for k, v in persisted.items() if v is not None}                      # 303
async def upsert(self, identity: QueryIdentity, data: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:  # 328
    persisted = {k: v for k, v in persisted.items() if v is not None}                      # 347
async def patch(self, identity: QueryIdentity, data: Mapping[str, Any]) -> Mapping[str, Any]:  # 393; SETs every supplied key (no allowlist)
```

### Does NOT Exist
- ~~A field named `columns`~~ — forbidden; collides with `Model.columns()` (definitions.py:42, 84).
- ~~A dedicated `columns_definition` endpoint~~ — not added (Q4: generic CRUD only).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/tenant_models.py", "action": "MODIFY"},
    {"path": "querysource/models.py", "action": "MODIFY"},
    {"path": "querysource/repositories/definitions.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_columns_definition.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/tenant_models.py#TenantQueryDefinition",
    "sym:querysource/models.py#QueryModel",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.create",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.upsert"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Copy the `grouping` declaration style exactly (`List[str]`, `db_type='array'`, `default_factory=list`).
- Both models change in this one task — a split would break every tenant read between the two commits (spec §7 "Strict runtime model").
- Rows without the key must still read as `[]` (existing `_base_row` fixtures lack it).

---

## Implementation Blueprint

### Steps (in order)
1. Add the field to `TenantQueryDefinition` — *why*: persistence validation and `_TENANT_COLUMNS`.
2. Add the identical field to `QueryModel` — *why*: strict runtime model.
3. Drop empty values on create/upsert — *why*: S7, un-migrated stores keep accepting writes.
4. Write the tests.

### `querysource/tenant_models.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    grouping: List[str]' querysource/tenant_models.py)
# AFTER — insert below `    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)` (verified: querysource/tenant_models.py:50)
    columns_definition: List[str] = Field(
        required=False,
        db_type='array',
        default_factory=list,
        comment='Declared output columns of a multi-query definition (HEAD/PATCH inspection).',
    )
```

### `querysource/models.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    grouping: List[str]' querysource/models.py)
# AFTER — insert below `    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)` (verified: querysource/models.py:67)
    columns_definition: List[str] = Field(
        required=False,
        db_type='array',
        default_factory=list,
        comment='Declared output columns of a multi-query definition (HEAD/PATCH inspection).',
    )
```

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 2 (verified: grep -c '        persisted = {k: v for k, v in persisted.items() if v is not None}' querysource/repositories/definitions.py)
# Disambiguated: in create() the anchor is preceded by
#         validated = TenantQueryDefinition(**data)
#         persisted = validated.to_dict()
#         <blank + comment "# Remove None values to let database defaults apply">
# (lines 299-303); in upsert() it is line 347. Insert the SAME two lines below BOTH:
        if not persisted.get("columns_definition"):
            persisted.pop("columns_definition", None)
```
Add one comment line above each: `# FEAT-151: an empty declaration never requires the migrated column.`

### `tests/tenants/test_columns_definition.py` (CREATE)
```python
"""FEAT-151: columns_definition field, reads without the column, write policy."""
import pytest

from querysource.models import QueryModel
from querysource.repositories.definitions import _TENANT_COLUMNS, DefinitionRepository
from querysource.tenant_models import TenantQueryDefinition
from querysource.tenants import QueryIdentity, QueryStore


def test_models_declare_columns_definition() -> None:
    assert TenantQueryDefinition(query_slug="x").columns_definition == []
    assert QueryModel(query_slug="x", program_slug="p").columns_definition == []
    assert "columns_definition" in _TENANT_COLUMNS


def test_runtime_model_accepts_declared_columns() -> None:
    m = QueryModel(query_slug="x", program_slug="p", columns_definition=["a", "b"])
    assert m.columns_definition == ["a", "b"]


@pytest.mark.asyncio
async def test_repository_write_omits_empty_columns_definition() -> None:
    # FILL IN: reuse the _MockConn/_factory/_base_row pattern from
    # tests/tenants/test_tenant_repository_writes.py:66-110 (copy locally; do not
    # import private helpers across test modules). Assert that the INSERT SQL of
    # create() and upsert() does NOT contain "columns_definition" for
    # data without it, and DOES contain it for columns_definition=["a"].
    ...


@pytest.mark.asyncio
async def test_patch_writes_columns_definition() -> None:
    # FILL IN: patch(identity, {"columns_definition": ["a"]}) issues an UPDATE whose
    # SET clause names "columns_definition" (Q4: writable through generic CRUD).
    ...
```

### FILL IN checklist
- [ ] write-policy test body — bounded by AC-14.
- [ ] patch test body — bounded by §8 Q4 answer.

---

## Acceptance Criteria

- [ ] Both models expose `columns_definition` with default `[]`; `_TENANT_COLUMNS` contains it (AC-7).
- [ ] create/upsert omit it when empty and include it when non-empty (AC-14).
- [ ] create/upsert/patch accept a non-empty value (Q4).
- [ ] Existing repository tests stay green.
- [ ] `ruff check querysource/tenant_models.py querysource/models.py querysource/repositories/definitions.py tests/tenants/test_columns_definition.py`

## Validation Commands

- `pytest tests/tenants/test_columns_definition.py -q`
- `pytest tests/tenants/test_tenant_repository_writes.py -q`
- `pytest tests/tenants/test_tenant_repository_reads.py -q`
- `pytest tests/tenants/test_tenant_definition_model.py -q`

---

## Agent Instructions

1. Read the spec. 2. Verify the Codebase Contract. 3. Implement from the blueprint and complete every `FILL IN`. 4. Run the validation commands and ruff. 5. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
