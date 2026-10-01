"""Guarded SQL execution on PostgreSQL with the full-access ``DB*`` credentials (FEAT-156).

Shared by the MultiQuery ``ExecuteSQL`` destination and the FEAT-157 hooks. It must never
import from ``querysource.queries.multi``.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, List, Optional, Union

from asyncdb import AsyncDB

from querysource.conf import default_dsn
from querysource.exceptions import QueryException
from querysource.qs_parsers import HAS_RUST

try:
    from querysource.qs_parsers import sql_guard as _sql_guard
except ImportError:  # extension missing, or built before FEAT-156
    _sql_guard = None

sql_guard: Optional[Callable[[str], List[str]]] = _sql_guard
logger = logging.getLogger(__name__)
_MAX_STATEMENT_TIMEOUT_MS = 2_147_483_647


class GuardedSQLError(QueryException):
    """Guard rejection (category ``data``) or missing extension / DB failure (category ``infra``)."""

    def __init__(self, message: str, *, category: str) -> None:
        """Store ``category`` (``"data"`` | ``"infra"``); ``data`` maps to HTTP 422, ``infra`` to 500."""
        super().__init__(message, code=422 if category == "data" else 500)
        self.category = category


def guard_statements(sql: Union[str, List[str]]) -> List[str]:
    """Return the flattened, guard-approved statements of every script, in order.

    Args:
        sql: One script or a list of scripts.

    Returns:
        The top-level statements, in order.

    Raises:
        GuardedSQLError: empty input or non-str items (``data``); ``HAS_RUST`` False (``infra``);
            a blocked or unparsable statement (``data``, the guard's message).
    """
    scripts = [sql] if isinstance(sql, str) else sql
    if not isinstance(scripts, list) or not scripts:
        raise GuardedSQLError("sql must be a non-empty string or list of strings", category="data")
    if any(not isinstance(script, str) or not script.strip() for script in scripts):
        raise GuardedSQLError("every sql item must be a non-empty string", category="data")
    if not HAS_RUST or sql_guard is None:
        raise GuardedSQLError(
            "SQL guard unavailable (qs_parsers Rust extension not installed); refusing to execute SQL",
            category="infra",
        )
    statements: List[str] = []
    for index, script in enumerate(scripts, start=1):
        try:
            statements.extend(sql_guard(script))
        except ValueError as err:
            raise GuardedSQLError(f"script {index}: {err}", category="data") from err
    if not statements:
        raise GuardedSQLError("sql contains no executable statements", category="data")
    return statements


async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]:
    """Execute ``statements`` in ONE transaction on ``default_dsn`` and return their status tags.

    Args:
        statements: Output of :func:`guard_statements`.
        timeout: Per-statement timeout in seconds (``SET LOCAL statement_timeout``).

    Returns:
        One asyncpg status tag per statement (``"DELETE 1234"``, ``"INSERT 0 900"``).

    Raises:
        GuardedSQLError: ``timeout <= 0`` (``data``); any connection or statement error (``infra``);
            the transaction is rolled back.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise GuardedSQLError("timeout must be a finite number of seconds > 0", category="data")
    if not statements:
        return []
    # Never 0: PostgreSQL treats statement_timeout = 0 as "no timeout". Also cap at the int4
    # maximum PostgreSQL accepts for the setting.
    timeout_ms = min(max(1, math.ceil(timeout * 1000)), _MAX_STATEMENT_TIMEOUT_MS)
    results: List[str] = []
    current = 0
    try:
        db = AsyncDB("pg", dsn=default_dsn)
        async with await db.connection() as conn:
            raw = conn.engine()
            async with raw.transaction():
                await raw.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                for current, statement in enumerate(statements, start=1):
                    status = await raw.execute(statement)
                    logger.debug("guarded_sql: statement %d/%d -> %s", current, len(statements), status)
                    results.append(status)
    except Exception as err:  # every DB/driver failure is infra; the transaction was rolled back
        where = f"statement {current}" if current else "connection"
        raise GuardedSQLError(f"guarded SQL failed at {where}: {err}", category="infra") from err
    logger.info("guarded_sql: committed %d statement(s): %s", len(results), results)
    return results
