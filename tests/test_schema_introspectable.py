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


class TestTypedKwargsIntrospection:
    """``var: TYPE = kwargs.get|pop(...)`` introspection (follow-up improvement).

    The regex recovers the type annotation from local-variable and
    self-prefixed typed assignments inside ``__init__``, so attributes don't
    collapse to ``"Any"`` when the only type information lives there.
    """

    def test_typed_kwargs_capture_simple_types(self):
        from typing import List, Optional, Union

        class _Typed(SchemaIntrospectable):
            """Typed kwargs example."""

            def __init__(self, **kwargs) -> None:
                self._driver: str = kwargs.get("driver", "pg")
                self._count: int = kwargs.get("count", 0)
                self._enabled: bool = kwargs.pop("enabled", True)
                self._tags: List[str] = kwargs.get("tags", [])
                self._dsn: Optional[str] = kwargs.get("dsn")
                self._ids: Union[str, list] = kwargs.get("ids", None)

        attrs = {a["name"]: a for a in _Typed.get_attributes()}
        assert attrs["driver"]["type"] == "str"
        assert attrs["count"]["type"] == "int"
        assert attrs["enabled"]["type"] == "bool"
        assert attrs["tags"]["type"] == "List[str]"
        assert attrs["dsn"]["type"] == "Optional[str]"
        assert attrs["ids"]["type"] == "Union[str, list]"

        # ``dsn`` had no default — should be flagged required.
        assert attrs["dsn"]["required"] is True
        # The others have defaults — not required.
        assert attrs["driver"]["required"] is False
        assert attrs["enabled"]["required"] is False

    def test_typed_kwargs_drive_json_schema_types(self):
        from typing import List, Optional

        class _Schema(SchemaIntrospectable):
            """Schema-typed kwargs."""

            def __init__(self, **kwargs) -> None:
                driver: str = kwargs.get("driver", "pg") or "pg"
                self._pk: List[str] = kwargs.get("pk", []) or []
                self._dsn: Optional[str] = kwargs.get("dsn")
                method: str = (kwargs.get("method", "append") or "append").lower()
                _ = driver, method  # silence unused warnings

        props = _Schema.get_schema()["json_schema"]["properties"]
        assert props["driver"] == {"type": "string"}
        assert props["pk"] == {"type": "array", "items": {"type": "string"}}
        # Optional[str] becomes nullable.
        assert props["dsn"]["type"] == ["string", "null"]
        # The wrapped-in-parens form is recognised too.
        assert props["method"] == {"type": "string"}

    def test_typed_pattern_does_not_fold_across_lines(self):
        """Regression: the pattern was greedy across newlines and pulled the
        function signature's ``-> None:`` into the next assignment, capturing
        ``self.conditions`` as the "type" of an untyped kwarg.
        """

        class _Untyped(SchemaIntrospectable):
            """No annotations on these kwargs."""

            def __init__(self, **kwargs) -> None:
                self.conditions = kwargs.pop("conditions", None)
                self.fields: dict = kwargs.pop("fields", {})

        attrs = {a["name"]: a for a in _Untyped.get_attributes()}
        # ``conditions`` has no annotation — must stay ``Any``.
        assert attrs["conditions"]["type"] == "Any"
        # ``fields`` has a proper annotation — must be captured.
        assert attrs["fields"]["type"] == "dict"

    def test_pipe_union_types_become_nullable_when_none_present(self):
        from querysource.queries.multi._introspect import _type_to_json_schema

        # Optional[str] equivalent
        assert _type_to_json_schema("str | None")["type"] == ["string", "null"]
        # Multi-type union
        assert _type_to_json_schema("str | int")["type"] == ["string", "integer"]
        # Multi-type union with None
        assert _type_to_json_schema("str | int | None")["type"] == [
            "string", "integer", "null",
        ]

    def test_list_dict_generics_produce_proper_schema(self):
        from querysource.queries.multi._introspect import _type_to_json_schema

        assert _type_to_json_schema("List[int]") == {
            "type": "array", "items": {"type": "integer"},
        }
        assert _type_to_json_schema("list[str]") == {
            "type": "array", "items": {"type": "string"},
        }
        assert _type_to_json_schema("Dict[str, int]") == {"type": "object"}
        assert _type_to_json_schema("Optional[List[str]]") == {
            "type": ["array", "null"], "items": {"type": "string"},
        }
