"""Add ownership diagnostics and isolate implicit artifacts regression contracts."""
import logging
from unittest import mock

import pytest
from aiohttp import web

from querysource.ownership_logging import implicit_artifact_name, ownership_fields
from querysource.tenants import QueryIdentity, QueryStore


def _store(schema: str = "tenant1", contract: str = "tenant") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract=contract,
        columns=frozenset({"query_slug"}),
    )


@pytest.mark.asyncio
async def test_ownership_fields_exclude_secrets() -> None:
    """ownership fields exclude secrets."""
    identity = QueryIdentity(store=_store("tenant1"), slug="daily_metrics")
    fields = ownership_fields(identity)
    assert fields == {
        "owner": "tenant1",
        "schema": "tenant1",
        "table": "queries",
        "slug": "daily_metrics",
    }
    # No credentials/SQL/mutable-request fields ever, regardless of input shape.
    assert "password" not in fields
    assert "dsn" not in fields
    assert "query" not in fields
    assert "url" not in fields

    # A bare QueryStore (no slug attached) still yields owner/schema/table.
    store_only = ownership_fields(_store("tenant2", "legacy"))
    assert store_only == {"owner": "tenant2", "schema": "tenant2", "table": "queries"}
    assert "slug" not in store_only

    # A TenantOwnerEnvelope (plain dict, TASK-728/730 shape) works too.
    envelope = {
        "version": 1,
        "database_namespace": "localhost:5432/querysource",
        "schema": "tenant3",
        "table": "queries",
        "contract": "tenant",
    }
    envelope_fields = ownership_fields(envelope)
    assert envelope_fields == {"owner": "tenant3", "schema": "tenant3", "table": "queries"}
    assert "database_namespace" not in envelope_fields
    assert "version" not in envelope_fields

    # No identity resolved yet -> empty mapping, never a crash.
    assert ownership_fields(None) == {}


@pytest.mark.asyncio
async def test_same_slug_distinct_implicit_names() -> None:
    """same slug distinct implicit names."""
    identity_a = QueryIdentity(store=_store("tenant_a"), slug="report")
    identity_b = QueryIdentity(store=_store("tenant_b"), slug="report")

    name_a = implicit_artifact_name(identity_a, "req-1", "report")
    name_b = implicit_artifact_name(identity_b, "req-1", "report")
    # Same slug/filename, different owner -> distinct implicit names.
    assert name_a != name_b
    assert name_a == "tenant_a_req-1_report"
    assert name_b == "tenant_b_req-1_report"

    # Same owner, different execution/request id -> also distinct (no
    # collision between two concurrent runs of the identical slug).
    name_a_run2 = implicit_artifact_name(identity_a, "req-2", "report")
    assert name_a_run2 != name_a
    assert name_a_run2 == "tenant_a_req-2_report"


@pytest.mark.asyncio
async def test_legacy_and_explicit_filename_compatibility() -> None:
    """legacy and explicit filename compatibility."""
    identity = QueryIdentity(store=_store("tenant1"), slug="report")

    # No identity resolved -> filename passes through unchanged (legacy).
    assert implicit_artifact_name(None, "req-1", "report") == "report"
    # No request id -> unchanged.
    assert implicit_artifact_name(identity, None, "report") == "report"
    # Empty/None filename -> unchanged (nothing to namespace).
    assert implicit_artifact_name(identity, "req-1", None) is None
    assert implicit_artifact_name(identity, "req-1", "") == ""

    # Explicit destinations (paths / URIs) are NEVER prefixed (AC-3), even
    # with a resolved identity and request id.
    assert implicit_artifact_name(identity, "req-1", "/tmp/explicit.csv") == "/tmp/explicit.csv"
    assert implicit_artifact_name(identity, "req-1", "s3://bucket/key.csv") == "s3://bucket/key.csv"

    # DataOutput itself: an explicit `filename=` kwarg is never touched;
    # an implicit one (falling back to `slug`) IS namespaced when the
    # `query` object carries a resolved identity + execution id.
    from querysource.outputs.output import DataOutput

    class _FakeQueryWithIdentity:
        _definition_identity = identity
        _execution_id = "exec-123"

    request = mock.MagicMock(spec=web.Request)
    request.headers = {}

    out_explicit = DataOutput(
        request=request,
        query=_FakeQueryWithIdentity(),
        slug="report",
        filename="explicit_name.csv",
    )
    assert out_explicit.filename == "explicit_name.csv"

    out_implicit = DataOutput(
        request=request,
        query=_FakeQueryWithIdentity(),
        slug="report",
    )
    assert out_implicit.filename == "tenant1_exec-123_report"

    # A raw DataFrame/list `query` (no identity at all) -> filename
    # unchanged, exactly like before this task (no crash on getattr).
    out_no_identity = DataOutput(
        request=request,
        query=[{"a": 1}],
        slug="report",
    )
    assert out_no_identity.filename == "report"


@pytest.mark.asyncio
async def test_event_context_non_http_and_nested() -> None:
    """event context non http and nested."""
    # Direct/HTTP execution boundary: AbstractQuery.event_log() attaches
    # execution_id + ownership_fields to every payload, without ever
    # overriding a caller-supplied key (e.g. the output alias "slug").
    from querysource.queries.base import BaseQuery

    q = BaseQuery(slug="my_alias")
    q._definition_identity = QueryIdentity(store=_store("tenant1"), slug="stored_slug")

    captured = {}

    async def _fake_log_event(payload, status="query", **kwargs):
        captured["payload"] = payload
        captured["status"] = status

    with mock.patch("querysource.interfaces.queries.LogEvent", _fake_log_event):
        await q.event_log({"slug": "my_alias", "duration": 1.5})

    assert captured["payload"]["slug"] == "my_alias"  # caller's alias preserved
    assert captured["payload"]["owner"] == "tenant1"
    assert captured["payload"]["schema"] == "tenant1"
    assert captured["payload"]["table"] == "queries"
    assert captured["payload"]["execution_id"] == q._execution_id

    # Two different query objects (e.g. nested/concurrent executions) get
    # distinct execution ids — never guessed/shared from a mutable field.
    q2 = BaseQuery(slug="my_alias")
    assert q2._execution_id != q._execution_id

    # No identity resolved (e.g. inline/legacy query, no stored slug) ->
    # event still succeeds, just without owner fields — never a crash.
    q3 = BaseQuery(slug=None)
    captured.clear()
    with mock.patch("querysource.interfaces.queries.LogEvent", _fake_log_event):
        await q3.event_log({"slug": "inline"})
    assert "owner" not in captured["payload"]
    assert captured["payload"]["execution_id"] == q3._execution_id

    # Scheduled/remote (non-HTTP) execution boundary: scheduler jobs.py
    # attaches ownership from the TenantOwnerEnvelope (no QueryIdentity
    # available there) to its failure log, without changing the
    # notify(job_id, slug, error) call itself.
    from querysource.scheduler.jobs import scheduled_query_job

    owner_envelope = {
        "version": 1,
        "database_namespace": "localhost:5432/querysource",
        "schema": "tenant1",
        "table": "queries",
        "contract": "tenant",
    }

    class _FailingQS:
        def __init__(self, *, slug=None, tenant=None, **kwargs):
            self.tenant = tenant

        async def get_definition_repository(self):
            class _Registry:
                def resolve(self, tenant):
                    return _store("tenant1", "tenant")

            class _Repo:
                registry = _Registry()

            return _Repo()

        async def query(self):
            raise ValueError("boom")

    notified = []

    class _FakeNotificationManager:
        def notify(self, job_id, slug, error):
            notified.append((job_id, slug, error))

    log_records = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record)

    scheduler_logger = logging.getLogger("QSScheduler.Jobs")
    handler = _CapturingHandler()
    scheduler_logger.addHandler(handler)
    scheduler_logger.setLevel(logging.WARNING)
    try:
        with mock.patch("querysource.queries.qs.QS", _FailingQS):
            await scheduled_query_job(
                "remote_slug", _FakeNotificationManager(), owner=owner_envelope
            )
    finally:
        scheduler_logger.removeHandler(handler)

    # notify() signature/args unchanged (AC-4).
    assert notified == [("query_remote_slug", "remote_slug", notified[0][2])]
    assert isinstance(notified[0][2], ValueError)
    # The warning log line carries the ownership context.
    assert len(log_records) == 1
    rendered = log_records[0].getMessage()
    assert "tenant1" in rendered
