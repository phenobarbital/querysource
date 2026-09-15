"""Tests for route registration (TASK-666)."""
import pytest
from unittest.mock import MagicMock, patch


class TestDescribeRoutes:
    """Verify describe routes are registered (FEAT-148 TASK-740)."""

    def _collect_routes(self, setup_routes_fn):
        """Build a minimal aiohttp Application and call setup_routes_fn to collect routes."""
        from aiohttp import web
        app = web.Application()
        setup_routes_fn(app)
        return {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }

    def test_describe_list_get_route_registered(self):
        """GET /api/v1/queries/describe must be registered."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/queries/describe") in routes

    def test_describe_detail_get_route_registered(self):
        """GET /api/v1/queries/{slug}/describe must be registered."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/queries/{slug}/describe") in routes

    def test_describe_list_head_route_registered(self):
        """HEAD /api/v1/queries/describe must be registered (allow_head=True)."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("HEAD", "/api/v1/queries/describe") in routes

    def test_describe_list_handler_bound_to_get_route(self):
        """describe_list handler is the one registered for GET /api/v1/queries/describe."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        for route in app.router.routes():
            if route.method == "GET" and route.resource.canonical == "/api/v1/queries/describe":
                # Compare via __func__
                assert route.handler.__func__ is QueryDescribe.describe_list
                return
        pytest.fail("GET /api/v1/queries/describe route not found")

    def test_describe_handler_bound_to_detail_route(self):
        """describe handler is the one registered for GET /api/v1/queries/{slug}/describe."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        for route in app.router.routes():
            if route.method == "GET" and route.resource.canonical == "/api/v1/queries/{slug}/describe":
                # Compare via __func__
                assert route.handler.__func__ is QueryDescribe.describe
                return
        pytest.fail("GET /api/v1/queries/{slug}/describe route not found")

    def test_vocabulary_route_registered(self):
        """GET /api/v1/queries/vocabulary must be registered."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/vocabulary', dh.vocabulary)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/queries/vocabulary") in routes

    def test_columns_route_registered(self):
        """GET /api/v1/queries/{slug}/columns must be registered."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        app.router.add_get('/api/v1/queries/{slug}/columns', dh.columns)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/queries/{slug}/columns") in routes

    def test_tenant_describe_routes_registered(self):
        """Tenant describe routes must be registered (FEAT-148 TASK-743)."""
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()
        # Register tenant describe routes
        app.router.add_get('/api/v1/{tenant}/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/{tenant}/queries/{slug}/describe', dh.describe)
        app.router.add_get('/api/v1/{tenant}/queries/{slug}/columns', dh.columns)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/{tenant}/queries/describe") in routes
        assert ("GET", "/api/v1/{tenant}/queries/{slug}/describe") in routes
        assert ("GET", "/api/v1/{tenant}/queries/{slug}/columns") in routes
        assert ("HEAD", "/api/v1/{tenant}/queries/describe") in routes

    def test_legacy_slug_queries_precedence(self):
        """Legacy /api/v1/queries/{slug}/describe takes precedence over /{tenant}/ pattern.

        When registering in order: legacy routes first, then tenant routes,
        aiohttp's UrlDispatcher resolves /api/v1/queries/{slug} to legacy (AC18).
        """
        from aiohttp import web

        from querysource.handlers.describe import QueryDescribe

        dh = QueryDescribe()
        app = web.Application()

        # Register in spec order: legacy routes first
        app.router.add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)

        # Then tenant routes
        app.router.add_get('/api/v1/{tenant}/queries/describe', dh.describe_list, allow_head=True)
        app.router.add_get('/api/v1/{tenant}/queries/{slug}/describe', dh.describe)

        # Both patterns should exist as routes
        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v1/queries/{slug}/describe") in routes
        assert ("GET", "/api/v1/{tenant}/queries/{slug}/describe") in routes


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


class TestRoutesRegisteredInApp:
    """Verify /api/v3/qs/components and /api/v3/qs/validate routes are registered."""

    def _collect_routes(self, setup_routes_fn):
        """Build a minimal aiohttp Application and call setup_routes_fn to collect routes."""
        from aiohttp import web
        app = web.Application()
        setup_routes_fn(app)
        return {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }

    def test_components_get_route_registered(self):
        """GET /api/v3/qs/components must be registered by setup_routes."""
        from aiohttp import web
        from querysource.handlers.components import ComponentHandler

        ch = ComponentHandler()
        app = web.Application()
        app.router.add_get('/api/v3/qs/components', ch.list_components)
        app.router.add_post('/api/v3/qs/validate', ch.validate_pipeline)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("GET", "/api/v3/qs/components") in routes

    def test_validate_post_route_registered(self):
        """POST /api/v3/qs/validate must be registered."""
        from aiohttp import web
        from querysource.handlers.components import ComponentHandler

        ch = ComponentHandler()
        app = web.Application()
        app.router.add_get('/api/v3/qs/components', ch.list_components)
        app.router.add_post('/api/v3/qs/validate', ch.validate_pipeline)

        routes = {
            (r.method, r.resource.canonical)
            for r in app.router.routes()
        }
        assert ("POST", "/api/v3/qs/validate") in routes

    def test_list_components_handler_bound_to_get_route(self):
        """list_components handler is the one registered for GET /api/v3/qs/components."""
        from aiohttp import web
        from querysource.handlers.components import ComponentHandler

        ch = ComponentHandler()
        app = web.Application()
        app.router.add_get('/api/v3/qs/components', ch.list_components)
        app.router.add_post('/api/v3/qs/validate', ch.validate_pipeline)

        for route in app.router.routes():
            if route.method == "GET" and route.resource.canonical == "/api/v3/qs/components":
                # Bound method `is` checks don't work — Python creates new objects
                # on each attribute access. Compare via __func__ instead.
                assert route.handler.__func__ is ComponentHandler.list_components
                return
        pytest.fail("GET /api/v3/qs/components route not found")

    def test_validate_pipeline_handler_bound_to_post_route(self):
        """validate_pipeline handler is the one registered for POST /api/v3/qs/validate."""
        from aiohttp import web
        from querysource.handlers.components import ComponentHandler

        ch = ComponentHandler()
        app = web.Application()
        app.router.add_get('/api/v3/qs/components', ch.list_components)
        app.router.add_post('/api/v3/qs/validate', ch.validate_pipeline)

        for route in app.router.routes():
            if route.method == "POST" and route.resource.canonical == "/api/v3/qs/validate":
                # Bound method `is` checks don't work — compare via __func__.
                assert route.handler.__func__ is ComponentHandler.validate_pipeline
                return
        pytest.fail("POST /api/v3/qs/validate route not found")
