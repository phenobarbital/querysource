"""FEAT-148 TASK-735 — describe pagination helpers."""
import pytest

from querysource.handlers._pagination import (
    PaginationParams,
    build_order_by,
    build_scan_sql,
    compose_where,
)


def test_build_order_by_default_unchanged():
    params = PaginationParams.from_query_string({})
    assert build_order_by(params) == 'ORDER BY "updated_at" DESC'


def test_build_order_by_nulls_last():
    params = PaginationParams.from_query_string({})
    assert build_order_by(params, nulls_last=True) == 'ORDER BY "updated_at" DESC NULLS LAST'


@pytest.mark.parametrize("where,extra,expected", [
    ("", "", ""),
    ("", '"provider" = 1', 'WHERE "provider" = 1'),
    ('WHERE "provider" = 1', "", 'WHERE "provider" = 1'),
    ('WHERE "provider" = 1', '"program_slug" = 2', 'WHERE "provider" = 1 AND ("program_slug" = 2)'),
])
def test_compose_where(where, extra, expected):
    assert compose_where(where, extra) == expected


def test_compose_where_rejects_bad_where():
    with pytest.raises(ValueError):
        compose_where('provider = 1', "")


def test_build_scan_sql_shape_and_validation():
    sql = build_scan_sql(
        "troc", "queries",
        ["query_slug", "description"],
        "",
        'ORDER BY "updated_at" DESC',
        100,
    )
    assert sql == (
        'SELECT "query_slug", "description" FROM "troc"."queries" '
        'ORDER BY "updated_at" DESC LIMIT 100'
    )
    assert "OFFSET" not in sql

    with pytest.raises(ValueError):
        build_scan_sql("troc; DROP TABLE", "queries", ["query_slug"], "", "", 10)
    with pytest.raises(ValueError):
        build_scan_sql("troc", "queries", ["password"], "", "", 10)
    with pytest.raises(ValueError):
        build_scan_sql("troc", "queries", [], "", "", 10)
    with pytest.raises(ValueError):
        build_scan_sql("troc", "queries", ["query_slug"], "", "", 0)
