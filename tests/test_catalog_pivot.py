"""Catalog tests for the pivot and crosstab transforms."""
import pytest

from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def catalog():
    return {c.name: c for c in ComponentRegistry.get_catalog()}


@pytest.mark.parametrize("name", ["pivot", "crosstab"])
def test_index_columns_required_and_columnref(catalog, name):
    js = catalog[name].json_schema
    assert js["required"] == ["index", "columns"]
    for col in ("index", "columns", "values"):
        assert js["properties"][col]["$ref"] == "#/$defs/columnRef"
    # columnRef is string-or-list
    one_of = js["$defs"]["columnRef"]["oneOf"]
    assert {o.get("type") for o in one_of} == {"string", "array"}


def test_pivot_type_enum_and_extras(catalog):
    js = catalog["pivot"].json_schema
    assert js["properties"]["type"]["enum"] == ["pivot", "crosstab"]
    assert js["properties"]["aggregate"]["default"] == "first"
    assert js["properties"]["multilevel"]["type"] == "boolean"
    assert js["properties"]["totals"]["required"] == ["name"]


def test_crosstab_type_enum_default(catalog):
    js = catalog["crosstab"].json_schema
    assert js["properties"]["type"]["enum"] == ["crosstab", "pivot"]
    assert js["properties"]["aggregate"]["default"] == "count"
