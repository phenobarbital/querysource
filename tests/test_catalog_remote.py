"""Unit tests for ThreadQuery catalog and schema update (TASK-698)."""
from querysource.queries.multi.sources.query import ThreadQuery


class TestCatalogRemoteKeys:
    def test_remote_attribute_in_catalog(self):
        attr_names = [a["name"] for a in ThreadQuery._catalog["attributes"]]
        assert "remote" in attr_names
        assert "worker" in attr_names

    def test_remote_attribute_is_bool(self):
        attrs = {a["name"]: a for a in ThreadQuery._catalog["attributes"]}
        assert attrs["remote"]["type"] == "bool"
        assert attrs["remote"]["required"] is False
        assert attrs["remote"]["default"] is False

    def test_worker_attribute_is_str(self):
        attrs = {a["name"]: a for a in ThreadQuery._catalog["attributes"]}
        assert attrs["worker"]["type"] == "str"
        assert attrs["worker"]["required"] is False
        assert attrs["worker"]["default"] is None

    def test_remote_in_json_schema(self):
        props = ThreadQuery._catalog["json_schema"]["properties"]
        assert "remote" in props
        assert props["remote"]["type"] == "boolean"
        assert "worker" in props
        assert props["worker"]["type"] == "string"

    def test_example_shows_remote_query(self):
        example = ThreadQuery._catalog["example"]
        assert "remote" in example
        assert "worker" in example
