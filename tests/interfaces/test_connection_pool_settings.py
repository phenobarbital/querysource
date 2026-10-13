"""Session-hygiene settings carried by every QS.* PostgreSQL connection.

QuerySource acquires a connection, runs one query and releases it, so idle
backends are reaped in minutes. These tests pin the contract between
``querysource.conf`` and the ``pgargs`` handed to asyncdb, and verify that
asyncdb's drivers actually pick the values up (without opening a socket).
"""
import asyncio
import re

import pytest
from asyncdb.drivers.pg import pg, pgPool

from querysource.conf import (
    DB_IDLE_IN_TRANSACTION_TIMEOUT,
    DB_KEEPALIVE_IDLE,
    DB_POOL_MAX_INACTIVE,
    DB_SESSION_TIMEOUT,
)
from querysource.connections import QueryConnection
from querysource.interfaces.connections import Connection

_UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "h": 3600, "d": 86400}
_DUMMY_DSN = "postgres://u:p@127.0.0.1:5432/db"


def _to_seconds(value: str) -> float:
    """Convert a PostgreSQL time GUC literal (``10min``, ``60s``) to seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*", str(value))
    assert match, f"not a PostgreSQL time literal: {value!r}"
    number, unit = match.groups()
    return float(number) * _UNITS[unit or "ms"]


@pytest.mark.parametrize("pgargs", [QueryConnection.pgargs, Connection.pgargs])
def test_pgargs_carry_session_hygiene_settings(pgargs: dict) -> None:
    """Both pgargs dicts send the server-side timeouts and the client prune."""
    settings = pgargs["server_settings"]
    assert settings["idle_session_timeout"] == f"{DB_SESSION_TIMEOUT}"
    assert settings["idle_in_transaction_session_timeout"] == f"{DB_IDLE_IN_TRANSACTION_TIMEOUT}"
    assert settings["tcp_keepalives_idle"] == f"{DB_KEEPALIVE_IDLE}"
    assert pgargs["max_inactive_timeout"] == DB_POOL_MAX_INACTIVE


def test_defaults_are_minutes_not_an_hour() -> None:
    """Idle backends must be reaped well below the historical 60min."""
    assert _to_seconds(DB_SESSION_TIMEOUT) <= 15 * 60
    assert _to_seconds(DB_IDLE_IN_TRANSACTION_TIMEOUT) <= 5 * 60
    assert _to_seconds(DB_KEEPALIVE_IDLE) <= 5 * 60


def test_client_prune_runs_before_server_reaps() -> None:
    """The pool must drop idle connections before PostgreSQL terminates them."""
    assert 0 < DB_POOL_MAX_INACTIVE < _to_seconds(DB_SESSION_TIMEOUT)


async def test_asyncdb_pool_driver_picks_up_pgargs() -> None:
    """``AsyncPool('pg', **pgargs)`` forwards the values to asyncpg knobs."""
    loop = asyncio.get_running_loop()
    pool = pgPool(dsn=_DUMMY_DSN, loop=loop, **dict(QueryConnection.pgargs))
    assert pool._max_inactive_timeout == DB_POOL_MAX_INACTIVE
    assert pool._server_settings["idle_session_timeout"] == f"{DB_SESSION_TIMEOUT}"
    assert pool._server_settings["tcp_keepalives_idle"] == f"{DB_KEEPALIVE_IDLE}"
    assert pool.application_name == "QS.Master"


async def test_asyncdb_connection_driver_accepts_pgargs() -> None:
    """``get_connection()`` builds ``AsyncDB('pg', **pgargs)``; it must not choke."""
    loop = asyncio.get_running_loop()
    conn = pg(dsn=_DUMMY_DSN, loop=loop, **dict(QueryConnection.pgargs))
    assert conn._max_inactive_timeout == DB_POOL_MAX_INACTIVE
    assert conn._server_settings["idle_session_timeout"] == f"{DB_SESSION_TIMEOUT}"
