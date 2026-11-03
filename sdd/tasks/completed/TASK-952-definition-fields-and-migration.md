# TASK-952: `user_filtering` / `user_filter_options` fields and ADD-only migration

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 (model + migration half). Both definition models reject
unknown kwargs and the tenant repository validates whole rows, so the
migration only ADDS columns and `h_filtering` stays declared (deprecated)
until 5.3.0 (spec §2 refinements, §7 Known Risks).

---

## Scope

- Add `user_filtering` and `user_filter_options` to `QueryModel` and `TenantQueryDefinition`,
  directly after `h_filtering`; change `h_filtering`'s comment to mark it deprecated.
- Create `docs/sql/user_filtering.sql` (idempotent, ADD-only).
- Add both columns to the `_FULL_COLUMNS` DDL in `tests/tenants/test_query_move.py`.
- Write `tests/test_models_user_filtering.py`.

**NOT in scope**: describe output (TASK-953); any code reading the fields (TASK-954+).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/models.py` | MODIFY | Two fields on `QueryModel`, deprecated comment |
| `querysource/tenant_models.py` | MODIFY | Same on `TenantQueryDefinition` |
| `docs/sql/user_filtering.sql` | CREATE | ADD-only migration |
| `tests/tenants/test_query_move.py` | MODIFY | Test DDL carries both columns |
| `tests/test_models_user_filtering.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.models import QueryModel                    # verified: querysource/models.py:49
from querysource.tenant_models import TenantQueryDefinition  # verified: querysource/tenant_models.py:24
# Field / Optional are already imported in both model modules (used at models.py:75, tenant_models.py:57)
```

### Existing Signatures to Use
```python
# querysource/models.py
class QueryModel(Model):                                                       # line 49
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)   # line 75
    h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")  # line 76
    class Meta: name = QS_QUERIES_TABLE; schema = QS_QUERIES_SCHEMA; strict = True   # lines 108-112
# querysource/tenant_models.py
class TenantQueryDefinition(BaseModel):                                        # line 24
    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')  # line 58
# querysource/conf.py:372-373 — QS_QUERIES_SCHEMA fallback 'public', QS_QUERIES_TABLE fallback 'queries'
# querysource/repositories/definitions.py:131-136 — store columns derived from model columns (picks new fields up automatically)
```

### Does NOT Exist
- ~~a migrations framework (alembic, yoyo)~~ — none in the repo; `docs/sql/` holds plain SQL only.
- ~~DDL for the definitions table in `docs/sql/`~~ — only `datasources_logic.sql`, `useful_pg_extensions.sql`.
- ~~`QueryModel.__post_init__` alias logic~~ — not wanted; `h_filtering` stays a plain field and is never consulted.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/models.py", "action": "MODIFY"},
    {"path": "querysource/tenant_models.py", "action": "MODIFY"},
    {"path": "docs/sql/user_filtering.sql", "action": "CREATE"},
    {"path": "tests/tenants/test_query_move.py", "action": "MODIFY"},
    {"path": "tests/test_models_user_filtering.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/models.py#QueryModel",
    "sym:querysource/tenant_models.py#TenantQueryDefinition"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Replace the `h_filtering` line in both models with the three lines below — *why*: ADD-only rename (spec §2).
2. Create the SQL file — *why*: operators run it per store (open question Q1 keeps the location provisional).
3. Extend the test DDL — *why*: AC-2; the move contract must carry the new columns.

### `querysource/models.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")' querysource/models.py)
# REPLACE the line at querysource/models.py:76 with:
    h_filtering: bool = Field(
        required=False, default=False,
        comment="DEPRECATED alias of user_filtering; column dropped in 5.3.0. Never consulted.",
    )
    user_filtering: bool = Field(
        required=False, default=False,
        comment="Per-user automatic filtering compiled from user_filter_options (FEAT-184).",
    )
    user_filter_options: Optional[dict] = Field(
        required=False, db_type='jsonb', default_factory=dict,
        comment="Typed rule list: {rules, program, bypass} (FEAT-184).",
    )
```

### `querysource/tenant_models.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')" querysource/tenant_models.py)
# REPLACE the line at querysource/tenant_models.py:58 with the same three fields as above (single quotes to match the module style)
```

### `docs/sql/user_filtering.sql` (CREATE)
```sql
-- FEAT-184: per-user automatic filtering columns.
-- Run against the legacy store (QS_QUERIES_SCHEMA.QS_QUERIES_TABLE, default public.queries)
-- and against every tenant <schema>.queries table. Idempotent and ADD-only:
-- h_filtering is kept until 5.3.0 because definition models reject unknown columns.
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Tenant stores: repeat for each schema, e.g.
-- ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;
-- ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;

-- 5.3.0 (not part of FEAT-184):
-- ALTER TABLE public.queries DROP COLUMN IF EXISTS h_filtering;
```

### `tests/tenants/test_query_move.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false, query_raw text, is_raw boolean DEFAULT false," tests/tenants/test_query_move.py)
# REPLACE line 40 with:
  qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false,
  user_filtering boolean DEFAULT false, user_filter_options jsonb DEFAULT '{{}}',
  query_raw text, is_raw boolean DEFAULT false,
```
**Why**: the string is `.format()`-ed, so braces stay doubled.

### `tests/test_models_user_filtering.py` (CREATE)
```python
"""FEAT-184 TASK-952 — definition fields."""
from __future__ import annotations

from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition


def test_query_model_defaults() -> None:
    model = QueryModel(query_slug="s", program_slug="default")
    assert model.user_filtering is False
    assert model.user_filter_options == {}
    assert model.h_filtering is False


# FILL IN: test_query_model_accepts_values — user_filtering=True, user_filter_options={"rules": [...]}
# FILL IN: test_tenant_definition_declares_fields — same on TenantQueryDefinition(query_slug="s")
# FILL IN: test_legacy_row_with_only_h_filtering_still_validates — TenantQueryDefinition(query_slug="s", h_filtering=True)
#          constructs and user_filtering stays False (spec §2: h_filtering never enables filtering)
```

### FILL IN checklist
- [ ] tenant_models three-field block
- [ ] remaining tests

---

## Acceptance Criteria

- [ ] Spec AC-1 and AC-2
- [ ] Existing tenant repository tests still pass
- [ ] `ruff check querysource/models.py querysource/tenant_models.py tests/test_models_user_filtering.py` clean

---

## Validation Commands

- `pytest tests/test_models_user_filtering.py -q`
- `pytest tests/tenants/test_tenant_repository_reads.py -q`
- `pytest tests/tenants/test_tenant_repository_writes.py -q`
- `pytest tests/tenants/test_columns_definition.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Verify the Codebase Contract; implement from the blueprint; complete every `# FILL IN:`.
3. Run the Validation Commands; commit only the listed files.
4. Close with `parrot sdd task close TASK-952 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (FEAT-184)
**Date**:
**Notes**: Seat: gpt-5.6-luna x2 then gpt-5.6-sol (attempt 3) · merged manually (engine task-tier validation unavailable: attempt 3 had no execution_id; sub-worktree lacked built .so). Validated in feature worktree: tests/test_models_user_filtering.py + tests/tenants -> 155 passed. Review fix f28c0c7: tests/tenants/test_tenant_repository_reads.py (outside task file list) fixture needed the two new columns because revision hashes the model's to_dict(). Residual lint: models.py DTZ005 (left to /sdd-done). feedback NOT recorded (repo-level fixture lesson).

**Deviations from spec**: none
