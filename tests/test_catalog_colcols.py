"""Catalog tests for the column-selection transforms: PluckCols, DropCols, FilterCols."""
import pytest

from querysource.queries.multi.transformations.FilterCols import (
    FilterCols,
    SUPPORTED_EXPRESSIONS,
)
from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def catalog():
    return {c.name: c for c in ComponentRegistry.get_catalog()}


@pytest.mark.parametrize("name", ["PluckCols", "DropCols"])
def test_name_selectors_anyof_and_types(catalog, name):
    entry = catalog[name]
    js = entry.json_schema
    # at least one selector required -> anyOf over the 5 selectors
    required_keys = {r["required"][0] for r in js["anyOf"]}
    assert required_keys == {"columns", "pattern", "regex", "startswith", "endswith"}
    props = js["properties"]
    for list_sel in ("columns", "startswith", "endswith"):
        assert props[list_sel]["$ref"] == "#/$defs/stringList"
    assert js["$defs"]["stringList"]["items"]["type"] == "string"
    for str_sel in ("pattern", "regex"):
        assert props[str_sel]["type"] == "string"


def test_filtercols_expression_enum_bound(catalog):
    entry = catalog["FilterCols"]
    js = entry.json_schema
    assert js["required"] == ["expression"]
    enum = js["properties"]["expression"]["enum"]
    assert enum == FilterCols.supported_expressions()
    assert set(enum) == set(SUPPORTED_EXPRESSIONS)
