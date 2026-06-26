"""Unit tests for the auth/apikey query-parameter blocklist.

Authentication/transport params (e.g. ``?auth=...``, ``?apikey=...``) get
appended to QuerySource / MultiQuery API URLs by upstream gateways and clients.
Before this fix they flowed straight into ``query_parameters`` / ``parse_qs`` and
were interpreted as query conditions/filters. These tests assert they are
stripped at the source for both ``QueryHandler`` and ``QueryView``.
"""
from __future__ import annotations

from multidict import MultiDict

from querysource.conf import EXCLUDED_QUERY_PARAMETERS
from querysource.utils.handlers import (
    QueryHandler,
    _is_excluded_param,
)


class _FakeRelUrl:
    def __init__(self, query: MultiDict) -> None:
        self.query = query


class _FakeRequest:
    """Minimal stand-in for ``aiohttp.web.Request`` exposing only the query
    surface used by ``query_parameters`` and ``parse_qs``.
    """

    def __init__(self, **params: str) -> None:
        md = MultiDict(params)
        self.query = md
        self.rel_url = _FakeRelUrl(md)


# ---------------------------------------------------------------------------
# _is_excluded_param helper
# ---------------------------------------------------------------------------


class TestIsExcludedParam:
    def test_default_blocklist_contains_auth_and_apikey(self) -> None:
        assert "auth" in EXCLUDED_QUERY_PARAMETERS
        assert "apikey" in EXCLUDED_QUERY_PARAMETERS

    def test_matches_case_insensitively(self) -> None:
        assert _is_excluded_param("auth")
        assert _is_excluded_param("AUTH")
        assert _is_excluded_param("ApiKey")
        assert _is_excluded_param("API_KEY")

    def test_regular_params_are_not_excluded(self) -> None:
        assert not _is_excluded_param("store_id")
        assert not _is_excluded_param("filter")
        assert not _is_excluded_param("foo")


# ---------------------------------------------------------------------------
# QueryHandler.query_parameters / parse_qs
# ---------------------------------------------------------------------------


class TestQueryHandlerFiltering:
    def test_query_parameters_strips_auth_params(self) -> None:
        handler = QueryHandler()
        req = _FakeRequest(
            auth="secret-token",
            apikey="abc123",
            store_id="42",
            program="walmart",
        )
        params = handler.query_parameters(req)
        assert params == {"store_id": "42", "program": "walmart"}
        assert "auth" not in params
        assert "apikey" not in params

    def test_query_parameters_case_insensitive(self) -> None:
        handler = QueryHandler()
        req = _FakeRequest(Auth="x", APIKEY="y", keep="z")
        params = handler.query_parameters(req)
        assert params == {"keep": "z"}

    def test_parse_qs_strips_auth_params(self) -> None:
        handler = QueryHandler()
        req = _FakeRequest(
            apikey="abc123",
            store_id="42",
        )
        qry = handler.parse_qs(req)
        assert "apikey" not in qry
        assert qry["store_id"] == "42"

    def test_parse_qs_still_parses_legit_values(self) -> None:
        handler = QueryHandler()
        req = _FakeRequest(auth="should-be-dropped", ids="[1,2,3]")
        qry = handler.parse_qs(req)
        assert "auth" not in qry
        # parseqs turns a bracketed string into a list
        assert qry["ids"] == ["1", "2", "3"]

    def test_no_auth_params_is_unchanged(self) -> None:
        handler = QueryHandler()
        req = _FakeRequest(store_id="42", program="walmart")
        assert handler.query_parameters(req) == {
            "store_id": "42",
            "program": "walmart",
        }
