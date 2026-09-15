"""Propagate job ownership and synchronize committed CRUD regression contracts."""
from typing import ClassVar
from unittest import mock

import pytest
from aiohttp import web

from querysource.tenants import QueryStore


def _store(schema: str = "tenant1", contract: str = "tenant") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract=contract,
        columns=frozenset({"query_slug"}),
    )


def _envelope(store: QueryStore) -> dict:
    return {
        "version": 1,
        "database_namespace": store.database_namespace,
        "schema": store.schema,
        "table": store.table,
        "contract": store.contract,
    }


class _FakeRegistry:
    def __init__(self, resolved_store: QueryStore) -> None:
        self._resolved_store = resolved_store

    def resolve(self, tenant):
        return self._resolved_store


class _FakeRepo:
    def __init__(self, resolved_store: QueryStore) -> None:
        self.registry = _FakeRegistry(resolved_store)


class _FakeQS:
    """Stand-in for QS/MultiQS: records construction + query() calls."""

    instances: ClassVar[list["_FakeQS"]] = []

    def __init__(self, *, slug=None, tenant=None, **kwargs):
        self.slug = slug
        self.tenant = tenant
        self.query_called = False
        self.repo = None
        type(self).instances.append(self)

    async def get_definition_repository(self):
        return self.repo

    async def query(self):
        self.query_called = True


@pytest.fixture(autouse=True)
def _reset_fake_qs():
    _FakeQS.instances = []
    yield
    _FakeQS.instances = []


@pytest.mark.asyncio
async def test_query_multi_refresh_envelope_roundtrip() -> None:
    """query multi refresh envelope roundtrip."""
    from querysource.scheduler.jobs import (
        cache_refresh_job,
        scheduled_multiqs_job,
        scheduled_query_job,
    )

    store = _store("tenant1", "tenant")
    envelope = _envelope(store)

    # AC-1: a matching envelope revalidates cleanly and QS/MultiQS receive
    # the owner's tenant selector — query() actually runs. Each created
    # fake instance is wired with a repo whose registry resolves the
    # schema back to `matching_store`, so revalidation passes.
    def _make_fake_qs_cls(matching_store):
        class _Bound(_FakeQS):
            def __init__(self, *, slug=None, tenant=None, **kwargs):
                super().__init__(slug=slug, tenant=tenant, **kwargs)
                self.repo = _FakeRepo(matching_store)

        return _Bound

    with mock.patch("querysource.queries.qs.QS", _make_fake_qs_cls(store)):
        await scheduled_query_job("q1", None, owner=envelope)
    assert len(_FakeQS.instances) == 1
    assert _FakeQS.instances[0].tenant == "tenant1"
    assert _FakeQS.instances[0].query_called is True

    _FakeQS.instances = []
    with mock.patch("querysource.queries.qs.QS", _make_fake_qs_cls(store)):
        await cache_refresh_job("q2", None, owner=envelope)
    assert _FakeQS.instances[0].query_called is True

    _FakeQS.instances = []
    with mock.patch("querysource.queries.MultiQS", _make_fake_qs_cls(store)):
        await scheduled_multiqs_job("q3", None, owner=envelope)
    assert _FakeQS.instances[0].tenant == "tenant1"
    assert _FakeQS.instances[0].query_called is True

    # AC-1 "revalidate ... then execute selected owner": when the CURRENT
    # registry resolves this schema to a materially different physical
    # store than the envelope recorded (e.g. database_namespace drifted
    # after a restart), the job must NOT execute the query and must notify
    # with a TenantError instead.
    drifted_store = QueryStore(
        database_namespace="otherhost:5432/other",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug"}),
    )
    notified = []

    class _FakeNotificationManager:
        def notify(self, job_id, slug, error):
            notified.append((job_id, slug, error))

    _FakeQS.instances = []
    with mock.patch("querysource.queries.qs.QS", _make_fake_qs_cls(drifted_store)):
        await scheduled_query_job("q4", _FakeNotificationManager(), owner=envelope)
    assert _FakeQS.instances[0].query_called is False
    assert len(notified) == 1
    job_id, slug, error = notified[0]
    assert job_id == "query_q4"
    assert slug == "q4"
    assert "no longer matches" in str(error)


@pytest.mark.asyncio
async def test_api_serialization_filter_pause_resume_delete() -> None:
    """api serialization filter pause resume delete."""
    from querysource.handlers.scheduler import SchedulerJobsView, _kind_from_id

    # _kind_from_id must classify BOTH the legacy shape and TASK-729's
    # qsj2-qualified shape (AC-2 — a non-default-store job must not
    # serialize as "unknown").
    assert _kind_from_id("query_slug") == "query"
    assert _kind_from_id("multi_slug") == "multi"
    assert _kind_from_id("cache_slug") == "cache"
    assert _kind_from_id("qsj2-query-a1b2c3d4e5f6-slug") == "query"
    assert _kind_from_id("qsj2-multi-a1b2c3d4e5f6-slug") == "multi"
    assert _kind_from_id("qsj2-cache-a1b2c3d4e5f6-slug") == "cache"
    assert _kind_from_id("garbage") == "unknown"

    class _MockTrigger:
        def __str__(self):
            return "interval[0:01:00]"

    def _mock_job(job_id, owner=None):
        job = mock.MagicMock()
        job.id = job_id
        job.name = f"Scheduled: {job_id}"
        job.kwargs = {"slug": "shared", "owner": owner}
        job.next_run_time = None
        job.trigger = _MockTrigger()
        job.coalesce = False
        job.max_instances = 1
        job.misfire_grace_time = None
        job.pending = True
        return job

    view = SchedulerJobsView.__new__(SchedulerJobsView)
    tenant_owner = _envelope(_store("tenant1", "tenant"))
    serialized = view._serialize_job(_mock_job("qsj2-query-abc123-shared", tenant_owner))
    assert serialized["kind"] == "query"
    assert serialized["owner"]["schema"] == "tenant1"
    assert serialized["owner"]["contract"] == "tenant"

    # A legacy job (owner=None in kwargs, e.g. pre-TASK-730 registration)
    # must not surface a bogus "owner" key.
    serialized_legacy = view._serialize_job(_mock_job("query_shared", owner=None))
    assert "owner" not in serialized_legacy

    # AC-2: pause/resume/delete act on the FULL job id; controlling one
    # owner's job for a shared slug text must never affect the other
    # owner's job for the identical slug.
    class _FakeScheduler:
        def __init__(self):
            self.calls: list[tuple[str, tuple]] = []

        def set_job_paused(self, job_id, paused):
            self.calls.append(("set_job_paused", (job_id, paused)))
            return job_id == "qsj2-query-abc123-shared"

        def remove_job(self, job_id):
            self.calls.append(("remove_job", (job_id,)))
            return job_id == "qsj2-query-abc123-shared"

    fake_scheduler = _FakeScheduler()
    mock_request2 = mock.MagicMock(spec=web.Request)

    async def _json_action():
        return {"action": "pause"}

    mock_request2.json = _json_action
    view2 = SchedulerJobsView(mock_request2)
    view2._get_scheduler = lambda: fake_scheduler
    view2.match_parameters = lambda request: {"job_id": "qsj2-query-abc123-shared"}
    view2.json_response = lambda response, status=200, headers=None: web.json_response(
        response, status=status, headers=headers
    )
    resp = await view2.patch()
    assert resp.status == 200
    assert fake_scheduler.calls == [("set_job_paused", ("qsj2-query-abc123-shared", True))]
    # Only the exact qualified id was touched — the legacy "query_shared"
    # job for a different owner was never named in any call.
    assert all("query_shared" != c[1][0] for c in fake_scheduler.calls)

    fake_scheduler2 = _FakeScheduler()
    mock_request3 = mock.MagicMock(spec=web.Request)
    view3 = SchedulerJobsView(mock_request3)
    view3._get_scheduler = lambda: fake_scheduler2
    view3.match_parameters = lambda request: {"job_id": "query_shared"}
    view3.json_response = lambda response, status=200, headers=None: web.json_response(
        response, status=status, headers=headers
    )
    resp3 = await view3.delete()
    assert resp3.status == 404  # fake only "removes" the qsj2 id
    assert fake_scheduler2.calls == [("remove_job", ("query_shared",))]


@pytest.mark.asyncio
async def test_crud_commit_then_sync_failure_header() -> None:
    """crud commit then sync failure header."""
    from querysource.handlers.manager import QueryManager
    from querysource.tenants import QueryIdentity, TenantRegistry

    tenant_registry = TenantRegistry()
    store = _store("tenant1", "tenant")
    tenant_registry._stores = (store,)
    tenant_registry._default_store = store

    class _FakeDefRepo:
        registry = tenant_registry

        async def patch(self, identity: QueryIdentity, data: dict):
            return {"query_slug": identity.slug, **data}

    class _RaisingScheduler:
        async def register_slug(self, slug, *, tenant=None):
            raise RuntimeError("scheduler jobstore unavailable")

    class _OkScheduler:
        def __init__(self):
            self.calls = []

        async def register_slug(self, slug, *, tenant=None):
            self.calls.append((slug, tenant))
            return {"slug": slug, "registered": [f"query_{slug}"], "removed": []}

    def _request(app):
        request = mock.MagicMock(spec=web.Request)
        request.app = app
        request.match_info = {"slug": "shared"}
        request.query = {}
        return request

    # Sync failure: the DB mutation already committed (repo.patch
    # succeeded) — the response body/status must still reflect that
    # success (no rollback claim), but X-QS-Scheduler-Sync: failed must be
    # present (AC-3).
    app_fail = {
        "qs_tenant_registry": tenant_registry,
        "qs_definition_repository": _FakeDefRepo(),
        "qs_scheduler": _RaisingScheduler(),
    }
    request_fail = _request(app_fail)
    manager_fail = QueryManager(request_fail)

    async def _json_data(req=None):
        return {"description": "patched"}

    manager_fail.json_data = _json_data
    manager_fail.get_arguments = lambda: request_fail.match_info
    resp_fail = await manager_fail.patch()
    assert resp_fail.status == 200
    assert resp_fail.headers.get("X-QS-Scheduler-Sync") == "failed"

    # Successful sync: no scheduler-sync header at all.
    ok_scheduler = _OkScheduler()
    app_ok = {
        "qs_tenant_registry": tenant_registry,
        "qs_definition_repository": _FakeDefRepo(),
        "qs_scheduler": ok_scheduler,
    }
    request_ok = _request(app_ok)
    manager_ok = QueryManager(request_ok)
    manager_ok.json_data = _json_data
    manager_ok.get_arguments = lambda: request_ok.match_info
    resp_ok = await manager_ok.patch()
    assert resp_ok.status == 200
    assert "X-QS-Scheduler-Sync" not in resp_ok.headers
    assert ok_scheduler.calls == [("shared", "tenant1")]

    # No scheduler at all (feature/route not wired) -> treated as success,
    # no failure header, no crash.
    app_none = {
        "qs_tenant_registry": tenant_registry,
        "qs_definition_repository": _FakeDefRepo(),
    }
    request_none = _request(app_none)
    manager_none = QueryManager(request_none)
    manager_none.json_data = _json_data
    manager_none.get_arguments = lambda: request_none.match_info
    resp_none = await manager_none.patch()
    assert resp_none.status == 200
    assert "X-QS-Scheduler-Sync" not in resp_none.headers


@pytest.mark.asyncio
async def test_notification_callback_arity_preserved() -> None:
    """notification callback arity preserved."""
    from querysource.scheduler.jobs import scheduled_query_job
    from querysource.scheduler.notifications import NotificationManager

    received: list[tuple] = []

    def custom_callback(job_id: str, slug: str, error: Exception) -> None:
        received.append((job_id, slug, error))

    manager = NotificationManager()
    manager.add_callback(custom_callback)

    class _FailingQS:
        def __init__(self, *, slug=None, tenant=None, **kwargs):
            pass

        async def query(self):
            raise ValueError("boom")

    # No owner -> legacy path, no revalidation, straight to query() (AC-4
    # "notify(job_id, slug, error) unchanged").
    with mock.patch("querysource.queries.qs.QS", _FailingQS):
        await scheduled_query_job("legacy_slug", manager)

    assert len(received) == 1
    job_id, slug, error = received[0]
    assert job_id == "query_legacy_slug"
    assert slug == "legacy_slug"
    assert isinstance(error, ValueError)
    assert str(error) == "boom"
