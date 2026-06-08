"""Unit tests for the ``Query`` catalog and schema (TASK-698).

The ``Query`` documentation now lives in the sibling companion file
``query.catalog.yaml`` (hybrid model) instead of a ``ThreadQuery._catalog``
class attribute. These tests assert on the merged catalog entry produced by
``ComponentRegistry`` so they verify the end-to-end result regardless of where
the source documentation lives.
"""
import pytest

from querysource.queries.multi.registry import ComponentRegistry


@pytest.fixture(scope="module")
def query_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Query" in catalog, "Query component missing from catalog"
    return catalog["Query"]


class TestCatalogRemoteKeys:
    def test_remote_attribute_in_catalog(self, query_entry):
        attr_names = [a.name for a in query_entry.attributes]
        assert "remote" in attr_names
        assert "worker" in attr_names

    def test_remote_attribute_is_bool(self, query_entry):
        attrs = {a.name: a for a in query_entry.attributes}
        assert attrs["remote"].type == "bool"
        assert attrs["remote"].required is False
        assert attrs["remote"].default is False

    def test_worker_attribute_is_str(self, query_entry):
        attrs = {a.name: a for a in query_entry.attributes}
        assert attrs["worker"].type == "str"
        assert attrs["worker"].required is False
        assert attrs["worker"].default is None

    def test_remote_in_json_schema(self, query_entry):
        props = query_entry.json_schema["properties"]
        assert "remote" in props
        assert props["remote"]["type"] == "boolean"
        assert "worker" in props
        assert props["worker"]["type"] == "string"

    def test_example_shows_remote_query(self, query_entry):
        assert "remote" in query_entry.example
        assert "worker" in query_entry.example

    def test_oneof_mutual_exclusion(self, query_entry):
        """slug/query mutual exclusion is preserved from the companion schema."""
        one_of = query_entry.json_schema.get("oneOf")
        assert one_of == [{"required": ["slug"]}, {"required": ["query"]}]
