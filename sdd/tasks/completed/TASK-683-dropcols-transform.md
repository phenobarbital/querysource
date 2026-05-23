# TASK-683: Implement DropCols Transformation

**Feature**: FEAT-098 — MultiQS New Transformations
**Spec**: `sdd/specs/multiqs-new-transformations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

DropCols is one of three new column-selection transformations for MultiQS
pipelines (spec §3 Module 2). It drops the columns that match any of the
provided selectors, keeping everything else. This is the "blacklist" counterpart
to PluckCols (TASK-682).

---

## Scope

- Implement `DropCols` class in a new module file.
- Support five column-matching modes: `columns` (exact), `pattern` (glob/fnmatch),
  `regex`, `startswith`, `endswith`. All optional, at least one required.
- Implement a private `_resolve_columns(df)` method that unions matches from all
  provided selectors against a DataFrame's actual column names.
- Handle dict-of-DataFrames input (apply per-DF, return new dict).
- Silently ignore exact column names in `columns` that are not found in the DF
  (use `errors="ignore"` pattern from `filters.py:248`).
- Raise `DriverError` when no matching mode is provided at all.
- Raise `DriverError` on invalid regex.
- Follow the `Map.py` kwargs.pop pattern in `__init__` for introspection support.
- Follow the structured docstring format (`Usage:`, `Attributes:`, `Example:`).

**NOT in scope**:
- PluckCols or FilterCols (TASK-682, TASK-684)
- Unit tests (TASK-685)
- Any changes to MultiQS dispatch or registry code

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/transformations/DropCols.py` | CREATE | DropCols transformation implementation |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from typing import Union                                                          # stdlib
import fnmatch                                                                     # stdlib — glob pattern matching
import re                                                                          # stdlib — regex pattern matching
import pandas as pd                                                                # verified: used throughout codebase
from ....exceptions import DriverError, DataNotFound                               # verified: querysource/exceptions.py
from .abstract import AbstractTransform                                            # verified: querysource/queries/multi/transformations/abstract.py:16
```

### Existing Signatures to Use
```python
# querysource/queries/multi/abstract.py:20
class AbstractMulti(SchemaIntrospectable, ABC):
    _category: str = "Components"                           # line 31
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 33
        self.data = data                                    # line 40
        for k, v in kwargs.items():                         # line 41
            setattr(self, k, v)                             # line 42
    async def start(self): ...                              # line 66
    async def run(self): ...                                # line 73 — abstract

# querysource/queries/multi/transformations/abstract.py:16
class AbstractTransform(AbstractMulti):
    _category = "Transformations"                           # line 25
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 27
        self._backend = 'pandas'                            # line 28
        self.logger = navconfig_logging.getLogger(...)      # line 29
        super().__init__(data, **kwargs)                    # line 30
    async def start(self):                                  # line 39 — validates dict/DF type + empty
    async def run(self): ...                                # line 59 — abstract
```

### Reference Pattern — Map.py __init__
```python
# querysource/queries/multi/transformations/Map.py:48
class Map(AbstractTransform):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.replace_columns: bool = kwargs.pop('replace_columns', False)  # line 49
        super(Map, self).__init__(data, **kwargs)                          # line 55
        if not hasattr(self, 'fields'):                                    # line 56
            raise DriverError("Map Transform: Missing Fields ...")         # line 57-58
```

### Reference — drop_columns utility
```python
# querysource/types/dt/filters.py:237
def drop_columns(df: pd.DataFrame, columns=None, endswith=None, startswith=None):
    if columns and isinstance(columns, list):
        df.drop(axis=1, columns=columns, inplace=True, errors="ignore")  # line 248
    elif endswith and isinstance(endswith, list):
        cols_to_drop = [col for col in df.columns if col.endswith(tuple(endswith))]
    elif startswith and isinstance(startswith, list):
        cols_to_drop = [col for col in df.columns if col.startswith(tuple(startswith))]
```

### Does NOT Exist
- ~~`AbstractTransform.columns`~~ — not a built-in attribute; must be set via kwargs
- ~~`AbstractTransform._resolve_columns()`~~ — does not exist on the base; define it in DropCols
- ~~`AbstractTransform.validate_columns()`~~ — no such method
- ~~`querysource.queries.multi.transformations._column_mixin`~~ — no shared mixin module

---

## Implementation Notes

### Pattern to Follow
```python
class DropCols(AbstractTransform):
    """Drop the columns matching any of the specified selectors.

    Usage: Use in a MultiQuery ``Transform`` step to remove unwanted columns
    from a DataFrame via exact names, glob patterns, regex, or
    prefix/suffix matching.

    Attributes:
        columns: List of exact column names to drop. Optional.
        pattern: Glob/fnmatch pattern (e.g. ``"debug_*"``). Optional.
        regex: Regular expression pattern (e.g. ``"^tmp_"``). Optional.
        startswith: List of prefix strings. Optional.
        endswith: List of suffix strings. Optional.

    Example:
        {"Transform": [{"DropCols": {"columns": ["internal_id", "debug_flag"]}}]}
        {"Transform": [{"DropCols": {"startswith": ["debug_"], "endswith": ["_tmp"]}}]}
        {"Transform": [{"DropCols": {"regex": "^tmp_.*$"}}]}
    """

    def __init__(self, data, **kwargs):
        # Pop all five selectors BEFORE super().__init__
        self.columns = kwargs.pop('columns', None)
        self.pattern = kwargs.pop('pattern', None)
        self.regex = kwargs.pop('regex', None)
        self.startswith = kwargs.pop('startswith', None)
        self.endswith = kwargs.pop('endswith', None)
        super().__init__(data, **kwargs)
        if not any([self.columns, self.pattern, self.regex, self.startswith, self.endswith]):
            raise DriverError("DropCols: At least one column selector is required ...")

    def _resolve_columns(self, df: pd.DataFrame) -> list[str]:
        """Return the union of columns matched by all provided selectors."""
        ...

    async def run(self):
        await self.start()
        # Handle single DF vs dict-of-DFs
        # Use df.drop(columns=matched, errors="ignore") for exact names
        ...
```

### Key Constraints
- Pop all kwargs before `super().__init__()` so introspection regex works.
- `_resolve_columns()` is synchronous.
- Exact column names that are missing are silently ignored (`errors="ignore"`).
- Pattern/regex/startswith/endswith silently skip non-matches.
- If dropping all columns leaves zero columns, raise `DataNotFound`.
- Module file name must be `DropCols.py` (class name == file stem).

### References in Codebase
- `querysource/queries/multi/transformations/Map.py` — kwargs.pop + docstring pattern
- `querysource/queries/multi/transformations/abstract.py` — base class lifecycle
- `querysource/types/dt/filters.py:237` — existing `drop_columns()` utility for reference

---

## Acceptance Criteria

- [ ] `DropCols` removes exact-named columns on single DataFrame input
- [ ] `DropCols` removes columns matching glob pattern (`pattern` attribute)
- [ ] `DropCols` removes columns matching regex (`regex` attribute)
- [ ] `DropCols` removes columns matching `startswith` prefixes
- [ ] `DropCols` removes columns matching `endswith` suffixes
- [ ] `DropCols` unions matches when multiple modes are combined
- [ ] `DropCols` works on dict-of-DataFrames input (applies per-DF)
- [ ] `DropCols` silently ignores exact column names not present in the DataFrame
- [ ] `DropCols` raises `DriverError` when no matching mode is provided
- [ ] `DropCols` raises `DriverError` on invalid regex pattern
- [ ] `get_transform_module("DropCols")` returns the class
- [ ] Introspection (`get_attributes()`, `get_schema()`) returns correct metadata
- [ ] No linting errors: `ruff check querysource/queries/multi/transformations/DropCols.py`

---

## Test Specification

> Tests are in TASK-685. Minimal smoke test here for the agent to validate.

```python
import asyncio
import pandas as pd
from querysource.queries.multi.transformations.DropCols import DropCols

df = pd.DataFrame({"name": ["A"], "email": ["a@x"], "debug_flag": [True]})
obj = DropCols(data=df, columns=["debug_flag"])
result = asyncio.get_event_loop().run_until_complete(obj.run())
assert "debug_flag" not in result.columns
assert list(result.columns) == ["name", "email"]
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiqs-new-transformations.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists
   - Confirm `AbstractTransform` signature at `transformations/abstract.py:16`
   - If anything has changed, update the contract FIRST
   - **NEVER** reference an import or method not in the contract without verifying it
4. **Implement** following the scope, codebase contract, and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-683-dropcols-transform.md`
7. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
