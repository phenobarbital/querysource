"""Unit tests for ComponentHandler (TASK-665)."""
import pytest
from querysource.handlers.components import ComponentHandler


class TestComponentHandler:
    def test_handler_instantiates(self):
        handler = ComponentHandler()
        assert hasattr(handler, 'list_components')
        assert hasattr(handler, 'validate_pipeline')

    def test_list_components_is_coroutine(self):
        import asyncio
        handler = ComponentHandler()
        assert asyncio.iscoroutinefunction(handler.list_components)

    def test_validate_pipeline_is_coroutine(self):
        import asyncio
        handler = ComponentHandler()
        assert asyncio.iscoroutinefunction(handler.validate_pipeline)

    def test_handler_importable(self):
        from querysource.handlers.components import ComponentHandler
        assert ComponentHandler is not None
