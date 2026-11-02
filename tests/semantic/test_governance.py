"""Tests for semantic PBAC governance and row-filter selection."""
from __future__ import annotations

import logging

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.semantic import governance
from querysource.semantic.exceptions import SemanticFieldDenied
from querysource.semantic.models import SemanticQuery, SemanticResource


@pytest.mark.asyncio
async def test_governance_pbac_off_allows_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """PBAC disabled permits a check even without a principal."""
    monkeypatch.setattr(governance, "resolve_evaluator", lambda request, *, detached: (False, None))
    decision = await governance.check(None, None, "s", "semantic:execute", logger=logging.getLogger("t"))
    assert decision.allowed
    assert not decision.pbac_enabled


@pytest.mark.asyncio
async def test_governance_field_denied_names_field(fake_evaluator) -> None:
    """A denied metric is reported as a semantic field denial."""
    fake_evaluator.deny.add("epson.sales:metric:margin")
    principal = QSPrincipal(user_id="7", username="a")

    with pytest.raises(SemanticFieldDenied) as exc_info:
        await governance.enforce_query(
            None,
            principal,
            "epson.sales",
            SemanticQuery(metrics=["margin"]),
            logger=logging.getLogger("t"),
        )

    assert exc_info.value.field == "margin"
    assert exc_info.value.code == 403


def test_governance_row_filters_roles_and_default(inline_resource_yaml: str) -> None:
    """Default filters precede role filters and unknown roles are ignored."""
    resource = SemanticResource.from_yaml(inline_resource_yaml)
    principal = QSPrincipal(user_id="7", roles=["analyst", "unknown"])

    filters, matched_roles = governance.governance_filters(resource, principal)
    assert [item.field for item in filters if not isinstance(item, str)] == ["region"]
    assert matched_roles == ["analyst"]

    default_filters, default_roles = governance.governance_filters(resource, None)
    assert default_filters == []
    assert default_roles == []


def test_principal_from_session_shape() -> None:
    """Session identity maps to the principal userinfo shape."""
    principal = governance.principal_from_session({"user": {"user_id": "7", "username": "a", "groups": ["g"]}})
    assert principal is not None
    assert principal.channel == "http"
    assert set(principal.to_userinfo()) >= {"username", "user_id", "groups", "roles"}



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        SemanticQuery(metrics=["orders.margin"]),
        SemanticQuery(dimensions=["orders.region"]),
        SemanticQuery(dimensions=["order_date__month"]),
        SemanticQuery(metrics=["revenue"], filters=[{"field": "region", "op": "=", "value": "ES"}]),
        SemanticQuery(metrics=["revenue"], filters=["orders.region = 'ES'"]),
        SemanticQuery(metrics=["revenue"], order_by=["region desc"]),
    ],
)
async def test_governance_denies_qualified_grained_filter_and_order_by_fields(fake_evaluator, query) -> None:
    """Denials apply to canonical names however a field is spelled or used."""
    fake_evaluator.deny.update(
        {
            "s:metric:margin",
            "s:dimension:region",
            "s:dimension:order_date",
        }
    )
    with pytest.raises(SemanticFieldDenied):
        await governance.enforce_query(
            None, QSPrincipal(user_id="7", username="a"), "s", query, logger=logging.getLogger("t")
        )
