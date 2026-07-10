"""Map transform — companion catalog + class-bound transform-function enum."""
import asyncio

import pandas as pd
import pytest

from querysource.queries.multi.transformations.Map import Map
from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def map_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Map" in catalog, "Map component missing from catalog"
    return catalog["Map"]


def test_fields_is_required(map_entry):
    assert map_entry.json_schema["required"] == ["fields"]


def test_transformfunc_enum_bound_to_class(map_entry):
    enum = map_entry.json_schema["$defs"]["transformFunc"]["enum"]
    assert enum == Map.transform_functions()
    assert "getFunction" not in enum  # resolver helper excluded
    assert "rename_column" in enum and "concat" in enum


def test_fields_value_union_forms(map_entry):
    one_of = map_entry.json_schema["properties"]["fields"]["additionalProperties"]["oneOf"]
    types = [o.get("type") for o in one_of]
    assert types == ["string", "object", "array"]
    # the object form constrains its keys to transform functions
    obj_form = next(o for o in one_of if o.get("type") == "object")
    assert obj_form["propertyNames"]["$ref"] == "#/$defs/transformFunc"


# --------------------------------------------------------------------------- #
# Runtime wiring (reset_index / replace_columns)
# --------------------------------------------------------------------------- #
def _df():
    return pd.DataFrame({"name": ["a", "b"], "revenue": [10, 20]})


def test_rename_keeps_source_by_default():
    """Default replace_columns=False keeps the source column alongside the target."""
    op = Map(data=_df(), fields={"customer_name": "name"})
    out = asyncio.run(op.run())
    assert "customer_name" in out.columns
    assert "name" in out.columns  # source preserved


def test_replace_columns_drops_source():
    """replace_columns=True drops the source column after copying."""
    op = Map(data=_df(), fields={"customer_name": "name"}, replace_columns=True)
    out = asyncio.run(op.run())
    assert "customer_name" in out.columns
    assert "name" not in out.columns


def test_reset_index_applied_when_true():
    df = _df()
    df.index = [5, 9]  # non-default index
    op = Map(data=df, fields={"customer_name": "name"}, reset_index=True)
    out = asyncio.run(op.run())
    assert list(out.index) == [0, 1]


def test_reset_index_off_by_default():
    df = _df()
    df.index = [5, 9]
    op = Map(data=df, fields={"customer_name": "name"})
    out = asyncio.run(op.run())
    assert list(out.index) == [5, 9]
