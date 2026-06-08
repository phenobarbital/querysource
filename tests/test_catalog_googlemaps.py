"""Catalog test for the GoogleMaps transform + ``type`` dispatch wiring."""
import pandas as pd
import pytest

from querysource.queries.multi.transformations.google.maps import GoogleMaps
from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "GoogleMaps" in catalog
    return catalog["GoogleMaps"]


def test_map_size_false_positive_removed(entry):
    """map_size came from a commented line; it must not appear."""
    assert "map_size" not in entry.json_schema["properties"]
    assert "map_size" not in [a.name for a in entry.attributes]


def test_type_enum_bound_to_class(entry):
    enum = entry.json_schema["properties"]["type"]["enum"]
    assert enum == GoogleMaps.supported_route_types()
    assert enum == ["get_route", "waypoint_route"]


@pytest.mark.parametrize("t", ["get_route", "waypoint_route"])
def test_type_dispatch_resolves(t):
    g = GoogleMaps(data=pd.DataFrame({"x": [1]}), type=t)
    assert g._route_type == t


def test_type_defaults_to_get_route():
    g = GoogleMaps(data=pd.DataFrame({"x": [1]}))
    assert g._route_type == "get_route"


def test_invalid_type_rejected():
    from querysource.exceptions import DriverError
    with pytest.raises(DriverError):
        GoogleMaps(data=pd.DataFrame({"x": [1]}), type="bogus")


def test_expected_attrs(entry):
    names = {a.name for a in entry.attributes}
    assert {"zoom", "map_scale", "timestamp_key", "departure_time", "type"} <= names
