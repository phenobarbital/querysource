"""Run-as assignment and append-only audit inside the definition write transaction."""
import logging

import pytest
from asyncdb.drivers.pg import UndefinedTableError

from querysource.repositories.definitions import DefinitionRepository
from querysource.tenants import QueryIdentity, QueryStore

SCHED = {"scheduler": {"every": "1h"}}


def _store() -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )


class _TxConn:
    """Records SQL; supports transaction/commit/rollback and scripted results."""

    def __init__(self, previous, written, run_as=None, fail_on=None):
        self.previous = previous
        self.written = written
        self.run_as = run_as
        self.fail_on = fail_on
        self.log = []

    async def transaction(self):
        self.log.append("BEGIN")
        return self

    async def commit(self):
        self.log.append("COMMIT")

    async def rollback(self):
        self.log.append("ROLLBACK")

    async def fetch_one(self, sql, *args, **kwargs):
        sql = sql.strip()
        self.log.append(sql.split("(")[0].strip()[:60])
        if self.fail_on and self.fail_on[0] in sql:
            raise self.fail_on[1]
        if "for update" in sql:
            return {"attributes": self.previous}
        if sql.startswith("SELECT scheduler_run_as_user_id"):
            return {"scheduler_run_as_user_id": self.run_as}
        if sql.startswith("UPDATE") and "scheduler_run_as_user_id" in sql:
            return {"query_slug": "q"}
        if sql.startswith("UPDATE"):
            return {"query_slug": "q", "attributes": self.written}
        if "INSERT INTO" in sql and "_run_as_audit" in sql:
            return {"audit_id": 1}
        if sql.startswith("SAVEPOINT") or "SAVEPOINT" in sql:
            return None
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _repo(conn):
    async def factory():
        return conn

    return DefinitionRepository(registry=None, connection_factory=factory)


def _count(conn, prefix):
    return sum(1 for c in conn.log if c.startswith(prefix))


async def _patch(conn, data=None, actor=7):
    return await _repo(conn).patch(
        QueryIdentity(store=_store(), slug="q"),
        data or {"attributes": {}},
        run_as_actor=actor,
        request_info={"ip": "1.1.1.1"},
    )


def _patched_row(conn):
    orig = conn.fetch_one

    async def fetch_one(sql, *args, **kw):
        res = await orig(sql, *args, **kw)
        if sql.startswith("UPDATE") and isinstance(res, dict) and "attributes" in res:
            res = {**_full_row(), "attributes": conn.written}
        return res

    conn.fetch_one = fetch_one
    return conn


def _full_row():
    return {"query_slug": "q", "description": "d", "attributes": {}, "params": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,written,run_as,operation",
    [({}, SCHED, None, "set"), (SCHED, {"scheduler": {"every": "2h"}}, 3, "change"), (SCHED, {}, 3, "clear")],
)
async def test_run_as_set_change_clear_audited(previous, written, run_as, operation):
    conn = _patched_row(_TxConn(previous, written, run_as))
    await _patch(conn)
    assert _count(conn, "INSERT INTO") == 1
    assert _count(conn, "UPDATE") == 2  # definition + run-as column
    assert conn.log[0] == "BEGIN" and conn.log[-1] == "COMMIT"
    assert "ROLLBACK" not in conn.log


@pytest.mark.asyncio
async def test_run_as_untouched_on_non_schedule_edit():
    conn = _patched_row(_TxConn({}, {}))
    await _patch(conn, {"description": "x"})
    assert _count(conn, "INSERT INTO") == 0
    assert _count(conn, "UPDATE") == 1
    assert conn.log[-1] == "COMMIT"


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_definition():
    conn = _patched_row(_TxConn({}, SCHED, fail_on=("_run_as_audit", RuntimeError("boom"))))
    with pytest.raises(RuntimeError):
        await _patch(conn)
    assert "ROLLBACK" in conn.log
    assert "COMMIT" not in conn.log


@pytest.mark.asyncio
async def test_unmigrated_store_still_commits(caplog):
    conn = _patched_row(_TxConn({}, SCHED, fail_on=("_run_as_audit", UndefinedTableError("x"))))
    with caplog.at_level(logging.WARNING):
        await _patch(conn)
    assert conn.log[-1] == "COMMIT"
    assert "ROLLBACK" not in conn.log
    assert any("not migrated" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_no_actor_leaves_column_and_audit_untouched(caplog):
    conn = _patched_row(_TxConn({}, SCHED))
    with caplog.at_level(logging.WARNING):
        await _patch(conn, actor=None)
    assert _count(conn, "INSERT INTO") == 0
    assert _count(conn, "UPDATE") == 1
    assert any("without an actor" in r.message for r in caplog.records)
