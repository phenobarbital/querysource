"""FEAT-184 TASK-958 — provider capability allowlist for user filtering."""

from __future__ import annotations

import pytest

from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel
from querysource.providers.documentdb import documentdbProvider
from querysource.providers.mysql import mysqlProvider
from querysource.providers.pg import pgProvider
from querysource.providers.sql import sqlProvider

OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"}]}


def _definition(**overrides: object) -> QueryModel:
    data: dict[str, object] = {
        "query_slug": "s",
        "program_slug": "epson",
        "query_raw": "SELECT * FROM t {where_cond}",
        "user_filtering": True,
        "user_filter_options": OPTIONS,
    }
    data.update(overrides)
    return QueryModel(**data)


@pytest.mark.parametrize("provider_class", [mysqlProvider, documentdbProvider])
async def test_non_sql_provider_denies_user_filtering(provider_class: type) -> None:
    """Providers outside the SQL allowlist fail closed during construction."""
    with pytest.raises(UserFilterDenied) as exc:
        provider_class(
            slug="s",
            qstype="slug",
            definition=_definition(),
            conditions={},
        )

    assert exc.value.reason_code == "parser_required"


@pytest.mark.parametrize("provider_class", [sqlProvider, pgProvider])
async def test_sql_provider_hierarchy_allows_user_filtering(provider_class: type) -> None:
    """The SQL provider hierarchy remains opted in to user filtering."""
    provider = provider_class(
        slug="s",
        qstype="slug",
        definition=_definition(),
        conditions={},
    )

    assert provider.user_filtering_required() is True


async def test_non_sql_provider_without_user_filtering_is_unchanged() -> None:
    """The capability guard is inactive for definitions without the feature flag."""
    provider = mysqlProvider(
        slug="s",
        qstype="slug",
        definition=_definition(user_filtering=False),
        conditions={},
    )

    assert provider.user_filtering_required() is False
