"""GroupBy catalog tests — companion + class-bound aggregation enum.

The ``aggFunc`` enum in GroupBy.catalog.yaml is declared as
``enum_from_class: aggregation_functions`` and resolved by the companion-doc
builder against ``GroupBy.aggregation_functions()``. These tests lock that
binding so the documented enum can never drift from the code's accepted set.
"""
import pytest

from querysource.queries.multi.operators.GroupBy import GroupBy
from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def groupby_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "GroupBy" in catalog, "GroupBy component missing from catalog"
    return catalog["GroupBy"]


def test_aggfunc_enum_is_bound_to_class(groupby_entry):
    """The generated aggFunc enum equals the live class source of truth."""
    enum = groupby_entry.json_schema["$defs"]["aggFunc"]["enum"]
    assert enum == GroupBy.aggregation_functions()
    # directive must be fully resolved, never leaked into the output
    assert "enum_from_class" not in groupby_entry.json_schema["$defs"]["aggFunc"]


def test_aggfunc_includes_special_function(groupby_entry):
    enum = groupby_entry.json_schema["$defs"]["aggFunc"]["enum"]
    assert "avg_first_last" in enum  # from supported_functions
    assert "sum" in enum and "mean" in enum and "count" in enum


def test_columns_values_reference_aggfunc(groupby_entry):
    """``columns`` values accept a single aggFunc or a list of them."""
    cols = groupby_entry.json_schema["properties"]["columns"]
    one_of = cols["additionalProperties"]["oneOf"]
    refs = [s.get("$ref") for s in one_of] + [
        s.get("items", {}).get("$ref") for s in one_of
    ]
    assert "#/$defs/aggFunc" in refs


def test_by_is_array_of_strings(groupby_entry):
    by = groupby_entry.json_schema["properties"]["by"]
    assert by["type"] == "array"
    assert by["items"]["type"] == "string"
