"""FEAT-150 guard: scheduler jobs never pass principal= (trusted-service model unchanged)."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@patch("querysource.queries.qs.QS")
async def test_scheduled_query_job_never_passes_principal(mock_qs_cls):
    from querysource.scheduler.jobs import scheduled_query_job

    mock_instance = AsyncMock()
    mock_qs_cls.return_value = mock_instance

    await scheduled_query_job(slug="test_slug")

    mock_qs_cls.assert_called_once()
    assert "principal" not in mock_qs_cls.call_args.kwargs


@pytest.mark.asyncio
@patch("querysource.queries.MultiQS")
async def test_scheduled_multiqs_job_never_passes_principal(mock_multiqs_cls):
    from querysource.scheduler.jobs import scheduled_multiqs_job

    mock_instance = AsyncMock()
    mock_multiqs_cls.return_value = mock_instance

    await scheduled_multiqs_job(slug="test_slug")

    mock_multiqs_cls.assert_called_once()
    assert "principal" not in mock_multiqs_cls.call_args.kwargs


@pytest.mark.asyncio
@patch("querysource.queries.qs.QS")
async def test_cache_refresh_job_never_passes_principal(mock_qs_cls):
    from querysource.scheduler.jobs import cache_refresh_job

    mock_instance = AsyncMock()
    mock_qs_cls.return_value = mock_instance

    await cache_refresh_job(slug="test_slug")

    mock_qs_cls.assert_called_once()
    assert "principal" not in mock_qs_cls.call_args.kwargs
