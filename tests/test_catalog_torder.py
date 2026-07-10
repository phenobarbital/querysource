"""Catalog test for the tOrder transform."""
import pytest

from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "tOrder" in catalog
    return catalog["tOrder"]


def test_columns_required_columnref(entry):
    js = entry.json_schema
    assert js["required"] == ["columns"]
    assert js["properties"]["columns"]["$ref"] == "#/$defs/columnRef"
    assert {o.get("type") for o in js["$defs"]["columnRef"]["oneOf"]} == {"string", "array"}


def test_ascending_bool_or_bool_list(entry):
    one_of = entry.json_schema["properties"]["ascending"]["oneOf"]
    assert one_of[0]["type"] == "boolean"
    assert one_of[1]["type"] == "array"
    assert one_of[1]["items"]["type"] == "boolean"


def test_na_position_enum(entry):
    assert entry.json_schema["properties"]["na_position"]["enum"] == ["first", "last"]
