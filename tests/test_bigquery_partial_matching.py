"""FEAT-180: BigQuery partial-matching operators (Rust and Cython paths)."""
import logging as stdlib_logging
import sys
import types

import pytest

# ``navconfig.logging`` probes Logstash during import. Sandboxed runners deny
# sockets, so retain a minimal fallback while using the real module elsewhere.
try:
    import navconfig.logging  # noqa: F401
except Exception:
    stdlib_logging.Logger.notice = stdlib_logging.Logger.info
    fake_navconfig_logging = types.ModuleType("navconfig.logging")
    fake_navconfig_logging.logging = stdlib_logging
    sys.modules["navconfig.logging"] = fake_navconfig_logging

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers.bigquery import BigQueryParser

SQL = "SELECT * FROM t {where_cond}"
_FAMILIES = [
    ("startswith", "andre", '"andre%"'),
    ("endswith", "andre", '"%andre"'),
    ("contains", "andre", '"%andre%"'),
]
CORPUS = [
    ({"n": {"like": "an_re%"}}, 'n LIKE "an_re%"'),
    ({"n": {"not_like": "an_re%"}}, 'n NOT LIKE "an_re%"'),
    ({"n": {"ilike": "an_re%"}}, 'LOWER(n) LIKE LOWER("an_re%")'),
    ({"n": {"not_ilike": "an_re%"}}, 'LOWER(n) NOT LIKE LOWER("an_re%")'),
]
for _name, _value, _literal in _FAMILIES:
    CORPUS += [
        ({"n": {_name: _value}}, f"n LIKE {_literal}"),
        ({"n": {f"not_{_name}": _value}}, f"n NOT LIKE {_literal}"),
        ({"n": {f"i{_name}": _value}}, f"LOWER(n) LIKE LOWER({_literal})"),
        ({"n": {f"not_i{_name}": _value}}, f"LOWER(n) NOT LIKE LOWER({_literal})"),
    ]


def _rust_supports() -> bool:
    """Return whether the installed Rust extension includes the BigQuery twin."""
    if not bqmod.HAS_RUST:
        return False
    try:
        rendered = bqmod._rs.bq_filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})
    except Exception:
        return False
    return 'n LIKE "andre%"' in rendered


RUST_AVAILABLE = _rust_supports()
PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(not RUST_AVAILABLE, reason="stale _qs_parsers (TASK-851)"),
    ),
    "cython",
]


def _make_parser(filter_: dict, cond_definition: dict | None = None) -> BigQueryParser:
    """Build a parser wired directly to the Cython filtering path."""
    parser = BigQueryParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = cond_definition or {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict, cond_definition: dict | None = None) -> str:
    """Render one filter through the selected implementation path."""
    if path == "rust":
        return bqmod._rs.bq_filter_conditions(SQL, filter_, cond_definition or {})
    return await _make_parser(filter_, cond_definition)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    """Return text following the WHERE clause, when present."""
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1].strip()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_bq_rendering(path: str, filter_: dict, expected: str) -> None:
    """All LIKE-family operators render their BigQuery form."""
    assert _where_body(await _render(path, filter_)) == expected


@pytest.mark.parametrize(
    "filter_,expected",
    [
        ({"n": {"contains": "50%"}}, 'n LIKE "%50\\\\%%"'),
        ({"n": {"contains": 'say "hi"'}}, 'n LIKE "%say \\"hi\\"%"'),
        ({"payload.city": {"contains": "abc"}}, 'JSON_VALUE(payload, \'$.city\') LIKE "%abc%"'),
        ({"meta": {"contains": "abc"}}, 'meta LIKE "%abc%"'),
        ({"meta": {"other": "x"}}, 'JSON_VALUE(meta, \'$.other\') = "x"'),
    ],
)
async def test_bq_escaping_json_and_precedence(filter_: dict, expected: str) -> None:
    """BigQuery literals escape safely and table names precede JSON extraction."""
    assert _where_body(await _render("cython", filter_)) == expected


@pytest.mark.parametrize("operator", ["regex", "iregex", "not_regex", "not_iregex"])
async def test_bq_regex_raises(operator: str) -> None:
    """BigQuery rejects every regex operator through the Cython fallback."""
    with pytest.raises(ParserError):
        await _render("cython", {"n": {operator: "^andre"}})


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("operand", [None, 5, ["a"]])
@pytest.mark.parametrize("operator", ["like", "startswith"])
async def test_bq_non_string_operand_rejected(path: str, operator: str, operand: object) -> None:
    """A non-string operand (notably None) is rejected, never rendered as the string 'None'."""
    with pytest.raises((ParserError, ValueError)):
        await _render(path, {"n": {operator: operand}})
