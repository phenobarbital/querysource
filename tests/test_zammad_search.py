"""Tests for zammad provider type:search mode (TASK-644 / FEAT-092)."""
import pytest
from unittest.mock import AsyncMock, MagicMock


SEARCH_RESPONSE_PAGE = {
    "tickets": [8080],
    "tickets_count": 1,
    "assets": {
        "Ticket": {
            "8080": {
                "id": 8080,
                "group_id": 2,
                "title": "Kiosk failure",
                "updated_at": "2026-05-12T23:13:43.025Z",
            }
        }
    },
}

SEARCH_RESPONSE_EMPTY = {
    "tickets": [],
    "tickets_count": 0,
    "assets": {},
}

BASE_CONDITIONS = {
    "api_url": "https://test.zammad.example/",
    "api_token": "test-token",
}


def make_zammad(extra_conditions: dict):
    """Instantiate zammad bypassing the complex httpSource.__init__."""
    from querysource.providers.sources.zammad import zammad

    instance = object.__new__(zammad)
    instance._conditions = {**BASE_CONDITIONS, **extra_conditions}
    instance._args = {}
    instance._headers = {}

    mock_env = MagicMock()
    mock_env.get = lambda k, fallback=None: fallback if fallback is not None else k
    instance._env = mock_env

    instance.__post_init__(definition=None, conditions=instance._conditions)
    return instance


class TestZammadPostInitType:
    def test_default_type_uses_list_endpoint(self):
        z = make_zammad({})
        assert "/api/v1/tickets/" in z.base_url
        assert "/tickets/search" not in z.base_url
        assert z._zammad_type == "tickets"

    def test_explicit_tickets_type_uses_list_endpoint(self):
        z = make_zammad({"type": "tickets"})
        assert "/api/v1/tickets/" in z.base_url
        assert "/tickets/search" not in z.base_url
        assert z._zammad_type == "tickets"

    def test_search_type_sets_search_url(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        assert "/tickets/search" in z.base_url
        assert "updated_at" in z.base_url
        assert z._zammad_type == "search"

    def test_type_firstdate_lastdate_popped_from_conditions(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        assert "type" not in z._conditions
        assert "firstdate" not in z._conditions
        assert "lastdate" not in z._conditions

    def test_invalid_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown type"):
            make_zammad({"type": "unknown_type"})

    def test_search_without_firstdate_raises_value_error(self):
        with pytest.raises(ValueError, match="firstdate"):
            make_zammad({"type": "search", "lastdate": "2026-05-13 14:00:00"})

    def test_search_url_contains_iso8601_dates(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        # Dates should be converted: space→T, appended Z
        assert "2026-05-13T10%3A00%3A00Z" in z.base_url or "2026-05-13T10:00:00Z" in z.base_url

    def test_search_url_keeps_page_placeholder(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        assert "{page}" in z.base_url
        assert "{api_url}" in z.base_url


class TestZammadSearchQuery:
    @pytest.mark.asyncio
    async def test_search_response_normalised_to_flat_list(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        z.build_url = MagicMock(return_value="http://fake/search?query=...")
        z.request = AsyncMock(side_effect=[
            (SEARCH_RESPONSE_PAGE, None),
            (SEARCH_RESPONSE_EMPTY, None),
        ])
        result = await z._search_query()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 8080
        assert result[0]["title"] == "Kiosk failure"

    @pytest.mark.asyncio
    async def test_search_stops_on_empty_tickets_count(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        z.build_url = MagicMock(return_value="http://fake/search?query=...")
        z.request = AsyncMock(return_value=(SEARCH_RESPONSE_EMPTY, None))
        result = await z._search_query()
        assert result == []
        z.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_dispatches_to_search_query(self):
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        z.build_url = MagicMock(return_value="http://fake/search?query=...")
        z.request = AsyncMock(return_value=(SEARCH_RESPONSE_EMPTY, None))
        result = await z.query()
        assert result == []

    @pytest.mark.asyncio
    async def test_search_paginates_multiple_pages(self):
        page2 = {
            "tickets": [9090],
            "tickets_count": 1,
            "assets": {
                "Ticket": {
                    "9090": {"id": 9090, "title": "Second ticket", "updated_at": "2026-05-13T12:00:00Z"}
                }
            },
        }
        z = make_zammad({
            "type": "search",
            "firstdate": "2026-05-13 10:00:00",
            "lastdate": "2026-05-13 14:00:00",
        })
        z.build_url = MagicMock(return_value="http://fake/search?query=...")
        z.request = AsyncMock(side_effect=[
            (SEARCH_RESPONSE_PAGE, None),
            (page2, None),
            (SEARCH_RESPONSE_EMPTY, None),
        ])
        result = await z._search_query()
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {8080, 9090}
