"""Run-as column: tolerant reads and payload guards in DefinitionRepository."""
import pytest
from asyncdb.drivers.pg import UndefinedColumnError

from querysource.repositories.definitions import RUN_AS_COLUMN, DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity
from querysource.tenants import QueryStore


def _tenant_store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


def _base_row(**overrides) -> dict:
    row = {"query_slug": "test_query", "description": "A test query", "query_raw": "SELECT 1"}
    row.update(overrides)
    return row


class _MockConn:
    """Async-context mock connection recording SQL calls."""

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _factory(conn):
    async def connection_factory():
        return conn

    return connection_factory


class _SeqConn(_MockConn):
    """fetch_all/fetch_one consume queued outcomes (exceptions are raised)."""

    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)

    def _next(self):
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    async def fetch_all(self, sql, *args, **kwargs):
        self.calls.append(("fetch_all", sql, args))
        return self._next()

    async def fetch_one(self, sql, *args, **kwargs):
        self.calls.append(("fetch_one", sql, args))
        return self._next()


def _repo(conn):
    return DefinitionRepository(registry=None, connection_factory=_factory(conn))


def _identity():
    return QueryIdentity(store=_tenant_store(), slug="test_query")


def test_row_to_persisted_pops_run_as():
    repo = _repo(_MockConn())
    persisted, _ = repo._row_to_persisted(_base_row(**{RUN_AS_COLUMN: 42}), _tenant_store())
    assert RUN_AS_COLUMN not in persisted
    assert persisted["query_slug"] == "test_query"


@pytest.mark.parametrize("method", ["patch", "upsert", "create"])
async def test_payload_rejects_run_as_key(method):
    conn = _MockConn()
    repo = _repo(conn)
    target = _tenant_store() if method == "create" else _identity()
    with pytest.raises(TenantError) as ei:
        await getattr(repo, method)(target, {"query_slug": "test_query", RUN_AS_COLUMN: 1})
    assert ei.value.error_code == "invalid_tenant"
    assert conn.calls == []


async def test_schedulable_tolerates_missing_column():
    rows = [{"query_slug": "a", RUN_AS_COLUMN: None}]
    conn = _SeqConn([UndefinedColumnError("column does not exist"), rows])
    result = await _repo(conn).schedulable(_tenant_store("fallback_schema"))
    assert result[0][RUN_AS_COLUMN] is None
    assert f"NULL AS {RUN_AS_COLUMN}" in conn.calls[1][1]


async def test_schedulable_migrated_and_other_errors():
    conn = _SeqConn([[{"query_slug": "a", RUN_AS_COLUMN: 7}]])
    result = await _repo(conn).schedulable(_tenant_store())
    assert result[0][RUN_AS_COLUMN] == 7
    with pytest.raises(RuntimeError):
        await _repo(_SeqConn([RuntimeError("boom")])).schedulable(_tenant_store())


async def test_get_run_as_values():
    ident = _identity()
    assert await _repo(_SeqConn([{RUN_AS_COLUMN: 42}])).get_run_as(ident) == 42
    assert await _repo(_SeqConn([{RUN_AS_COLUMN: None}])).get_run_as(ident) is None
    assert await _repo(_SeqConn([None])).get_run_as(ident) is None
    missing = UndefinedColumnError(f'column "{RUN_AS_COLUMN}" does not exist')
    assert await _repo(_SeqConn([missing])).get_run_as(ident) is None
