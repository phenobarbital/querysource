"""Tests for Airtable OAuth2 route registration in QuerySource.setup() (TASK-680)."""
import importlib

import pytest
from aiohttp import web


def _all_registered_paths(app: web.Application) -> list[str]:
    """Enumerate registered route paths from an aiohttp app."""
    paths = []
    for resource in app.router.resources():
        info = resource.get_info()
        # info is one of: {'path': ...} (PlainResource) or {'formatter': ...} (DynamicResource)
        path = info.get('path') or info.get('formatter')
        if path:
            paths.append(path)
    return paths


class TestAirtableOAuthRoutes:
    @pytest.fixture
    def querysource_fresh(self, monkeypatch):
        """Reload conf + services so the flag value takes effect."""
        # Force reload to re-evaluate module-level config reads
        from querysource import conf, services
        importlib.reload(conf)
        importlib.reload(services)
        # Singleton — clear instance cache between tests
        if hasattr(services.QuerySource, '_instances'):
            services.QuerySource._instances.clear()
        return services

    def test_routes_absent_when_flag_off(self, monkeypatch, querysource_fresh):
        monkeypatch.setattr(querysource_fresh, 'QS_AIRTABLE_OAUTH_ENABLED', False)
        app = web.Application()
        qs = querysource_fresh.QuerySource(lazy=True)
        qs.setup(app)
        paths = _all_registered_paths(app)
        assert not any('/integrations/airtable' in p for p in paths), (
            f"Expected no airtable integration routes when flag is off, got: {paths}"
        )

    def test_routes_present_when_flag_on(self, monkeypatch, querysource_fresh):
        monkeypatch.setattr(querysource_fresh, 'QS_AIRTABLE_OAUTH_ENABLED', True)
        app = web.Application()
        qs = querysource_fresh.QuerySource(lazy=True)
        qs.setup(app)
        paths = _all_registered_paths(app)
        assert '/api/v1/qs/integrations/airtable/connect' in paths
        assert '/api/v1/qs/integrations/airtable/callback' in paths
