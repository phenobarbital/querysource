"""Smoke tests for DatasourceView PBAC list filtering.

Tests _pbac_filter helper: silent no-op when PBAC disabled, filters by allowed
names when enabled, returns empty list without raising when all denied.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from querysource.datasources.handlers.datasource import DatasourceView


def _make_request(app_dict=None):
    """Build a minimal mock request."""
    req = MagicMock()
    req.app = app_dict or {}
    return req


def _make_view():
    """Build a DatasourceView without invoking __init__."""
    return DatasourceView.__new__(DatasourceView)


class TestDatasourceViewPbacFilter:
    """Tests for DatasourceView._pbac_filter (passed request explicitly)."""

    @pytest.mark.asyncio
    async def test_disabled_returns_all(self):
        """When PBAC is disabled (no 'security' key), all items are returned."""
        v = _make_view()
        req = _make_request(app_dict={})
        items = [{"name": "postgres"}, {"name": "mysql"}]
        out = await v._pbac_filter(
            req, items, "name",
            resource_type="datasource",
            action="datasource:list",
        )
        assert out == items

    @pytest.mark.asyncio
    async def test_filters_by_allowed(self):
        """Items whose name is denied are removed from the result."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=["postgres"], denied=["mysql"])
        )
        v = _make_view()
        req = _make_request(app_dict={"security": guardian})
        items = [{"name": "postgres"}, {"name": "mysql"}]
        out = await v._pbac_filter(
            req, items, "name",
            resource_type="datasource",
            action="datasource:list",
        )
        assert out == [{"name": "postgres"}]

    @pytest.mark.asyncio
    async def test_returns_empty_silently(self):
        """When all items are denied, an empty list is returned (no 404/exception)."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=[], denied=["postgres"])
        )
        v = _make_view()
        req = _make_request(app_dict={"security": guardian})
        items = [{"name": "postgres"}]
        out = await v._pbac_filter(
            req, items, "name",
            resource_type="datasource",
            action="datasource:list",
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """Empty input list returns empty without calling filter_resources."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock()
        v = _make_view()
        req = _make_request(app_dict={"security": guardian})
        out = await v._pbac_filter(
            req, [], "name",
            resource_type="datasource",
            action="datasource:list",
        )
        assert out == []
        guardian.filter_resources.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_guardian_exception_returns_all_failopen(self):
        """If guardian.filter_resources raises, fail-open: return all items."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(side_effect=RuntimeError("guardian down"))
        v = _make_view()
        req = _make_request(app_dict={"security": guardian})
        items = [{"name": "postgres"}, {"name": "mysql"}]
        out = await v._pbac_filter(
            req, items, "name",
            resource_type="datasource",
            action="datasource:list",
        )
        assert out == items  # fail-open for listing

    @pytest.mark.asyncio
    async def test_driver_filter_uses_driver_action(self):
        """Driver items can be filtered with driver:list action."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=["pg"], denied=["mongo"])
        )
        v = _make_view()
        req = _make_request(app_dict={"security": guardian})
        items = [{"name": "pg", "default": True}, {"name": "mongo", "default": True}]
        out = await v._pbac_filter(
            req, items, "name",
            resource_type="driver",
            action="driver:list",
        )
        assert out == [{"name": "pg", "default": True}]
