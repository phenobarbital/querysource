---
type: feature
base_branch: dev
---

# Feature Specification: MultiQS New Component — tExplode

**Feature ID**: FEAT-099
**Date**: 2026-05-23
**Author**: Jesus Lara
**Status**: draft
**Target version**: 4.x

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQS pipelines frequently ingest data where a single column contains nested
structures — JSON arrays, Python lists, or dictionaries. Currently there is no
declarative transformation step that can "explode" these nested values into
multiple rows. Users must write ad-hoc post-processing or rely on the low-level
utility function `querysource.types.dt.transforms.explode()`, which only
handles delimiter-based string splitting and cannot expand dictionaries into
columns.

The flowtask framework already ships a `tExplode` component
(`flowtask/flowtask/components/tExplode.py`) with advanced features like
dictionary expansion and column propagation. QuerySource needs an equivalent
transformation that follows the `AbstractTransform` lifecycle, is
auto-discovered by `ComponentRegistry`, and supports both single-DataFrame and
dict-of-DataFrames input.

### Goals

- Provide **tExplode**: a transformation that converts a column of lists or
  dictionaries into multiple rows.
- Support **drop_original**: optionally remove the source column after exploding.
- Support **explode_dataset**: expand nested dictionaries into separate columns
  via `pandas.json_normalize`.
- Support **advanced_mode**: enhanced processing with parent-index tracking,
  empty-list preservation, and column propagation.
- Support **propagate_columns**: propagate specified parent columns to child
  rows (only in `advanced_mode`).
- Follow the existing `AbstractTransform` lifecycle and be auto-discovered by
  `get_transform_module()` / `ComponentRegistry`.
- Support both single-DataFrame and dict-of-DataFrames input.
- Expose correct introspection via `SchemaIntrospectable`.

### Non-Goals (explicitly out of scope)

- Delimiter-based string splitting — the existing utility function
  `querysource.types.dt.transforms.explode()` handles that use case.
- Polars or Modin backend support — pandas only for now.
- Recursive multi-level explosion (exploding a column, then exploding a
  resulting column in the same step).
- Re-implementing the flowtask `FlowComponent` lifecycle — tExplode will use
  QuerySource's `AbstractTransform` base class exclusively.

---

## 2. Architectural Design

### Overview

`tExplode` extends `AbstractTransform` directly (not `tPandas`) to retain full
control over dict-of-DataFrames handling and the two-mode execution logic
(standard vs. advanced). The component:

1. Validates input via `await self.start()` (inherited from `AbstractTransform`).
2. In standard mode: explodes the target column using `DataFrame.explode()`,
   optionally normalizes dictionaries via `pandas.json_normalize`, and
   optionally drops the original column.
3. In advanced mode: tracks parent indices, explodes only non-empty lists,
   normalizes dictionaries, propagates specified parent columns to child rows,
   and concatenates parent + child DataFrames.
4. Returns the transformed DataFrame (or dict of DataFrames).

### Component Diagram

```
MultiQS Pipeline
  │
  ├─ Source(s) → result (DataFrame or dict)
  │
  ├─ Transform chain:
  │     ├─ ... (prior transforms)
  │     ├─ tExplode(data=result, column=..., ...)
  │     │     ├─ start() → validate input
  │     │     ├─ run()   → explode column → normalize dicts → propagate
  │     │     └─ close() → cleanup
  │     └─ ... (subsequent transforms)
  │
  └─ Output / Destination
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractTransform` | extends | Base class providing lifecycle + validation |
| `get_transform_module()` | auto-discovered | File name = class name convention |
| `ComponentRegistry.discover_all()` | auto-registered | Glob scan of `transformations/*.py` |
| `SchemaIntrospectable` | inherits (via `AbstractMulti`) | Introspects `kwargs.pop()` for schema generation |
| `pandas.json_normalize` | uses | For expanding dict columns into separate columns |

### Data Models

```python
# No new Pydantic models needed — tExplode operates on pd.DataFrame directly.
# Configuration is passed via kwargs following the established pattern.
```

### New Public Interfaces

```python
class tExplode(AbstractTransform):
    """Explode a column of lists or dicts into multiple rows."""

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # column: str — name of the column to explode (required)
        # drop_original: bool — drop source column after exploding (default: False)
        # explode_dataset: bool — expand dicts into columns (default: True)
        # advanced_mode: bool — enable parent tracking + propagation (default: False)
        # propagate_columns: list — columns to propagate from parent (default: [])
        ...

    async def run(self) -> Union[dict, pd.DataFrame]:
        ...
```

---

## 3. Module Breakdown

### Module 1: tExplode Component

- **Path**: `querysource/queries/multi/transformations/tExplode.py`
- **Responsibility**: Implements the `tExplode` transformation — explodes a
  column of lists or dictionaries into multiple rows, with optional dictionary
  expansion and column propagation.
- **Depends on**: `AbstractTransform`, `pandas`, `pandas.json_normalize`,
  `querysource.exceptions.DriverError`, `querysource.exceptions.DataNotFound`

### Module 2: Unit Tests

- **Path**: `tests/test_texplode.py`
- **Responsibility**: Unit and integration tests for all tExplode modes and
  edge cases.
- **Depends on**: Module 1

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_texplode_init_requires_column` | 1 | Raises `DriverError` when `column` kwarg is missing |
| `test_texplode_basic_list_explode` | 1 | Explodes a column of lists into rows (standard mode) |
| `test_texplode_dict_explode_with_normalize` | 1 | Explodes + json_normalize when `explode_dataset=True` |
| `test_texplode_drop_original` | 1 | Source column is removed when `drop_original=True` |
| `test_texplode_no_drop_original` | 1 | Source column is preserved when `drop_original=False` |
| `test_texplode_explode_dataset_false` | 1 | Dicts stay as values when `explode_dataset=False` |
| `test_texplode_advanced_mode_basic` | 1 | Advanced mode tracks parent index, explodes non-empty lists |
| `test_texplode_advanced_propagate_columns` | 1 | Parent columns propagated to child rows in advanced mode |
| `test_texplode_advanced_empty_lists_preserved` | 1 | Rows with empty lists are kept in advanced mode |
| `test_texplode_dict_of_dataframes` | 1 | Handles dict-of-DataFrames input (applies to each value) |
| `test_texplode_empty_dataframe` | 1 | Raises `DataNotFound` on empty input |
| `test_texplode_column_not_found` | 1 | Raises `DriverError` when column doesn't exist in DataFrame |
| `test_texplode_async_context_manager` | 1 | Works correctly via `async with tExplode(...) as t: await t.run()` |

### Integration Tests

| Test | Description |
|---|---|
| `test_texplode_in_transform_chain` | tExplode used in a MultiQS Transform step via YAML/dict config |
| `test_texplode_registry_discovery` | `ComponentRegistry.discover_all()` finds tExplode |
| `test_texplode_introspection_schema` | `SchemaIntrospectable` generates correct JSON schema |

### Test Data / Fixtures

```python
@pytest.fixture
def df_with_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "tags": [["a", "b"], ["c"], ["d", "e", "f"]],
        "name": ["Alice", "Bob", "Carol"]
    })

@pytest.fixture
def df_with_dicts():
    return pd.DataFrame({
        "id": [1, 2],
        "details": [
            {"color": "red", "size": 10},
            {"color": "blue", "size": 20}
        ],
        "name": ["Alice", "Bob"]
    })

@pytest.fixture
def df_with_empty_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "items": [["x", "y"], [], ["z"]],
        "group": ["A", "B", "C"]
    })
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `tExplode` class exists at `querysource/queries/multi/transformations/tExplode.py`
- [ ] Extends `AbstractTransform` and follows the `__init__` / `start()` / `run()` / `close()` lifecycle
- [ ] `column` parameter is required — raises `DriverError` if missing
- [ ] Standard mode: explodes a list column into rows via `DataFrame.explode()`
- [ ] Standard mode with `explode_dataset=True`: expands dict values via `pandas.json_normalize`
- [ ] `drop_original=True` removes the source column from the result
- [ ] Advanced mode (`advanced_mode=True`): tracks parent indices, propagates specified columns
- [ ] `propagate_columns` copies specified parent column values to child rows (advanced mode only)
- [ ] Handles dict-of-DataFrames input (applies transformation to each DataFrame)
- [ ] Raises `DataNotFound` when result is empty
- [ ] Auto-discovered by `get_transform_module("tExplode")` and `ComponentRegistry`
- [ ] Introspection via `SchemaIntrospectable` produces correct schema
- [ ] All unit tests pass (`pytest tests/test_texplode.py -v`)
- [ ] No breaking changes to existing transformations or MultiQS pipeline

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

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

import logging                                          # stdlib
from navconfig.logging import logging as navconfig_logging  # verified: querysource/queries/multi/transformations/abstract.py:5
```

### Existing Class Signatures

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
    def _print_info(self, df: pd.DataFrame) -> None:    # line 32
    async def start(self):                              # line 39 — validates dict/DataFrame types
    async def run(self):                                # line 59 (abstract)

# querysource/queries/multi/transformations/tPandas.py:12
class tPandas(AbstractTransform):
    # NOT used by tExplode — listed for reference only
    def __init__(self, data, **kwargs) -> None:         # line 36
    async def _run(self) -> DataFrame:                  # line 44 (abstract)
    async def run(self):                                # line 53 — calls start() + _run()
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `tExplode` | `AbstractTransform` | extends | `transformations/abstract.py:16` |
| `tExplode` | `get_transform_module("tExplode")` | dynamic import | `multi/__init__.py:37-50` |
| `tExplode` | `ComponentRegistry.discover_all()` | glob scan | `multi/registry.py:119-131` |
| `tExplode` | MultiQS Transform dispatch | `async with obj as o: result = await o.run()` | `multi/__init__.py:340-343` |

### Auto-Discovery Mechanism

```python
# querysource/queries/multi/__init__.py:37-50
def get_transform_module(clsname: str):
    clsobj = import_module(f'.transformations.{clsname}', package=__package__)
    return getattr(clsobj, clsname)
    # CONSTRAINT: file stem MUST equal class name → tExplode.py exports tExplode

# querysource/queries/multi/registry.py:119-131
# ComponentRegistry.discover_all() globs transformations/*.py, skips _* and abstract.py,
# calls get_transform_module(stem) for each file.
```

### Transform Dispatch (MultiQS pipeline)

```python
# querysource/queries/multi/__init__.py:318-356
# For each {"tExplode": {...}} in the Transform list:
#   clobj = get_transform_module("tExplode")     # line 340
#   obj = clobj(data=result, **component)          # line 341
#   async with obj as o:                           # line 342
#       result = await o.run()                     # line 343
```

### Existing Utility (string-split explode — NOT reused)

```python
# querysource/types/dt/transforms.py:258-296
def explode(df, field, columns=None, is_string=True, delimiter=","):
    # Splits string values by delimiter, then calls df.explode()
    # This handles a DIFFERENT use case (string splitting) — tExplode handles
    # list/dict column explosion. Do NOT import or wrap this function.
```

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.queries.multi.transformations.tExplode`~~ — does not exist yet (this spec creates it)
- ~~`AbstractTransform.run_on_dict()`~~ — no such method; dict handling must be implemented in `run()`
- ~~`AbstractTransform._run()`~~ — this is only on `tPandas`, not on `AbstractTransform`
- ~~`AbstractMulti.validate()`~~ — no such method; validation is in `start()`
- ~~`tPandas.explode()`~~ — no such method on any base class
- ~~`querysource.utils.explode`~~ — does not exist; the utility is at `querysource.types.dt.transforms.explode`

### Reference Code: flowtask tExplode

The user specified `flowtask/flowtask/components/tExplode.py` as the reference
implementation. Key patterns to carry forward:

```python
# flowtask/flowtask/components/tExplode.py (verified, read in full)
# - __init__: pops column, drop_original, explode_dataset, advanced_mode, propagate_columns
# - start(): validates self.data is a DataFrame
# - run(): two branches — standard mode vs advanced_mode
#   Standard: DataFrame.explode() + json_normalize + optional drop
#   Advanced: parent index tracking, non-empty list filtering, json_normalize,
#             column propagation, concat parent + child
# - Returns self._result (flowtask pattern — QuerySource returns from run() directly)
```

**Differences from flowtask that tExplode must account for:**
1. Base class is `AbstractTransform`, not `FlowComponent`
2. `start()` is inherited and validates dict-of-DataFrames
3. No `self.previous` / `self.input` — data comes via `__init__(data=...)`
4. No `self._variables` / `self.add_metric` — use `self.logger` for diagnostics
5. No `self._result` — return the DataFrame directly from `run()`
6. Must handle dict-of-DataFrames (iterate and apply to each)

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Init pattern**: Pop transform-specific kwargs before calling `super().__init__()`.
  See `Map.__init__()` (`Map.py:48-55`) and `tOrder.__init__()` (`tOrder.py:39-53`).
- **Run pattern**: Call `await self.start()` first, then operate on `self.data`.
  See `Map.run()` (`Map.py:61-107`).
- **Dict-of-DataFrames**: Check `isinstance(self.data, dict)` in `run()` and
  apply the transformation to each DataFrame value. Return a dict with the
  same keys.
- **Error handling**: Use `DriverError` for config/validation errors,
  `DataNotFound` for empty results, `QueryException` for runtime errors.
- **Logging**: Use `self.logger` (set by `AbstractTransform.__init__`).
- **No print statements**: Use `self.logger.debug()` instead (unlike the
  flowtask version which uses `print()`).

### Known Risks / Gotchas

- **Memory**: `json_normalize` on large DataFrames with deeply nested dicts
  can be memory-intensive. Log a warning when the exploded result exceeds
  10× the input row count.
- **Mixed column types**: If the target column contains a mix of lists and
  scalars, `DataFrame.explode()` handles this gracefully (scalars remain
  as-is). Document this behavior.
- **Advanced mode concat**: The flowtask version concatenates parent + child
  rows, which duplicates the parent row data. This is intentional — the
  parent row appears once (with the original list), and child rows appear
  with the expanded values. Ensure the spec and tests reflect this.
- **Empty list handling in advanced mode**: Rows with empty lists are
  preserved in the parent DataFrame but NOT exploded. This prevents data
  loss.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=1.3.0` | `DataFrame.explode()`, `json_normalize` |

No new external dependencies required — pandas is already a core dependency.

---

## 8. Open Questions

- [ ] Should `advanced_mode` concat produce parent + child rows (flowtask behavior), or replace parent rows with child rows? — *Owner: Jesus Lara*
- [ ] Should tExplode support a `delimiter` parameter for string-split explode in addition to list/dict explode, or leave that to the existing utility function? — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` — all tasks run sequentially in one worktree.
- **Parallelism**: Module 1 (implementation) and Module 2 (tests) can be
  developed together since tExplode has no cross-module dependencies.
- **Cross-feature dependencies**: None. This feature is self-contained.
  `FEAT-098` (PluckCols/DropCols/FilterCols) is independent and can be
  developed in parallel.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-23 | Jesus Lara / Claude | Initial draft — tExplode transformation component for MultiQS pipelines, full feature set with advanced_mode and propagate_columns |
