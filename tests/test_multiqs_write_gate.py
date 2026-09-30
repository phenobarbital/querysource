"""FEAT-155 / TASK-817: PBAC pre-flight gate for write-capable Output destinations."""
from unittest.mock import AsyncMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth._resource_types import ResourceType
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.queries.multi import WRITE_DESTINATIONS, MultiQS, _output_step_names


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", groups=("sales",))


def _pipeline(output: list) -> dict:
    return {"queries": {"a": {"slug": "report_a"}}, "Output": output}


DELETE_STEP = {"TableDelete": {"schema": "s", "table": "t", "pk": ["id"]}}
TABLE_STEP = {"Table": {"schema": "s", "table": "t", "method": "append"}}


def _allow(monkeypatch):
    mock = AsyncMock(
        return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True)
    )
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    return mock


def _datasource_calls(mock):
    return [c for c in mock.await_args_list if c.args[1] == ResourceType.DATASOURCE]


async def test_preflight_gate_enforced(principal, monkeypatch):
    mock = _allow(monkeypatch)
    mqs = MultiQS(query=_pipeline([DELETE_STEP, TABLE_STEP]), principal=principal)
    await mqs._preflight_principal()
    calls = _datasource_calls(mock)
    assert len(calls) == 1
    assert calls[0].args[1:4] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")

    async def _deny(principal, resource_type, *a, **kw):
        if resource_type == ResourceType.DATASOURCE:
            raise QueryAccessDenied()
        return enforcement.AccessDecision(allowed=True, pbac_enabled=True)

    monkeypatch.setattr(enforcement, "enforce_principal", AsyncMock(side_effect=_deny))
    mqs = MultiQS(query=_pipeline([DELETE_STEP]), principal=principal)

    async def _must_not_run(*a, **kw):
        raise AssertionError("no child may run after a denied write gate")

    mqs.get_definition_repository = _must_not_run
    with pytest.raises(QueryAccessDenied):
        await mqs.query()


async def test_preflight_gate_skipped(principal, monkeypatch):
    mock = _allow(monkeypatch)
    await MultiQS(query=_pipeline([TABLE_STEP]), principal=principal)._preflight_principal()
    assert _datasource_calls(mock) == []

    mock = _allow(monkeypatch)
    await MultiQS(query=_pipeline([DELETE_STEP]))._preflight_principal()
    mock.assert_not_awaited()

    assert _output_step_names(None) == set()
    assert _output_step_names([DELETE_STEP, "bad"]) == {"TableDelete"}
    assert "TableDelete" in WRITE_DESTINATIONS
