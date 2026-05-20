"""Backward-compatibility tests for TASK-661 — Refactor Existing Abstract Classes."""
import pytest
from querysource.queries.multi.abstract import AbstractMulti
from querysource.queries.multi.transformations.abstract import AbstractTransform
from querysource.queries.multi.operators.abstract import AbstractOperator
from querysource.queries.multi.components.abstract import AbstractComponent


class TestInheritance:
    def test_transform_inherits_abstract_multi(self):
        assert issubclass(AbstractTransform, AbstractMulti)

    def test_operator_inherits_abstract_multi(self):
        assert issubclass(AbstractOperator, AbstractMulti)

    def test_component_inherits_abstract_multi(self):
        assert issubclass(AbstractComponent, AbstractMulti)


class TestCategory:
    def test_transform_category(self):
        assert AbstractTransform._category == "Transformations"

    def test_operator_category(self):
        assert AbstractOperator._category == "Operators"

    def test_component_category(self):
        assert AbstractComponent._category == "Components"


class TestBackwardCompat:
    def test_concat_instantiates(self):
        from querysource.queries.multi.operators.Concat import Concat
        op = Concat(data={"a": [1, 2], "b": [3, 4]})
        assert op.data is not None

    def test_map_instantiates(self):
        from querysource.queries.multi.transformations.Map import Map
        t = Map(data={"a": [1]}, fields={"x": "y"})
        assert t.data is not None

    def test_introspection_available(self):
        from querysource.queries.multi.operators.Concat import Concat
        schema = Concat.get_schema()
        assert "json_schema" in schema
        assert "attributes" in schema

    def test_transform_introspection_available(self):
        from querysource.queries.multi.transformations.Map import Map
        schema = Map.get_schema()
        assert "json_schema" in schema
        assert "attributes" in schema

    def test_operator_get_description(self):
        from querysource.queries.multi.operators.Concat import Concat
        desc = Concat.get_description()
        assert desc["name"] == "Concat"
        assert desc["category"] == "Operators"

    def test_transform_get_description(self):
        from querysource.queries.multi.transformations.Map import Map
        desc = Map.get_description()
        assert desc["name"] == "Map"
        assert desc["category"] == "Transformations"


class TestNoDuplicateBoilerplate:
    """Verify that abstract classes delegate to AbstractMulti for shared methods."""

    def test_transform_has_no_aenter(self):
        # AbstractTransform should NOT define its own __aenter__ — inherited from AbstractMulti
        assert '__aenter__' not in AbstractTransform.__dict__

    def test_operator_has_no_aenter(self):
        assert '__aenter__' not in AbstractOperator.__dict__

    def test_component_has_no_aenter(self):
        assert '__aenter__' not in AbstractComponent.__dict__

    def test_transform_has_no_aexit(self):
        assert '__aexit__' not in AbstractTransform.__dict__

    def test_operator_has_no_aexit(self):
        assert '__aexit__' not in AbstractOperator.__dict__

    def test_component_has_no_aexit(self):
        assert '__aexit__' not in AbstractComponent.__dict__

    def test_operator_has_no_print_info(self):
        # _print_info should be inherited from AbstractMulti, not redefined
        assert '_print_info' not in AbstractOperator.__dict__

    def test_component_has_no_print_info(self):
        assert '_print_info' not in AbstractComponent.__dict__
