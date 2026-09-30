"""Unit tests for ExecuteSQLDestination (FEAT-156, TASK-821)."""
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import querysource.interfaces.guarded_sql as guarded_sql
import querysource.queries.multi.destinations.execute_sql as execute_sql
from querysource.exceptions import OutputError
from querysource.interfaces.guarded_sql import GuardedSQLError
from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination

REFRESH_SQL = (
    "DELETE FROM wm_assembly.employee_detail_profile "
    "WHERE activity_date >= (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date "
    "AND activity_date < CURRENT_DATE"
)


@pytest.fixture
def data() -> pd.DataFrame:
    return pd.DataFrame({"employee_id": [1, 2]})


@pytest.mark.parametrize("kwargs", [{}, {"sql": ""}, {"sql": []}, {"sql": ["ok", 3]},
                                    {"sql": REFRESH_SQL, "driver": "mysql"},
                                    {"sql": REFRESH_SQL, "timeout": 0}, {"sql": REFRESH_SQL, "timeout": True},
                                    {"sql": REFRESH_SQL, "timeout": float("inf")}, {"sql": REFRESH_SQL, "timeout": float("nan")}])
def test_execsql_init_validation(data, kwargs) -> None:
    with pytest.raises(OutputError) as exc:
        ExecuteSQLDestination(data, **kwargs)
    assert exc.value.category == "data"


async def test_execsql_passthrough(data, monkeypatch) -> None:
    monkeypatch.setattr(execute_sql, "guard_statements", lambda sql: [REFRESH_SQL])
    monkeypatch.setattr(execute_sql, "execute_guarded", AsyncMock(return_value=["DELETE 1234"]))
    dest = ExecuteSQLDestination(data, sql=REFRESH_SQL)
    assert await dest.run() is data
    assert dest.results == ["DELETE 1234"]


@pytest.mark.skipif(not guarded_sql.HAS_RUST, reason="Rust extension not installed")
async def test_execsql_blocked_never_connects(data, monkeypatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr(guarded_sql, "AsyncDB", factory)
    dest = ExecuteSQLDestination(data, sql="DROP TABLE x")
    with pytest.raises(OutputError) as exc:
        await dest.run()
    assert exc.value.category == "data"
    factory.assert_not_called()


async def test_execsql_wraps_guarded_error(data, monkeypatch) -> None:
    original = GuardedSQLError("x", category="infra")
    monkeypatch.setattr(execute_sql, "guard_statements", lambda sql: [REFRESH_SQL])
    monkeypatch.setattr(execute_sql, "execute_guarded", AsyncMock(side_effect=original))
    dest = ExecuteSQLDestination(data, sql=REFRESH_SQL)
    with pytest.raises(OutputError) as exc:
        await dest.run()
    assert exc.value.category == "infra"
    assert exc.value.__cause__ is original
