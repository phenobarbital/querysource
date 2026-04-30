"""Smoke tests for MultiQuery PBAC enforcement.

Verifies all-or-nothing pre-flight behavior: one denied slug rejects all;
all allowed passes through; and MultiQS accepts the user_session kwarg.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web

from querysource.handlers.multi import QueryHandler


class TestMultiQueryPreflight:
    """Tests for QueryHandler._preflight_multiquery."""

    def _make_handler(self):
        h = QueryHandler.__new__(QueryHandler)
        h.logger = MagicMock()
        return h

    @pytest.mark.asyncio
    async def test_one_denied_rejects_all(self):
        """If any slug is denied, HTTPNotFound is raised."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=["a"], denied=["b"], policies_applied=[])
        )
        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value={"username": "alice"})
        h._enforce_pbac = AsyncMock()

        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        with pytest.raises(web.HTTPNotFound):
            await h._preflight_multiquery(
                request, slugs=["a", "b"], files=[], has_raw_query=False
            )

    @pytest.mark.asyncio
    async def test_all_allowed_passes(self):
        """When all slugs are allowed, no exception is raised."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=["a", "b"], denied=[], policies_applied=[])
        )
        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value={"username": "alice"})
        h._enforce_pbac = AsyncMock()

        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        # Should NOT raise
        await h._preflight_multiquery(
            request, slugs=["a", "b"], files=[], has_raw_query=False
        )

    @pytest.mark.asyncio
    async def test_pbac_disabled_is_noop(self):
        """When app['security'] is absent, pre-flight does nothing."""
        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value=None)
        h._enforce_pbac = AsyncMock()

        request = MagicMock(spec=web.Request)
        request.app = {}  # no 'security'

        # Must NOT raise
        await h._preflight_multiquery(
            request, slugs=["a", "b"], files=["f1"], has_raw_query=True
        )
        h._enforce_pbac.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_session_raises_404(self):
        """When PBAC is enabled but session is None, raise HTTPNotFound."""
        guardian = MagicMock()
        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value=None)

        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        with pytest.raises(web.HTTPNotFound):
            await h._preflight_multiquery(
                request, slugs=["x"], files=[], has_raw_query=False
            )

    @pytest.mark.asyncio
    async def test_files_checked_separately(self):
        """Files are checked with slug:execute in a separate filter_resources call."""
        calls = []

        async def mock_filter(resources, request, resource_type, action):
            calls.append((resources, action))
            return MagicMock(allowed=list(resources), denied=[])

        guardian = MagicMock()
        guardian.filter_resources = mock_filter

        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value={"username": "alice"})
        h._enforce_pbac = AsyncMock()

        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        await h._preflight_multiquery(
            request, slugs=["slug1"], files=["file1"], has_raw_query=False
        )
        assert len(calls) == 2  # one for slugs, one for files

    @pytest.mark.asyncio
    async def test_raw_query_uses_enforce_pbac(self):
        """has_raw_query=True triggers a single _enforce_pbac call."""
        guardian = MagicMock()
        guardian.filter_resources = AsyncMock(
            return_value=MagicMock(allowed=[], denied=[], policies_applied=[])
        )
        h = self._make_handler()
        h._get_user_session = AsyncMock(return_value={"username": "alice"})
        h._enforce_pbac = AsyncMock()

        request = MagicMock(spec=web.Request)
        request.app = {"security": guardian}

        await h._preflight_multiquery(
            request, slugs=[], files=[], has_raw_query=True
        )
        h._enforce_pbac.assert_awaited_once()
        call_kwargs = h._enforce_pbac.call_args.kwargs
        assert call_kwargs.get('action') == "raw_query:execute"


class TestMultiQSConstructor:
    """Tests for MultiQS.__init__ user_session kwarg."""

    def test_accepts_user_session_kwarg(self):
        from querysource.queries.multi import MultiQS

        instance = MultiQS(slug="s", user_session={"username": "alice"})
        assert instance._user_session == {"username": "alice"}

    def test_user_session_default_none(self):
        from querysource.queries.multi import MultiQS

        instance = MultiQS(slug="s")
        assert getattr(instance, '_user_session', None) is None
