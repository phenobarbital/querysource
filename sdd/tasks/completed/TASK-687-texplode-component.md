# TASK-687: Implement tExplode Transformation Component

**Feature**: FEAT-099 — MultiQS New Component — tExplode
**Spec**: `sdd/specs/multiqs-new-component-explode.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task creates the `tExplode` transformation component for MultiQS
pipelines. `tExplode` converts a column of lists or dictionaries into multiple
rows — a common need when ingesting nested JSON/API data. It implements
Spec §2 (Architectural Design) and §3 Module 1.

The flowtask framework's `tExplode` (`flowtask/flowtask/components/tExplode.py`)
is the reference implementation, adapted to QuerySource's `AbstractTransform`
lifecycle.

---

## Scope

- Implement `tExplode` class extending `AbstractTransform` in a new file.
- Pop five kwargs in `__init__`: `column` (required), `drop_original` (bool,
  default `False`), `explode_dataset` (bool, default `True`),
  `advanced_mode` (bool, default `False`), `propagate_columns` (list,
  default `[]`).
- Raise `DriverError` if `column` is not provided.
- Implement `run()` with two execution branches:
  - **Standard mode** (default): `DataFrame.explode(column)` + optional
    `json_normalize` when `explode_dataset=True` + optional
    `drop(column)` when `drop_original=True`.
  - **Advanced mode** (`advanced_mode=True`): track parent indices, explode
    only non-empty lists, `json_normalize` for dict expansion, propagate
    specified parent columns to child rows, concat parent + child
    DataFrames.
- Handle `dict`-of-DataFrames input: iterate over each value, apply the
  transformation, return a dict with the same keys.
- Raise `DataNotFound` when the result DataFrame is empty.
- Use `self.logger` for diagnostics (no `print()` statements).

**NOT in scope**:
- Tests (TASK-688).
- Delimiter-based string splitting (existing utility handles that).
- Changes to `ComponentRegistry`, `get_transform_module`, or any other existing file.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/transformations/tExplode.py` | CREATE | tExplode component implementation |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase.
> The implementing agent MUST use these exact imports, class names, and method signatures.
> **DO NOT** invent, guess, or assume any import, attribute, or method not listed here.
> If you need something not listed, VERIFY it exists first with `grep` or `read`.

### Verified Imports

```python
from typing import Union                               # stdlib
import pandas as pd                                     # stdlib
from pandas import json_normalize                       # pandas top-level

from ....exceptions import (                            # verified: querysource/exceptions.py:6,48,58
    DataNotFound,
    DriverError,
    QueryException
)
from .abstract import AbstractTransform                 # verified: querysource/queries/multi/transformations/abstract.py:16
```

### Existing Signatures to Use

```python
# querysource/queries/multi/abstract.py:20
class AbstractMulti(SchemaIntrospectable, ABC):
    _category: str = "Components"                       # line 31
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 33
        self.data = data                                # line 40
        for k, v in kwargs.items():                     # line 41
            setattr(self, k, v)                         # line 42
    async def __aenter__(self):                         # line 48
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 52
    async def start(self):                              # line 66
    async def run(self):                                # line 73 (abstract)
    async def close(self):                              # line 80

# querysource/queries/multi/transformations/abstract.py:16
class AbstractTransform(AbstractMulti):
    _category = "Transformations"                       # line 25
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 27
        self._backend = 'pandas'                        # line 28
        self.logger = navconfig_logging.getLogger(...)  # line 29
        super().__init__(data, **kwargs)                 # line 30
    async def start(self):                              # line 39 — validates dict/DataFrame, raises DriverError/DataNotFound
    async def run(self):                                # line 59 (abstract — tExplode implements this)
```

### Auto-Discovery Contract

```python
# querysource/queries/multi/__init__.py:37
def get_transform_module(clsname: str):
    # Imports .transformations.<clsname> and returns getattr(module, clsname)
    # CONSTRAINT: filename stem MUST equal class name → tExplode.py exports tExplode

# querysource/queries/multi/registry.py:119-131
# ComponentRegistry.discover_all() globs transformations/*.py, skips _* and abstract.py
# No manual registration needed — file existence is sufficient.
```

### Does NOT Exist

- ~~`AbstractTransform.run_on_dict()`~~ — no such method; dict handling must be in `run()`
- ~~`AbstractTransform._run()`~~ — only on `tPandas`, not on `AbstractTransform`
- ~~`AbstractMulti.validate()`~~ — no such method; validation is in `start()`
- ~~`tPandas.explode()`~~ — no such method on any base class
- ~~`querysource.utils.explode`~~ — does not exist; utility is at `querysource.types.dt.transforms.explode`
- ~~`self.previous` / `self.input`~~ — flowtask patterns, NOT available in QuerySource
- ~~`self._variables` / `self.add_metric`~~ — flowtask patterns, NOT available in QuerySource
- ~~`self._result`~~ — flowtask pattern; return DataFrame directly from `run()`

---

## Implementation Notes

### Pattern to Follow — Init (from Map.py:48-55)

```python
class Map(AbstractTransform):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.replace_columns: bool = kwargs.pop('replace_columns', False)
        # ... pop kwargs BEFORE super().__init__()
        super(Map, self).__init__(data, **kwargs)
        if not hasattr(self, 'fields'):
            raise DriverError("Map Transform: Missing Fields for transformation.")
```

### Pattern to Follow — Run (from Map.py:61-107)

```python
    async def run(self):
        await self.start()      # <— always call start() first
        # ... transform self.data ...
        return self.data
```

### Pattern to Follow — tOrder Init (from tOrder.py:39-53)

```python
class tOrder(tPandas):
    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        self._column = kwargs.pop("columns", None)
        # ... validate required attrs ...
        if not self._column:
            raise DriverError("tOrder requires a column for ordering => **columns**")
        super(tOrder, self).__init__(data, **kwargs)
```

### Dict-of-DataFrames Handling

```python
# In run(), check for dict input and apply to each value:
async def run(self):
    await self.start()
    if isinstance(self.data, dict):
        result = {}
        for key, df in self.data.items():
            result[key] = self._explode_dataframe(df)
        return result
    return self._explode_dataframe(self.data)
```

### Key Constraints

- File name MUST be `tExplode.py` (class name = file stem for auto-discovery)
- Pop all kwargs BEFORE `super().__init__()` to prevent them leaking into `setattr`
- Call `await self.start()` at the beginning of `run()` — it validates input types
- Use `self.logger.debug()` / `self.logger.warning()` — never `print()`
- Raise `DataNotFound` when result DataFrame is empty (not `QueryException`)
- Raise `DriverError` for missing/invalid configuration (e.g. missing `column`)

### Reference: flowtask tExplode Execution Logic

**Standard mode** (`advanced_mode=False`):
1. `exploded_df = self.data.explode(self.column).reset_index(drop=True)`
2. If `explode_dataset`: `data_df = json_normalize(exploded_df[self.column])`
   then `df = pd.concat([exploded_df, data_df], axis=1)`
3. If `drop_original`: `df.drop(self.column, axis=1)`
4. Return df

**Advanced mode** (`advanced_mode=True`, `explode_dataset=True`):
1. Add `_parent_idx` helper column
2. Filter rows with non-empty lists
3. Explode filtered rows, normalize dicts via `json_normalize`
4. Build union of parent + JSON columns
5. For `propagate_columns`: copy parent values to child rows
6. Remove `_parent_idx` helper
7. Concat original (parent) + exploded (child) DataFrames
8. If `drop_original`: drop the source column
9. Return df

---

## Acceptance Criteria

- [ ] `tExplode` class exists at `querysource/queries/multi/transformations/tExplode.py`
- [ ] Extends `AbstractTransform` (not `tPandas`)
- [ ] `__init__` pops `column`, `drop_original`, `explode_dataset`, `advanced_mode`, `propagate_columns`
- [ ] Raises `DriverError` when `column` is missing
- [ ] Standard mode: `DataFrame.explode()` + optional `json_normalize` + optional drop
- [ ] Advanced mode: parent tracking + dict expansion + column propagation
- [ ] Dict-of-DataFrames: applies transformation to each value
- [ ] Raises `DataNotFound` on empty result
- [ ] Auto-discovered by `get_transform_module("tExplode")`
- [ ] No `print()` statements — uses `self.logger`
- [ ] No linting errors: `ruff check querysource/queries/multi/transformations/tExplode.py`
- [ ] Import works: `from querysource.queries.multi.transformations.tExplode import tExplode`

---

## Test Specification

Tests are in TASK-688. This task focuses on implementation only.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiqs-new-component-explode.spec.md`
2. **Read the flowtask reference** at `../flowtask/flowtask/components/tExplode.py` for the execution logic
3. **Read existing transforms** for patterns: `Map.py`, `tOrder.py`
4. **Verify the Codebase Contract** — confirm every import still exists
5. **Implement** `tExplode.py` following the scope and patterns above
6. **Run lint**: `ruff check querysource/queries/multi/transformations/tExplode.py`
7. **Verify import**: `python -c "from querysource.queries.multi.transformations.tExplode import tExplode; print(tExplode)"`
8. **Move this file** to `sdd/tasks/completed/TASK-687-texplode-component.md`
9. **Update index** → `"done"`

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-05-23
**Notes**: Implemented tExplode.py with full standard and advanced mode support. All kwargs popped before super().__init__(). Uses self.logger throughout (no print statements). Raises DriverError for missing/invalid config, DataNotFound for empty results. Handles dict-of-DataFrames. Passes ruff lint. Import verified.

**Deviations from spec**: none
