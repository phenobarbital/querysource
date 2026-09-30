"""FEAT-157 — MultiQS source pre/post-hook wiring (TASK-825)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import querysource.auth.enforcement as enforcement
import querysource.queries.multi as multiqs_module
from querysource.auth._resource_types import ResourceType
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import DriverError, QueryAccessDenied
from querysource.interfaces import source_hooks
from querysource.interfaces.source_hooks import GuardedSQLError
from querysource.queries.multi import MultiQS


class _Stop(Exception):
    """Raised by the fake ThreadQuery so query() stops right after dispatch."""


@pytest.fixture
def same_db(monkeypatch):
    for db_key, pg_key in (("DBHOST", "PG_HOST"), ("DBPORT", "PG_PORT"), ("DBNAME", "PG_DATABASE")):
        monkeypatch.setattr(multiqs_module.conf, pg_key, getattr(multiqs_module.conf, db_key))


@pytest.fixture
def guard_ok(monkeypatch):
    monkeypatch.setattr(source_hooks, "guard_statements", lambda sql: [sql] if isinstance(sql, str) else list(sql))


@pytest.fixture
def captured(monkeypatch):
    calls: dict = {}

    def _fake_thread_query(name, query, request, queue, **kwargs):
        calls[name] = {"query": dict(query), **kwargs}
        raise _Stop()

    monkeypatch.setattr(multiqs_module, "ThreadQuery", _fake_thread_query)
    return calls


def _repo(provider: str = "db"):
    definition = SimpleNamespace(identity=None, runtime=SimpleNamespace(provider=provider), revision="r")
    repo = SimpleNamespace(registry=SimpleNamespace(resolve=lambda tenant: None), get=AsyncMock(return_value=definition))
    return AsyncMock(return_value=repo)


async def test_hooks_not_forwarded_as_conditions(same_db, guard_ok, captured):
    mqs = MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a = 1"}})
    mqs.get_definition_repository = _repo()
    with pytest.raises(Exception):  # _Stop is re-wrapped by self.Error(...)
        await mqs.query()
    assert "pre-hook" not in captured["q1"]["query"]
    assert captured["q1"]["hooks"].pre == ("UPDATE t SET a = 1",)
    assert captured["q1"]["hooks"].post == ()


async def test_slug_child_on_pg_provider_gets_hooks(same_db, guard_ok, captured):
    mqs = MultiQS(queries={"q1": {"slug": "s", "post-hook": ["UPDATE t SET a = 1"]}})
    mqs.get_definition_repository = _repo("db")
    mqs.load_provider = lambda provider: SimpleNamespace(sql_hooks_dialect="postgres")
    with pytest.raises(Exception):
        await mqs.query()
    assert captured["q1"]["hooks"].post == ("UPDATE t SET a = 1",)


async def test_hooks_gate(same_db, guard_ok, captured, monkeypatch):
    principal = QSPrincipal(user_id="35", groups=("sales",))
    allow = AsyncMock(return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True))
    monkeypatch.setattr(enforcement, "enforce_principal", allow)
    entry = {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a = 1"}
    mqs = MultiQS(queries={"q1": dict(entry)}, principal=principal)
    mqs.get_definition_repository = _repo()
    with pytest.raises(Exception):
        await mqs.query()
    calls = [c.args[1:4] for c in allow.await_args_list]
    assert (ResourceType.DATASOURCE, "pg_admin", "datasource:use") in calls

    captured.clear()
    deny = AsyncMock(side_effect=QueryAccessDenied())
    monkeypatch.setattr(enforcement, "enforce_principal", deny)
    mqs = MultiQS(queries={"q1": dict(entry)}, principal=principal)
    mqs.get_definition_repository = _repo()
    with pytest.raises(QueryAccessDenied):
        await mqs.query()
    assert captured == {}


async def test_hooks_rejected_from_request_conditions(same_db, guard_ok, captured):
    mqs = MultiQS(
        queries={"q1": {"query": "SELECT 1", "driver": "pg"}},
        conditions={"q1": {"pre-hook": "UPDATE t SET a = 1"}},
    )
    mqs.get_definition_repository = _repo()
    with pytest.raises(DriverError, match="request conditions"):
        await mqs.query()
    assert captured == {}

    mqs = MultiQS(slug="s", conditions={"pre-hook": "UPDATE t SET a = 1"})
    mqs.get_slug = AsyncMock(return_value=SimpleNamespace(query_raw=""))
    mqs.get_definition_repository = _repo()
    with pytest.raises(DriverError, match="request conditions"):
        await mqs.query()
    assert captured == {}


@pytest.mark.parametrize(
    "entry,provider_dialect",
    [
        ({"slug": "s"}, None),  # slug on a provider without dialect (sqlserver/bigquery)
        ({"query": "SELECT 1", "driver": "mysql"}, "n/a"),
        ({"query": "SELECT 1", "driver": "pg", "datasource": "x"}, "n/a"),
    ],
)
async def test_hooks_unsupported_driver(same_db, guard_ok, captured, entry, provider_dialect):
    mqs = MultiQS(queries={"q1": {**entry, "pre-hook": "UPDATE t SET a = 1"}})
    mqs.get_definition_repository = _repo("sqlserver")
    mqs.load_provider = lambda provider: SimpleNamespace(sql_hooks_dialect=None)
    with pytest.raises(DriverError, match="not supported"):
        await mqs.query()
    assert captured == {}


async def test_hooks_on_sources_or_files_rejected(same_db, guard_ok):
    mqs = MultiQS(files={"f": {"path": "/tmp/x.csv", "post-hook": "UPDATE t SET a = 1"}})
    with pytest.raises(DriverError, match="only supported on PostgreSQL"):
        await mqs.query()

    mqs = MultiQS(query={"sources": [{"AirtableSource": {"pre-hook": "UPDATE t SET a = 1"}}]})
    with pytest.raises(DriverError, match="only supported on PostgreSQL"):
        await mqs.query()


async def test_hooks_db_mismatch(guard_ok, captured, monkeypatch):
    monkeypatch.setattr(multiqs_module.conf, "PG_HOST", "other-host-xyz")
    mqs = MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a = 1"}})
    mqs.get_definition_repository = _repo()
    with pytest.raises(DriverError, match="different databases"):
        await mqs.query()
    assert captured == {}


async def test_hooks_guard_rejection_becomes_driver_error(same_db, captured, monkeypatch):
    def _reject(sql):
        raise GuardedSQLError("statement 1: drop is not allowed", category="data")

    monkeypatch.setattr(source_hooks, "guard_statements", _reject)
    mqs = MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg", "pre-hook": "DROP TABLE x"}})
    mqs.get_definition_repository = _repo()
    with pytest.raises(DriverError, match="drop is not allowed"):
        await mqs.query()
    assert captured == {}


async def test_pipeline_without_hooks_untouched(captured, monkeypatch):
    guard = MagicMock()
    enforce = AsyncMock()
    monkeypatch.setattr(source_hooks, "guard_statements", guard)
    monkeypatch.setattr(enforcement, "enforce_principal", enforce)
    mqs = MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg"}})
    mqs.load_provider = MagicMock()
    mqs.get_definition_repository = _repo()
    with pytest.raises(Exception):
        await mqs.query()
    guard.assert_not_called()
    enforce.assert_not_awaited()
    mqs.load_provider.assert_not_called()
    assert captured["q1"]["query"] == {"query": "SELECT 1", "driver": "pg"}
    assert captured["q1"]["hooks"] is None
