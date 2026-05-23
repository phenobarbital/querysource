# TASK-684: Implement FilterCols Transformation

**Feature**: FEAT-098 — MultiQS New Transformations
**Spec**: `sdd/specs/multiqs-new-transformations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

FilterCols is one of three new column-selection transformations for MultiQS
pipelines (spec §3 Module 3). Unlike PluckCols/DropCols which select by name,
FilterCols removes columns based on data quality expressions — e.g. drop all
columns where every value is null.

---

## Scope

- Implement `FilterCols` class in a new module file.
- Support three predefined expressions (fixed set, no extras):
  - `"all_null"` — drop columns where every value is NaN/None
  - `"all_empty"` — drop columns where every value is NaN/None/empty-string
  - `"constant"` — drop columns where all non-null values are identical (`nunique(dropna=True) <= 1`)
- Raise `DriverError` for unknown expression values.
- Handle dict-of-DataFrames input (apply per-DF, return new dict).
- Follow the `Map.py` kwargs.pop pattern in `__init__` for introspection support.
- Follow the structured docstring format (`Usage:`, `Attributes:`, `Example:`).

**NOT in scope**:
- PluckCols or DropCols (TASK-682, TASK-683)
- Unit tests (TASK-685)
- Additional expressions beyond `all_null`, `all_empty`, `constant`
- Any changes to MultiQS dispatch or registry code

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/transformations/FilterCols.py` | CREATE | FilterCols transformation implementation |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from typing import Union                                                          # stdlib
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

### Pandas Operations Reference
```python
# all_null: drop columns where every value is NaN
df.dropna(axis=1, how="all")

# all_empty: drop columns where every value is NaN/None/empty-string
# Replace empty strings with NaN first, then dropna
df.replace("", pd.NA).dropna(axis=1, how="all")

# constant: drop columns where nunique <= 1
cols_to_drop = [c for c in df.columns if df[c].nunique(dropna=True) <= 1]
df.drop(columns=cols_to_drop)
```

### Does NOT Exist
- ~~`AbstractTransform.expression`~~ — not a built-in attribute; must be set via kwargs
- ~~`AbstractTransform.filter_expression()`~~ — no such method
- ~~`querysource.queries.multi.transformations.ColumnFilter`~~ — does not exist
- ~~`querysource.types.dt.filters.filter_columns()`~~ — no such function in filters.py

---

## Implementation Notes

### Pattern to Follow
```python
SUPPORTED_EXPRESSIONS = {"all_null", "all_empty", "constant"}

class FilterCols(AbstractTransform):
    """Drop columns matching a predefined data-quality expression.

    Usage: Use in a MultiQuery ``Transform`` step to remove columns
    based on their data content — e.g. columns that are entirely null,
    entirely empty, or contain a single constant value.

    Attributes:
        expression: Predefined expression name. Required.
            Supported values: ``"all_null"``, ``"all_empty"``, ``"constant"``.

    Example:
        {"Transform": [{"FilterCols": {"expression": "all_null"}}]}
        {"Transform": [{"FilterCols": {"expression": "constant"}}]}
    """

    def __init__(self, data, **kwargs):
        self.expression: str = kwargs.pop('expression', None)
        super().__init__(data, **kwargs)
        if not self.expression:
            raise DriverError("FilterCols: 'expression' is required.")
        if self.expression not in SUPPORTED_EXPRESSIONS:
            raise DriverError(
                f"FilterCols: Unknown expression '{self.expression}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXPRESSIONS))}"
            )

    def _apply_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the expression filter to a single DataFrame."""
        ...

    async def run(self):
        await self.start()
        # Handle single DF vs dict-of-DFs
        ...
```

### Key Constraints
- Pop `expression` before `super().__init__()` so introspection regex works.
- `_apply_filter()` is synchronous.
- If filtering removes all columns, raise `DataNotFound`.
- The set of supported expressions is fixed — no plugin mechanism.
- Module file name must be `FilterCols.py` (class name == file stem).

### References in Codebase
- `querysource/queries/multi/transformations/Map.py` — kwargs.pop + docstring pattern
- `querysource/queries/multi/transformations/abstract.py` — base class lifecycle

---

## Acceptance Criteria

- [ ] `FilterCols` with `"all_null"` removes columns where all values are NaN
- [ ] `FilterCols` with `"all_empty"` removes columns where all values are NaN/None/empty
- [ ] `FilterCols` with `"constant"` removes columns with only one unique value
- [ ] `FilterCols` raises `DriverError` for unknown expressions
- [ ] `FilterCols` raises `DriverError` when `expression` is not provided
- [ ] `FilterCols` works on dict-of-DataFrames input (applies per-DF)
- [ ] Empty DataFrame input raises `DataNotFound` (via `AbstractTransform.start()`)
- [ ] `get_transform_module("FilterCols")` returns the class
- [ ] Introspection (`get_attributes()`, `get_schema()`) returns correct metadata
- [ ] No linting errors: `ruff check querysource/queries/multi/transformations/FilterCols.py`

---

## Test Specification

> Tests are in TASK-685. Minimal smoke test here for the agent to validate.

```python
import asyncio
import pandas as pd
from querysource.queries.multi.transformations.FilterCols import FilterCols

df = pd.DataFrame({"name": ["A", "B"], "empty": [None, None], "val": [1, 1]})
obj = FilterCols(data=df, expression="all_null")
result = asyncio.get_event_loop().run_until_complete(obj.run())
assert "empty" not in result.columns
assert "name" in result.columns
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
6. **Move this file** to `sdd/tasks/completed/TASK-684-filtercols-transform.md`
7. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
