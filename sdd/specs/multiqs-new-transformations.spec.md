---
type: feature
base_branch: dev
---

# Feature Specification: MultiQS New Transformations — PluckCols, DropCols, FilterCols

**Feature ID**: FEAT-098
**Date**: 2026-05-23
**Author**: Jesus Lara
**Status**: draft
**Target version**: 4.x

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQS pipelines currently lack dedicated column-selection transformations.
Users who need to reduce a DataFrame to a specific set of columns must either
abuse the `Map` transform (which is designed for renaming/computing, not
selecting) or write a custom post-processing step. There is no way at all to
declaratively drop columns by name, nor to filter columns based on data
quality expressions (e.g. "remove columns where all values are null").

### Goals

- Provide **PluckCols**: a transformation that keeps only the listed columns.
- Provide **DropCols**: a transformation that removes the listed columns, keeping everything else.
- Provide **FilterCols**: a transformation that removes columns matching a simple expression (e.g. all-null, all-empty, constant-value).
- All three must follow the existing `AbstractTransform` lifecycle and be
  auto-discovered by `get_transform_module()` / `ComponentRegistry`.
- All three must support both single-DataFrame and dict-of-DataFrames input.
- All three must expose correct introspection via `SchemaIntrospectable`.

### Non-Goals (explicitly out of scope)

- Row-level filtering — that is handled by the existing `Filter` operator.
- Column renaming or computation — that is handled by `Map`.
- Polars or Modin backend support — pandas only for now.
- Complex expression parsing (SQL WHERE-style) for FilterCols — only simple
  predefined predicates are in scope.

---

## 2. Architectural Design

### Overview

Three new transformation modules are added under
`querysource/queries/multi/transformations/`. Each inherits from
`AbstractTransform`, implements `async run()`, and follows the established
pattern of receiving `data` (single DataFrame or dict of DataFrames) plus
`**kwargs` for configuration.

They are discovered automatically by `get_transform_module()` via
`importlib.import_module()` on the module name — no changes to the registry or
MultiQS dispatch logic are required.

**Pipeline usage:**
```json
{
  "Transform": [
    {"PluckCols": {"columns": ["name", "email", "phone"]}},
    {"DropCols": {"columns": ["internal_id", "debug_flag"]}},
    {"FilterCols": {"expression": "all_null"}}
  ]
}
```

### Component Diagram

```
MultiQS.query()
    │
    ├── Step 1: Data Ingestion (ThreadSource/FileSource/ThreadQuery)
    │       ↓
    ├── Step 2: Join/Concat/Merge/Melt operators
    │       ↓
    ├── Step 3: Transform chain ──→ get_transform_module(name)
    │       │
    │       ├── PluckCols(data, columns=[...])    ← NEW
    │       ├── DropCols(data, columns=[...])      ← NEW
    │       ├── FilterCols(data, expression=...)   ← NEW
    │       ├── Map(data, fields={...})
    │       ├── pivot(data, ...)
    │       └── ...other transforms...
    │       ↓
    ├── Step 4: Filter / GroupBy
    │       ↓
    └── Step 5: Output destinations
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractTransform` | extends | All three new classes inherit from it |
| `get_transform_module()` | discovered by | Auto-import via `importlib` on module name |
| `ComponentRegistry.discover_all()` | discovered by | Glob scan of `transformations/*.py` picks them up |
| `SchemaIntrospectable` | inherited via | Introspection works automatically via `AbstractMulti` |
| `MultiQS.query()` lines 318-356 | invoked by | No changes needed — existing dispatch loop handles them |

### Data Models

```python
# PluckCols configuration
columns: list[str]  # required — column names to keep

# DropCols configuration
columns: list[str]  # required — column names to drop

# FilterCols configuration
expression: str  # required — predefined predicate name
# Supported expressions:
#   "all_null"    — drop columns where every value is NaN/None
#   "all_empty"   — drop columns where every value is NaN/None/empty-string
#   "constant"    — drop columns where all non-null values are identical
```

### New Public Interfaces

```python
# querysource/queries/multi/transformations/PluckCols.py
class PluckCols(AbstractTransform):
    """Keep only the specified columns, dropping all others."""
    columns: list[str]
    async def run(self) -> pd.DataFrame: ...

# querysource/queries/multi/transformations/DropCols.py
class DropCols(AbstractTransform):
    """Drop the specified columns, keeping all others."""
    columns: list[str]
    async def run(self) -> pd.DataFrame: ...

# querysource/queries/multi/transformations/FilterCols.py
class FilterCols(AbstractTransform):
    """Drop columns matching a predefined expression."""
    expression: str
    async def run(self) -> pd.DataFrame: ...
```

---

## 3. Module Breakdown

### Module 1: PluckCols

- **Path**: `querysource/queries/multi/transformations/PluckCols.py`
- **Responsibility**: Select (keep) only the columns listed in the `columns`
  attribute. All other columns are dropped. If `columns` contains a name not
  present in the DataFrame, raise `DriverError` with a clear message listing
  the missing column(s).
- **Depends on**: `AbstractTransform`

### Module 2: DropCols

- **Path**: `querysource/queries/multi/transformations/DropCols.py`
- **Responsibility**: Drop the columns listed in the `columns` attribute,
  keeping all others. If a column name is not present in the DataFrame, skip
  it silently (use `errors="ignore"` to match the existing `drop_columns()`
  utility pattern in `querysource/types/dt/filters.py:248`).
- **Depends on**: `AbstractTransform`

### Module 3: FilterCols

- **Path**: `querysource/queries/multi/transformations/FilterCols.py`
- **Responsibility**: Drop columns that match a predefined expression.
  Supported expressions:
  - `"all_null"` — `df.dropna(axis=1, how="all")`
  - `"all_empty"` — drop columns where all values are NaN, None, or empty string
  - `"constant"` — drop columns where `nunique(dropna=True) <= 1`
  If `expression` is not one of the supported values, raise `DriverError`.
- **Depends on**: `AbstractTransform`

### Module 4: Tests

- **Path**: `tests/test_multiqs_column_transforms.py`
- **Responsibility**: Unit tests for PluckCols, DropCols, and FilterCols.
- **Depends on**: Modules 1-3

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_pluck_cols_basic` | PluckCols | Keep 2 of 5 columns, verify only those remain |
| `test_pluck_cols_missing_column` | PluckCols | Request non-existent column → DriverError |
| `test_pluck_cols_dict_input` | PluckCols | Dict of DataFrames — each DF gets plucked |
| `test_drop_cols_basic` | DropCols | Drop 2 of 5 columns, verify rest remain |
| `test_drop_cols_missing_column` | DropCols | Non-existent column silently ignored |
| `test_drop_cols_dict_input` | DropCols | Dict of DataFrames — each DF gets dropped |
| `test_filter_cols_all_null` | FilterCols | Column with all NaN removed |
| `test_filter_cols_all_empty` | FilterCols | Column with NaN + empty strings removed |
| `test_filter_cols_constant` | FilterCols | Column with single unique value removed |
| `test_filter_cols_invalid_expression` | FilterCols | Unknown expression → DriverError |
| `test_filter_cols_dict_input` | FilterCols | Dict of DataFrames support |
| `test_empty_dataframe` | All | Empty DataFrame → DataNotFound |

### Integration Tests

| Test | Description |
|---|---|
| `test_transform_chain_pluck_then_drop` | Chain PluckCols + DropCols in sequence |
| `test_get_transform_module_discovery` | `get_transform_module("PluckCols")` returns the class |

### Test Data / Fixtures

```python
@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie"],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "phone": ["111", "222", "333"],
        "internal_id": [1, 2, 3],
        "debug_flag": [True, True, True],
        "all_null_col": [None, None, None],
        "empty_col": [None, "", None],
        "constant_col": ["X", "X", "X"],
    })

@pytest.fixture
def sample_dict(sample_df):
    return {"df1": sample_df.copy(), "df2": sample_df.copy()}
```

---

## 5. Acceptance Criteria

- [ ] `PluckCols` keeps only listed columns on single DataFrame input
- [ ] `PluckCols` keeps only listed columns on dict-of-DataFrames input
- [ ] `PluckCols` raises `DriverError` when a requested column does not exist
- [ ] `DropCols` removes listed columns on single DataFrame input
- [ ] `DropCols` removes listed columns on dict-of-DataFrames input
- [ ] `DropCols` silently ignores columns not present in the DataFrame
- [ ] `FilterCols` with `"all_null"` removes columns where all values are NaN
- [ ] `FilterCols` with `"all_empty"` removes columns where all values are NaN/None/empty
- [ ] `FilterCols` with `"constant"` removes columns with only one unique value
- [ ] `FilterCols` raises `DriverError` for unknown expressions
- [ ] All three transforms work with dict-of-DataFrames input (apply per-DF)
- [ ] All three transforms raise `DataNotFound` on empty DataFrame input
- [ ] `get_transform_module("PluckCols")` / `"DropCols"` / `"FilterCols"` succeeds
- [ ] `ComponentRegistry.discover_all()` includes all three new transforms
- [ ] Introspection (`get_attributes()`, `get_schema()`) returns correct metadata for each
- [ ] All unit tests pass (`pytest tests/test_multiqs_column_transforms.py -v`)
- [ ] No breaking changes to existing transformations or public API

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
from querysource.queries.multi.transformations.abstract import AbstractTransform  # verified: querysource/queries/multi/transformations/abstract.py:16
from querysource.exceptions import DriverError, DataNotFound, QueryException      # verified: querysource/exceptions.py
import pandas as pd                                                                # verified: used throughout
```

### Existing Class Signatures

```python
# querysource/queries/multi/abstract.py:20
class AbstractMulti(SchemaIntrospectable, ABC):
    _category: str = "Components"                           # line 31
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 33
        self.data = data                                    # line 40
        for k, v in kwargs.items():                         # line 41
            setattr(self, k, v)                             # line 42
    async def __aenter__(self): ...                         # line 48
    async def __aexit__(self, ...): ...                     # line 52
    async def start(self): ...                              # line 66 — overridable
    async def run(self): ...                                # line 73 — abstract
    async def close(self): ...                              # line 80

# querysource/queries/multi/transformations/abstract.py:16
class AbstractTransform(AbstractMulti):
    _category = "Transformations"                           # line 25
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 27
        self._backend = 'pandas'                            # line 28
        self.logger = navconfig_logging.getLogger(...)      # line 29
        super().__init__(data, **kwargs)                    # line 30
    async def start(self):                                  # line 39 — validates input data
    async def run(self): ...                                # line 59 — abstract

# querysource/queries/multi/transformations/tPandas.py:12
class tPandas(AbstractTransform):
    """Alternative abstract base adding _run() lifecycle."""
    def __init__(self, data, **kwargs):                     # line 36
        self.type: str = None                               # line 38
        self.condition: str = ''                             # line 39
        self.pd_args = kwargs.pop("pd_args", {})            # line 41
    async def _run(self) -> DataFrame: ...                  # line 44 — abstract
    async def run(self): ...                                # line 53

# querysource/queries/multi/transformations/Map.py:14
class Map(AbstractTransform):
    def __init__(self, data, **kwargs):                     # line 48
        self.replace_columns: bool = kwargs.pop('replace_columns', False)  # line 49
        self.reset_index: bool = ...                        # line 51-54
        # Validates self.fields exists                      # line 56
    async def run(self): ...                                # line 61
```

### How Transforms Are Discovered & Invoked

```python
# querysource/queries/multi/__init__.py:37
def get_transform_module(clsname: str):
    """Dynamic import: .transformations.<clsname> → getattr(module, clsname)"""
    # Expects: module file name == class name (e.g. PluckCols.py → class PluckCols)

# querysource/queries/multi/__init__.py:318-343 (in MultiQS.query())
# Transform dispatch loop:
for s in step:
    for s_name, component in s.items():
        clobj = get_transform_module(s_name)       # line 340
        obj = clobj(data=result, **component)       # line 341
        async with obj as o:                        # line 342 — calls start() + close()
            result = await o.run()                  # line 343

# querysource/queries/multi/registry.py:119-131
# ComponentRegistry scans transformations/*.py (excluding _* and abstract.py)
```

### Utility Functions (reference only — do not import into new transforms)

```python
# querysource/types/dt/filters.py:237
def drop_columns(df, columns=None, endswith=None, startswith=None):
    """Drop columns by name, suffix, or prefix. Uses errors='ignore'."""
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `PluckCols` | `AbstractTransform` | inheritance | `transformations/abstract.py:16` |
| `DropCols` | `AbstractTransform` | inheritance | `transformations/abstract.py:16` |
| `FilterCols` | `AbstractTransform` | inheritance | `transformations/abstract.py:16` |
| All three | `get_transform_module()` | auto-discovery | `multi/__init__.py:37` |
| All three | `ComponentRegistry` | glob scan | `registry.py:119-131` |
| All three | `MultiQS.query()` | dispatch loop | `multi/__init__.py:338-343` |

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.queries.multi.transformations.PluckCols`~~ — does not exist yet (to be created)
- ~~`querysource.queries.multi.transformations.DropCols`~~ — does not exist yet (to be created)
- ~~`querysource.queries.multi.transformations.FilterCols`~~ — does not exist yet (to be created)
- ~~`AbstractTransform.columns`~~ — not a built-in attribute; must be set via kwargs
- ~~`AbstractTransform.expression`~~ — not a built-in attribute; must be set via kwargs
- ~~`AbstractTransform.validate_columns()`~~ — no such method; validation is manual
- ~~`querysource.queries.multi.transformations.SelectCols`~~ — does not exist
- ~~`querysource.queries.multi.transformations.ColumnFilter`~~ — does not exist

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Map pattern** (`Map.py`): Pop transform-specific kwargs in `__init__` before
  calling `super().__init__()`, then validate required attributes exist. This
  ensures introspection via `kwargs.pop()` regex works correctly.
- **start() lifecycle**: Call `await self.start()` at the beginning of `run()` to
  trigger `AbstractTransform.start()` data validation (type + empty checks).
- **Dict-of-DataFrames handling**: When `self.data` is a dict, iterate over values
  and apply the column operation to each DataFrame independently, returning a
  new dict. Match the pattern used by `AbstractTransform.start()` at line 42-53.
- **Module naming**: File name must match class name exactly (e.g. `PluckCols.py`
  contains `class PluckCols`) — required by `get_transform_module()`.
- **Docstring format**: Follow the structured docstring style from `Map.py` with
  `Usage:`, `Attributes:`, and `Example:` sections for introspection support.

### Known Risks / Gotchas

- **Column case sensitivity**: pandas column lookups are case-sensitive. Users must
  provide exact column names. Document this in the docstrings.
- **Empty DataFrame after FilterCols**: If all columns match the expression, the
  resulting DataFrame has zero columns. This should raise `DataNotFound`.
- **Performance on wide DataFrames**: `FilterCols("all_null")` calls
  `df.dropna(axis=1, how="all")` which scans every cell. For very wide DataFrames
  (1000+ columns) this may be slow, but is acceptable for the current use case.
- **Thread safety**: Transforms run in the async event loop (not in threads),
  so no thread-safety concerns.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=1.5` | Already a core dependency — DataFrame operations |

No new external dependencies required.

---

## 8. Open Questions

- [ ] Should `PluckCols` support glob patterns (e.g. `"revenue_*"`) in addition
  to exact column names? — *Owner: Jesus*
- [ ] Should `FilterCols` support additional expressions beyond `all_null`,
  `all_empty`, and `constant` (e.g. `low_variance`, `high_cardinality`)? — *Owner: Jesus*
- [ ] Should `DropCols` support `endswith`/`startswith` patterns like the existing
  `drop_columns()` utility in `filters.py`? — *Owner: Jesus*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` — all three modules are small and tightly related.
- **Parallelism**: Modules 1-3 are independent of each other and could be
  implemented in parallel, but sequential is fine given their small size.
  Module 4 (tests) depends on all three modules.
- **Cross-feature dependencies**: None — this spec is self-contained.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-23 | Jesus Lara | Initial draft |
