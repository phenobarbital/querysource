"""FEAT-184 TASK-954 — parser user_filter step (requires `make build-inplace`)."""
from __future__ import annotations

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel, QueryObject
from querysource.parsers.sql import SQLParser

OPTIONS = {
    "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"},
    ],
    "program": {"restricted": True},
}
SQL = "SELECT * FROM epson.users {where_cond}"


def _parser(definition: QueryModel, *, principal=None, userinfo=None, filter_: dict | None = None) -> SQLParser:
    parser = SQLParser(
        definition=definition,
        conditions=QueryObject(query_raw=SQL),
        query=SQL,
        principal=principal,
        userinfo=userinfo,
    )
    parser.cond_definition = {}
    parser.filter = filter_ if filter_ is not None else {}
    parser.program_slug = definition.program_slug
    return parser


@pytest.fixture
def definition() -> QueryModel:
    return QueryModel(
        query_slug="s",
        program_slug="epson",
        query_raw=SQL,
        filtering={"active": True},
        user_filtering=True,
        user_filter_options=OPTIONS,
    )


def test_parser_accepts_identity_kwargs(definition: QueryModel) -> None:
    principal = QSPrincipal(user_id="7", programs=("epson",))
    parser = _parser(definition, principal=principal, userinfo={"a": 1})
    assert parser.principal is principal and parser.userinfo == {"a": 1}


def test_parser_user_filter_merges_new_dict(definition: QueryModel) -> None:
    principal = QSPrincipal(user_id="7", programs=("epson",))
    parser = _parser(
        definition,
        principal=principal,
        userinfo={"attributes": {"store_id": ["1", "2"]}},
        filter_=definition.filtering,
    )

    parser._user_filter_sync()

    assert parser.filter is not definition.filtering
    assert definition.filtering == {"active": True}
    assert parser.filter == {"active": True, "user_id": "7", "store_id": ["1", "2"]}
    assert parser.user_filter_applied == ("user_id", "store_id")


def test_parser_user_filter_overrides_caller_key(definition: QueryModel) -> None:
    principal = QSPrincipal(user_id="7", programs=("epson",))
    parser = _parser(
        definition,
        principal=principal,
        userinfo={"attributes": {"store_id": ["1"]}},
        filter_={"user_id": "999"},
    )

    parser._user_filter_sync()

    assert parser.filter["user_id"] == "7"


async def test_parser_renders_in_for_lists(definition: QueryModel) -> None:
    principal = QSPrincipal(user_id="7", programs=("epson",))
    parser = _parser(
        definition,
        principal=principal,
        userinfo={"attributes": {"store_id": ["1", "2"]}},
        filter_={"user_id": "999"},
    )
    parser._user_filter_sync()

    sql = await parser.build_query()

    assert "IN (" in sql
    assert "999" not in sql


def test_parser_denial_propagates(definition: QueryModel) -> None:
    principal = QSPrincipal(user_id="7", programs=("walmart",))
    parser = _parser(definition, principal=principal)

    with pytest.raises(UserFilterDenied):
        parser._user_filter_sync()


def test_parser_noop_without_flag() -> None:
    definition = QueryModel(
        query_slug="s",
        program_slug="epson",
        query_raw=SQL,
        filtering={"active": True},
        user_filtering=False,
    )
    parser = _parser(definition, filter_={"active": True})

    parser._user_filter_sync()

    assert parser.filter == {"active": True}
    assert parser.user_filter_applied == ()
