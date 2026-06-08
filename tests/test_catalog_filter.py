"""Filter operator — companion catalog + conditions/filter_conditions wiring.

Two concerns:
  * The catalog entry (built from Filter.catalog.yaml) exposes the create_filter
    condition shape, the operator enum, and a ``filterFunc`` enum bound live to
    ``Filter.filter_functions()``.
  * ``conditions`` now feeds create_filter in run() (wired alongside ``filter``),
    and ``filter_conditions`` is populated from kwargs by AbstractMulti.__init__.
"""
import asyncio

import pandas as pd
import pytest

from querysource.queries.multi.operators.filter.flt import Filter
from querysource.queries.multi.registry import ComponentRegistry


# --------------------------------------------------------------------------- #
# Catalog / schema
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def filter_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Filter" in catalog, "Filter component missing from catalog"
    return catalog["Filter"]


def test_filterfunc_enum_bound_to_class(filter_entry):
    enum = filter_entry.json_schema["$defs"]["filterFunc"]["enum"]
    assert enum == Filter.filter_functions()
    assert "drop_rows" in enum and "drop_na" in enum
    # builders must be excluded
    assert "create_filter" not in enum and "build_condition" not in enum


def test_conditions_and_filter_are_condition_arrays(filter_entry):
    props = filter_entry.json_schema["properties"]
    for key in ("conditions", "filter"):
        assert props[key]["type"] == "array"
        assert props[key]["items"]["$ref"] == "#/$defs/filterCondition"


def test_operator_enum(filter_entry):
    assert filter_entry.json_schema["properties"]["operator"]["enum"] == ["&", "|"]


def test_filter_conditions_property_names_bound(filter_entry):
    fc = filter_entry.json_schema["properties"]["filter_conditions"]
    assert fc["propertyNames"]["$ref"] == "#/$defs/filterFunc"


# --------------------------------------------------------------------------- #
# Runtime wiring
# --------------------------------------------------------------------------- #
def test_conditions_now_filters_rows():
    """`conditions` feeds create_filter (combined with `filter`)."""
    df = pd.DataFrame({
        "status": ["active", "inactive", "active", "active"],
        "revenue": [2000, 5000, 300, 1500],
    })
    op = Filter(
        data=df.copy(),
        conditions=[
            {"column": "status", "expression": "==", "value": "active"},
            {"column": "revenue", "expression": ">", "value": 1000},
        ],
        operator="&",
    )
    out = asyncio.run(op.run())
    assert sorted(out["revenue"].tolist()) == [1500, 2000]


def test_filter_conditions_populated_from_kwargs_and_applied():
    """`filter_conditions` is set by AbstractMulti.__init__ and applied in run()."""
    df = pd.DataFrame({
        "status": ["active", "inactive", "active"],
        "revenue": [10, 20, 30],
    })
    op = Filter(data=df.copy(), filter_conditions={"drop_rows": {"status": ["inactive"]}})
    assert op.filter_conditions == {"drop_rows": {"status": ["inactive"]}}
    out = asyncio.run(op.run())
    assert "inactive" not in out["status"].tolist()
    assert sorted(out["revenue"].tolist()) == [10, 30]
