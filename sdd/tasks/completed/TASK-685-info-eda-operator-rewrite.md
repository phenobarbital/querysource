# TASK-685: Rewrite Info Operator with Extended EDA Statistics

**Feature**: FEAT-098 — MultiQS Info EDA
**Spec**: `sdd/specs/multiqs-info-eda.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The current `Info` operator in MultiQS is a basic diagnostic tool that returns column names, data types, first-row values, and sample data as a flat JSON dict. It provides no statistical profiling. This task rewrites the operator to compute extended EDA statistics per column, returning results as tabular DataFrames (one per source) or JSON.

This implements **Spec Module 1** and **Module 3** (docstring update for SchemaIntrospectable).

---

## Scope

- Rewrite `querysource/queries/multi/operators/Info.py` to compute 18 EDA statistics per column per source DataFrame.
- Add a `_compute_column_eda(series)` helper method that produces the per-column stats dict.
- Support `output_format` attribute: `"dataframe"` (default) returns `dict[str, pd.DataFrame]`; `"json"` returns JSON-serializable dict via `json_encoder`.
- Use `pd.api.types.is_numeric_dtype()` to branch between numeric and non-numeric stats.
- Use vectorized pandas operations where possible (`df.describe()`, `df.skew()`, `df.kurtosis()`).
- Handle edge cases: empty DataFrames, mixed-type columns, non-DataFrame inputs.
- Update the class docstring with `Attributes:` and `Example:` sections for `SchemaIntrospectable` auto-discovery.
- Write comprehensive unit tests.

**EDA columns produced per row (one row = one column from the source DataFrame):**
`column_name`, `dtype`, `non_null_count`, `null_count`, `null_percent`, `unique_count`, `duplicate_percent`, `min`, `max`, `mean`, `std`, `median`, `mode`, `skewness`, `kurtosis`, `q1`, `q3`, `memory_usage`, `sample_values`

**NOT in scope**:
- Changes to `MultiQS.__init__.py` pipeline dispatch (TASK-686).
- Integration tests with MultiQS pipeline (TASK-687).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/operators/Info.py` | MODIFY | Complete rewrite with EDA computation |
| `tests/test_info_eda.py` | CREATE | Unit tests for the rewritten Info operator |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase.
> The implementing agent MUST use these exact imports, class names, and method signatures.
> **DO NOT** invent, guess, or assume any import, attribute, or method not listed here.

### Verified Imports

```python
# Base class for operators
from .abstract import AbstractOperator  # verified: querysource/queries/multi/operators/abstract.py:16

# Exceptions
from ....exceptions import DriverError, QueryException  # verified: querysource/exceptions.py

# JSON encoder for json output mode
from datamodel.parsers.json import json_encoder  # verified: used at Info.py:3

# pandas (already a project dependency)
import pandas as pd
from pandas import DataFrame  # verified: used at Info.py:2
```

### Existing Signatures to Use

```python
# querysource/queries/multi/operators/abstract.py
class AbstractOperator(AbstractMulti):  # line 16
    _category = "Operators"  # line 24
    def __init__(self, data: dict, **kwargs) -> None:  # line 26
        self._backend = kwargs.get('backend', 'pandas')  # line 27
        if self._backend == 'modin':  # line 29
            import modin.pandas as mpd
            self._pd = mpd  # line 31
        else:
            self._pd = pd  # line 33
        super().__init__(data, **kwargs)  # line 34

# querysource/queries/multi/abstract.py
class AbstractMulti(SchemaIntrospectable, ABC):  # line 20
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 33
        self.data = data  # line 40
        for k, v in kwargs.items():
            setattr(self, k, v)  # lines 41-42
    def _print_info(self, df: pd.DataFrame) -> None:  # line 88
```

### Does NOT Exist

- ~~`AbstractOperator.output_format`~~ — not a base class attribute; must be added in Info
- ~~`AbstractOperator._compute_stats()`~~ — no such method; implement in Info
- ~~`Info._validate_dtypes()`~~ — no such method in current Info
- ~~`pandas.DataFrame.eda()`~~ — not a real pandas method
- ~~`querysource.utils.eda`~~ — no EDA utility module exists
- ~~`querysource.queries.multi.operators.Describe`~~ — does not exist (DescribeWriter is in `outputs/writers/`)

---

## Implementation Notes

### Pattern to Follow

Follow the `Concat` operator pattern (`querysource/queries/multi/operators/Concat.py`):

```python
# Concat.py — reference pattern
class Concat(AbstractOperator):
    """Docstring with Usage:, Attributes:, and Example: sections."""
    async def start(self):
        # Validate inputs (all must be DataFrames)
        for _, data in self.data.items():
            if isinstance(data, DataFrame):
                self._backend = 'pandas'
                ...
            else:
                raise DriverError(...)
    async def run(self):
        try:
            # Core computation
            df = self._pd.concat(self.data, ignore_index=True)
            self._print_info(df)
            return df
        except Exception as err:
            raise QueryException(...) from err
```

### Key Constraints

- Pop `output_format` from kwargs BEFORE calling `super().__init__()` to prevent it becoming a stray attribute: `self.output_format = kwargs.pop('output_format', 'dataframe')`
- Use `self._pd` (not bare `pd`) for DataFrame construction to support modin backend.
- Use vectorized pandas: call `df.describe()`, `df.skew()`, `df.kurtosis()`, `df.quantile([0.25, 0.75])` on all numeric columns at once, then merge results per-column.
- Guard non-numeric stats with `pd.api.types.is_numeric_dtype(series)` — return `None` for mean, std, median, skewness, kurtosis, q1, q3 on non-numeric columns.
- Truncate individual `sample_values` entries to 200 chars.
- For empty DataFrames, return an empty EDA DataFrame with the correct columns (no exceptions).
- JSON-encode `sample_values` list via `json_encoder`.
- The docstring MUST include `Attributes:` and `Example:` sections for `SchemaIntrospectable.get_attributes()` and `get_description()` to parse correctly.

---

## Acceptance Criteria

- [ ] `Info` operator computes all 18 EDA columns per source column
- [ ] `output_format="dataframe"` (default) returns `dict[str, pd.DataFrame]`
- [ ] `output_format="json"` returns a JSON-serializable dict
- [ ] Numeric-only stats return `None` for non-numeric columns
- [ ] Empty DataFrames produce valid EDA output (no exceptions)
- [ ] Non-DataFrame inputs raise `DriverError`
- [ ] All unit tests pass: `pytest tests/test_info_eda.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/operators/Info.py`
- [ ] `SchemaIntrospectable` parses the updated docstring correctly

---

## Test Specification

```python
# tests/test_info_eda.py
import pytest
import pandas as pd
from querysource.queries.multi.operators.Info import Info


@pytest.fixture
def mixed_dtypes_df():
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Alice", None, "Eve"],
        "score": [95.5, 82.3, None, 91.0, 78.5],
        "active": [True, False, True, True, None],
        "created": pd.to_datetime(
            ["2024-01-01", "2024-02-15", None, "2024-04-01", "2024-05-20"]
        ),
    })


@pytest.fixture
def multi_source_data(mixed_dtypes_df):
    return {
        "source_a": mixed_dtypes_df,
        "source_b": pd.DataFrame({
            "product": ["Widget", "Gadget", "Widget"],
            "price": [9.99, 24.99, 9.99],
            "quantity": [100, 50, 200],
        }),
    }


class TestInfoEDA:
    @pytest.mark.asyncio
    async def test_single_dataframe(self, mixed_dtypes_df):
        """Single-source dict; verify all EDA columns present."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        assert isinstance(result, dict)
        assert "src" in result
        eda_df = result["src"]
        assert isinstance(eda_df, pd.DataFrame)
        assert len(eda_df) == len(mixed_dtypes_df.columns)
        expected_cols = [
            "column_name", "dtype", "non_null_count", "null_count",
            "null_percent", "unique_count", "duplicate_percent",
            "min", "max", "mean", "std", "median", "mode",
            "skewness", "kurtosis", "q1", "q3",
            "memory_usage", "sample_values",
        ]
        for col in expected_cols:
            assert col in eda_df.columns

    @pytest.mark.asyncio
    async def test_multiple_dataframes(self, multi_source_data):
        """Multi-source dict; verify one EDA DataFrame per source."""
        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        assert "source_a" in result
        assert "source_b" in result
        assert len(result["source_a"]) == 5  # 5 columns
        assert len(result["source_b"]) == 3  # 3 columns

    @pytest.mark.asyncio
    async def test_null_percent(self, mixed_dtypes_df):
        """Verify null_count and null_percent accuracy."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        name_row = eda[eda["column_name"] == "name"].iloc[0]
        assert name_row["null_count"] == 1
        assert abs(name_row["null_percent"] - 20.0) < 0.01

    @pytest.mark.asyncio
    async def test_duplicate_percent(self):
        """Verify unique_count and duplicate_percent."""
        df = pd.DataFrame({"x": [1, 1, 2, 2, 3]})
        info = Info(data={"src": df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        row = eda.iloc[0]
        assert row["unique_count"] == 3
        assert abs(row["duplicate_percent"] - 40.0) < 0.01

    @pytest.mark.asyncio
    async def test_numeric_stats(self, mixed_dtypes_df):
        """Verify mean, std, median, skewness, kurtosis, q1, q3 for numeric."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        score_row = eda[eda["column_name"] == "score"].iloc[0]
        assert score_row["mean"] is not None
        assert score_row["std"] is not None
        assert score_row["median"] is not None

    @pytest.mark.asyncio
    async def test_categorical_stats(self, mixed_dtypes_df):
        """Non-numeric columns: numeric-only stats should be None."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        name_row = eda[eda["column_name"] == "name"].iloc[0]
        assert name_row["mean"] is None
        assert name_row["std"] is None
        assert name_row["skewness"] is None

    @pytest.mark.asyncio
    async def test_empty_dataframe(self):
        """Empty DataFrame produces valid EDA output with no exceptions."""
        df = pd.DataFrame({"a": pd.Series([], dtype="int64")})
        info = Info(data={"src": df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        assert len(eda) == 1
        assert eda.iloc[0]["non_null_count"] == 0
        assert eda.iloc[0]["null_percent"] == 0.0

    @pytest.mark.asyncio
    async def test_output_format_json(self, mixed_dtypes_df):
        """output_format='json' returns JSON-serializable dict."""
        info = Info(data={"src": mixed_dtypes_df}, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, (dict, str))

    @pytest.mark.asyncio
    async def test_memory_usage(self, mixed_dtypes_df):
        """memory_usage matches pandas deep memory usage."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        for _, row in eda.iterrows():
            col = row["column_name"]
            expected = mixed_dtypes_df[col].memory_usage(deep=True)
            assert row["memory_usage"] == expected

    @pytest.mark.asyncio
    async def test_non_dataframe_input(self):
        """Non-DataFrame input raises DriverError."""
        from querysource.exceptions import DriverError
        info = Info(data={"src": "not a dataframe"})
        with pytest.raises(DriverError):
            async with info as i:
                await i.run()
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiqs-info-eda.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists (`grep` or `read` the source)
   - Confirm every class/method in "Existing Signatures" still has the listed attributes
   - If anything has changed, update the contract FIRST, then implement
   - **NEVER** reference an import, attribute, or method not in the contract without verifying it exists
4. **Read the current `Info.py`** at `querysource/queries/multi/operators/Info.py` to understand what you're replacing
5. **Read `Concat.py`** at `querysource/queries/multi/operators/Concat.py` for the operator pattern
6. **Implement** following the scope, codebase contract, and notes above
7. **Run tests**: `source .venv/bin/activate && pytest tests/test_info_eda.py -v`
8. **Run lint**: `source .venv/bin/activate && ruff check querysource/queries/multi/operators/Info.py`
9. **Verify** all acceptance criteria are met

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-05-23
**Notes**: All 10 unit tests pass. Added object-dtype casting for nullable numeric stat columns (min, max, mean, std, median, mode, skewness, kurtosis, q1, q3) to preserve Python None identity (pandas coerces None to np.nan in float64 columns). Compiled .so files copied to worktree for test environment.

**Deviations from spec**: Minor — added nullable-column dtype casting not mentioned in spec, but required to satisfy `is None` checks in test specification.
