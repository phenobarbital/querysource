"""Smoke tests for route registration (TASK-666)."""
import pytest


class TestRouteRegistration:
    def test_services_imports_query_source(self):
        from querysource.services import QuerySource
        assert QuerySource is not None

    def test_component_handler_importable(self):
        from querysource.handlers.components import ComponentHandler
        handler = ComponentHandler()
        assert hasattr(handler, 'list_components')
        assert hasattr(handler, 'validate_pipeline')

    def test_component_handler_in_handlers_package(self):
        import querysource.handlers.components as m
        assert hasattr(m, 'ComponentHandler')
