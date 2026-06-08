"""Forecast transform — companion catalog + value_column->columns alias."""
import pandas as pd
import pytest

from querysource.queries.multi.transformations.Forecast import Forecast
from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Forecast" in catalog
    return catalog["Forecast"]


def test_required_and_anyof(entry):
    js = entry.json_schema
    assert js["required"] == ["index_column", "model"]
    anyof = {tuple(r["required"]) for r in js["anyOf"]}
    assert anyof == {("columns",), ("value_column",)}


def test_model_enum_bound_to_class(entry):
    enum = entry.json_schema["properties"]["model"]["enum"]
    assert enum == Forecast.supported_models()
    assert enum == ["ARIMA", "SARIMA", "Exponential"]


def test_order_is_3_int_tuple(entry):
    order = entry.json_schema["properties"]["order"]
    assert order["type"] == "array"
    assert order["minItems"] == 3 and order["maxItems"] == 3
    assert order["items"]["type"] == "integer"
    assert order["default"] == [1, 1, 1]


def test_value_column_promoted_to_columns():
    """The user's value_column example must set self.columns and not raise."""
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=6, freq="ME"),
        "revenue": [1, 2, 3, 4, 5, 6],
    })
    op = Forecast(data=df, index_column="date", value_column="revenue", model="ARIMA")
    assert op.columns == ["revenue"]


def test_explicit_columns_wins_over_alias():
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=3, freq="ME"),
        "a": [1, 2, 3],
    })
    op = Forecast(data=df, index_column="date", columns=["a"], value_column="ignored",
                  model="ARIMA")
    assert op.columns == ["a"]
