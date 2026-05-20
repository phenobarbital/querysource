"""Unit tests for ComponentHandler (TASK-665)."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

from querysource.handlers.components import ComponentHandler


def _make_request(method="GET", path="/api/v3/qs/components", query_string="", body=None):
    """Build a minimal mock aiohttp request."""
    request = MagicMock(spec=web.Request)
    request.method = method
    request.path = path
    # Mock rel_url with query support
    url_mock = MagicMock()
    url_mock.query = {}
    if query_string:
        # Parse a simple key=value query string
        for kv in query_string.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                url_mock.query[k] = v
    request.rel_url = url_mock
    # Mock app for PBAC (no security = fast-path no-op)
    app_mock = MagicMock()
    app_mock.get = MagicMock(return_value=None)  # security=None → PBAC disabled
    request.app = app_mock
    if body is not None:
        request.json = AsyncMock(return_value=body)
    else:
        request.json = AsyncMock(return_value={})
    return request


def _make_component_info(name="TestOp", category="Operators"):
    """Build a minimal real ComponentInfo dataclass instance."""
    from querysource.queries.multi.registry import ComponentInfo
    return ComponentInfo(
        name=name,
        category=category,
        description="Test description",
        usage="Test usage",
        attributes=[],
        json_schema={},
        example={},
    )


class TestComponentHandlerBasic:
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


class TestListComponents:
    @pytest.mark.asyncio
    async def test_list_components_returns_200(self):
        """list_components returns a 200 JSON response."""
        handler = ComponentHandler()
        request = _make_request()
        mock_info = _make_component_info("Join", "Operators")

        with patch(
            "querysource.handlers.components.ComponentRegistry.get_catalog",
            return_value=[mock_info],
        ):
            response = await handler.list_components(request)

        assert response.status == 200
        assert response.content_type == "application/json"

    @pytest.mark.asyncio
    async def test_list_components_returns_json_list(self):
        """list_components body is a JSON array."""
        handler = ComponentHandler()
        request = _make_request()
        mock_info = _make_component_info("Concat", "Operators")

        with patch(
            "querysource.handlers.components.ComponentRegistry.get_catalog",
            return_value=[mock_info],
        ):
            response = await handler.list_components(request)

        body = json.loads(response.body)
        assert isinstance(body, list)

    @pytest.mark.asyncio
    async def test_list_components_category_filter(self):
        """?category= query param filters results by category."""
        handler = ComponentHandler()
        request = _make_request(query_string="category=Transformations")

        op_info = _make_component_info("Join", "Operators")
        tr_info = _make_component_info("Pivot", "Transformations")

        with patch(
            "querysource.handlers.components.ComponentRegistry.get_catalog",
            return_value=[op_info, tr_info],
        ):
            response = await handler.list_components(request)

        assert response.status == 200
        body = json.loads(response.body)
        # After filtering, only Transformations category should remain
        assert all(item["category"] == "Transformations" for item in body)

    @pytest.mark.asyncio
    async def test_list_components_exception_returns_500(self):
        """Registry exceptions result in a 500 response."""
        handler = ComponentHandler()
        request = _make_request()

        with patch(
            "querysource.handlers.components.ComponentRegistry.get_catalog",
            side_effect=RuntimeError("registry exploded"),
        ):
            response = await handler.list_components(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body


class TestValidatePipeline:
    @pytest.mark.asyncio
    async def test_validate_valid_pipeline_returns_200(self):
        """validate_pipeline returns 200 with valid=True for a valid pipeline."""
        from querysource.queries.multi.registry import ValidationResult
        handler = ComponentHandler()
        payload = {"queries": {"q1": {}}, "Join": {"left": "q1", "right": "q2"}}
        request = _make_request(method="POST", body=payload)

        mock_result = ValidationResult(valid=True, errors=[])

        with patch(
            "querysource.handlers.components.ComponentRegistry.validate_pipeline",
            return_value=mock_result,
        ):
            response = await handler.validate_pipeline(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["valid"] is True
        assert body["errors"] == []

    @pytest.mark.asyncio
    async def test_validate_invalid_pipeline_returns_200_with_errors(self):
        """validate_pipeline returns 200 with valid=False for an invalid pipeline."""
        from querysource.queries.multi.registry import ValidationResult, ValidationError
        handler = ComponentHandler()
        payload = {}  # missing sources
        request = _make_request(method="POST", body=payload)

        mock_result = ValidationResult(
            valid=False,
            errors=[ValidationError(step="pipeline", field="queries", message="Missing source")],
        )

        with patch(
            "querysource.handlers.components.ComponentRegistry.validate_pipeline",
            return_value=mock_result,
        ):
            response = await handler.validate_pipeline(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["valid"] is False
        assert len(body["errors"]) == 1
        assert body["errors"][0]["step"] == "pipeline"

    @pytest.mark.asyncio
    async def test_validate_invalid_json_returns_400(self):
        """validate_pipeline returns 400 when body is not valid JSON."""
        handler = ComponentHandler()
        request = _make_request(method="POST")
        request.json = AsyncMock(side_effect=Exception("not JSON"))

        response = await handler.validate_pipeline(request)

        assert response.status == 400
        body = json.loads(response.body)
        assert body["valid"] is False
        assert any("Invalid JSON" in e["message"] for e in body["errors"])

    @pytest.mark.asyncio
    async def test_validate_registry_exception_returns_500(self):
        """Registry exceptions during validate_pipeline result in a 500 response."""
        handler = ComponentHandler()
        payload = {"queries": {"q1": {}}}
        request = _make_request(method="POST", body=payload)

        with patch(
            "querysource.handlers.components.ComponentRegistry.validate_pipeline",
            side_effect=RuntimeError("unexpected failure"),
        ):
            response = await handler.validate_pipeline(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body
