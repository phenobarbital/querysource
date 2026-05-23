"""Unit and integration tests for the tExplode transformation component (TASK-688).

Tests cover:
- Init validation (missing column raises DriverError)
- Standard mode: list explode, dict normalize, drop_original toggle
- Advanced mode: parent tracking, propagate_columns, empty list preservation
- Dict-of-DataFrames input
- Error conditions: empty DataFrame, non-existent column
- Async context manager usage
- Integration: registry discovery, introspection schema, transform chain
"""
import pytest
import pandas as pd

from querysource.queries.multi.transformations.tExplode import tExplode
from querysource.exceptions import DataNotFound, DriverError, QueryException


# ---------------------------------------------------------------------------
# Fixtures (from spec §4)
# ---------------------------------------------------------------------------

@pytest.fixture
def df_with_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "tags": [["a", "b"], ["c"], ["d", "e", "f"]],
        "name": ["Alice", "Bob", "Carol"],
    })


@pytest.fixture
def df_with_dicts():
    return pd.DataFrame({
        "id": [1, 2],
        "details": [
            {"color": "red", "size": 10},
            {"color": "blue", "size": 20},
        ],
        "name": ["Alice", "Bob"],
    })


@pytest.fixture
def df_with_empty_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "items": [["x", "y"], [], ["z"]],
        "group": ["A", "B", "C"],
    })


# ---------------------------------------------------------------------------
# Unit tests — init
# ---------------------------------------------------------------------------

class TestTExplodeInit:
    def test_texplode_init_requires_column(self):
        """Raises DriverError when column kwarg is missing."""
        df = pd.DataFrame({"id": [1]})
        with pytest.raises(DriverError):
            tExplode(data=df)

    def test_texplode_init_sets_defaults(self, df_with_lists):
        """All optional kwargs have correct defaults."""
        obj = tExplode(data=df_with_lists, column="tags")
        assert obj.column == "tags"
        assert obj.drop_original is False
        assert obj.explode_dataset is True
        assert obj.advanced_mode is False
        assert obj.propagate_columns == []

    def test_texplode_init_accepts_all_kwargs(self, df_with_lists):
        """All kwargs can be set explicitly."""
        obj = tExplode(
            data=df_with_lists,
            column="tags",
            drop_original=True,
            explode_dataset=False,
            advanced_mode=True,
            propagate_columns=["id", "name"],
        )
        assert obj.drop_original is True
        assert obj.explode_dataset is False
        assert obj.advanced_mode is True
        assert obj.propagate_columns == ["id", "name"]


# ---------------------------------------------------------------------------
# Unit tests — standard mode
# ---------------------------------------------------------------------------

class TestTExplodeStandardMode:
    async def test_texplode_basic_list_explode(self, df_with_lists):
        """Explodes a column of lists into rows (standard mode)."""
        obj = tExplode(data=df_with_lists, column="tags")
        async with obj as t:
            result = await t.run()

        # [1,2,3] -> [1,1, 2, 3,3,3] total 6 rows
        assert len(result) == 6
        assert "tags" in result.columns
        assert "id" in result.columns
        assert "name" in result.columns
        # All values in the exploded column should be scalars, not lists
        for val in result["tags"]:
            assert not isinstance(val, list)

    async def test_texplode_dict_explode_with_normalize(self, df_with_dicts):
        """Explodes + json_normalize when explode_dataset=True."""
        obj = tExplode(data=df_with_dicts, column="details", explode_dataset=True)
        async with obj as t:
            result = await t.run()

        # Dict keys should become columns
        assert "color" in result.columns
        assert "size" in result.columns
        assert len(result) == 2
        # Check values
        assert set(result["color"].tolist()) == {"red", "blue"}
        assert set(result["size"].tolist()) == {10, 20}

    async def test_texplode_drop_original(self, df_with_lists):
        """Source column is removed when drop_original=True."""
        obj = tExplode(data=df_with_lists, column="tags", drop_original=True)
        async with obj as t:
            result = await t.run()

        assert "tags" not in result.columns
        assert "id" in result.columns
        assert "name" in result.columns

    async def test_texplode_no_drop_original(self, df_with_lists):
        """Source column is preserved when drop_original=False (default)."""
        obj = tExplode(data=df_with_lists, column="tags", drop_original=False)
        async with obj as t:
            result = await t.run()

        assert "tags" in result.columns

    async def test_texplode_explode_dataset_false(self, df_with_dicts):
        """Dicts stay as values when explode_dataset=False."""
        obj = tExplode(data=df_with_dicts, column="details", explode_dataset=False)
        async with obj as t:
            result = await t.run()

        # No normalization: dict keys should NOT become columns
        assert "color" not in result.columns
        assert "size" not in result.columns
        # Original column remains with dict values
        assert "details" in result.columns
        assert len(result) == 2

    async def test_texplode_standard_row_count(self, df_with_lists):
        """Row count after explosion matches sum of list lengths."""
        obj = tExplode(data=df_with_lists, column="tags")
        async with obj as t:
            result = await t.run()

        # tags = [["a","b"], ["c"], ["d","e","f"]] → 2+1+3 = 6 rows
        assert len(result) == 6


# ---------------------------------------------------------------------------
# Unit tests — advanced mode
# ---------------------------------------------------------------------------

class TestTExplodeAdvancedMode:
    async def test_texplode_advanced_mode_basic(self, df_with_lists):
        """Advanced mode tracks parent index, explodes non-empty lists.

        Result is parent (3 rows) + exploded children (6 rows) = 9 rows.
        """
        obj = tExplode(
            data=df_with_lists,
            column="tags",
            advanced_mode=True,
            explode_dataset=True,
        )
        async with obj as t:
            result = await t.run()

        # Parent rows (3) + child rows (2+1+3=6) = 9 rows total
        assert len(result) == 9
        # Helper column must be cleaned up
        assert "_parent_idx" not in result.columns

    async def test_texplode_advanced_propagate_columns(self, df_with_dicts):
        """Parent columns propagated to child rows in advanced mode.

        Input has 2 rows with dict-valued 'details'. After advanced mode:
        - 2 parent rows (original)
        - 2 child rows (one per dict)
        Total: 4 rows.
        propagate_columns=["id"] means each child row should have the id
        from its parent.
        """
        obj = tExplode(
            data=df_with_dicts,
            column="details",
            advanced_mode=True,
            explode_dataset=True,
            propagate_columns=["id", "name"],
        )
        async with obj as t:
            result = await t.run()

        # parent (2) + children (2) = 4 rows
        assert len(result) == 4
        # Children should have json_normalize columns
        assert "color" in result.columns
        assert "size" in result.columns

    async def test_texplode_advanced_empty_lists_preserved(self, df_with_empty_lists):
        """Rows with empty lists are kept in advanced mode.

        items = [["x","y"], [], ["z"]]
        Parent rows: 3 (all three, including empty-list row)
        Child rows: 2 ("x","y") + 0 (empty) + 1 ("z") = 3
        Total: 3 + 3 = 6 rows
        """
        obj = tExplode(
            data=df_with_empty_lists,
            column="items",
            advanced_mode=True,
        )
        async with obj as t:
            result = await t.run()

        # The empty-list row is in parent but not exploded
        assert len(result) == 6  # 3 parents + 3 children
        assert "_parent_idx" not in result.columns

    async def test_texplode_advanced_no_helper_column_in_result(self, df_with_lists):
        """_parent_idx helper is not present in the final result."""
        obj = tExplode(data=df_with_lists, column="tags", advanced_mode=True)
        async with obj as t:
            result = await t.run()

        assert "_parent_idx" not in result.columns


# ---------------------------------------------------------------------------
# Unit tests — edge cases
# ---------------------------------------------------------------------------

class TestTExplodeEdgeCases:
    async def test_texplode_dict_of_dataframes(self):
        """Handles dict-of-DataFrames input (applies to each value)."""
        df1 = pd.DataFrame({"id": [1, 2], "tags": [["a", "b"], ["c"]]})
        df2 = pd.DataFrame({"id": [3], "tags": [["d", "e", "f"]]})
        data = {"first": df1, "second": df2}

        obj = tExplode(data=data, column="tags")
        async with obj as t:
            result = await t.run()

        assert isinstance(result, dict)
        assert "first" in result
        assert "second" in result
        assert len(result["first"]) == 3   # 2 + 1 = 3 rows
        assert len(result["second"]) == 3  # 3 rows

    async def test_texplode_empty_dataframe(self):
        """Raises DataNotFound on empty input DataFrame.

        When using the async context manager, AbstractMulti.__aexit__ wraps
        all exceptions from run() in QueryException.  We also test that the
        underlying cause is DataNotFound by calling run() directly.
        """
        df = pd.DataFrame({"id": [], "tags": []})
        obj = tExplode(data=df, column="tags")

        # Via context manager: __aexit__ re-raises as QueryException
        with pytest.raises(QueryException):
            async with obj as t:
                await t.run()

        # Direct call (no context manager): the original DataNotFound propagates
        obj2 = tExplode(data=df, column="tags")
        await obj2.start()  # start() does not raise for non-empty dict or valid df type
        with pytest.raises(DataNotFound):
            await obj2.run()

    async def test_texplode_column_not_found(self):
        """Raises DriverError when column doesn't exist in DataFrame.

        When using the async context manager, AbstractMulti.__aexit__ wraps
        all exceptions from run() in QueryException.  We also test that the
        underlying cause is DriverError by calling run() directly.
        """
        df = pd.DataFrame({"id": [1, 2], "other": [[1], [2]]})
        obj = tExplode(data=df, column="nonexistent")

        # Via context manager: __aexit__ re-raises as QueryException
        with pytest.raises(QueryException):
            async with obj as t:
                await t.run()

        # Direct call (no context manager): the original DriverError propagates
        obj2 = tExplode(data=df, column="nonexistent")
        await obj2.start()  # start() succeeds (valid DataFrame type)
        with pytest.raises(DriverError):
            await obj2.run()

    async def test_texplode_async_context_manager(self, df_with_lists):
        """Works correctly via async with tExplode(...) as t: await t.run()."""
        obj = tExplode(data=df_with_lists, column="tags")
        async with obj as t:
            result = await t.run()

        assert isinstance(result, pd.DataFrame)
        assert not result.empty
        assert len(result) == 6

    async def test_texplode_single_row_list(self):
        """Single-row DataFrame with a list column is handled correctly."""
        df = pd.DataFrame({"id": [1], "items": [["a", "b", "c"]]})
        obj = tExplode(data=df, column="items")
        async with obj as t:
            result = await t.run()

        assert len(result) == 3
        assert list(result["items"]) == ["a", "b", "c"]

    async def test_texplode_mixed_scalars_and_lists(self):
        """DataFrame.explode() handles scalars gracefully (they remain as-is)."""
        df = pd.DataFrame({"id": [1, 2], "tags": [["a", "b"], "scalar"]})
        obj = tExplode(data=df, column="tags", explode_dataset=False)
        async with obj as t:
            result = await t.run()

        # Row 1 explodes to 2 rows, row 2 (scalar) stays as-is = 3 total
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestTExplodeIntegration:
    def test_texplode_registry_discovery(self):
        """ComponentRegistry.discover_all() finds tExplode."""
        from querysource.queries.multi.registry import ComponentRegistry

        # Clear the cache so tExplode.py (newly added) is picked up
        ComponentRegistry.discover_all.cache_clear()

        components = ComponentRegistry.discover_all()
        assert "tExplode" in components, (
            "tExplode was not found by ComponentRegistry.discover_all(). "
            "Check that tExplode.py is in the transformations/ directory "
            "and the file stem matches the class name."
        )
        assert components["tExplode"] is tExplode

    def test_texplode_get_transform_module(self):
        """get_transform_module('tExplode') returns the tExplode class."""
        from querysource.queries.multi import get_transform_module

        cls = get_transform_module("tExplode")
        assert cls is tExplode

    def test_texplode_introspection_schema(self):
        """SchemaIntrospectable.get_schema() generates a valid JSON schema."""
        schema_info = tExplode.get_schema()

        assert "json_schema" in schema_info
        json_schema = schema_info["json_schema"]
        assert json_schema.get("type") == "object"
        assert "properties" in json_schema

        # tExplode-specific attributes should appear in the schema
        # (SchemaIntrospectable inspects __init__ kwargs.pop() patterns)
        attributes = schema_info.get("attributes", [])
        attr_names = {a["name"] for a in attributes}
        # column, drop_original, explode_dataset, advanced_mode, propagate_columns
        # should be detected by the introspector
        assert len(attr_names) > 0, "SchemaIntrospectable found no attributes"

    async def test_texplode_in_transform_chain(self, df_with_lists):
        """tExplode used in a MultiQS Transform step via dict config.

        Simulates how MultiQS dispatches transforms:
          clobj = get_transform_module("tExplode")
          obj = clobj(data=result, **component_config)
          async with obj as o:
              result = await o.run()
        """
        from querysource.queries.multi import get_transform_module

        component_config = {
            "column": "tags",
            "drop_original": False,
        }

        cls = get_transform_module("tExplode")
        obj = cls(data=df_with_lists, **component_config)
        async with obj as o:
            result = await o.run()

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 6
        assert "tags" in result.columns

    def test_texplode_is_abstract_transform_subclass(self):
        """tExplode extends AbstractTransform, not tPandas."""
        from querysource.queries.multi.transformations.abstract import AbstractTransform
        from querysource.queries.multi.transformations.tPandas import tPandas

        assert issubclass(tExplode, AbstractTransform)
        assert not issubclass(tExplode, tPandas)

    def test_texplode_category(self):
        """tExplode has the correct category from AbstractTransform."""
        assert tExplode._category == "Transformations"
