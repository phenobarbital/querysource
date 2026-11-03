"""FEAT-184 TASK-955 — provider identity forwarding and fail-closed guard."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel
from querysource.providers.sql import sqlProvider

OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"}]}


def _definition(**overrides) -> QueryModel:
    data = {
        "query_slug": "s",
        "program_slug": "epson",
        "query_raw": "SELECT * FROM t {where_cond}",
        "user_filtering": True,
        "user_filter_options": OPTIONS,
    }
    data.update(overrides)
    return QueryModel(**data)


async def test_provider_forwards_identity_to_parser() -> None:
    principal = QSPrincipal(user_id="7")
    provider = sqlProvider(
        slug="s",
        qstype="slug",
        definition=_definition(),
        conditions={},
        principal=principal,
        userinfo={"x": 1},
    )

    assert provider._parser.principal is principal
    assert provider._parser.userinfo == {"x": 1}


async def test_sql_provider_raw_guard() -> None:
    provider = sqlProvider(
        slug="s",
        qstype="slug",
        definition=_definition(is_raw=True, query_raw="SELECT * FROM t"),
        conditions={},
        connection=MagicMock(),
    )

    with pytest.raises(UserFilterDenied) as exc:
        await provider.prepare_connection()

    assert exc.value.reason_code == "parser_required"


async def test_provider_parserless_guard() -> None:
    class ParserlessProvider(sqlProvider):
        __parser__ = None

    with pytest.raises(UserFilterDenied) as exc:
        ParserlessProvider(
            slug="s",
            qstype="slug",
            definition=_definition(),
            conditions={},
        )

    assert exc.value.reason_code == "parser_required"


async def test_sql_provider_query_without_placeholders_guard() -> None:
    provider = sqlProvider(
        query="SELECT 1",
        qstype="query",
        definition=_definition(query_raw="SELECT 1"),
        conditions={},
        connection=MagicMock(),
    )

    with pytest.raises(UserFilterDenied) as exc:
        await provider.prepare_connection()

    assert exc.value.reason_code == "parser_required"


async def test_provider_no_flag_unchanged() -> None:
    provider = sqlProvider(
        slug="s",
        qstype="slug",
        definition=_definition(user_filtering=False, is_raw=True, query_raw="SELECT * FROM t"),
        conditions={},
        connection=MagicMock(),
    )

    assert await provider.prepare_connection() is provider


async def test_user_identity_changes_rendered_query() -> None:
    definition = _definition()
    first = sqlProvider(
        slug="s",
        qstype="slug",
        definition=definition,
        conditions={},
        principal=QSPrincipal(user_id="7"),
    )
    second = sqlProvider(
        slug="s",
        qstype="slug",
        definition=definition,
        conditions={},
        principal=QSPrincipal(user_id="8"),
    )

    first._parser._user_filter_sync()
    second._parser._user_filter_sync()
    first_query = await first._parser.build_query()
    second_query = await second._parser.build_query()

    assert first_query != second_query
    assert "'7'" in first_query
    assert "'8'" in second_query
