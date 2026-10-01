"""Unit tests for querysource.interfaces.guarded_sql (FEAT-156, TASK-820)."""
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

import querysource.interfaces.guarded_sql as guarded_sql
from querysource.conf import default_dsn
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements

needs_rust = pytest.mark.skipif(not guarded_sql.HAS_RUST, reason="Rust extension not installed")


class _Tx:
    """Async context manager recording enter/exit (and the exception type) into ``log``."""

    def __init__(self, log: List[Any]) -> None:
        self.log = log

    async def __aenter__(self) -> "_Tx":
        self.log.append("tx_enter")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.log.append(("tx_exit", exc_type))
        return False


@pytest.fixture
def fake_db(monkeypatch):
    """Patch AsyncDB; return (asyncdb_factory, raw_conn, log)."""
    log: List[Any] = []
    raw = MagicMock()
    raw.transaction = MagicMock(side_effect=lambda: _Tx(log))

    async def _execute(sql: str) -> str:
        log.append(sql)
        return "SET" if sql.startswith("SET LOCAL") else f"{sql.split()[0].upper()} 1"

    raw.execute = AsyncMock(side_effect=_execute)
    conn = MagicMock()
    conn.engine = MagicMock(return_value=raw)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.connection = AsyncMock(return_value=conn)
    factory = MagicMock(return_value=db)
    monkeypatch.setattr(guarded_sql, "AsyncDB", factory)
    return factory, raw, log


@needs_rust
def test_guard_statements_blocked() -> None:
    with pytest.raises(GuardedSQLError) as exc:
        guard_statements("DROP TABLE x")
    assert exc.value.category == "data"


def test_guard_statements_no_rust_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(guarded_sql, "HAS_RUST", False)
    with pytest.raises(GuardedSQLError) as exc:
        guard_statements("DELETE FROM t")
    assert exc.value.category == "infra"


async def test_execute_guarded_uses_default_dsn(fake_db) -> None:
    factory, _raw, _log = fake_db
    await execute_guarded(["DELETE FROM t"])
    factory.assert_called_once_with("pg", dsn=default_dsn)


@needs_rust
def test_guard_statements_flattens() -> None:
    result = guard_statements(
        ["DELETE FROM t WHERE a = 1; INSERT INTO t VALUES (1)", "UPDATE t SET a = 2"]
    )
    assert result == ["DELETE FROM t WHERE a = 1", "INSERT INTO t VALUES (1)", "UPDATE t SET a = 2"]


@pytest.mark.parametrize("bad", ["", [], ["ok", 3], None])
def test_guard_statements_rejects_bad_input(bad) -> None:
    with pytest.raises(GuardedSQLError) as exc:
        guard_statements(bad)
    assert exc.value.category == "data"


async def test_execute_guarded_one_transaction(fake_db) -> None:
    _factory, _raw, log = fake_db
    result = await execute_guarded(["DELETE FROM t", "INSERT INTO t VALUES (1)"], timeout=5)
    assert log == [
        "tx_enter",
        "SET LOCAL statement_timeout = 5000",
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
        ("tx_exit", None),
    ]
    assert result == ["DELETE 1", "INSERT 1"]


async def test_execute_guarded_db_error(fake_db) -> None:
    _factory, raw, log = fake_db
    calls = {"n": 0}

    async def _execute(sql: str) -> str:
        log.append(sql)
        calls["n"] += 1
        if calls["n"] == 3:  # SET LOCAL, stmt 1, stmt 2
            raise asyncpg.exceptions.PostgresError("boom")
        return "OK"

    raw.execute = AsyncMock(side_effect=_execute)
    with pytest.raises(GuardedSQLError) as exc:
        await execute_guarded(["DELETE FROM t", "INSERT INTO t VALUES (1)"])
    assert exc.value.category == "infra"
    assert "statement 2" in str(exc.value)
    assert log[-1] == ("tx_exit", asyncpg.exceptions.PostgresError)


async def test_execute_guarded_rejects_bad_timeout() -> None:
    with pytest.raises(GuardedSQLError) as exc:
        await execute_guarded(["DELETE FROM t"], timeout=0)
    assert exc.value.category == "data"


@pytest.mark.parametrize(
    ("timeout", "expected_ms"),
    [(0.0001, 1), (0.0015, 2), (10**9, 2_147_483_647)],
)
async def test_execute_guarded_timeout_never_zero_and_capped(fake_db, timeout, expected_ms) -> None:
    """Sub-millisecond timeouts must not become 0 (= no limit); huge ones are capped at int4."""
    _factory, _raw, log = fake_db
    await execute_guarded(["DELETE FROM t"], timeout=timeout)
    assert log[1] == f"SET LOCAL statement_timeout = {expected_ms}"


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1])
async def test_execute_guarded_rejects_non_finite_timeout(bad) -> None:
    with pytest.raises(GuardedSQLError) as exc:
        await execute_guarded(["DELETE FROM t"], timeout=bad)
    assert exc.value.category == "data"
