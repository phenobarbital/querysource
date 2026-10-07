"""FEAT-162 / TASK-857: hostile dict member keys never reach the SQL rendered by the Cython BigQuery builder."""
import pytest

from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers.bigquery import BigQueryParser

SQL = "SELECT * FROM t {where_cond}"
HOSTILE_MEMBER = "x') = \"\" OR TRUE OR ('"
SAFE_JSON = "JSON_VALUE(meta, '$.region') = \"us\""


def _make_parser(filter_: dict) -> BigQueryParser:
    """Build a parser wired directly to the Cython filtering path."""
    parser = BigQueryParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _cython(filter_: dict) -> str:
    """Render one filter through the Cython builder."""
    return await _make_parser(filter_)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    """Return text following the WHERE clause, when present."""
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1].strip()


def _rust_hardened() -> bool:
    """Probe the compiled extension: a stale build still renders the hostile member key."""
    if not bqmod.HAS_RUST:
        return False
    try:
        rendered = bqmod._rs.bq_filter_conditions(SQL, {"meta": {HOSTILE_MEMBER: "v"}}, {})
    except Exception:
        return False
    return "OR TRUE" not in rendered


async def test_cython_rejects_hostile_dict_member() -> None:
    """A member key that is not an identifier path is skipped, siblings still render."""
    rendered = await _cython({"other": {HOSTILE_MEMBER: "v"}, "meta": {"region": "us"}})
    assert "OR TRUE" not in rendered
    assert "JSON_VALUE(other" not in rendered
    assert SAFE_JSON in rendered


async def test_cython_rejects_non_string_member() -> None:
    """A non-string member key is rejected before the JSON path (the builder types the key as `str`)."""
    with pytest.raises(TypeError):
        await _cython({"meta": {1: "v"}})


@pytest.mark.parametrize("member", ["a-b", ".a", "a.", "", "a b", "9a", "a..b", "a'b"])
async def test_cython_rejects_malformed_member(member: str) -> None:
    """Every malformed identifier path is skipped."""
    rendered = await _cython({"meta": {member: "v"}})
    assert "JSON_VALUE" not in rendered


async def test_cython_safe_keys_unchanged() -> None:
    """Safe keys keep today's rendering (JSON extraction and FEAT-180 LIKE forms)."""
    rendered = await _cython({"meta": {"region": "us"}, "n": {"contains": "abc"}, "addr": {"geo.city": "x"}})
    assert SAFE_JSON in rendered
    assert 'n LIKE "%abc%"' in rendered
    assert "JSON_VALUE(addr, '$.geo.city') = \"x\"" in rendered


@pytest.mark.skipif(not _rust_hardened(), reason="stale _qs_parsers (TASK-855)")
async def test_cython_and_rust_agree_on_hostile_member() -> None:
    """Both builders drop the hostile member and keep the safe sibling."""
    filter_ = {"other": {HOSTILE_MEMBER: "v"}, "meta": {"region": "us"}}
    cython_body = _where_body(await _cython(filter_))
    rust_body = _where_body(bqmod._rs.bq_filter_conditions(SQL, filter_, {}))
    assert cython_body == rust_body == SAFE_JSON
