"""Regression coverage for scheduled MultiQS delegated identities."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.repositories.definitions import RUN_AS_COLUMN
from querysource.scheduler.jobs import scheduled_multiqs_job
from querysource.scheduler.scheduler import QSScheduler
from querysource.tenants import TenantRegistry


def _make_sched_mocked() -> QSScheduler:
    """Build an unstarted scheduler with its external collaborators mocked."""
    scheduler = QSScheduler.__new__(QSScheduler)
    scheduler.logger = MagicMock()
    scheduler._timezone = "UTC"
    scheduler._notification_manager = MagicMock()
    scheduler._registry = TenantRegistry()
    scheduler._scheduler = MagicMock()
    return scheduler


def test_multi_job_kwargs_carry_run_as() -> None:
    """Multi jobs carry the id and auth handle, never credential material."""
    scheduler = _make_sched_mocked()
    auth = MagicMock()
    scheduler._auth = auth
    row = {
        "query_slug": "delegated_source",
        "attributes": {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}},
        "provider": "multi",
        "query_raw": '{"queries": {}}',
        RUN_AS_COLUMN: 42,
    }

    scheduler._register_query_row(row)

    kwargs = scheduler._scheduler.add_job.call_args.kwargs["kwargs"]
    assert kwargs["run_as_user_id"] == 42
    assert kwargs["identity_auth"] is auth
    assert not any("token" in key or "credential" in key for key in kwargs)


@pytest.mark.asyncio
async def test_scheduled_multiqs_job_builds_context() -> None:
    """A stored run-as id creates the scheduler identity context for MultiQS."""
    auth = MagicMock()
    with patch("querysource.queries.MultiQS") as mock_cls:
        instance = MagicMock()
        instance.query = AsyncMock(return_value=({}, {}))
        mock_cls.return_value = instance

        await scheduled_multiqs_job(slug="delegated_source", run_as_user_id=42, identity_auth=auth)

    context = mock_cls.call_args.kwargs["identity_context"]
    assert context.user_id == 42
    assert context.auth is auth
    assert context.origin == "scheduler"


@pytest.mark.asyncio
async def test_scheduler_sync_never_writes_run_as() -> None:
    """SchedulerJobsView POST only re-syncs and never calls repository writers."""
    from querysource.handlers.scheduler import SchedulerJobsView

    repository = MagicMock()
    scheduler = MagicMock()
    scheduler.repository = repository
    scheduler.register_slug = AsyncMock(
        return_value={"slug": "delegated_source", "registered": [], "removed": []}
    )
    request = MagicMock()
    request.json = AsyncMock(return_value={"slug": "delegated_source"})
    request.app = {"qs_scheduler": scheduler}
    view = SchedulerJobsView.__new__(SchedulerJobsView)
    view._request = request

    response = await view.post()

    assert response.status == 200
    scheduler.register_slug.assert_awaited_once_with("delegated_source", tenant=None)
    for method in ("patch", "upsert", "_apply_run_as"):
        getattr(repository, method).assert_not_called()
