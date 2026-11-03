"""FEAT-184 TASK-952 — definition fields."""
from __future__ import annotations

from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition


def test_query_model_defaults() -> None:
    model = QueryModel(query_slug="s", program_slug="default")
    assert model.user_filtering is False
    assert model.user_filter_options == {}
    assert model.h_filtering is False


def test_query_model_accepts_values() -> None:
    options = {"rules": [{"column": "tenant_id", "attribute": "tenant_id"}]}
    model = QueryModel(
        query_slug="s",
        program_slug="default",
        user_filtering=True,
        user_filter_options=options,
    )
    assert model.user_filtering is True
    assert model.user_filter_options == options


def test_tenant_definition_declares_fields() -> None:
    options = {"rules": [{"column": "tenant_id", "attribute": "tenant_id"}]}
    definition = TenantQueryDefinition(
        query_slug="s",
        user_filtering=True,
        user_filter_options=options,
    )
    assert definition.user_filtering is True
    assert definition.user_filter_options == options
    assert definition.h_filtering is False


def test_legacy_row_with_only_h_filtering_still_validates() -> None:
    definition = TenantQueryDefinition(query_slug="s", h_filtering=True)
    assert definition.h_filtering is True
    assert definition.user_filtering is False
    assert definition.user_filter_options == {}
