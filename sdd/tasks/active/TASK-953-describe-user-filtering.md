# TASK-953: Describe exposure of `user_filtering` (alias + admin-only rules)

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-952
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 (describe half), AC-15. `describe_slug()` builds `base` from
`QueryModel.columns(QueryModel)`, so the new fields must exist on the model
(TASK-952) before the describe payload can carry them.

---

## Scope

- Add `"user_filter_options"` to `ADMIN_FIELDS`.
- In the `capabilities` dict, add `"user_filtering"` and make `"h_filtering"` an alias with the same value.
- Write `tests/handlers/test_describe_user_filtering.py`.

**NOT in scope**: `docs/DESCRIBE_API.md` (TASK-957).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/describe.py` | MODIFY | ADMIN_FIELDS + capabilities |
| `tests/handlers/test_describe_user_filtering.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.describe import DescribeGrants, describe_slug, ADMIN_FIELDS   # verified: describe.py:54, :230, :35
from querysource.models import QueryModel                                             # verified: querysource/models.py:49
```

### Existing Signatures to Use
```python
# querysource/queries/describe.py
ADMIN_FIELDS: tuple[str, ...] = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")  # line 35
class DescribeGrants(BaseModel): raw: bool = False; admin: bool = False           # line 54 (frozen)
def redact_payload(payload: dict, grants: DescribeGrants) -> tuple[dict, list[str]]   # line 185
def describe_slug(definition: QueryModel | dict, grants: DescribeGrants, *, columns_link: str, vocabulary_link: str) -> dict  # line 230
    base = {name: _get(name) for name in QueryModel.columns(QueryModel)}          # line 245
    capabilities = {..., "h_filtering": bool(base.get("h_filtering") or False), ...}   # lines 262-270 (h_filtering at 267)
    return {**payload, "derived": derived, "redacted": redacted}                  # line 288
```

### Does NOT Exist
- ~~`DescribeGrants.superuser`~~ — only `raw` and `admin`.
- ~~a separate redaction list for capabilities~~ — `capabilities` are never redacted; only top-level payload keys are.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/describe.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_describe_user_filtering.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/describe.py#describe_slug",
    "sym:querysource/queries/describe.py#DescribeGrants",
    "sym:querysource/queries/describe.py#redact_payload"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Extend `ADMIN_FIELDS` — *why*: rules reveal who sees what; same policy as other admin-only fields (spec §2).
2. Replace the `h_filtering` capability line — *why*: alias reports `user_filtering`, never the dead legacy flag.

### `querysource/queries/describe.py` (MODIFY — ADMIN_FIELDS)
```python
# occurrences: 1 (verified: grep -c '^ADMIN_FIELDS: tuple\[str, ...\] = ' querysource/queries/describe.py)
# REPLACE line 35 with:
ADMIN_FIELDS: tuple[str, ...] = (
    "dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by", "user_filter_options",
)
```

### `querysource/queries/describe.py` (MODIFY — capabilities)
```python
# occurrences: 1 (verified: grep -c '"h_filtering": bool(base.get("h_filtering") or False),' querysource/queries/describe.py)
# REPLACE line 267 with:
        "user_filtering": bool(base.get("user_filtering") or False),
        # Deprecated alias (FEAT-184): same value as user_filtering, removed in 5.3.0.
        "h_filtering": bool(base.get("user_filtering") or False),
```

### `tests/handlers/test_describe_user_filtering.py` (CREATE)
```python
"""FEAT-184 TASK-953 — describe exposure of user_filtering."""
from __future__ import annotations

from querysource.models import QueryModel
from querysource.queries.describe import DescribeGrants, describe_slug


def _describe(model: QueryModel, *, admin: bool) -> dict:
    return describe_slug(model, DescribeGrants(admin=admin), columns_link="c", vocabulary_link="v")


def test_describe_capabilities_user_filtering() -> None:
    model = QueryModel(query_slug="s", program_slug="acme", user_filtering=True,
                       user_filter_options={"rules": [{"column": "user_id", "attribute": "user_id"}]})
    caps = _describe(model, admin=False)["derived"]["capabilities"]
    assert caps["user_filtering"] is True and caps["h_filtering"] is True


# FILL IN: test_describe_user_filter_options_admin_only — non-admin: key absent and listed in payload["redacted"];
#          admin: payload["user_filter_options"] equals the rules
# FILL IN: test_describe_legacy_h_filtering_not_reported — h_filtering=True, user_filtering=False → both capabilities False
```

### FILL IN checklist
- [ ] remaining tests

---

## Acceptance Criteria

- [ ] Spec AC-15
- [ ] Existing describe tests still pass
- [ ] `ruff check querysource/queries/describe.py tests/handlers/test_describe_user_filtering.py` clean

---

## Validation Commands

- `pytest tests/handlers/test_describe_user_filtering.py -q`
- `pytest tests/handlers/test_describe_detail.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-952 is done; verify the Codebase Contract; implement from the blueprint.
3. Run the Validation Commands; commit only the listed files.
4. Close with `parrot sdd task close TASK-953 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
