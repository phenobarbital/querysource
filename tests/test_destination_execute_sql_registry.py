"""Registry + PBAC write-gate tests for the ExecuteSQL step (FEAT-156, TASK-822)."""
from unittest.mock import AsyncMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth._resource_types import ResourceType
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.outputs.destinations import get_destination
from querysource.queries.multi import WRITE_DESTINATIONS, MultiQS
from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination


def _pipeline() -> dict:
    return {
        "queries": {"profile": {"query": "SELECT 1"}},
        "Output": [{"ExecuteSQL": {"sql": "DELETE FROM wm_assembly.t WHERE d < CURRENT_DATE"}}],
    }


def _principal() -> QSPrincipal:
    return QSPrincipal(user_id="35", groups=("ops",))


def test_registry_has_executesql() -> None:
    assert get_destination("ExecuteSQL") is ExecuteSQLDestination
    assert WRITE_DESTINATIONS == frozenset({"TableDelete", "ExecuteSQL"})


async def test_preflight_gate_executesql(monkeypatch) -> None:
    mock = AsyncMock(return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True))
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    mqs = MultiQS(query=_pipeline(), principal=_principal())
    await mqs._preflight_principal()
    assert any(
        call.args[1:4] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")
        for call in mock.await_args_list
    )


async def test_preflight_gate_executesql_denied(monkeypatch) -> None:
    mock = AsyncMock(side_effect=QueryAccessDenied())
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    mqs = MultiQS(query=_pipeline(), principal=_principal())
    with pytest.raises(QueryAccessDenied):
        await mqs._preflight_principal()
