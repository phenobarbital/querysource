"""FEAT-159 scheduler-path integration + opt-in PG append-only audit."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from querysource.repositories.definitions import DefinitionRepository
from querysource.scheduler.jobs import scheduled_multiqs_job
from querysource.scheduler.scheduler import QSScheduler
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry


class _TransactionConnection:
    """Minimal transactional connection that records a scheduled-definition patch."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def transaction(self) -> None:
        """Record transaction start."""
        self.calls.append("BEGIN")

    async def commit(self) -> None:
        """Record transaction commit."""
        self.calls.append("COMMIT")

    async def rollback(self) -> None:
        """Record transaction rollback."""
        self.calls.append("ROLLBACK")

    async def fetch_one(self, sql: str, *args: object) -> dict | None:
        """Return the rows needed by ``DefinitionRepository.patch``."""
        self.calls.append(sql)
        if "for update" in sql:
            return {"attributes": {}}
        if sql.lstrip().startswith("UPDATE") and "scheduler_run_as_user_id" not in sql:
            return {"query_slug": "scheduled", "attributes": {"scheduler": {"every": "1h"}}}
        if sql.lstrip().startswith("SELECT scheduler_run_as_user_id"):
            return {"scheduler_run_as_user_id": None}
        if sql.lstrip().startswith("UPDATE"):
            return {"query_slug": "scheduled"}
        if "INSERT INTO" in sql:
            return {"audit_id": 1}
        return None

    async def __aenter__(self) -> "_TransactionConnection":
        """Enter the fake connection context."""
        return self

    async def __aexit__(self, *args: object) -> bool:
        """Leave the fake connection context."""
        return False


def _store() -> QueryStore:
    """Return a tenant store for repository-path verification."""
    return QueryStore("localhost:5432/querysource", "tenant1", "queries", "tenant", frozenset({"query_slug"}))


def _scheduler() -> QSScheduler:
    """Build an unstarted scheduler with mocked collaborators."""
    scheduler = QSScheduler.__new__(QSScheduler)
    scheduler.logger = MagicMock()
    scheduler._timezone = "UTC"
    scheduler._notification_manager = MagicMock()
    scheduler._registry = TenantRegistry()
    scheduler._scheduler = MagicMock()
    return scheduler


@pytest.mark.asyncio
async def test_scheduled_delegated_run_as() -> None:
    """Patch run-as/audit, register it, and pass scheduler identity into MultiQS."""
    connection = _TransactionConnection()

    async def connection_factory() -> _TransactionConnection:
        return connection

    repository = DefinitionRepository(registry=None, connection_factory=connection_factory)
    await repository.patch(
        QueryIdentity(store=_store(), slug="scheduled"),
        {"attributes": {"scheduler": {"every": "1h"}}},
        run_as_actor=42,
    )
    assert any("UPDATE" in sql and "scheduler_run_as_user_id" in sql for sql in connection.calls)
    assert any("INSERT INTO" in sql and "_run_as_audit" in sql for sql in connection.calls)

    scheduler = _scheduler()
    auth = MagicMock()
    scheduler._auth = auth
    scheduler._register_query_row(
        {
            "query_slug": "scheduled",
            "attributes": {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}},
            "provider": "multi",
            "query_raw": '{"sources": []}',
            "scheduler_run_as_user_id": 42,
        }
    )
    kwargs = scheduler._scheduler.add_job.call_args.kwargs["kwargs"]
    assert kwargs["run_as_user_id"] == 42

    with patch("querysource.queries.MultiQS") as multi_qs:
        instance = MagicMock()
        instance.query = AsyncMock(return_value=({}, {}))
        multi_qs.return_value = instance
        await scheduled_multiqs_job("scheduled", run_as_user_id=42, identity_auth=auth)

    context = multi_qs.call_args.kwargs["identity_context"]
    assert context.user_id == 42
    assert context.auth is auth


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("QS_TEST_POSTGRES_DSN"), reason="needs QS_TEST_POSTGRES_DSN")
async def test_run_as_audit_append_only() -> None:
    """The fixture's run-as audit trigger rejects updates and deletes."""
    from conftest import provision_tenant_services

    async with provision_tenant_services() as services:
        schema = services["tenant_schemas"][0]
        audit_table = f'"{schema}".queries_run_as_audit'
        connection = services["connection"]
        _, error = await connection.execute(
            f"INSERT INTO {audit_table} (query_slug, old_user_id, new_user_id, operation, changed_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            "scheduled",
            None,
            42,
            "set",
            42,
        )
        assert error is None
        for statement in (f"UPDATE {audit_table} SET changed_by = $1", f"DELETE FROM {audit_table}"):
            _, error = await connection.execute(statement, 7) if statement.startswith("UPDATE") else await connection.execute(statement)
            assert error is not None
            assert "run-as audit is append-only" in str(error)
