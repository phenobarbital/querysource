"""Unit and integration tests for PluckCols, DropCols, and FilterCols transforms.

FEAT-098 — MultiQS New Transformations (TASK-685)
"""
import pytest
import pandas as pd

from querysource.exceptions import DataNotFound, DriverError
from querysource.queries.multi import get_transform_module
from querysource.queries.multi.registry import ComponentRegistry
from querysource.queries.multi.transformations.DropCols import DropCols
from querysource.queries.multi.transformations.FilterCols import FilterCols
from querysource.queries.multi.transformations.PluckCols import PluckCols


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie"],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "phone": ["111", "222", "333"],
        "internal_id": [1, 2, 3],
        "revenue_q1": [100, 200, 300],
        "revenue_q2": [110, 210, 310],
        "debug_flag": [True, True, True],
        "debug_trace": ["x", "y", "z"],
        "tmp_scratch": ["a", "b", "c"],
        "all_null_col": [None, None, None],
        "empty_col": [None, "", None],
        "constant_col": ["X", "X", "X"],
    })


@pytest.fixture
def sample_dict(sample_df):
    return {"df1": sample_df.copy(), "df2": sample_df.copy()}


# ---------------------------------------------------------------------------
# PluckCols — exact column names
# ---------------------------------------------------------------------------

class TestPluckCols:

    @pytest.mark.asyncio
    async def test_pluck_cols_exact(self, sample_df):
        """Keep 2 of N columns by exact name."""
        obj = PluckCols(data=sample_df, columns=["name", "email"])
        async with obj as o:
            result = await o.run()
        assert list(result.columns) == ["name", "email"]
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_pluck_cols_glob_pattern(self, sample_df):
        """pattern: 'revenue_*' keeps matching columns."""
        obj = PluckCols(data=sample_df, pattern="revenue_*")
        async with obj as o:
            result = await o.run()
        assert set(result.columns) == {"revenue_q1", "revenue_q2"}

    @pytest.mark.asyncio
    async def test_pluck_cols_regex(self, sample_df):
        """regex: '^(name|email)$' keeps matching columns."""
        obj = PluckCols(data=sample_df, regex="^(name|email)$")
        async with obj as o:
            result = await o.run()
        assert set(result.columns) == {"name", "email"}

    @pytest.mark.asyncio
    async def test_pluck_cols_startswith(self, sample_df):
        """startswith: ['rev'] keeps matching columns."""
        obj = PluckCols(data=sample_df, startswith=["rev"])
        async with obj as o:
            result = await o.run()
        assert set(result.columns) == {"revenue_q1", "revenue_q2"}

    @pytest.mark.asyncio
    async def test_pluck_cols_endswith(self, sample_df):
        """endswith: ['_id'] keeps matching columns."""
        obj = PluckCols(data=sample_df, endswith=["_id"])
        async with obj as o:
            result = await o.run()
        assert "internal_id" in result.columns
        # Verify nothing unexpected was kept
        for col in result.columns:
            assert col.endswith("_id")

    @pytest.mark.asyncio
    async def test_pluck_cols_combined(self, sample_df):
        """Multiple modes unioned together."""
        obj = PluckCols(data=sample_df, columns=["name"], pattern="revenue_*")
        async with obj as o:
            result = await o.run()
        assert "name" in result.columns
        assert "revenue_q1" in result.columns
        assert "revenue_q2" in result.columns
        # email and phone should NOT be kept
        assert "email" not in result.columns
        assert "phone" not in result.columns

    @pytest.mark.asyncio
    async def test_pluck_cols_missing_exact(self, sample_df):
        """Exact name not present → DriverError (called directly, not via context manager)."""
        obj = PluckCols(data=sample_df, columns=["name", "nonexistent_col"])
        with pytest.raises(DriverError, match="nonexistent_col"):
            await obj.run()

    def test_pluck_cols_no_selector(self, sample_df):
        """No matching mode provided → DriverError raised in __init__."""
        with pytest.raises(DriverError, match="At least one column selector"):
            PluckCols(data=sample_df)

    @pytest.mark.asyncio
    async def test_pluck_cols_dict_input(self, sample_dict):
        """Dict of DataFrames — each DF gets plucked independently."""
        obj = PluckCols(data=sample_dict, columns=["name", "email"])
        async with obj as o:
            result = await o.run()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"df1", "df2"}
        for df in result.values():
            assert list(df.columns) == ["name", "email"]

    @pytest.mark.asyncio
    async def test_pluck_cols_invalid_regex(self, sample_df):
        """Invalid regex → DriverError (called directly, not via context manager)."""
        obj = PluckCols(data=sample_df, regex="[invalid(regex")
        with pytest.raises(DriverError, match="Invalid regex"):
            await obj.run()

    @pytest.mark.asyncio
    async def test_pluck_cols_preserves_order(self, sample_df):
        """Result columns follow original DataFrame column order."""
        obj = PluckCols(data=sample_df, columns=["email", "name"])
        async with obj as o:
            result = await o.run()
        # Order should match original df, not the order of columns list
        assert list(result.columns) == ["name", "email"]

    @pytest.mark.asyncio
    async def test_pluck_cols_dict_inconsistent_schemas(self):
        """Dict where one DF lacks a requested exact column → DriverError.

        Called directly (not via context manager) so DriverError is not wrapped
        in QueryException by AbstractMulti.__aexit__.
        """
        df1 = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        df2 = pd.DataFrame({"b": [5, 6], "c": [7, 8]})  # no column 'a'
        obj = PluckCols(data={"df1": df1, "df2": df2}, columns=["a"])
        with pytest.raises(DriverError, match="not found in DataFrame"):
            await obj.run()


# ---------------------------------------------------------------------------
# DropCols
# ---------------------------------------------------------------------------

class TestDropCols:

    @pytest.mark.asyncio
    async def test_drop_cols_exact(self, sample_df):
        """Drop 2 of N columns by exact name."""
        obj = DropCols(data=sample_df, columns=["internal_id", "tmp_scratch"])
        async with obj as o:
            result = await o.run()
        assert "internal_id" not in result.columns
        assert "tmp_scratch" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_glob_pattern(self, sample_df):
        """pattern: 'debug_*' drops matching columns."""
        obj = DropCols(data=sample_df, pattern="debug_*")
        async with obj as o:
            result = await o.run()
        assert "debug_flag" not in result.columns
        assert "debug_trace" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_regex(self, sample_df):
        """regex: '^tmp_' drops matching columns."""
        obj = DropCols(data=sample_df, regex="^tmp_")
        async with obj as o:
            result = await o.run()
        assert "tmp_scratch" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_startswith(self, sample_df):
        """startswith: ['debug_'] drops matching columns."""
        obj = DropCols(data=sample_df, startswith=["debug_"])
        async with obj as o:
            result = await o.run()
        assert "debug_flag" not in result.columns
        assert "debug_trace" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_endswith(self, sample_df):
        """endswith: ['_flag'] drops matching columns."""
        obj = DropCols(data=sample_df, endswith=["_flag"])
        async with obj as o:
            result = await o.run()
        assert "debug_flag" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_combined(self, sample_df):
        """Multiple modes unioned together."""
        obj = DropCols(data=sample_df, columns=["internal_id"], pattern="debug_*")
        async with obj as o:
            result = await o.run()
        assert "internal_id" not in result.columns
        assert "debug_flag" not in result.columns
        assert "debug_trace" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_missing_exact(self, sample_df):
        """Non-existent exact column is silently ignored (no error)."""
        obj = DropCols(data=sample_df, columns=["nonexistent_col", "name"])
        async with obj as o:
            result = await o.run()
        # 'name' was in the list and exists → dropped
        assert "name" not in result.columns
        # 'email' was not in the drop list → kept
        assert "email" in result.columns

    @pytest.mark.asyncio
    async def test_drop_cols_dict_input(self, sample_dict):
        """Dict of DataFrames — each DF gets dropped independently."""
        obj = DropCols(data=sample_dict, columns=["internal_id", "tmp_scratch"])
        async with obj as o:
            result = await o.run()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"df1", "df2"}
        for df in result.values():
            assert "internal_id" not in df.columns
            assert "tmp_scratch" not in df.columns
            assert "name" in df.columns

    def test_drop_cols_no_selector(self, sample_df):
        """No matching mode provided → DriverError raised in __init__."""
        with pytest.raises(DriverError, match="At least one column selector"):
            DropCols(data=sample_df)

    @pytest.mark.asyncio
    async def test_drop_cols_invalid_regex(self, sample_df):
        """Invalid regex → DriverError (called directly, not via context manager)."""
        obj = DropCols(data=sample_df, regex="[bad(regex")
        with pytest.raises(DriverError, match="Invalid regex"):
            await obj.run()


# ---------------------------------------------------------------------------
# FilterCols
# ---------------------------------------------------------------------------

class TestFilterCols:

    @pytest.mark.asyncio
    async def test_filter_cols_all_null(self, sample_df):
        """Column with all NaN removed."""
        obj = FilterCols(data=sample_df, expression="all_null")
        async with obj as o:
            result = await o.run()
        assert "all_null_col" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_filter_cols_all_empty(self, sample_df):
        """Column with NaN + empty strings removed."""
        obj = FilterCols(data=sample_df, expression="all_empty")
        async with obj as o:
            result = await o.run()
        # all_null_col is all-None → removed
        assert "all_null_col" not in result.columns
        # empty_col has None and "" → removed
        assert "empty_col" not in result.columns
        assert "name" in result.columns

    @pytest.mark.asyncio
    async def test_filter_cols_constant(self, sample_df):
        """Column with single unique value removed."""
        obj = FilterCols(data=sample_df, expression="constant")
        async with obj as o:
            result = await o.run()
        # constant_col has only "X" → removed
        assert "constant_col" not in result.columns
        # debug_flag has only True → removed
        assert "debug_flag" not in result.columns
        assert "name" in result.columns

    def test_filter_cols_invalid_expression(self, sample_df):
        """Unknown expression → DriverError raised in __init__."""
        with pytest.raises(DriverError, match="Unknown expression"):
            FilterCols(data=sample_df, expression="all_zeros")

    def test_filter_cols_no_expression(self, sample_df):
        """Missing expression → DriverError raised in __init__."""
        with pytest.raises(DriverError, match="'expression' attribute is required"):
            FilterCols(data=sample_df)

    @pytest.mark.asyncio
    async def test_filter_cols_dict_input(self, sample_dict):
        """Dict of DataFrames — filter applied per-DF."""
        obj = FilterCols(data=sample_dict, expression="all_null")
        async with obj as o:
            result = await o.run()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"df1", "df2"}
        for df in result.values():
            assert "all_null_col" not in df.columns
            assert "name" in df.columns

    @pytest.mark.asyncio
    async def test_filter_cols_constant_single_row_drops_all(self):
        """Single-row DF: every column has nunique==1 → all dropped → DataNotFound.

        Called directly (not via context manager) so DataNotFound is not
        wrapped in QueryException by AbstractMulti.__aexit__.
        """
        df = pd.DataFrame({"a": [1], "b": ["hello"], "c": [True]})
        obj = FilterCols(data=df, expression="constant")
        with pytest.raises(DataNotFound):
            await obj.run()

    @pytest.mark.asyncio
    async def test_filter_cols_constant_skips_all_null(self, sample_df):
        """'constant' does NOT drop all-null columns (nunique==0 != 1).

        All-null columns are the domain of the 'all_null' expression.
        """
        obj = FilterCols(data=sample_df, expression="constant")
        async with obj as o:
            result = await o.run()
        # all_null_col has nunique==0 → NOT dropped by "constant"
        assert "all_null_col" in result.columns
        # constant_col has nunique==1 → IS dropped by "constant"
        assert "constant_col" not in result.columns


# ---------------------------------------------------------------------------
# Empty DataFrame — all three transforms raise DataNotFound
# ---------------------------------------------------------------------------

class TestEmptyDataFrame:

    @pytest.mark.asyncio
    async def test_pluck_empty_raises(self):
        """Empty DataFrame → DataNotFound (called directly, not via context manager)."""
        empty_df = pd.DataFrame({"a": pd.Series([], dtype="int64")})
        obj = PluckCols(data=empty_df, columns=["a"])
        with pytest.raises(DataNotFound):
            await obj.run()

    @pytest.mark.asyncio
    async def test_drop_empty_raises(self):
        """Empty DataFrame → DataNotFound (called directly, not via context manager)."""
        empty_df = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype="str")})
        obj = DropCols(data=empty_df, columns=["a"])
        with pytest.raises(DataNotFound):
            await obj.run()

    @pytest.mark.asyncio
    async def test_filter_empty_raises(self):
        """Empty DataFrame → DataNotFound (called directly, not via context manager)."""
        empty_df = pd.DataFrame({"a": pd.Series([], dtype="int64")})
        obj = FilterCols(data=empty_df, expression="all_null")
        with pytest.raises(DataNotFound):
            await obj.run()


# ---------------------------------------------------------------------------
# Integration: Transform chain PluckCols → DropCols
# ---------------------------------------------------------------------------

class TestIntegration:

    @pytest.mark.asyncio
    async def test_transform_chain_pluck_then_drop(self, sample_df):
        """Chain PluckCols + DropCols in sequence."""
        # First, keep only revenue columns + name
        pluck = PluckCols(data=sample_df, columns=["name"], pattern="revenue_*")
        async with pluck as p:
            after_pluck = await p.run()

        # Verify pluck result
        assert set(after_pluck.columns) == {"name", "revenue_q1", "revenue_q2"}

        # Then drop revenue_q1 from the result
        drop = DropCols(data=after_pluck, columns=["revenue_q1"])
        async with drop as d:
            after_drop = await d.run()

        assert "revenue_q1" not in after_drop.columns
        assert "revenue_q2" in after_drop.columns
        assert "name" in after_drop.columns

    def test_get_transform_module_discovery(self):
        """get_transform_module() discovers all three new transforms."""
        for cls_name in ["PluckCols", "DropCols", "FilterCols"]:
            cls = get_transform_module(cls_name)
            assert cls is not None, f"get_transform_module('{cls_name}') returned None"
            assert cls.__name__ == cls_name

    def test_component_registry_discovery(self):
        """ComponentRegistry.discover_all() includes all three new transforms."""
        # Clear LRU cache so this test always performs a real filesystem scan,
        # even when the registry was already populated by a prior test or import.
        # If ComponentRegistry.discover_all ever stops using an LRU cache,
        # this call can be removed.
        ComponentRegistry.discover_all.cache_clear()
        components = ComponentRegistry.discover_all()
        for cls_name in ["PluckCols", "DropCols", "FilterCols"]:
            assert cls_name in components, (
                f"ComponentRegistry.discover_all() did not include '{cls_name}'"
            )
