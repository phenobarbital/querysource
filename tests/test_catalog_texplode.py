"""Catalog test for the tExplode transform."""
import pytest

from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "tExplode" in catalog
    return catalog["tExplode"]


def test_column_required(entry):
    assert entry.json_schema["required"] == ["column"]
    assert entry.json_schema["properties"]["column"]["type"] == "string"


def test_flag_types(entry):
    props = entry.json_schema["properties"]
    for flag in ("drop_original", "explode_dataset", "advanced_mode"):
        assert props[flag]["type"] == "boolean"


def test_propagate_columns_is_string_array(entry):
    pc = entry.json_schema["properties"]["propagate_columns"]
    assert pc["type"] == "array"
    assert pc["items"]["type"] == "string"
