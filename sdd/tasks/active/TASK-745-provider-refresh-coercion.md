# TASK-745: Parse `refresh` in `BaseProvider` with `to_flag`

**Feature**: FEAT-149 — Falsy Refresh — flag-condition coercion for `refresh` and `paged`
**Spec**: `sdd/specs/falsy-refresh.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-744
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2. `BaseProvider.__init__` sets `self._refresh = bool(self._conditions['refresh'])`
(`providers/abstract.py:84`), so `refresh="false"` is `True`, and `QS.query()`
(`queries/qs.py:385,414`) then bypasses the cache. This is the core bug of FEAT-149.

---

## Scope

- Import `to_flag` from `..types` in `querysource/providers/abstract.py`.
- Replace the `bool(...)` at line 84 with `to_flag(...)`; on `ValueError` log a warning
  through `self._logger` (value as `repr`, truncated to 64 chars) and set `False`.
- Keep `del self._conditions['refresh']` running in every case.

**NOT in scope**: `externalProvider.refresh()` (stays `True`); the parser (TASK-746);
tests (TASK-747); `uap.py`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | import `to_flag`; use it for `refresh` at lines 83-85 |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..parsers.abstract import AbstractParser     # verified: querysource/providers/abstract.py:20
from ..types import to_flag                       # created by TASK-744 (querysource/types/__init__.py:2)
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):                                        # line 23
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '',
                 connection: Callable = None,
                 definition: Union[QueryModel, dict] = None,
                 conditions: dict = None, request: web.Request = None,
                 **kwargs):                                     # line 38
        self._logger = logging.getLogger(f'QS.{self.__name__}') # line 50
        self._refresh: bool = False                             # line 71
        if conditions:
            # making a copy of conditions:
            self._conditions = copy.deepcopy(conditions)        # line 82
            if 'refresh' in self._conditions:                   # line 83
                self._refresh = bool(self._conditions['refresh'])   # line 84
                del self._conditions['refresh']                 # line 85
    def refresh(self):                                          # line 257
        return self._refresh                                    # line 258
```

### Does NOT Exist
- ~~`self.logger`~~ on `BaseProvider` — the attribute is `self._logger` (line 50).
- ~~Changes needed in `external.py`~~ — `externalProvider.refresh()` overrides and returns `True`; leave it.
- ~~`from querysource.types.validators import to_flag`~~ — import via the package: `from ..types import to_flag`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/providers/abstract.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/providers/abstract.py#BaseProvider.__init__",
    "sym:querysource/providers/abstract.py#BaseProvider.refresh"
  ]
}
```

---

## Delegation Contract

```json
{
  "schema_version": 1,
  "task_id": "TASK-745",
  "spec_path": "sdd/specs/falsy-refresh.spec.md",
  "design_complete": true,
  "targets": [
    {
      "path": "querysource/providers/abstract.py",
      "action": "modify",
      "expected_sha256": "041505acfc47c59d029d17b0a7b45a3a6252832fd64e00ec27564ce84e9ca01a",
      "planned_changes": "Import to_flag from ..types after the AbstractParser import; replace bool() coercion of the refresh condition with to_flag plus a ValueError fallback that logs a warning and sets False.",
      "blocks": ["impl-provider-import", "impl-provider-refresh"]
    }
  ],
  "references": [
    {
      "path": "querysource/providers/abstract.py",
      "sha256": "041505acfc47c59d029d17b0a7b45a3a6252832fd64e00ec27564ce84e9ca01a",
      "start_line": 80,
      "end_line": 87,
      "purpose": "the refresh block being replaced"
    }
  ],
  "implementation_blocks": ["impl-provider-import", "impl-provider-refresh"],
  "acceptance_criteria": [
    "BaseProvider with conditions {'refresh': 'false'} returns refresh() is False",
    "unrecognized refresh values log a warning and yield False",
    "'refresh' is removed from _conditions in all cases"
  ],
  "validation_commands": [["pytest", "tests/unit/test_provider_describe_columns.py", "-q"]]
}
```

```python id=impl-provider-import
# Apply to querysource/providers/abstract.py: insert below line 20
# `from ..parsers.abstract import AbstractParser`
from ..types import to_flag
```

```python id=impl-provider-refresh
# Apply to querysource/providers/abstract.py: replace lines 83-85
            if 'refresh' in self._conditions:
                raw_refresh = self._conditions['refresh']
                try:
                    self._refresh = to_flag(raw_refresh)
                except ValueError:
                    self._logger.warning(
                        "Unrecognized 'refresh' condition value %s; treating as False",
                        repr(raw_refresh)[:64]
                    )
                    self._refresh = False
                del self._conditions['refresh']
```

---

## Implementation Notes

### Key Constraints
- Use `self._logger` (not `self.logger`).
- Lazy `%s` logging args; never log the unbounded raw value — `repr(...)[:64]`.
- Keep the edit to lines 83-85 plus one import line; nothing else in the file changes.

---

## Implementation Blueprint

### Steps (in order)
1. Add `from ..types import to_flag` below `from ..parsers.abstract import AbstractParser` — *why*: `parsers.abstract` already imports `..types`, so the import order has no new cycle risk.
2. Replace lines 83-85 with the block below — *why*: `to_flag` fixes the `"false"`-is-truthy bug; the `except` keeps a malformed value from ever breaking provider construction (spec G5).
3. Run the validation command — *why*: confirms the module still imports and the provider unit suite passes.

### `querysource/providers/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^from ..parsers.abstract import AbstractParser' querysource/providers/abstract.py)
# AFTER — insert below `from ..parsers.abstract import AbstractParser` (verified: querysource/providers/abstract.py:20)
from ..types import to_flag

# occurrences: 1 (verified: grep -c "self._refresh = bool(self._conditions\['refresh'\])" querysource/providers/abstract.py)
# REPLACE lines 83-85 (anchor: `self._refresh = bool(self._conditions['refresh'])`, verified: querysource/providers/abstract.py:84)
            if 'refresh' in self._conditions:
                raw_refresh = self._conditions['refresh']
                try:
                    self._refresh = to_flag(raw_refresh)
                except ValueError:
                    self._logger.warning(
                        "Unrecognized 'refresh' condition value %s; treating as False",
                        repr(raw_refresh)[:64]
                    )
                    self._refresh = False
                del self._conditions['refresh']
```
**Why**: fully decided by spec §3 Module 2; there are no judgement calls left, so this task carries a Delegation Contract.

### FILL IN checklist
- [ ] none — the blueprint is complete

---

## Acceptance Criteria

- [ ] `refresh="false"` → `refresh() is False`; `refresh=""` → `True`; `refresh="maybe"` → `False` + WARNING.
- [ ] `'refresh'` removed from `_conditions` in all cases.
- [ ] `querysource/providers/external.py` unchanged.
- [ ] `ruff check querysource/providers/abstract.py` introduces no new findings.

---

## Validation Commands

- `pytest tests/unit/test_provider_describe_columns.py -q`

---

## Test Specification

Dedicated provider tests are written in TASK-747 (`tests/test_flag_conditions.py`:
`test_provider_refresh_false_string`, `test_provider_refresh_values`,
`test_provider_refresh_unrecognized_warns`, `test_provider_no_refresh_default`).

---

## Agent Instructions

1. Read the spec (§3 Module 2, §6).
2. Confirm TASK-744 is done (`from querysource.types import to_flag` works).
3. Re-check the `expected_sha256` and anchors; update the index → `"in-progress"`.
4. Apply the blueprint; run the validation command.
5. Move this file to `sdd/tasks/completed/`, set the index to `"done"`, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
