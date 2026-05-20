"""Unit tests for AbstractMulti base class (TASK-660)."""
import pytest
from querysource.queries.multi.abstract import AbstractMulti


class ConcreteStep(AbstractMulti):
    """A test step.

    Usage: Used for testing the AbstractMulti base class.
    """
    _category = "TestCategory"
    test_attr: str = "default"

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)

    async def start(self):
        pass

    async def run(self):
        return self.data


class TestAbstractMultiInit:
    def test_sets_data(self):
        step = ConcreteStep(data={"key": "value"})
        assert step.data == {"key": "value"}

    def test_sets_kwargs_as_attrs(self):
        step = ConcreteStep(data={}, custom="val")
        assert step.custom == "val"

    def test_multiple_kwargs(self):
        step = ConcreteStep(data={}, x=1, y=2, z="hello")
        assert step.x == 1
        assert step.y == 2
        assert step.z == "hello"


class TestAbstractMultiContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        step = ConcreteStep(data={})
        async with step as s:
            assert s is step

    @pytest.mark.asyncio
    async def test_aexit_raises_query_exception_on_error(self):
        from querysource.exceptions import QueryException
        step = ConcreteStep(data={})
        with pytest.raises(QueryException):
            async with step:
                raise ValueError("test error")


class TestGetAttributes:
    def test_returns_typed_list(self):
        attrs = ConcreteStep.get_attributes()
        names = [a["name"] for a in attrs]
        assert "test_attr" in names

    def test_fallback_any_for_untyped(self):
        attrs = ConcreteStep.get_attributes()
        for a in attrs:
            assert "type" in a

    def test_each_attr_has_required_keys(self):
        attrs = ConcreteStep.get_attributes()
        for a in attrs:
            assert "name" in a
            assert "type" in a
            assert "default" in a
            assert "required" in a

    def test_returns_list(self):
        attrs = ConcreteStep.get_attributes()
        assert isinstance(attrs, list)


class TestGetSchema:
    def test_json_schema_format(self):
        schema = ConcreteStep.get_schema()
        assert "json_schema" in schema
        assert schema["json_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_simplified_attributes(self):
        schema = ConcreteStep.get_schema()
        assert "attributes" in schema
        assert isinstance(schema["attributes"], list)

    def test_json_schema_has_type_object(self):
        schema = ConcreteStep.get_schema()
        assert schema["json_schema"]["type"] == "object"

    def test_json_schema_has_title(self):
        schema = ConcreteStep.get_schema()
        assert schema["json_schema"]["title"] == "ConcreteStep"

    def test_json_schema_has_properties(self):
        schema = ConcreteStep.get_schema()
        assert "properties" in schema["json_schema"]

    def test_json_schema_has_required(self):
        schema = ConcreteStep.get_schema()
        assert "required" in schema["json_schema"]
        assert isinstance(schema["json_schema"]["required"], list)


class TestGetDescription:
    def test_extracts_name(self):
        desc = ConcreteStep.get_description()
        assert desc["name"] == "ConcreteStep"

    def test_extracts_category(self):
        desc = ConcreteStep.get_description()
        assert desc["category"] == "TestCategory"

    def test_extracts_description(self):
        desc = ConcreteStep.get_description()
        assert "test step" in desc["description"].lower()

    def test_extracts_usage(self):
        desc = ConcreteStep.get_description()
        assert "testing" in desc["usage"].lower()

    def test_has_all_keys(self):
        desc = ConcreteStep.get_description()
        for key in ("name", "description", "usage", "category", "example"):
            assert key in desc


class TestAbstractMethod:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AbstractMulti(data={})  # type: ignore[abstract]
