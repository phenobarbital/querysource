"""Unit and integration tests for the tDiff transformation component.

Tests cover:
- Init validation (missing pk raises DriverError)
- Config validation (invalid method, dtype_policy, include, hash+tolerance)
- Data extraction (requires dict of exactly 2 DataFrames, current/previous keys)
- PK validation (missing column, nulls, duplicates)
- Core diff: new, modified, deleted, unchanged row classification
- Composite primary keys
- compare_columns / ignore_columns filtering
- Schema drift detection
- changed_columns tracking (bool and custom name)
- Hash method
- Float tolerance (numpy.isclose)
- String normalization (strip_strings, case_sensitive)
- Dtype policy (strict vs coerce)
- include filtering (subset of change types, single string, unchanged)
- raise_on_empty
- Empty output (no changes with restricted include)
- Null-safe comparison
- Async context manager usage
- Integration: registry discovery, get_transform_module, transform chain
"""
import numpy as np
import pandas as pd
import pytest

from querysource.exceptions import DataNotFound, DriverError, QueryException
from querysource.queries.multi.transformations.tDiff import tDiff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def current_df():
    """Current (A) snapshot — 4 users, one new vs previous."""
    return pd.DataFrame({
        "user_id": [1, 2, 3, 4],
        "nombre": ["Alice", "Bob Updated", "Carol", "Dave"],
        "email": ["alice@x.com", "bob@x.com", "carol@x.com", "dave@x.com"],
    })


@pytest.fixture
def previous_df():
    """Previous (B) snapshot — user 4 absent, user 5 will be deleted."""
    return pd.DataFrame({
        "user_id": [1, 2, 3, 5],
        "nombre": ["Alice", "Bob", "Carol", "Eve"],
        "email": ["alice@x.com", "bob@x.com", "carol@x.com", "eve@x.com"],
    })


@pytest.fixture
def data_dict(current_df, previous_df):
    return {"current": current_df, "previous": previous_df}


@pytest.fixture
def identical_pair():
    """Two identical DataFrames — no changes expected."""
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "value": ["a", "b", "c"],
    })
    return {"a": df.copy(), "b": df.copy()}


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------

class TestTDiffInit:
    def test_pk_required(self):
        data = {"a": pd.DataFrame({"id": [1]}), "b": pd.DataFrame({"id": [1]})}
        with pytest.raises(DriverError, match="pk"):
            tDiff(data=data)

    def test_defaults(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id")
        assert obj.pk == "user_id"
        assert obj.compare_columns is None
        assert obj.ignore_columns == []
        assert obj.include == ["new", "modified", "deleted"]
        assert obj.change_column == "_change"
        assert obj.changed_columns is False
        assert obj.method == "compare"
        assert obj.float_tolerance is None
        assert obj.strip_strings is False
        assert obj.case_sensitive is True
        assert obj.dtype_policy == "strict"
        assert obj.raise_on_empty is False

    def test_all_kwargs_accepted(self, data_dict):
        obj = tDiff(
            data=data_dict,
            pk=["user_id"],
            compare_columns=["nombre"],
            ignore_columns=["email"],
            include=["new"],
            change_column="_status",
            changed_columns="_cols",
            method="hash",
            strip_strings=True,
            case_sensitive=False,
            dtype_policy="coerce",
            raise_on_empty=True,
            current="current",
            previous="previous",
        )
        assert obj.change_column == "_status"
        assert obj.changed_columns == "_cols"
        assert obj.method == "hash"
        assert obj.dtype_policy == "coerce"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class TestTDiffConfigValidation:
    async def test_invalid_method(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", method="bogus")
        with pytest.raises(DriverError, match="invalid method"):
            await obj.run()

    async def test_invalid_dtype_policy(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", dtype_policy="loose")
        with pytest.raises(DriverError, match="invalid dtype_policy"):
            await obj.run()

    async def test_invalid_include(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["new", "removed"])
        with pytest.raises(DriverError, match="invalid include"):
            await obj.run()

    async def test_hash_with_tolerance_rejected(self, data_dict):
        obj = tDiff(
            data=data_dict, pk="user_id",
            method="hash", float_tolerance={"rtol": 1e-3},
        )
        with pytest.raises(DriverError, match="incompatible"):
            await obj.run()

    async def test_include_as_string_coerced_to_list(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include="new")
        result = await obj.run()
        assert set(result["_change"].unique()) <= {"new"}


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

class TestTDiffExtractFrames:
    async def test_single_dataframe_rejected(self):
        df = pd.DataFrame({"id": [1], "v": ["a"]})
        obj = tDiff(data={"only": df}, pk="id")
        with pytest.raises(DriverError, match="exactly 2"):
            await obj.run()

    async def test_three_dataframes_rejected(self):
        df = pd.DataFrame({"id": [1], "v": ["a"]})
        obj = tDiff(data={"a": df, "b": df, "c": df}, pk="id")
        with pytest.raises(DriverError, match="exactly 2"):
            await obj.run()

    async def test_current_previous_keys(self):
        a = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
        b = pd.DataFrame({"id": [1, 3], "v": ["a", "c"]})
        obj = tDiff(
            data={"src_b": b, "src_a": a},
            pk="id", current="src_a", previous="src_b",
        )
        result = await obj.run()
        new_rows = result[result["_change"] == "new"]
        deleted_rows = result[result["_change"] == "deleted"]
        assert set(new_rows["id"]) == {2}
        assert set(deleted_rows["id"]) == {3}

    async def test_invalid_key_rejected(self):
        df = pd.DataFrame({"id": [1], "v": ["a"]})
        obj = tDiff(
            data={"a": df, "b": df},
            pk="id", current="x", previous="b",
        )
        with pytest.raises(DriverError, match="key 'x' not found"):
            await obj.run()


# ---------------------------------------------------------------------------
# PK validation
# ---------------------------------------------------------------------------

class TestTDiffPKValidation:
    async def test_missing_pk_column(self):
        a = pd.DataFrame({"id": [1], "v": ["a"]})
        b = pd.DataFrame({"other": [1], "v": ["a"]})
        obj = tDiff(data={"a": a, "b": b}, pk="id")
        with pytest.raises(DriverError, match="pk column 'id' is missing"):
            await obj.run()

    async def test_null_pk_rejected(self):
        a = pd.DataFrame({"id": [1, None], "v": ["a", "b"]})
        b = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
        obj = tDiff(data={"a": a, "b": b}, pk="id")
        with pytest.raises(DriverError, match="pk has nulls"):
            await obj.run()

    async def test_duplicate_pk_rejected(self):
        a = pd.DataFrame({"id": [1, 1], "v": ["a", "b"]})
        b = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
        obj = tDiff(data={"a": a, "b": b}, pk="id")
        with pytest.raises(DriverError, match="pk is duplicated"):
            await obj.run()


# ---------------------------------------------------------------------------
# Core diff — new / modified / deleted / unchanged
# ---------------------------------------------------------------------------

class TestTDiffCore:
    async def test_basic_diff(self, data_dict):
        """user 4 is new, user 2 modified, user 5 deleted, users 1 & 3 excluded (default include)."""
        obj = tDiff(data=data_dict, pk="user_id")
        result = await obj.run()

        changes = result.set_index("user_id")["_change"].to_dict()
        assert changes[4] == "new"
        assert changes[2] == "modified"
        assert changes[5] == "deleted"
        assert 1 not in changes
        assert 3 not in changes

    async def test_include_unchanged(self, data_dict):
        obj = tDiff(
            data=data_dict, pk="user_id",
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        changes = result.set_index("user_id")["_change"].to_dict()
        assert changes[1] == "unchanged"
        assert changes[3] == "unchanged"

    async def test_include_only_new(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["new"])
        result = await obj.run()
        assert set(result["_change"].unique()) == {"new"}
        assert set(result["user_id"]) == {4}

    async def test_include_only_deleted(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["deleted"])
        result = await obj.run()
        assert set(result["_change"].unique()) == {"deleted"}
        assert set(result["user_id"]) == {5}

    async def test_no_changes_returns_empty(self, identical_pair):
        obj = tDiff(data=identical_pair, pk="id")
        result = await obj.run()
        assert result.empty
        assert "_change" in result.columns

    async def test_new_rows_carry_a_values(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["new"])
        result = await obj.run()
        row = result[result["user_id"] == 4].iloc[0]
        assert row["nombre"] == "Dave"
        assert row["email"] == "dave@x.com"

    async def test_deleted_rows_carry_b_values(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["deleted"])
        result = await obj.run()
        row = result[result["user_id"] == 5].iloc[0]
        assert row["nombre"] == "Eve"
        assert row["email"] == "eve@x.com"

    async def test_modified_rows_carry_a_values(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", include=["modified"])
        result = await obj.run()
        row = result[result["user_id"] == 2].iloc[0]
        assert row["nombre"] == "Bob Updated"


# ---------------------------------------------------------------------------
# Composite primary key
# ---------------------------------------------------------------------------

class TestTDiffCompositeKey:
    async def test_composite_pk(self):
        a = pd.DataFrame({
            "org": ["X", "X", "Y"],
            "uid": [1, 2, 1],
            "score": [10, 20, 30],
        })
        b = pd.DataFrame({
            "org": ["X", "X", "Y"],
            "uid": [1, 2, 1],
            "score": [10, 99, 30],
        })
        obj = tDiff(data={"a": a, "b": b}, pk=["org", "uid"])
        result = await obj.run()
        mod = result[result["_change"] == "modified"]
        assert len(mod) == 1
        assert mod.iloc[0]["org"] == "X"
        assert mod.iloc[0]["uid"] == 2


# ---------------------------------------------------------------------------
# compare_columns / ignore_columns
# ---------------------------------------------------------------------------

class TestTDiffColumnSelection:
    async def test_compare_columns_limits_scope(self):
        a = pd.DataFrame({"id": [1], "name": ["A"], "notes": ["changed"]})
        b = pd.DataFrame({"id": [1], "name": ["A"], "notes": ["original"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            compare_columns=["name"],
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"

    async def test_ignore_columns_excludes(self):
        a = pd.DataFrame({"id": [1], "name": ["A"], "ts": ["2024-01-02"]})
        b = pd.DataFrame({"id": [1], "name": ["A"], "ts": ["2024-01-01"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            ignore_columns=["ts"],
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"

    async def test_compare_column_missing_raises(self):
        a = pd.DataFrame({"id": [1], "name": ["A"]})
        b = pd.DataFrame({"id": [1], "name": ["A"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            compare_columns=["nonexistent"],
        )
        with pytest.raises(DriverError, match="compare column 'nonexistent'"):
            await obj.run()


# ---------------------------------------------------------------------------
# Schema drift
# ---------------------------------------------------------------------------

class TestTDiffSchemaDrift:
    async def test_schema_drift_detected(self):
        a = pd.DataFrame({"id": [1], "col_a": [10], "common": [1]})
        b = pd.DataFrame({"id": [1], "col_b": [20], "common": [1]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            include=["new", "modified", "deleted", "unchanged"],
        )
        await obj.run()
        assert "col_a" in obj._schema_drift["only_in_current"]
        assert "col_b" in obj._schema_drift["only_in_previous"]


# ---------------------------------------------------------------------------
# changed_columns tracking
# ---------------------------------------------------------------------------

class TestTDiffChangedColumns:
    async def test_changed_columns_bool(self):
        a = pd.DataFrame({"id": [1], "x": [10], "y": ["hello"]})
        b = pd.DataFrame({"id": [1], "x": [99], "y": ["hello"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            changed_columns=True,
            include=["modified"],
        )
        result = await obj.run()
        assert "_changed_columns" in result.columns
        assert result.iloc[0]["_changed_columns"] == ["x"]

    async def test_changed_columns_custom_name(self):
        a = pd.DataFrame({"id": [1], "x": [10], "y": ["hello"]})
        b = pd.DataFrame({"id": [1], "x": [99], "y": ["world"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            changed_columns="_diffs",
            include=["modified"],
        )
        result = await obj.run()
        assert "_diffs" in result.columns
        assert set(result.iloc[0]["_diffs"]) == {"x", "y"}

    async def test_changed_columns_empty_for_unchanged(self):
        a = pd.DataFrame({"id": [1], "x": [10]})
        b = pd.DataFrame({"id": [1], "x": [10]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            changed_columns=True,
            include=["unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_changed_columns"] == []


# ---------------------------------------------------------------------------
# Hash method
# ---------------------------------------------------------------------------

class TestTDiffHashMethod:
    async def test_hash_detects_modification(self):
        a = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
        b = pd.DataFrame({"id": [1, 2], "val": ["a", "CHANGED"]})
        obj = tDiff(data={"a": a, "b": b}, pk="id", method="hash")
        result = await obj.run()
        mod = result[result["_change"] == "modified"]
        assert len(mod) == 1
        assert mod.iloc[0]["id"] == 2

    async def test_hash_no_changes(self, identical_pair):
        obj = tDiff(data=identical_pair, pk="id", method="hash")
        result = await obj.run()
        assert result.empty


# ---------------------------------------------------------------------------
# Float tolerance
# ---------------------------------------------------------------------------

class TestTDiffFloatTolerance:
    async def test_float_within_tolerance_unchanged(self):
        a = pd.DataFrame({"id": [1], "val": [1.0000]})
        b = pd.DataFrame({"id": [1], "val": [1.0001]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            float_tolerance={"atol": 1e-3},
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"

    async def test_float_outside_tolerance_modified(self):
        a = pd.DataFrame({"id": [1], "val": [1.0]})
        b = pd.DataFrame({"id": [1], "val": [2.0]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            float_tolerance={"atol": 0.01},
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "modified"


# ---------------------------------------------------------------------------
# String normalization
# ---------------------------------------------------------------------------

class TestTDiffStringNormalization:
    async def test_strip_strings(self):
        a = pd.DataFrame({"id": [1], "name": ["Alice"]})
        b = pd.DataFrame({"id": [1], "name": ["  Alice  "]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            strip_strings=True,
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"
        # output values are NOT stripped
        assert result.iloc[0]["name"] == "Alice"

    async def test_case_insensitive(self):
        a = pd.DataFrame({"id": [1], "name": ["alice"]})
        b = pd.DataFrame({"id": [1], "name": ["ALICE"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            case_sensitive=False,
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"


# ---------------------------------------------------------------------------
# Dtype policy
# ---------------------------------------------------------------------------

class TestTDiffDtypePolicy:
    async def test_strict_rejects_incompatible_dtypes(self):
        a = pd.DataFrame({"id": [1], "val": [10]})
        b = pd.DataFrame({"id": [1], "val": ["ten"]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            dtype_policy="strict",
        )
        with pytest.raises(DriverError, match="incompatible dtypes"):
            await obj.run()

    async def test_coerce_casts_b_to_a(self):
        a = pd.DataFrame({"id": [1], "val": [10.0]})
        b = pd.DataFrame({"id": [1], "val": [10]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            dtype_policy="coerce",
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"


# ---------------------------------------------------------------------------
# raise_on_empty
# ---------------------------------------------------------------------------

class TestTDiffRaiseOnEmpty:
    async def test_raise_on_empty_true(self, identical_pair):
        obj = tDiff(data=identical_pair, pk="id", raise_on_empty=True)
        with pytest.raises(DataNotFound, match="no changes"):
            await obj.run()

    async def test_raise_on_empty_false(self, identical_pair):
        obj = tDiff(data=identical_pair, pk="id", raise_on_empty=False)
        result = await obj.run()
        assert result.empty


# ---------------------------------------------------------------------------
# Null-safe comparison
# ---------------------------------------------------------------------------

class TestTDiffNullSafe:
    async def test_both_null_is_unchanged(self):
        a = pd.DataFrame({"id": [1], "val": [None]})
        b = pd.DataFrame({"id": [1], "val": [None]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"

    async def test_one_null_is_modified(self):
        a = pd.DataFrame({"id": [1], "val": pd.array(["hello"], dtype=object)})
        b = pd.DataFrame({"id": [1], "val": pd.array([None], dtype=object)})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            include=["modified"],
        )
        result = await obj.run()
        assert len(result) == 1
        assert result.iloc[0]["_change"] == "modified"

    async def test_nan_equality(self):
        a = pd.DataFrame({"id": [1], "val": [float("nan")]})
        b = pd.DataFrame({"id": [1], "val": [float("nan")]})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()
        assert result.iloc[0]["_change"] == "unchanged"


# ---------------------------------------------------------------------------
# Custom change_column name
# ---------------------------------------------------------------------------

class TestTDiffChangeColumn:
    async def test_custom_change_column(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id", change_column="_status")
        result = await obj.run()
        assert "_status" in result.columns
        assert "_change" not in result.columns


# ---------------------------------------------------------------------------
# Empty output with label column present
# ---------------------------------------------------------------------------

class TestTDiffEmptyOutput:
    async def test_empty_output_has_columns(self, identical_pair):
        obj = tDiff(data=identical_pair, pk="id", include=["new"])
        result = await obj.run()
        assert result.empty
        assert "_change" in result.columns
        assert "id" in result.columns
        assert "value" in result.columns

    async def test_empty_output_with_changed_columns(self, identical_pair):
        obj = tDiff(
            data=identical_pair, pk="id",
            include=["new"], changed_columns=True,
        )
        result = await obj.run()
        assert result.empty
        assert "_changed_columns" in result.columns


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------

class TestTDiffContextManager:
    async def test_async_context_manager(self, data_dict):
        obj = tDiff(data=data_dict, pk="user_id")
        async with obj as t:
            result = await t.run()
        assert isinstance(result, pd.DataFrame)
        assert not result.empty


# ---------------------------------------------------------------------------
# Large-ish realistic scenario
# ---------------------------------------------------------------------------

class TestTDiffRealistic:
    async def test_mixed_changes(self):
        """100 rows: 10 new, 10 deleted, 10 modified, 70 unchanged."""
        rng = np.random.default_rng(42)
        n = 100
        ids = list(range(1, n + 1))

        prev_ids = list(range(1, 91)) + list(range(101, 111))  # 90 common + 10 only-B
        prev_vals = [f"v{i}" for i in prev_ids]
        b = pd.DataFrame({"id": prev_ids, "val": prev_vals})

        cur_ids = list(range(1, 91)) + list(range(91, 101))  # 90 common + 10 only-A
        cur_vals = [f"v{i}" for i in cur_ids]
        # modify 10 of the common rows (ids 1-10)
        for i in range(10):
            cur_vals[i] = f"MODIFIED_{i}"

        a = pd.DataFrame({"id": cur_ids, "val": cur_vals})
        obj = tDiff(
            data={"a": a, "b": b}, pk="id",
            include=["new", "modified", "deleted", "unchanged"],
        )
        result = await obj.run()

        counts = result["_change"].value_counts().to_dict()
        assert counts.get("new", 0) == 10
        assert counts.get("modified", 0) == 10
        assert counts.get("deleted", 0) == 10
        assert counts.get("unchanged", 0) == 80


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestTDiffIntegration:
    def test_registry_discovery(self):
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()
        components = ComponentRegistry.discover_all()
        assert "tDiff" in components
        assert components["tDiff"] is tDiff

    def test_get_transform_module(self):
        from querysource.queries.multi import get_transform_module
        cls = get_transform_module("tDiff")
        assert cls is tDiff

    def test_is_abstract_transform_subclass(self):
        from querysource.queries.multi.transformations.abstract import AbstractTransform
        assert issubclass(tDiff, AbstractTransform)

    def test_category(self):
        assert tDiff._category == "Transformations"

    async def test_transform_chain_dispatch(self, data_dict):
        """Simulates how MultiQS dispatches transforms."""
        from querysource.queries.multi import get_transform_module
        config = {"pk": "user_id", "include": ["new", "modified", "deleted"]}
        cls = get_transform_module("tDiff")
        obj = cls(data=data_dict, **config)
        async with obj as o:
            result = await o.run()
        assert isinstance(result, pd.DataFrame)
        assert "_change" in result.columns
        assert len(result) > 0
