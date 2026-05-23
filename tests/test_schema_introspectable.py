"""Unit tests for the SchemaIntrospectable mixin (TASK-667)."""
from __future__ import annotations

import pytest

from querysource.queries.multi._introspect import SchemaIntrospectable


class _Fake(SchemaIntrospectable):
    """Test class.

    Usage: Fake(foo='hello', bar=1)

    Example:
        {"foo": "hello"}
    """

    _category = "Destinations"

    def __init__(self, **kwargs) -> None:
        self._foo = kwargs.pop("foo", "default-foo")
        self._bar = kwargs.pop("bar", 42)


class TestSchemaIntrospectable:
    def test_get_attributes_lists_kwarg_pops(self):
        attrs = _Fake.get_attributes()
        names = {a["name"] for a in attrs}
        assert {"foo", "bar"}.issubset(names)

    def test_get_attributes_includes_defaults(self):
        attrs = {a["name"]: a for a in _Fake.get_attributes()}
        assert attrs["foo"]["default"] == "default-foo"
        assert attrs["bar"]["default"] == 42

    def test_get_schema_returns_json_schema_and_attributes(self):
        schema = _Fake.get_schema()
        assert schema["json_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["json_schema"]["title"] == "_Fake"
        assert "foo" in schema["json_schema"]["properties"]
        assert "bar" in schema["json_schema"]["properties"]

    def test_get_description_reads_category(self):
        desc = _Fake.get_description()
        assert desc["name"] == "_Fake"
        assert desc["category"] == "Destinations"
        assert desc["description"].startswith("Test class")

    def test_skips_backend_kwarg(self):
        class _WithBackend(SchemaIntrospectable):
            def __init__(self, **kwargs):
                self._backend = kwargs.pop("backend", "sqlite")
                self._x = kwargs.pop("x", 1)
        attrs = {a["name"] for a in _WithBackend.get_attributes()}
        assert "backend" not in attrs
        assert "x" in attrs

    def test_default_category_is_components(self):
        class _Plain(SchemaIntrospectable):
            def __init__(self, **kwargs):
                self._z = kwargs.pop("z", 0)
        assert _Plain._category == "Components"

    def test_get_schema_structure(self):
        schema = _Fake.get_schema()
        assert "json_schema" in schema
        assert "attributes" in schema
        assert isinstance(schema["attributes"], list)


class TestDescribeClass:
    """Tests for the module-level describe_class helper."""

    def test_returns_icon_default_for_category(self):
        from querysource.queries.multi._introspect import describe_class

        class _Op:
            """Some operator."""
            _category = "Operators"

        desc = describe_class(_Op)
        assert desc["icon"] == "git-merge"

    def test_class_icon_attribute_overrides_default(self):
        from querysource.queries.multi._introspect import describe_class

        class _Custom:
            """Custom component."""
            _category = "Sources"
            _icon = "airtable"

        desc = describe_class(_Custom)
        assert desc["icon"] == "airtable"

    def test_example_is_string(self):
        from querysource.queries.multi._introspect import describe_class

        class _WithExample:
            """A class with a JSON example.

            Example:
                {
                    "Step": {
                        "key": "value"
                    }
                }
            """
            _category = "Operators"

        desc = describe_class(_WithExample)
        assert isinstance(desc["example"], str)
        assert '"Step"' in desc["example"]
        # Closing braces are preserved (regression: brace-balanced parsing).
        assert desc["example"].count("{") == desc["example"].count("}")

    def test_usage_falls_back_to_body_when_no_usage_section(self):
        from querysource.queries.multi._introspect import describe_class

        class _NoUsageSection:
            """One-line summary.

            This is the body paragraph that should become the usage
            fallback when no explicit Usage: section is present.

            Args:
                x: something.
            """
            _category = "Sources"

        desc = describe_class(_NoUsageSection)
        assert "body paragraph" in desc["usage"]
        assert "Args" not in desc["usage"]

    def test_literal_block_becomes_example_when_no_explicit_example(self):
        from querysource.queries.multi._introspect import describe_class

        class _LiteralOnly:
            """One-line summary.

            YAML configuration example::

                Output:
                  - Foo:
                      bar: baz

            Trailing prose paragraph.
            """
            _category = "Destinations"

        desc = describe_class(_LiteralOnly)
        assert "Output:" in desc["example"]
        assert "bar: baz" in desc["example"]
        # The trailing prose should become usage.
        assert "Trailing prose paragraph" in desc["usage"]
