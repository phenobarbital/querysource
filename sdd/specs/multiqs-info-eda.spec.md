---
type: feature
base_branch: dev
---

# Feature Specification: MultiQS Info EDA

**Feature ID**: FEAT-098
**Date**: 2026-05-23
**Author**: Jesus Lara
**Status**: draft
**Target version**: 2.18.0

---

## 1. Motivation & Business Requirements

### Problem Statement

The current `Info` operator in MultiQS is a basic diagnostic tool that returns column names, data types, first-row values, and sample data as a flat JSON dict. It provides no statistical profiling — no null percentages, no duplicate analysis, no distribution metrics — making it useless for Exploratory Data Analysis (EDA).

Users who receive multiple DataFrames from a multi-source query currently have no built-in way to quickly assess data quality across all sources. They must export data and use external tools (pandas-profiling, sweetviz) for profiling. The existing `DescribeWriter` and `EDAWriter` output writers only work on a single DataFrame post-query and cannot profile the intermediate multi-source state.

### Goals

- Replace the current `Info` operator with a full EDA profiler that computes extended statistics per column per source DataFrame.
- Return EDA results in a **tabular format** (one row per column per DataFrame) as pandas DataFrames, making results compatible with downstream MultiQS steps (Transform, Filter, Output, destinations).
- Support both DataFrame and JSON output formats via a configurable parameter.
- Remove the early-return behavior so EDA results can flow through the full MultiQS pipeline (Transform → Filter → Output).
- Produce one EDA DataFrame per source, keyed by source name in the result dict, consistent with MultiQS's `dict[str, DataFrame]` convention.

### Non-Goals (explicitly out of scope)

- Visualization or HTML report generation (covered by `EDAWriter` output writer).
- Great Expectations integration (covered by `DescribeWriter`).
- Cross-source correlation analysis (potential future feature).
- Modifying the `DescribeWriter` or `EDAWriter` — they remain independent post-query output writers.

---

## 2. Architectural Design

### Overview

The existing `Info` operator class (`querysource/queries/multi/operators/Info.py`) will be rewritten to compute extended EDA statistics for every column in every DataFrame in the data dict. The operator will produce one EDA DataFrame per input source, where each row represents one column's profile.

Two output modes are supported via the `output_format` attribute:
- `"dataframe"` (default): returns `dict[str, pd.DataFrame]` — one EDA DataFrame per source, compatible with downstream pipeline steps.
- `"json"`: returns a JSON-serializable dict (legacy-compatible).

The early-return in `MultiQS.query()` (line 239 of `__init__.py`) will be removed so Info results flow into Step 3 (Transform/Filter) and Step 5 (Output/destinations).

### Component Diagram

```
Sources (ThreadQuery/FileSource/...)
    │
    ▼
result: dict[str, DataFrame]
    │
    ▼
┌─────────────────────────────────┐
│  Info Operator (EDA)            │
│  ┌───────────────────────────┐  │
│  │ For each source DataFrame │  │
│  │  → compute_eda(df)        │  │
│  │  → one row per column     │  │
│  └───────────────────────────┘  │
│  output_format == "dataframe"   │
│    → dict[str, DataFrame]       │
│  output_format == "json"        │
│    → json_encoder(dict)         │
└─────────────────────────────────┘
    │
    ▼ (no longer early-returns)
Transform / Filter / GroupBy
    │
    ▼
Output / Destinations
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractOperator` | extends | Info inherits from this; no changes to base class |
| `MultiQS.query()` | modifies | Remove early-return at lines 232-246; make Info flow into Step 3 |
| `ComponentRegistry` | unchanged | Info is already in `_KNOWN_PIPELINE_KEYS`; no registry changes needed |
| `json_encoder` | uses | For JSON output mode only |
| `SchemaIntrospectable` | inherits via AbstractOperator | Docstring/attributes auto-extracted for docs API |

### Data Models

```python
# EDA DataFrame schema — one row per column in the source DataFrame
# Returned as: dict[str, pd.DataFrame] where keys are source names

EDA_COLUMNS = [
    "column_name",       # str: name of the column
    "dtype",             # str: pandas dtype
    "non_null_count",    # int: count of non-null values
    "null_count",        # int: count of null/NaN values
    "null_percent",      # float: percentage of nulls (0.0–100.0)
    "unique_count",      # int: number of unique values
    "duplicate_percent", # float: percentage of duplicate values (0.0–100.0)
    "min",               # Any: minimum value (numeric/datetime) or None
    "max",               # Any: maximum value (numeric/datetime) or None
    "mean",              # float | None: mean (numeric only)
    "std",               # float | None: standard deviation (numeric only)
    "median",            # float | None: median (numeric only)
    "mode",              # Any: most frequent value
    "skewness",          # float | None: skewness (numeric only)
    "kurtosis",          # float | None: kurtosis (numeric only)
    "q1",                # float | None: 25th percentile (numeric only)
    "q3",                # float | None: 75th percentile (numeric only)
    "memory_usage",      # int: memory usage in bytes for this column
    "sample_values",     # str: JSON-encoded list of up to 5 sample values
]
```

### New Public Interfaces

```python
class Info(AbstractOperator):
    """Exploratory Data Analysis operator for MultiQuery pipelines.

    Attributes:
        output_format: "dataframe" (default) or "json".
    """
    output_format: str  # "dataframe" | "json", default "dataframe"

    async def start(self) -> None: ...
    async def run(self) -> Union[dict[str, pd.DataFrame], dict]: ...
```

---

## 3. Module Breakdown

### Module 1: Rewrite Info Operator

- **Path**: `querysource/queries/multi/operators/Info.py`
- **Responsibility**: Compute extended EDA statistics per column for each DataFrame in the data dict. Return results as `dict[str, DataFrame]` (default) or JSON dict.
- **Depends on**: `AbstractOperator`, `pandas`, `json_encoder`

**Implementation details:**
- Extract a helper `_compute_column_eda(series: pd.Series) -> dict` that produces the per-column stats dict.
- Use `pd.api.types.is_numeric_dtype()` to branch between numeric and non-numeric stats.
- Build one DataFrame per source with `pd.DataFrame(rows, columns=EDA_COLUMNS)`.
- For `output_format="json"`, convert the dict of DataFrames via `json_encoder`.

### Module 2: Update MultiQS Pipeline Dispatch

- **Path**: `querysource/queries/multi/__init__.py`
- **Responsibility**: Remove the early-return when `Info` is used. Make Info result flow into Steps 3–5 like Join/Concat.
- **Depends on**: Module 1

**Implementation details:**
- Lines 232-246: Replace the current `if 'Info' in self._options:` block that calls `return result, self._options` with a block that runs Info but does **not** return early — instead, the result dict flows into the subsequent Transform/Filter/Output steps.
- The `Info` options dict must be popped from `self._options` (like Join/Concat do) so it doesn't get re-processed in Step 3.

### Module 3: Update SchemaIntrospectable Docstring

- **Path**: `querysource/queries/multi/operators/Info.py` (docstring only)
- **Responsibility**: Update the class docstring to document the new `output_format` attribute and EDA behavior, so `SchemaIntrospectable.get_attributes()` and `get_description()` correctly report the new interface.
- **Depends on**: Module 1

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_info_eda_single_dataframe` | Module 1 | Single-source dict with mixed dtypes; verify all EDA columns present and correct |
| `test_info_eda_multiple_dataframes` | Module 1 | Multi-source dict; verify one EDA DataFrame per source |
| `test_info_eda_null_percent` | Module 1 | DataFrame with known null distribution; verify null_count and null_percent accuracy |
| `test_info_eda_duplicate_percent` | Module 1 | DataFrame with known duplicates; verify unique_count and duplicate_percent |
| `test_info_eda_numeric_stats` | Module 1 | Numeric columns; verify mean, std, median, skewness, kurtosis, q1, q3 |
| `test_info_eda_categorical_stats` | Module 1 | Object/string columns; verify mode, min/max are None or correct |
| `test_info_eda_empty_dataframe` | Module 1 | Empty DataFrame; verify graceful handling (zeroes, None for stats) |
| `test_info_eda_output_format_json` | Module 1 | `output_format="json"`; verify JSON-serializable dict returned |
| `test_info_eda_output_format_dataframe` | Module 1 | `output_format="dataframe"` (default); verify dict of DataFrames returned |
| `test_info_eda_memory_usage` | Module 1 | Verify memory_usage column matches `df[col].memory_usage(deep=True)` |
| `test_info_eda_sample_values` | Module 1 | Verify sample_values contains JSON-encoded list of up to 5 values |
| `test_info_non_dataframe_input` | Module 1 | Non-DataFrame in data dict; verify DriverError raised |

### Integration Tests

| Test | Description |
|---|---|
| `test_multiqs_info_no_early_return` | MultiQS pipeline with Info + Transform; verify Transform receives EDA DataFrames |
| `test_multiqs_info_with_output` | MultiQS pipeline with Info + Output destination; verify destination receives EDA DataFrames |
| `test_multiqs_info_json_mode` | MultiQS pipeline with `"Info": {"output_format": "json"}`; verify JSON returned |
| `test_multiqs_info_concat_fallback` | Verify Info can be used standalone (without Join/Concat) and result flows correctly |

### Test Data / Fixtures

```python
@pytest.fixture
def mixed_dtypes_df():
    """DataFrame with numeric, string, datetime, and boolean columns."""
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Alice", None, "Eve"],
        "score": [95.5, 82.3, None, 91.0, 78.5],
        "active": [True, False, True, True, None],
        "created": pd.to_datetime(["2024-01-01", "2024-02-15", None, "2024-04-01", "2024-05-20"]),
    })

@pytest.fixture
def multi_source_data(mixed_dtypes_df):
    """Dict of DataFrames simulating multi-source query results."""
    return {
        "source_a": mixed_dtypes_df,
        "source_b": pd.DataFrame({
            "product": ["Widget", "Gadget", "Widget"],
            "price": [9.99, 24.99, 9.99],
            "quantity": [100, 50, 200],
        }),
    }
```

---

## 5. Acceptance Criteria

- [x] All unit tests pass (`pytest tests/ -k test_info_eda -v`)
- [ ] All integration tests pass verifying Info flows through Transform/Output steps
- [ ] Current `Info` operator replaced — no backward-incompatible API changes to operator invocation (`"Info": {}` still works)
- [ ] EDA DataFrame contains all 18 columns from the data model (column_name through sample_values)
- [ ] Null percent and duplicate percent are accurate to within floating-point tolerance
- [ ] Numeric-only stats (mean, std, median, skewness, kurtosis, q1, q3) return `None` for non-numeric columns
- [ ] `output_format="json"` returns a JSON-serializable dict
- [ ] `output_format="dataframe"` (default) returns `dict[str, pd.DataFrame]`
- [ ] Empty DataFrames produce valid EDA output with zeroes and None values (no exceptions)
- [ ] MultiQS pipeline no longer early-returns when Info is used — downstream Transform/Filter/Output steps execute
- [ ] `SchemaIntrospectable` correctly reports the new `output_format` attribute in the docs API
- [ ] No new external dependencies required (uses only pandas built-ins)
- [ ] No breaking changes to existing MultiQS pipelines that don't use Info

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# Operator base class
from .abstract import AbstractOperator  # verified: querysource/queries/multi/operators/abstract.py:16

# AbstractOperator inherits from AbstractMulti
from ..abstract import AbstractMulti  # verified: querysource/queries/multi/abstract.py:20

# AbstractMulti inherits from SchemaIntrospectable
from ._introspect import SchemaIntrospectable  # verified: querysource/queries/multi/_introspect.py:94

# Exceptions
from ....exceptions import DriverError, QueryException  # verified: querysource/exceptions.py

# JSON encoder for json output mode
from datamodel.parsers.json import json_encoder  # verified: used in Info.py:3

# pandas — already a dependency
import pandas as pd
from pandas import DataFrame
```

### Existing Class Signatures

```python
# querysource/queries/multi/operators/abstract.py
class AbstractOperator(AbstractMulti):  # line 16
    _category = "Operators"  # line 24

    def __init__(self, data: dict, **kwargs) -> None:  # line 26
        self._backend = kwargs.get('backend', 'pandas')  # line 27
        # modin fallback at line 29-32
        super().__init__(data, **kwargs)  # line 34

    @abstractmethod
    async def start(self): ...  # line 37
    @abstractmethod
    async def run(self): ...  # line 42

# querysource/queries/multi/abstract.py
class AbstractMulti(SchemaIntrospectable, ABC):  # line 20
    _category: str = "Components"  # line 31
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 33
        self.data = data  # line 40
        for k, v in kwargs.items():
            setattr(self, k, v)  # line 41-42
    async def __aenter__(self): ...  # line 48
    async def __aexit__(self, exc_type, exc_value, traceback): ...  # line 52
    async def start(self): ...  # line 66
    @abstractmethod
    async def run(self): ...  # line 73
    async def close(self): ...  # line 80
    def _print_info(self, df: pd.DataFrame) -> None: ...  # line 88

# querysource/queries/multi/operators/Info.py (CURRENT — to be replaced)
class Info(AbstractOperator):  # line 10
    async def start(self): ...  # line 29 — validates all inputs are DataFrames
    async def run(self): ...  # line 38 — returns json_encoder(result) dict

# querysource/queries/multi/operators/Concat.py (reference pattern)
class Concat(AbstractOperator):  # line 8
    async def start(self): ...  # line 25 — validates + converts data to list
    async def run(self): ...  # line 37 — returns single DataFrame

# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):  # line 53
    async def query(self): ...  # line 108
    # Info dispatch: lines 232-246 (early-return — to be modified)
    # Join dispatch: lines 247-267
    # Concat dispatch: lines 268-280

def get_operator_module(clsname: str): ...  # line 21 — dynamic import from .operators.<clsname>
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `Info.run()` | `AbstractOperator.__init__()` | inheritance | `operators/abstract.py:26` |
| `Info.run()` | `self.data` (dict of DataFrames) | attribute from `AbstractMulti.__init__` | `abstract.py:40` |
| `Info.run()` | `self._pd` (pandas or modin) | attribute from `AbstractOperator.__init__` | `operators/abstract.py:27-33` |
| `Info.run()` | `json_encoder()` | function call (json mode) | `Info.py:3` |
| `MultiQS.query()` | `Info` | via `get_operator_module('Info')` | `__init__.py:233` |
| `Info` docstring | `SchemaIntrospectable.get_attributes()` | kwargs parsing regex | `_introspect.py:108-196` |

### Configuration References

- `output_format` attribute: set via `kwargs` in MultiQS options dict, e.g., `"Info": {"output_format": "json"}`.
- Default `output_format` is `"dataframe"` — achieved via `kwargs.pop('output_format', 'dataframe')` in `__init__` or `start()`.

### Does NOT Exist (Anti-Hallucination)

- ~~`AbstractOperator.output_format`~~ — not a base class attribute; must be added in Info
- ~~`querysource.queries.multi.operators.EDA`~~ — no separate EDA operator module exists
- ~~`AbstractMulti.to_json()`~~ — no such method on the base class
- ~~`AbstractOperator._compute_stats()`~~ — no such method; must be implemented in Info
- ~~`Info._validate_dtypes()`~~ — no such method in current Info
- ~~`pandas.DataFrame.eda()`~~ — not a real pandas method
- ~~`querysource.utils.eda`~~ — no EDA utility module exists
- ~~`querysource.queries.multi.operators.Describe`~~ — no Describe operator exists (DescribeWriter is in `outputs/writers/`, not in operators)

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Operator lifecycle**: Follow the `Concat` pattern — validate in `start()`, compute in `run()`. Use `async with` context manager protocol.
- **kwargs-to-attribute binding**: `AbstractMulti.__init__` already does `setattr(self, k, v)` for all kwargs. Declare `output_format` in `__init__` via `kwargs.pop('output_format', 'dataframe')` before calling `super().__init__()` to prevent it from becoming a stray attribute.
- **Backend flexibility**: Use `self._pd` (pandas or modin) for DataFrame construction, same as Concat/Join.
- **Error wrapping**: Wrap exceptions in `QueryException` with descriptive messages, following the pattern in current Info and Concat.
- **SchemaIntrospectable compliance**: The docstring must include `Attributes:` and `Example:` sections for the auto-docs API to extract them correctly.

### Known Risks / Gotchas

- **Performance on wide DataFrames**: Computing skewness, kurtosis, and percentiles for hundreds of columns may be slow. Mitigate by using vectorized pandas operations (`df.describe()`, `df.skew()`, `df.kurtosis()`) on all numeric columns at once, then merging per-column.
- **Memory for sample_values**: JSON-encoding sample values for columns with large objects (nested dicts, long strings) could produce large output. Truncate individual sample values to 200 chars.
- **Mixed-type columns**: Columns with mixed types (e.g., ints and strings) may cause `mean()`/`std()` to fail. Use `pd.api.types.is_numeric_dtype()` guard and catch exceptions per-column.
- **Breaking change for Info consumers**: The current Info returns JSON; the new default returns DataFrames. Consumers expecting JSON must set `output_format="json"`. Document this in release notes.
- **MultiQS early-return removal**: Existing pipelines using `"Info": {}` alone will now flow through Steps 3-5 instead of returning immediately. This is intentional but could surface latent bugs in downstream steps if they don't expect EDA DataFrames.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=1.5` | Built-in `describe()`, `skew()`, `kurtosis()`, `quantile()` — already a project dependency |

No new external dependencies required. All statistics are computed using pandas built-in methods.

---

## 8. Open Questions

- [ ] Should skewness/kurtosis use Fisher's definition (default in pandas) or Pearson's? — *Owner: Jesus Lara*
- [ ] Should `sample_values` include null values or only non-null samples? — *Owner: Jesus Lara*
- [ ] For datetime columns, should `mean` return the midpoint datetime or `None`? — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` — all 3 modules are tightly coupled (Module 2 depends on Module 1's output format, Module 3 is docstring-only).
- **Parallelism**: None — tasks must be sequential (Module 1 → Module 2 → Module 3).
- **Cross-feature dependencies**: None — this spec modifies only the Info operator and its dispatch in MultiQS. No conflicts with other in-flight specs.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-23 | Jesus Lara | Initial draft |
