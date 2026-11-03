"""FEAT-184 TASK-953 — describe exposure of user_filtering."""
from __future__ import annotations

from querysource.models import QueryModel
from querysource.queries.describe import DescribeGrants, describe_slug


def _describe(model: QueryModel, *, admin: bool) -> dict:
    return describe_slug(model, DescribeGrants(admin=admin), columns_link="c", vocabulary_link="v")


def test_describe_capabilities_user_filtering() -> None:
    model = QueryModel(
        query_slug="s",
        program_slug="acme",
        user_filtering=True,
        user_filter_options={"rules": [{"column": "user_id", "attribute": "user_id"}]},
    )
    caps = _describe(model, admin=False)["derived"]["capabilities"]
    assert caps["user_filtering"] is True and caps["h_filtering"] is True


def test_describe_user_filter_options_admin_only() -> None:
    rules = {"rules": [{"column": "user_id", "attribute": "user_id"}]}
    model = QueryModel(
        query_slug="s",
        program_slug="acme",
        user_filtering=True,
        user_filter_options=rules,
    )

    non_admin_payload = _describe(model, admin=False)
    assert "user_filter_options" not in non_admin_payload
    assert "user_filter_options" in non_admin_payload["redacted"]

    admin_payload = _describe(model, admin=True)
    assert admin_payload["user_filter_options"] == rules


def test_describe_legacy_h_filtering_not_reported() -> None:
    model = QueryModel(query_slug="s", program_slug="acme", h_filtering=True, user_filtering=False)
    caps = _describe(model, admin=False)["derived"]["capabilities"]
    assert caps["user_filtering"] is False and caps["h_filtering"] is False
