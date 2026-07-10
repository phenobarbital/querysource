"""Catalog test for the correlation transform."""
import pytest

from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "correlation" in catalog
    return catalog["correlation"]


def test_columns_required_array_of_strings(entry):
    js = entry.json_schema
    assert js["required"] == ["columns"]
    cols = js["properties"]["columns"]
    assert cols["type"] == "array" and cols["items"]["type"] == "string"


def test_method_enum(entry):
    assert entry.json_schema["properties"]["method"]["enum"] == [
        "pearson", "kendall", "spearman"
    ]


def test_grouped_shape(entry):
    grouped = entry.json_schema["properties"]["grouped"]
    assert grouped["type"] == "object"
    assert grouped["required"] == ["column", "col_name"]
